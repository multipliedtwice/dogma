from __future__ import annotations

import random
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'verification'))

import rimsky.export as export_module
import rimsky.packs as packs
import server as runtime_server
from rimsky.analysis import boundary_units
from rimsky.casting import cast_summary, cast_voices
from rimsky.export import EXPORT_ORPHANED, check_export_stop
from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import AppError, write_json
from server import Application
from test_core import FB2, SyntheticEngine, VariedInvalidEngine, complete_audio
from test_review_gate_1004 import make_pack

SKEW = 'Bundled runtime components are from different builds. Reinstall the application.'
MISSING_INFER = 'The bundled inference runtime is missing. Reinstall the application.'


def free(lock):
    found = []
    def probe():
        if lock.acquire(blocking=False):
            lock.release()
            found.append(True)
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return bool(found)


def notices(summary):
    return {notice['code']: notice for notice in summary['notices']}


class DialogueUnits(unittest.TestCase):
    def units(self, text):
        return [text[start:end] for start, end in boundary_units(text)]

    def test_colon_and_spaced_hyphen_dialogue_dashes_start_new_units(self):
        cases = {'Он спросил: — Ты кто?': ['Он спросил: ', '— Ты кто?'],
                 'Он спросил: – Ты кто?': ['Он спросил: ', '– Ты кто?'],
                 '- Да, - сказал он. - Нет.': ['- Да, ', '- сказал он. ', '- Нет.'],
                 'Он сказал: - Нет.': ['Он сказал: ', '- Нет.'],
                 '«Да», - сказал он.': ['«Да», ', '- сказал он.'],
                 'Ну… - протянул он. - Может быть!': ['Ну… ', '- протянул он. ', '- Может быть!'],
                 'Кто-то - не я, а сине-зелёный.': ['Кто-то - не я, а сине-зелёный.'],
                 'Итог: 5 - 3 = 2.': ['Итог: 5 - 3 = 2.'],
                 'Она посмотрела на формулу: F = ma.': ['Она посмотрела на формулу: F = ma.'],
                 '— Да, — сказал Иван. — Пойдём.': ['— Да, ', '— сказал Иван. ', '— Пойдём.']}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.units(text))

    def test_hyphen_dialogue_keeps_every_speech_boundary_in_a_long_passage(self):
        text = 'Да. ' * 60 + '- Нет, - сказал он.'
        units = self.units(text)
        self.assertEqual(62, len(units))
        self.assertEqual(['- Нет, ', '- сказал он.'], units[-2:])
        self.assertEqual(text, ''.join(units))

    def test_units_with_colons_and_hyphens_tile_random_text_exactly(self):
        generator = random.Random(10042)
        alphabet = ['а', 'б', 'Да', ' ', ' ', ':', '-', '—', '–', ',', '.', '!', '?', '…', '«', '»', '“', '”', '„', '1', '\n']
        for _ in range(800):
            text = ''.join(generator.choice(alphabet) for _ in range(generator.randint(1, 200)))
            units = boundary_units(text)
            with self.subTest(text=text):
                self.assertEqual(text, ''.join(text[start:end] for start, end in units))
                self.assertTrue(all(start < end for start, end in units))
                self.assertTrue(all(left[1] == right[0] for left, right in zip(units, units[1:])))
                self.assertTrue(all(not text[start].isspace() for start, _ in units[1:]))
                if len(units) > 1:
                    self.assertTrue(all(any(char.isalnum() for char in text[start:end]) for start, end in units))


class DegradedSpeech(unittest.TestCase):
    pool = [{'id': 'n', 'name': 'Narrator', 'role': 'narrator', 'speaker': 'Ryan'},
            {'id': 'a', 'name': 'A', 'speaker': 'Aiden', 'gender': 'male'}, {'id': 'b', 'name': 'B', 'speaker': 'Serena', 'gender': 'female'}]

    def part(self, source, start, characters, ordinal):
        return {'speaker': 'unknown', 'source_id': source, 'start': start, 'characters': characters, 'chapter_id': 'c1',
                'ordinal': ordinal, 'kind': 'paragraph', 'scene_id': ''}

    def test_unresolved_speech_excludes_parts_of_degraded_targets(self):
        parts = [self.part('c1-p00001', 0, 40, 0), self.part('c1-p00002', 60, 30, 1), self.part('c1-p00003', 0, 20, 2)]
        cast = cast_voices([], self.pool, parts)
        degraded = ['c1-p00001-0-40', 'c1-p00002-0-60', None, 'garbage', 'c1-p00009-x-1']
        found = notices(cast_summary(cast, self.pool, parts, degraded=2, degraded_sources=degraded))
        self.assertEqual((2, {'characters': 50}), (found['unresolved_speech']['count'], found['unresolved_speech']['params']))
        self.assertEqual(2, found['degraded_passages']['count'])
        whole = notices(cast_summary(cast, self.pool, parts, degraded=2))
        self.assertEqual((3, {'characters': 90}), (whole['unresolved_speech']['count'], whole['unresolved_speech']['params']))
        alone = notices(cast_summary(cast, self.pool, parts[:1], degraded=1, degraded_sources=degraded[:1]))
        self.assertNotIn('unresolved_speech', alone)
        self.assertEqual(1, alone['degraded_passages']['count'])

    def test_generation_with_only_degraded_passages_reports_no_unresolved_speech(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / 'library')
            try:
                book = store.import_data(FB2, 'fixture.fb2')
                pack = make_pack(root / 'pack')
                generation = store.create_generation(book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                                  'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
                jobs = Jobs(store, root, engine_factory=lambda _: VariedInvalidEngine())
                try:
                    jobs.start(generation['id'])
                    jobs.thread.join(10)
                    self.assertFalse(jobs.thread.is_alive())
                finally:
                    jobs.shutdown()
                done = store.generation(generation['id'])
            finally:
                store.close()
        self.assertEqual('review', done['status'], done['error'])
        self.assertTrue(done['analysis']['degraded'])
        codes = {notice['code']: notice['count'] for notice in done['analysis']['casting_notices']}
        self.assertEqual(len(done['segments']), codes['degraded_passages'])
        self.assertNotIn('unresolved_speech', codes)
        self.assertFalse(any('no resolved speaker' in warning for warning in done['analysis']['casting_warnings']))


class ApplicationCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.addCleanup(self.app.close)
        self.store = self.app.store


class ExportLocking(ApplicationCase):
    def completed(self):
        book = self.store.import_data(FB2, 'fixture.fb2')
        generation = self.store.create_generation(book['id'], {'device': 'cpu', 'pack_name': 'Verification'})
        complete_audio(self.store, generation)
        return self.store.generation(generation['id'])

    def mutate(self, op, data):
        with self.app.mutations:
            return self.app.dispatch(op, data)

    def exported(self):
        return [row['id'] for row in self.store.rows('SELECT id FROM exports')]

    def assert_nothing_published(self):
        self.assertEqual([], self.exported())
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_snapshot_holds_the_mutation_lock_and_the_encode_runs_without_it(self):
        generation = self.completed()
        capture, encoder = export_module.capture_audio, export_module.run_encoder
        held, released = [], []
        def captured(*arguments):
            held.append(not free(self.app.mutations))
            return capture(*arguments)
        def encoded(command, stop=None, **options):
            released.append(free(self.app.mutations))
            return encoder(command, stop, **options)
        with patch.object(export_module, 'capture_audio', side_effect=captured), patch.object(export_module, 'run_encoder', side_effect=encoded):
            result = self.app.dispatch('export', {'id': generation['id'], 'format': 'm4b'})
        self.assertTrue(held and all(held))
        self.assertTrue(released and all(released))
        self.assertEqual([result['id']], self.exported())
        self.assertGreater(result['size'], 128)
        self.assertTrue(free(self.app.mutations))

    def test_second_export_is_refused_and_keeps_the_pending_cancel(self):
        generation = self.completed()
        entered, release, results = threading.Event(), threading.Event(), []
        def blocked(command, stop=None, **options):
            entered.set()
            release.wait(10)
            check_export_stop(stop)
            raise AssertionError('export was not cancelled')
        def first():
            try:
                results.append(self.app.dispatch('export', {'id': generation['id'], 'format': 'm4b'}))
            except AppError as error:
                results.append(error)
        with patch.object(export_module, 'run_encoder', side_effect=blocked), \
                patch.object(export_module, 'resolve_binary', side_effect=lambda name, binaries: name):
            thread = threading.Thread(target=first)
            thread.start()
            self.assertTrue(entered.wait(10))
            self.assertTrue(free(self.app.mutations))
            self.assertEqual({'requested': True}, self.app.dispatch('cancel_export', {}))
            with self.assertRaises(AppError) as refused:
                self.app.dispatch('export', {'id': generation['id'], 'format': 'srt'})
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual('Another export is in progress.', str(refused.exception))
        self.assertEqual('export_cancelled', results[0].code)
        self.assert_nothing_published()
        result = self.app.dispatch('export', {'id': generation['id'], 'format': 'srt'})
        self.assertEqual([result['id']], self.exported())

    def test_deleting_the_generation_or_its_book_during_encoding_publishes_nothing(self):
        for operation, format in (('delete_generation', 'm4b'), ('delete_book', 'srt')):
            with self.subTest(operation=operation):
                generation = self.completed()
                audio = [self.store.root / 'audio' / f'{segment["audio_hash"]}.wav' for segment in generation['segments']]
                target = generation['id'] if operation == 'delete_generation' else generation['book_id']
                freeze, deleted, gone = export_module.freeze_audio, [], []
                def frozen(snapshot):
                    if not deleted:
                        worker = threading.Thread(target=lambda: deleted.append(self.mutate(operation, {'id': target})))
                        worker.start()
                        worker.join(10)
                        gone.append(not any(path.exists() for path in audio))
                    return freeze(snapshot)
                with patch.object(export_module, 'freeze_audio', side_effect=frozen), self.assertRaises(AppError) as raised:
                    self.app.dispatch('export', {'id': generation['id'], 'format': format})
                self.assertEqual(EXPORT_ORPHANED, str(raised.exception))
                self.assertEqual([True], [item['deleted'] for item in deleted])
                self.assertEqual([True], gone)
                self.assertEqual([], self.store.rows('SELECT id FROM generations WHERE id=?', (generation['id'],)))
                self.assert_nothing_published()


class CodexHealth(ApplicationCase):
    def test_codex_availability_is_reported_and_cached_for_a_minute(self):
        with patch.object(runtime_server, 'codex_path', return_value='/opt/homebrew/bin/codex') as found:
            self.assertIs(True, self.app.dispatch('health', {})['codex'])
            found.return_value = None
            self.assertIs(True, self.app.dispatch('health', {})['codex'])
            self.assertEqual(1, found.call_count)
            checked, value = self.app.health_cache['codex']
            self.app.health_cache['codex'] = (checked - 60, value)
            self.assertIs(False, self.app.dispatch('health', {})['codex'])
            self.assertEqual(2, found.call_count)


class FrozenComponents(unittest.TestCase):
    pack = {'name': 'Pack', '_fingerprint': '0' * 64, 'voices': [{}, {}], 'speech': {'variant': 'custom'}}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name) / 'runtime'
        for folder, name in (('core', 'rimsky-runtime'), ('infer', 'rimsky-infer'), ('align', 'rimsky-align')):
            (self.runtime / folder).mkdir(parents=True)
            (self.runtime / folder / name).write_text('', 'utf-8')

    def built(self, **digests):
        for folder, value in digests.items():
            path = self.runtime / folder / 'build-info.json'
            if value is None:
                path.unlink(missing_ok=True)
            elif isinstance(value, str) and len(value) == 64:
                write_json(path, {'component': folder, 'version': '0.1.14', 'source_digest': value})
            else:
                path.write_text(value if isinstance(value, str) else '{"component": "%s"}' % folder, 'utf-8')

    def status(self, frozen=True):
        with patch.object(sys, 'frozen', frozen, create=True), patch.object(sys, 'executable', str(self.runtime / 'core' / 'rimsky-runtime')):
            status = packs.health({'pack_path': 'pack'}, self.runtime, lambda path: self.pack)
        return status['ready'], status['runtime_error']

    def test_components_from_different_builds_block_a_frozen_runtime(self):
        same, other = 'a' * 64, 'b' * 64
        self.built(core=same, infer=other, align=same)
        self.assertEqual((False, SKEW), self.status())
        self.built(core=other, infer=same, align=same)
        self.assertEqual((False, SKEW), self.status())
        self.built(core=same, infer=same, align=other)
        self.assertEqual((False, SKEW), self.status())
        self.built(align=same)
        self.assertEqual((True, ''), self.status())
        (self.runtime / 'align' / 'rimsky-align').unlink()
        self.built(infer=other)
        self.assertEqual((False, SKEW), self.status())
        self.assertEqual('', self.status(frozen=False)[1])

    def test_missing_or_older_build_info_is_not_an_error(self):
        same = 'a' * 64
        for infer in (None, 0, 'not json', '[1, 2]'):
            with self.subTest(infer=infer):
                self.built(core=same, infer=infer, align=same)
                self.assertEqual((True, ''), self.status())
        self.built(core=None, infer=None, align=None)
        self.assertEqual((True, ''), self.status())

    def test_missing_inference_runtime_keeps_its_own_message(self):
        self.built(core='a' * 64, infer='b' * 64)
        (self.runtime / 'infer' / 'rimsky-infer').unlink()
        self.assertEqual((False, MISSING_INFER), self.status())


if __name__ == '__main__':
    unittest.main()
