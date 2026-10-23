from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, SyntheticEngine, pack_fixture, write_wave
from rimsky.analysis import cast_voices
from rimsky.casting import CASTING_SCHEMA, casting_conflicts, dialogue_contexts
from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import AppError, write_json


def characters(*names):
    return [{'id': name, 'name': name, 'description': 'low', 'aliases': [], 'evidence': []}
            for name in names]


def voices():
    return [{'id': 'n', 'role': 'narrator', 'speaker': 'Ryan'},
            {'id': 'a', 'speaker': 'Aiden', 'tags': ['low']},
            {'id': 'b', 'speaker': 'Serena', 'tags': ['bright']}]


def passage(speaker, source, ordinal, chapter='c1', kind='paragraph', start=0):
    return {'speaker': speaker, 'source_id': source, 'ordinal': ordinal,
            'chapter_id': chapter, 'kind': kind, 'characters': 30, 'start': start}


def assignment(cast):
    return {character['id']: character['voice_id'] for character in cast}


class ConversationCasting(unittest.TestCase):
    def test_non_interacting_characters_reuse_best_matching_performance(self):
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1), passage('narrator', 'p3', 2), passage('bob', 'p4', 3)]
        cast = cast_voices(characters('alice', 'bob'), voices(), parts)
        self.assertEqual(assignment(cast)['alice'], assignment(cast)['bob'])
        self.assertTrue(all(c['voice_reused'] for c in cast if c['id'] != 'narrator'))
        self.assertEqual(casting_conflicts(cast, voices(), parts), [])

    def test_paragraph_speakers_differ_even_with_narrator_interruptions(self):
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p1', 0, start=8),
                 passage('bob', 'p1', 0, start=20)]
        cast = cast_voices(characters('alice', 'bob'), voices(), parts)
        self.assertNotEqual(assignment(cast)['alice'], assignment(cast)['bob'])
        self.assertEqual(dialogue_contexts(parts)[0]['speakers'], ['alice', 'bob'])

    def test_conversation_spans_adjacent_dialogue_paragraphs(self):
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1), passage('alice', 'p3', 2)]
        cast = cast_voices(characters('alice', 'bob'), voices(), parts)
        self.assertNotEqual(assignment(cast)['alice'], assignment(cast)['bob'])
        self.assertEqual(dialogue_contexts(parts)[0]['source_ids'], ['p1', 'p2', 'p3'])

    def test_separate_conversations_in_same_chapter_reuse_repertoire(self):
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1), passage('narrator', 'p3', 2),
                 passage('narrator', 'p3b', 3), passage('carol', 'p4', 4), passage('dan', 'p5', 5)]
        cast = cast_voices(characters('alice', 'bob', 'carol', 'dan'), voices(), parts)
        assigned = assignment(cast)
        self.assertNotEqual(assigned['alice'], assigned['bob'])
        self.assertNotEqual(assigned['carol'], assigned['dan'])
        self.assertEqual(assigned['alice'], assigned['carol'])
        self.assertEqual(casting_conflicts(cast, voices(), parts), [])

    def test_chapter_boundary_does_not_create_global_voice_reservation(self):
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1, chapter='c2')]
        assigned = assignment(cast_voices(characters('alice', 'bob'), voices(), parts))
        self.assertEqual(assigned['alice'], assigned['bob'])
        self.assertEqual(len(dialogue_contexts(parts)), 2)

    def test_three_speakers_in_a_paragraph_share_only_when_repertoire_runs_out(self):
        parts = [passage(name, 'p1', 0, start=i * 10) for i, name in enumerate(('alice', 'bob', 'carol'))]
        cast = cast_voices(characters('alice', 'bob', 'carol'), voices(), parts)
        self.assertEqual(len({c['voice_id'] for c in cast if c['id'] != 'narrator'}), 2)
        conflicts = casting_conflicts(cast, voices(), parts)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['source_ids'], ['p1'])
        self.assertEqual(len(conflicts[0]['shared_speakers'][0]), 2)

    def test_adding_a_non_interacting_character_does_not_consume_another_voice(self):
        original = [passage('alice', 'p1', 0), passage('bob', 'p2', 1)]
        first = assignment(cast_voices(characters('alice', 'bob'), voices(), original))
        later = original + [passage('narrator', 'p3', 2), passage('narrator', 'p3b', 3), passage('carol', 'p4', 4)]
        second = assignment(cast_voices(characters('carol', 'bob', 'alice'), voices(), later))
        self.assertEqual(first['alice'], second['alice'])
        self.assertEqual(first['bob'], second['bob'])
        self.assertEqual(second['carol'], first['alice'])

    def test_more_available_profiles_do_not_force_more_distinct_voices(self):
        pool = voices() + [{'id': 'c', 'speaker': 'Vivian'}]
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1), passage('narrator', 'p3', 2), passage('bob', 'p4', 3)]
        assigned = assignment(cast_voices(characters('alice', 'bob'), pool, parts))
        self.assertEqual(assigned['alice'], assigned['bob'])

    def test_duplicate_speaker_profiles_are_not_false_distinct_performances(self):
        pool = voices() + [{'id': 'a-copy', 'speaker': 'aIdEn', 'tags': ['low', 'rough']}]
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1)]
        cast = cast_voices(characters('alice', 'bob'), pool, parts)
        selected = {c['voice_id'] for c in cast if c['id'] != 'narrator'}
        self.assertIn('b', selected)
        self.assertEqual(casting_conflicts(cast, pool, parts), [])

    def test_renamed_identical_clone_files_count_as_one_performance(self):
        pool = [{'id': 'n', 'role': 'narrator'},
                {'id': 'a', 'references': {'neutral': {'audio': 'a.wav'}}, 'tags': ['low']},
                {'id': 'b', 'references': {'neutral': {'audio': 'same.wav'}}, 'tags': ['low']},
                {'id': 'c', 'references': {'neutral': {'audio': 'other.wav'}}}]
        files = {'a.wav': '1' * 64, 'same.wav': '1' * 64, 'other.wav': '2' * 64}
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1)]
        cast = cast_voices(characters('alice', 'bob'), pool, parts, files, variant='clone')
        self.assertIn('c', assignment(cast).values())
        self.assertEqual(casting_conflicts(cast, pool, parts, files, variant='clone'), [])

    def test_unknown_speech_does_not_end_a_dialogue_run(self):
        parts = [passage('alice', 'p1', 0), passage('unknown', 'p2', 1), passage('bob', 'p3', 2)]
        contexts = dialogue_contexts(parts)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]['speakers'], ['alice', 'bob'])

    def test_paragraph_fragments_across_batches_are_grouped_together(self):
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p1', 1, start=400),
                 passage('bob', 'p1', 1, start=420)]
        contexts = dialogue_contexts(parts)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]['source_ids'], ['p1'])
        self.assertEqual(contexts[0]['speakers'], ['alice', 'bob'])

    def test_one_narration_paragraph_keeps_a_dialogue_run_open(self):
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1), passage('bob', 'p3', 2)]
        contexts = dialogue_contexts(parts)
        self.assertEqual([['alice', 'bob']], [context['speakers'] for context in contexts])
        self.assertEqual(['p1', 'p3'], contexts[0]['source_ids'])
        cast = cast_voices(characters('alice', 'bob'), voices(), parts)
        self.assertNotEqual(assignment(cast)['alice'], assignment(cast)['bob'])

    def test_second_narration_paragraph_or_scene_change_ends_a_dialogue_run(self):
        quiet = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1), passage('narrator', 'p3', 2), passage('bob', 'p4', 3)]
        scene = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1), {**passage('bob', 'p3', 2), 'scene_id': 's2'}]
        for parts in (quiet, scene):
            with self.subTest(parts=parts):
                self.assertEqual([['alice'], ['bob']], [context['speakers'] for context in dialogue_contexts(parts)])

    def test_headings_end_local_dialogue_runs(self):
        parts = [passage('alice', 'p1', 0), passage('narrator', 'p2', 1, kind='heading'),
                 passage('bob', 'p3', 2)]
        self.assertEqual(len(dialogue_contexts(parts)), 2)

    def test_old_chapter_totals_are_not_misrepresented_as_local_context(self):
        with self.assertRaises(AppError):
            cast_voices(characters('alice'), voices(), [{'speaker': 'alice', 'chapter_id': 'c1', 'characters': 20}])

    def test_recurring_characters_stay_stable_across_interconnected_conversations(self):
        parts = [passage('alice', 'p1', 0), passage('bob', 'p2', 1), passage('narrator', 'p3', 2), passage('narrator', 'p3b', 3),
                 passage('carol', 'p4', 4), passage('dan', 'p5', 5), passage('narrator', 'p6', 6), passage('narrator', 'p6b', 7),
                 passage('bob', 'p7', 8), passage('dan', 'p8', 9)]
        cast = cast_voices(characters('alice', 'bob', 'carol', 'dan'), voices(), parts)
        self.assertEqual(casting_conflicts(cast, voices(), parts), [])
        reversed_cast = cast_voices(characters('dan', 'carol', 'bob', 'alice'), voices(), parts)
        self.assertEqual(assignment(cast), assignment(reversed_cast))

    def test_path_aliases_do_not_invent_distinct_clone_performances(self):
        from rimsky.casting import performance_key
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ref.wav').write_bytes(b'synthetic reference')
            first = {'id': 'a', 'references': {'neutral': {'audio': './ref.wav'}}}
            second = {'id': 'b', 'references': {'neutral': {'audio': str(root / 'ref.wav')}}}
            files = {'ref.wav': '1' * 64}
            self.assertEqual(performance_key(first, 'clone', files, str(root)), performance_key(second, 'clone', files, str(root)))


class ThirdRecheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prepared_generation(self, chapters=None):
        pack = pack_fixture(self.root / 'pack')
        return self.store.create_generation(self.book['id'], {
            'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
            'device': 'cpu', 'pack_name': pack['name'],
        }, chapters=chapters)

    def run_job(self, generation, engine):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(5)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def test_casting_context_saved_for_unselected_chapters(self):
        generation = self.prepared_generation([self.book['chapters'][-1]['id']])
        ready = self.run_job(generation, SyntheticEngine())
        self.assertEqual(ready['status'], 'ready', ready['error'])
        inputs = ready['analysis']['casting_inputs']
        self.assertEqual(ready['analysis']['casting_schema'], CASTING_SCHEMA)
        self.assertTrue(all('source_id' in item and 'ordinal' in item for item in inputs))
        self.assertEqual({item['chapter_id'] for item in inputs}, {c['id'] for c in self.book['chapters']})
        self.assertEqual({s['chapter_id'] for s in ready['segments']}, {self.book['chapters'][-1]['id']})

    def test_incomplete_legacy_casting_reanalyzes_instead_of_using_chapter_totals(self):
        generation = self.prepared_generation()
        from rimsky.analysis import batches
        from rimsky.jobs import base_segments
        from rimsky.util import digest
        bases = base_segments(self.book, [c['id'] for c in self.book['chapters']])
        first = next(batches([s for s in bases if s['chapter_id'] == self.book['chapters'][0]['id']]))
        analysis = {'complete': False, 'completed_batches': [digest([s['id'] for s in first])],
                    'casting_inputs': [{'chapter_id': self.book['chapters'][0]['id'], 'speaker': 'narrator', 'characters': 30}]}
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps(analysis), generation['id']))
        engine = SyntheticEngine()
        ready = self.run_job(generation, engine)
        self.assertEqual(ready['status'], 'ready', ready['error'])
        self.assertEqual(engine.analysis_chapters, {c['id'] for c in self.book['chapters']})
        self.assertTrue(all('source_id' in item for item in ready['analysis']['casting_inputs']))

    def test_frozen_cast_is_not_changed_on_resume(self):
        generation = self.prepared_generation()
        stopped = self.run_job(generation, SyntheticEngine(fail_after=1))
        self.assertEqual(stopped['status'], 'failed')
        snapshot = (stopped['cast'], stopped['analysis'])
        engine = SyntheticEngine()
        ready = self.run_job(stopped, engine)
        self.assertEqual(ready['status'], 'ready', ready['error'])
        self.assertEqual((ready['cast'], ready['analysis']), snapshot)
        self.assertEqual(engine.analyzed, 0)

    def test_deleting_another_book_cannot_collect_in_flight_audio(self):
        generation = self.prepared_generation()
        other = self.store.import_data(FB2.replace(b'Verification book', b'Another book'), 'other.fb2')
        engine = SyntheticEngine()
        original = engine.call
        deleted = False
        def call(method, payload, stop):
            nonlocal deleted
            result = original(method, payload, stop)
            if method == 'render' and not deleted:
                deleted = True
                self.store.delete_book(other['id'])
            return result
        engine.call = call
        ready = self.run_job(generation, engine)
        self.assertTrue(deleted)
        self.assertEqual(ready['status'], 'ready', ready['error'])
        self.assertTrue(all((self.store.root / 'audio' / f'{s["audio_hash"]}.wav').is_file() for s in ready['segments']))

    def test_thread_start_failure_does_not_leave_generation_active(self):
        generation = self.prepared_generation()
        jobs = Jobs(self.store, self.root)
        with patch('rimsky.jobs.threading.Thread.start', side_effect=RuntimeError('injected startup failure')):
            with self.assertRaises((AppError, RuntimeError)):
                jobs.start(generation['id'])
        self.assertIsNone(jobs.active)
        self.assertIsNone(jobs.thread)
        self.assertEqual(self.store.generation(generation['id'])['status'], 'failed')
        jobs.shutdown()

    def test_failed_queue_write_does_not_leave_generation_active(self):
        generation = self.prepared_generation()
        jobs = Jobs(self.store, self.root)
        with patch.object(self.store, 'status', side_effect=RuntimeError('injected queue failure')):
            with self.assertRaises(RuntimeError):
                jobs.start(generation['id'])
        self.assertIsNone(jobs.active)
        self.assertIsNone(jobs.thread)
        self.assertEqual(self.store.generation(generation['id'])['status'], 'paused')


    def test_incomplete_current_casting_resumes_without_reanalyzing_completed_batches(self):
        generation = self.prepared_generation()
        engine = SyntheticEngine()
        original = engine.call
        calls = 0
        def call(method, payload, stop):
            nonlocal calls
            if method == 'analyze':
                calls += 1
                if calls >= 2:
                    raise AppError('injected analysis interruption')
            return original(method, payload, stop)
        engine.call = call
        paused = self.run_job(generation, engine)
        self.assertEqual(paused['status'], 'failed')
        first_inputs = paused['analysis']['casting_inputs']
        resumed_engine = SyntheticEngine()
        ready = self.run_job(paused, resumed_engine)
        self.assertEqual(ready['status'], 'ready', ready['error'])
        self.assertEqual(resumed_engine.analysis_chapters, {self.book['chapters'][1]['id']})
        self.assertEqual(ready['analysis']['casting_inputs'][:len(first_inputs)], first_inputs)
        keys = [(p['source_id'], p['start']) for p in ready['analysis']['casting_inputs']]
        self.assertEqual(len(keys), len(set(keys)))
