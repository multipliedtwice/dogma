"""Portable isolated candidate tests; never open a real library/profile."""
import json
import sqlite3
import sys
import threading
import unittest
from contextlib import contextmanager, closing
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parent
ROOT = next(parent for parent in CANDIDATE.parents if (parent / 'runtime/rimsky/store.py').is_file())
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'verification')]
import rimsky
rimsky.__path__.insert(0, str(CANDIDATE / 'runtime/rimsky'))
from rimsky import listener_stress
from rimsky.listener_family_projection import project
from rimsky import listener_family_projection
from rimsky.store import Store
from rimsky.series_characters import identity, seed_cast, series_state, upsert_character
from test_listener_stress_chronology import ListenerStressChronologyTest as _Fixtures


class FamilyProjectionTest(unittest.TestCase):
    setUp = _Fixtures.setUp
    tearDown = _Fixtures.tearDown
    store = _Fixtures.store
    report = _Fixtures.report
    def rows(self, store, book):
        return series_state(store, book)[1]

    def family(self, store, book, key='алекса'):
        return next(row for row in self.rows(store, book) if row['character_key'] == key)

    def test_noncast_family_creates_durable_identity_without_speaker_or_voice_approval(self):
        store, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        report = self.report(store, generation, 'Алекса', 'Але́кса')
        row = self.family(store, book)
        self.assertEqual('Алекса', row['canonical_name'])
        self.assertEqual('Але́кса', row['state']['pronunciations']['алекса'])
        self.assertEqual(report['id'], row['state']['listener_family_projection']['алекса']['report_id'])
        self.assertEqual('listener_pronunciation_family', row['state']['identity_source'])
        self.assertEqual('Алекса', seed_cast(store, book)[0]['name'])
        for key in ('voice_id', 'voice_source', 'speaker_verdict', 'playback_acceptance', 'kind', 'character_id'):
            self.assertNotIn(key, row['state'])
        self.assertEqual([], store.generation(generation['id'])['cast'])

    def test_shared_later_volume_rehydrates_after_origin_book_deleted(self):
        one, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        report = self.report(one, generation, 'Алекса', 'Але́кса')
        one.delete_book(book['id'])
        two, later, _ = self.store('two', 'Она ждала Алексу.', volume=5)
        self.assertEqual([], self.rows(two, later))
        frozen = listener_stress.snapshot(two, later)
        projected = self.family(two, later)
        self.assertEqual('Але́кса', projected['state']['pronunciations']['алекса'])
        self.assertEqual(report['id'], projected['state']['listener_family_projection']['алекса']['report_id'])
        self.assertEqual([], two.stress_reports(later['id']))
        self.assertEqual('Але́ксой Але́ксу.', listener_stress.apply('Алексой Алексу.', frozen)[0])

    def test_latest_human_choice_survives_old_report_reimport_and_other_library(self):
        one, first, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        old = self.report(one, generation, 'Алекса', 'Але́кса', '2026-10-23T00:00:00Z')
        two, later, other = self.store('two', 'Алекса ждала Алексу.', volume=5)
        new = self.report(two, other, 'Алекса', 'А́лекса', '2026-10-24T00:00:00Z')
        with listener_stress._journal(one) as db:
            raw_before = [tuple(row) for row in db.execute('SELECT report_id,raw,created,rowid FROM corrections ORDER BY rowid')]
        listener_stress.record(one, first, old)
        listener_stress.snapshot(one, first)
        for store, book in ((one, first), (two, later)):
            frozen = listener_stress.snapshot(store, book)
            row = self.family(store, book)
            self.assertEqual('А́лекса', row['state']['pronunciations']['алекса'])
            self.assertEqual(new['id'], row['state']['listener_family_projection']['алекса']['report_id'])
            self.assertEqual('А́лекса А́лексой А́лексу.', listener_stress.apply('Алекса Алексой Алексу.', frozen)[0])
        with listener_stress._journal(one) as db:
            self.assertEqual(raw_before, [tuple(row) for row in db.execute('SELECT report_id,raw,created,rowid FROM corrections ORDER BY rowid')])

    def test_same_second_uses_journal_order_not_replay_order(self):
        store, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        first = self.report(store, generation, 'Алекса', 'Але́кса')
        second = self.report(store, generation, 'Алекса', 'А́лекса')
        listener_stress.record(store, book, first)
        row = self.family(store, book)
        self.assertEqual('А́лекса', row['state']['pronunciations']['алекса'])
        self.assertEqual(second['id'], row['state']['listener_family_projection']['алекса']['report_id'])

    def test_snapshot_and_project_are_idempotent(self):
        store, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        self.report(store, generation, 'Алекса', 'Але́кса')
        listener_stress.snapshot(store, book)
        before = store.rows('SELECT * FROM series_character_state')
        with listener_stress._journal(store) as db:
            journal_before = [tuple(row) for row in db.execute('SELECT * FROM correction_resolutions ORDER BY id')]
        with patch.object(store, 'execute', wraps=store.execute) as writes:
            listener_stress.snapshot(store, book)
            project(store, book)
            self.assertEqual([], [call for call in writes.call_args_list if 'INSERT INTO series_character_state' in call.args[0]])
        self.assertEqual(before, store.rows('SELECT * FROM series_character_state'))
        with listener_stress._journal(store) as db:
            self.assertEqual(journal_before, [tuple(row) for row in db.execute('SELECT * FROM correction_resolutions ORDER BY id')])

    def test_existing_character_identity_voice_and_review_state_are_preserved(self):
        character = {'id': 'existing-alexa', 'name': 'Алекса', 'aliases': ['Лекса'], 'kind': 'person', 'voice_id': 'reviewed-voice'}
        store, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.', [character])
        upsert_character(store, identity(store, book), character, state={'voice_id': 'reviewed-voice',
            'voice_source': 'listener', 'custom_review': {'accepted': True}})
        self.report(store, generation, 'Алекса', 'Але́кса')
        row = self.family(store, book)
        self.assertEqual(['Лекса'], row['aliases'])
        for key, value in {'character_id': 'existing-alexa', 'voice_id': 'reviewed-voice',
            'voice_source': 'listener', 'kind': 'person', 'custom_review': {'accepted': True}}.items():
            self.assertEqual(value, row['state'][key])
        self.assertEqual(1, len(self.rows(store, book)))

    def test_common_lowercase_and_ambiguous_names_do_not_create_identity(self):
        for index, (source, word, marked) in enumerate((
            ('Он не видел мира.', 'мира', 'мира́'),
            ('Стоит человек.', 'Стоит', 'Стои́т'),
            ('Она увидела Алекса. Алекс вошёл. Она говорила с Алексой.', 'Алекса', 'Але́кса'))):
            store, book, generation = self.store(str(index), source, series='Isolated ' + str(index))
            self.report(store, generation, word, marked)
            listener_stress.snapshot(store, book)
            self.assertEqual([], self.rows(store, book))

    def test_stale_lowercase_family_projection_is_never_imported(self):
        store, book, generation = self.store('one', 'Он не видел мира.')
        report = self.report(store, generation, 'мира', 'мира́')
        with listener_stress._journal(store) as db:
            db.execute('UPDATE corrections SET family=?,occurrence=NULL WHERE report_id=?',
                (json.dumps({'мира': 'Мира́'}), report['id']))
        project(store, book)
        self.assertEqual([], self.rows(store, book))

    def test_projection_failure_preserves_raw_note_and_returns_explicit_hold(self):
        store, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        with patch('rimsky.listener_family_projection.project', side_effect=OSError('fixture projection unavailable')):
            report = self.report(store, generation, 'Алекса', 'Але́кса')
        self.assertEqual('updated', report['character_state']['status'])
        held = report['character_state']['identity_projection']
        self.assertEqual('held', held['status'])
        self.assertTrue(held['raw_report_saved'])
        self.assertIn('fixture projection unavailable', held['error'])
        self.assertEqual(report['id'], store.stress_reports(book['id'])[0]['id'])
        with listener_stress._journal(store) as db:
            self.assertEqual(report['id'], db.execute('SELECT report_id FROM corrections').fetchone()[0])
        listener_stress.snapshot(store, book)
        self.assertEqual('Але́кса', self.family(store, book)['state']['pronunciations']['алекса'])

    def test_independent_store_writer_cannot_overtake_an_old_projection(self):
        one, book, generation = self.store('one', 'Она говорила с Алексой. Алекса вошла.')
        self.report(one, generation, 'Алекса', 'Але́кса', '2026-10-23T00:00:00Z')
        two = Store(one.root, correction_profile=self.profile)
        self.stores.append(two)
        authority_read = threading.Event()
        release_old = threading.Event()
        writer_attempt = threading.Event()
        writer_done = threading.Event()
        errors, reports = [], []
        real_rows = listener_family_projection._project_rows
        real_journal = listener_stress._journal

        def delayed_rows(*args):
            if threading.current_thread().name == 'old-projector':
                authority_read.set()
                if not release_old.wait(5):
                    raise TimeoutError('Fixture did not release old projector')
            return real_rows(*args)

        @contextmanager
        def observed_journal(store):
            if threading.current_thread().name == 'new-human-report':
                writer_attempt.set()
            with real_journal(store) as db:
                yield db

        def old_projector():
            try:
                project(one, book)
            except BaseException as error:
                errors.append(error)

        def new_report():
            try:
                reports.append(self.report(two, generation, 'Алекса', 'А́лекса', '2026-10-24T00:00:00Z'))
            except BaseException as error:
                errors.append(error)
            finally:
                writer_done.set()

        with patch.object(listener_family_projection, '_project_rows', side_effect=delayed_rows), \
                patch.object(listener_stress, '_journal', side_effect=observed_journal):
            old = threading.Thread(target=old_projector, name='old-projector')
            newer = threading.Thread(target=new_report, name='new-human-report')
            old.start()
            try:
                self.assertTrue(authority_read.wait(5))
                # SQLite's reservation is cross-connection/process, not a
                # Python instance mutex. This assertion fails without it.
                with closing(sqlite3.connect(self.profile, timeout=0)) as probe:
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        probe.execute('BEGIN IMMEDIATE')
                newer.start()
                self.assertTrue(writer_attempt.wait(5))
                self.assertFalse(writer_done.is_set())
            finally:
                release_old.set()
                old.join(5)
                if newer.ident is not None:
                    newer.join(5)
            self.assertFalse(old.is_alive())
            self.assertFalse(newer.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(reports))
        state = self.family(one, book)['state']
        self.assertEqual('А́лекса', state['pronunciations']['алекса'])
        self.assertEqual(reports[0]['id'], state['listener_family_projection']['алекса']['report_id'])

    def test_legacy_validation_borrows_the_reserved_journal_connection(self):
        store, book, _ = self.store('one', 'Гундахар вошёл.')
        evidence = {'report_id': 'old-human-fixture', 'book_id': 'deleted-volume', 'generation_id': 'old-fixture',
            'selected_text': 'Гундахар', 'intended_stress': 'Гунда́хар', 'created': '2026-10-23T00:00:00Z', 'source': 'listener'}
        raw = {key: evidence[key] for key in ('book_id', 'generation_id', 'selected_text', 'intended_stress', 'created')}
        raw.update(id='legacy-family-fixture', kind='legacy_family', original_report_id=evidence['report_id'],
            legacy_evidence=evidence, legacy_series_id=identity(store, book)['id'],
            legacy_character={'canonical_name': 'Гундахар'})
        listener_stress.record(store, book, raw, _project=False)
        real_journal = listener_stress._journal
        depth = 0

        @contextmanager
        def nonnested_journal(store):
            nonlocal depth
            self.assertEqual(0, depth, 'Legacy validation must not open a second writer connection')
            depth += 1
            try:
                with real_journal(store) as db:
                    yield db
            finally:
                depth -= 1

        with patch.object(listener_stress, '_journal', side_effect=nonnested_journal):
            self.assertEqual([], project(store, book)['held'])
        self.assertEqual('Гунда́хар', self.family(store, book, 'гундахар')['state']['pronunciations']['гундахар'])

    def test_projection_rejects_local_transaction_before_reserving_journal(self):
        store, book, _ = self.store('one')
        store.db.execute('BEGIN IMMEDIATE')
        try:
            with patch.object(listener_stress, '_journal') as journal:
                with self.assertRaisesRegex(RuntimeError, 'Commit the local library transaction'):
                    project(store, book)
                journal.assert_not_called()
        finally:
            store.db.rollback()


del _Fixtures

if __name__ == '__main__':
    unittest.main()
