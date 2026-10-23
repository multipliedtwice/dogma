from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, AliasEngine, CastEngine, SyntheticEngine, VariedInvalidEngine
from rimsky import jobs as jobs_module
from rimsky.analysis import DEGRADED_HOLD
from rimsky.casting import cast_summary
from rimsky.jobs import DELIVERY_NOTICE, NOT_IN_REVIEW, Jobs
from rimsky.packs import load_pack
from rimsky.store import DEFAULTS, POLLED_ANALYSIS, Store
from rimsky.util import AnalysisCapacityError, AppError, file_digest, write_json
import server as runtime_server
from server import LOCK_FREE, Application

VOICES = [
    {'id': 'n', 'name': 'Narrator', 'role': 'narrator', 'family': 'narrator_baseline', 'speaker': 'Ryan'},
    {'id': 'nv', 'name': 'Narrator low', 'role': 'narrator_variant', 'family': 'narrator_low', 'speaker': 'Uncle_Fu'},
    {'id': 'm', 'name': 'Machine', 'role': 'mechanical', 'speaker': 'Dylan'},
    {'id': 'a', 'name': 'A', 'speaker': 'Aiden', 'gender': 'male'},
    {'id': 'b', 'name': 'B', 'speaker': 'Serena', 'gender': 'female'},
]


def make_pack(root, voices=VOICES, aligner=False, **speech):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'model.gguf').write_bytes(b'not-a-real-model; verification only')
    (root / 'tts').mkdir(exist_ok=True)
    (root / 'tts' / 'config.json').write_text('{}')
    files = ['model.gguf', 'tts/config.json']
    pack = {'schema': 1, 'name': 'Review verification pack', 'text_model': {'path': 'model.gguf', 'context': 8192},
            'speech': {'path': 'tts', 'variant': 'custom', 'instruction_control': True, **speech}, 'voices': voices}
    if aligner:
        (root / 'aligner').mkdir(exist_ok=True)
        (root / 'aligner' / 'config.json').write_text('{}')
        files.append('aligner/config.json')
        pack['aligner'] = {'path': 'aligner'}
    pack['files'] = {name: file_digest(root / name) for name in files}
    write_json(root / 'pack.json', pack)
    return load_pack(str(root), verify=True)


class Recording(CastEngine):
    def __init__(self):
        super().__init__()
        self.requests = []

    def call(self, method, payload, stop):
        if method == 'render':
            self.requests.append(payload)
        return super().call(method, payload, stop)


class Gate(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = make_pack(self.root / 'pack')

    def generation(self, pack=None):
        pack = pack or self.pack
        return self.store.create_generation(self.book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                              'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})

    def jobs(self, engine):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        self.addCleanup(jobs.shutdown)
        return jobs

    def finish(self, jobs, generation, narrate=False):
        (jobs.start_narration if narrate else jobs.start)(generation['id'])
        jobs.thread.join(10)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def reviewed(self, engine=None, pack=None):
        engine = engine or Recording()
        jobs = self.jobs(engine)
        done = self.finish(jobs, self.generation(pack))
        self.assertEqual('review', done['status'], done['error'])
        return jobs, engine, done

    def character(self, generation, name):
        return next(character for character in generation['cast'] if character['name'] == name)

    def test_default_setting_stops_for_review_before_any_speech(self):
        self.assertIs(True, DEFAULTS['cast_review'])
        jobs, engine, done = self.reviewed()
        self.assertIsNone(jobs.active)
        self.assertEqual(0, engine.rendered)
        self.assertFalse(any(segment['status'] == 'ready' for segment in done['segments']))
        self.assertIs(False, done['analysis']['approved'])
        self.assertTrue(done['analysis']['complete'])
        self.assertEqual({'stage': 'casting', 'done': 1, 'total': 1}, done['analysis']['progress'])
        self.assertIsInstance(done['analysis']['casting_notices'], list)
        polled = self.store.generation_poll(done['id'])['generation']['analysis']
        self.assertLessEqual(set(polled), set(POLLED_ANALYSIS))
        self.assertLessEqual({'complete', 'approved', 'casting_notices', 'casting_warnings', 'progress', 'degraded'}, set(polled))
        thread, analyzed = jobs.thread, engine.analyzed
        self.assertEqual('review', jobs.start(done['id'])['status'])
        self.assertEqual('review', jobs.repair(done['id'])['status'])
        jobs.pause(done['id'])
        self.assertEqual('review', self.store.generation(done['id'])['status'])
        self.assertIs(thread, jobs.thread)
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertIs(True, ready['analysis']['approved'])
        self.assertEqual(analyzed, engine.analyzed)
        self.assertGreater(engine.rendered, 0)
        with self.assertRaisesRegex(AppError, NOT_IN_REVIEW):
            jobs.start_narration(done['id'])

    def test_disabled_review_continues_into_narration(self):
        self.store.update_settings({'cast_review': False})
        done = self.finish(self.jobs(Recording()), self.generation())
        self.assertEqual('ready', done['status'], done['error'])
        self.assertIs(True, done['analysis']['approved'])

    def test_cast_review_setting_is_a_validated_boolean(self):
        for value in ('yes', 1, None):
            with self.subTest(value=value), self.assertRaises(AppError):
                self.store.update_settings({'cast_review': value})
        self.assertIs(False, self.store.update_settings({'cast_review': False})['cast_review'])

    def test_legacy_complete_analysis_without_approval_still_renders(self):
        jobs, engine, done = self.reviewed()
        legacy = {key: value for key, value in done['analysis'].items() if key != 'approved'}
        self.store.execute('UPDATE generations SET analysis=?,status=? WHERE id=?', (json.dumps(legacy), 'paused', done['id']))
        ready = self.finish(jobs, done)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertGreater(engine.rendered, 0)

    def test_cast_operations_are_refused_outside_review(self):
        generation = self.generation()
        jobs = self.jobs(Recording())
        with self.assertRaisesRegex(AppError, NOT_IN_REVIEW):
            jobs.cast_rename(generation['id'], 'x', 'Name')
        with self.assertRaisesRegex(AppError, NOT_IN_REVIEW):
            jobs.start_narration(generation['id'])
        jobs, engine, done = self.reviewed()
        ready = self.finish(jobs, done, narrate=True)
        masha = self.character(ready, 'Маша')
        with self.assertRaisesRegex(AppError, NOT_IN_REVIEW):
            jobs.cast_voice(ready['id'], masha['id'], 'b')

    def test_rename_keeps_the_previous_name_as_an_alias(self):
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        renamed = jobs.cast_rename(done['id'], ivan['id'], '  Иван Петрович  ')
        entry = next(character for character in renamed['cast'] if character['id'] == ivan['id'])
        self.assertEqual('Иван Петрович', entry['name'])
        self.assertIn('Иван', entry['aliases'])
        for bad in ('', '   ', 'x' * 201, 7):
            with self.subTest(name=bad), self.assertRaises(AppError):
                jobs.cast_rename(done['id'], ivan['id'], bad)
        with self.assertRaisesRegex(AppError, 'Unknown speaker'):
            jobs.cast_rename(done['id'], 'narrator', 'Someone')

    def test_merge_moves_lines_and_casting_inputs_and_keeps_the_target_voice(self):
        jobs, _, done = self.reviewed()
        masha, ivan = self.character(done, 'Маша'), self.character(done, 'Иван')
        with self.assertRaisesRegex(AppError, 'two different'):
            jobs.cast_merge(done['id'], masha['id'], masha['id'])
        merged = jobs.cast_merge(done['id'], ivan['id'], masha['id'])
        self.assertNotIn(ivan['id'], {character['id'] for character in merged['cast']})
        target = self.character(merged, 'Маша')
        self.assertEqual(masha['voice_id'], target['voice_id'])
        self.assertIn('Иван', target['aliases'])
        self.assertNotIn(ivan['id'], {segment['speaker'] for segment in merged['segments']})
        self.assertNotIn(ivan['id'], {entry['speaker'] for entry in merged['analysis']['casting_inputs']})
        self.assertGreaterEqual(sum(segment['speaker'] == masha['id'] for segment in merged['segments']), 2)
        self.assertIn('casting_notices', merged['analysis'])

    def test_voice_choice_follows_role_and_kind_rules(self):
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        for voice in ('n', 'nv', 'm', 'missing'):
            with self.subTest(voice=voice), self.assertRaisesRegex(AppError, 'cannot be used'):
                jobs.cast_voice(done['id'], ivan['id'], voice)
        chosen = self.character(jobs.cast_voice(done['id'], ivan['id'], 'b'), 'Иван')
        self.assertEqual(('b', 'character', 'b'), (chosen['voice_id'], chosen['role'], chosen['family']))
        machine = [{**character, 'kind': 'mechanical'} if character['id'] == ivan['id'] else character for character in done['cast']]
        self.store.execute('UPDATE generations SET cast=? WHERE id=?', (json.dumps(machine), done['id']))
        with self.assertRaisesRegex(AppError, 'cannot be used'):
            jobs.cast_voice(done['id'], ivan['id'], 'a')
        self.assertEqual('m', self.character(jobs.cast_voice(done['id'], ivan['id'], 'm'), 'Иван')['voice_id'])

    def test_voice_change_recomputes_flags_and_notices(self):
        twin = make_pack(self.root / 'twin', voices=[*VOICES, {'id': 'twin', 'name': 'Twin', 'speaker': 'Ryan'}])
        jobs, _, done = self.reviewed(pack=twin)
        ivan = self.character(done, 'Иван')
        self.assertNotIn('narrator_performance_shared', {notice['code'] for notice in done['analysis']['casting_notices']})
        changed = jobs.cast_voice(done['id'], ivan['id'], 'twin')
        notice = next(item for item in changed['analysis']['casting_notices'] if item['code'] == 'narrator_performance_shared')
        self.assertEqual([ivan['id']], notice['params']['ids'])
        self.assertEqual(len(changed['analysis']['casting_notices']), len(changed['analysis']['casting_warnings']))
        self.assertIs(True, self.character(changed, 'Иван')['voice_compatible'])

    def test_mechanical_character_uses_character_voices_when_the_pack_has_none(self):
        plain = make_pack(self.root / 'plain', voices=[voice for voice in VOICES if voice.get('role') != 'mechanical'])
        jobs, _, done = self.reviewed(pack=plain)
        ivan = self.character(done, 'Иван')
        machine = [{**character, 'kind': 'mechanical'} if character['id'] == ivan['id'] else character for character in done['cast']]
        self.store.execute('UPDATE generations SET cast=? WHERE id=?', (json.dumps(machine), done['id']))
        self.assertEqual('a', self.character(jobs.cast_voice(done['id'], ivan['id'], 'a'), 'Иван')['voice_id'])

    def test_narrator_voices_apply_per_chapter_and_as_book_default(self):
        engine = Recording()
        jobs, _, done = self.reviewed(engine)
        first, second = (chapter['id'] for chapter in self.book['chapters'])
        with self.assertRaisesRegex(AppError, 'Unknown chapter'):
            jobs.cast_narrator(done['id'], 'missing', 'nv')
        with self.assertRaisesRegex(AppError, 'narrator voice'):
            jobs.cast_narrator(done['id'], second, 'a')
        changed = jobs.cast_narrator(done['id'], second, 'nv')
        self.assertEqual({second: 'nv'}, changed['analysis']['narrator_voices'])
        self.assertEqual('n', next(character for character in changed['cast'] if character['id'] == 'narrator')['voice_id'])
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        chapters = {segment['spoken']: segment['chapter_id'] for segment in ready['segments'] if segment['speaker'] == 'narrator'}
        used = {(chapters[request['text']], request['voice_id']) for request in engine.requests if request['text'] in chapters}
        self.assertEqual({(first, 'n'), (second, 'nv')}, used)
        other_jobs, _, other = self.reviewed()
        default = other_jobs.cast_narrator(other['id'], '', 'nv')
        narrator = next(character for character in default['cast'] if character['id'] == 'narrator')
        self.assertEqual(('nv', 'narrator_variant'), (narrator['voice_id'], narrator['role']))
        self.assertEqual({'': 'nv'}, default['analysis']['narrator_voices'])

    def test_narrator_notice_follows_the_effective_narrator_of_each_chapter(self):
        kin = make_pack(self.root / 'kin', voices=[*VOICES, {'id': 'kin', 'name': 'Kin', 'family': 'narrator_low', 'speaker': 'Eric'}])
        jobs, _, done = self.reviewed(pack=kin)
        first, second = (chapter['id'] for chapter in self.book['chapters'])
        masha = self.character(done, 'Маша')
        shared = lambda generation: next(((item['count'], item['params']['ids'], item['params']['chapters']) for item in generation['analysis']['casting_notices']
                                          if item['code'] == 'narrator_performance_shared'), None)
        expected = (1, [masha['id']], [second])
        self.assertIsNone(shared(jobs.cast_voice(done['id'], masha['id'], 'kin')))
        self.assertEqual(expected, shared(jobs.cast_narrator(done['id'], '', 'nv')))
        self.assertIsNone(shared(jobs.cast_narrator(done['id'], second, 'n')))
        self.assertIsNone(shared(jobs.cast_narrator(done['id'], first, 'nv')))
        changed = jobs.cast_narrator(done['id'], second, 'nv')
        self.assertEqual(expected, shared(changed))
        self.assertEqual(len(changed['analysis']['casting_notices']), len(changed['analysis']['casting_warnings']))
        self.assertEqual(expected, shared(jobs.cast_rename(done['id'], masha['id'], 'Мария')))
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertEqual(expected, shared(jobs.revise(ready['id'], [{'id': ready['segments'][0]['id'], 'retake': True}])))

    def test_pack_voices_lists_the_pinned_pack(self):
        jobs, _, done = self.reviewed()
        voices = jobs.pack_voices(done['id'])
        self.assertEqual([voice['id'] for voice in VOICES], [voice['id'] for voice in voices])
        self.assertEqual({'id', 'name', 'role', 'family', 'group', 'gender', 'age_bands', 'castable'}, set(voices[0]))
        self.assertEqual(['narrator', 'narrator_variant', 'mechanical', 'character', 'character'], [voice['role'] for voice in voices])
        self.assertIn('pack_voices', LOCK_FREE)
        with self.assertRaises(AppError):
            jobs.pack_voices('missing')

    def test_pack_without_delivery_control_reports_one_generation_notice(self):
        quiet = make_pack(self.root / 'quiet', instruction_control=False)
        engine = Recording()
        jobs, _, done = self.reviewed(engine, quiet)
        expressive = sum(segment['delivery'] != 'neutral' for segment in done['segments'])
        self.assertGreater(expressive, 0)
        notice = next(item for item in done['analysis']['casting_notices'] if item['code'] == 'delivery_not_supported')
        self.assertEqual({'code': 'delivery_not_supported', 'count': expressive, 'params': {'count': expressive}}, notice)
        self.assertIn(DELIVERY_NOTICE.format(count=expressive), done['analysis']['casting_warnings'])
        self.assertEqual(len(done['analysis']['casting_notices']), len(done['analysis']['casting_warnings']))
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual({'neutral'}, {request['delivery'] for request in engine.requests})
        self.assertFalse(any('delivery control' in segment['warning'] for segment in ready['segments']))

    def test_unmerged_alias_pairs_reach_casting_as_hints(self):
        engine = AliasEngine(lambda payload: AnalysisCapacityError('Controlled pair limit'))
        seen = []
        real = jobs_module.cast_voices
        def spy(*arguments, **options):
            seen.append(options.get('same_hints'))
            return real(*arguments, **options)
        with patch.object(jobs_module, 'cast_voices', side_effect=spy):
            _, _, done = self.reviewed(engine)
        self.assertEqual([[tuple(pair) for pair in done['analysis']['unmerged_pairs']]], seen)
        self.assertTrue(seen[0])
        self.assertIn('alias_unmerged_capacity', {notice['code'] for notice in done['analysis']['casting_notices']})

    def test_degraded_notice_counts_only_passages_of_rendered_chapters(self):
        chapter = self.book['chapters'][0]['id']
        generation = self.store.create_generation(self.book['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                                    'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False}, chapters=[chapter])
        done = self.finish(self.jobs(VariedInvalidEngine()), generation)
        self.assertEqual('review', done['status'], done['error'])
        self.assertGreater(len(done['analysis']['degraded']), len(done['segments']))
        notice = next(item for item in done['analysis']['casting_notices'] if item['code'] == 'degraded_passages')
        self.assertEqual(len(done['segments']), notice['count'])
        self.assertEqual({DEGRADED_HOLD}, {segment['warning'] for segment in done['segments']})

    def test_revision_recomputes_the_casting_summary_from_corrected_speakers(self):
        self.store.update_settings({'cast_review': False})
        jobs = self.jobs(Recording())
        ready = self.finish(jobs, self.generation())
        self.assertNotIn('unresolved_speech', {notice['code'] for notice in ready['analysis']['casting_notices']})
        segment = next(item for item in ready['segments'] if item['speaker'] != 'narrator')
        revision = jobs.revise(ready['id'], [{'id': segment['id'], 'speaker': 'unknown'}])
        entry = next(item for item in revision['analysis']['casting_inputs'] if (item['source_id'], item['start']) == (segment['source_id'], segment['start']))
        self.assertEqual('unknown', entry['speaker'])
        self.assertIn('unresolved_speech', {notice['code'] for notice in revision['analysis']['casting_notices']})
        self.assertNotIn('unresolved_speech', {notice['code'] for notice in self.store.generation(ready['id'])['analysis']['casting_notices']})

    def test_retake_renders_a_new_take_of_unchanged_text(self):
        self.store.update_settings({'cast_review': False})
        engine = Recording()
        jobs = self.jobs(engine)
        ready = self.finish(jobs, self.generation())
        segment = ready['segments'][0]
        with self.assertRaisesRegex(AppError, 'Invalid passage correction'):
            jobs.revise(ready['id'], [{'id': segment['id'], 'retake': False}])
        first = jobs.revise(ready['id'], [{'id': segment['id'], 'retake': True}])
        self.assertEqual({segment['id']: 1}, first['analysis']['takes'])
        rendered = engine.rendered
        retaken = self.finish(jobs, first)
        self.assertEqual('ready', retaken['status'], retaken['error'])
        self.assertEqual(rendered + 1, engine.rendered)
        self.assertNotEqual(segment['audio_hash'], retaken['segments'][0]['audio_hash'])
        self.assertEqual([item['audio_hash'] for item in ready['segments'][1:]], [item['audio_hash'] for item in retaken['segments'][1:]])
        sibling = jobs.revise(ready['id'], [{'id': segment['id'], 'retake': True}])
        self.assertEqual({segment['id']: 2}, sibling['analysis']['takes'])
        again = self.finish(jobs, sibling)
        self.assertNotIn(again['segments'][0]['audio_hash'], {segment['audio_hash'], retaken['segments'][0]['audio_hash']})

    def test_active_is_cleared_before_the_terminal_status_is_published(self):
        jobs = self.jobs(Recording())
        observed = []
        real = self.store.status
        def spy(gid, status, error=''):
            if status in ('ready', 'failed', 'paused', 'review'):
                observed.append((status, jobs.active))
            return real(gid, status, error)
        with patch.object(self.store, 'status', side_effect=spy):
            self.finish(jobs, self.generation())
        self.assertEqual([('review', None)], observed)


def line(speaker, ordinal, chapter):
    return {'speaker': speaker, 'source_id': f'p{ordinal}', 'ordinal': ordinal, 'chapter_id': chapter, 'start': 0, 'characters': 50,
            'kind': 'paragraph', 'scene_id': ''}


class NarratorOverrideNotices(unittest.TestCase):
    pool = [{'id': 'n', 'role': 'narrator', 'family': 'narrator_baseline', 'speaker': 'Ryan'},
            {'id': 'nv', 'role': 'narrator_variant', 'family': 'narrator_low', 'speaker': 'Uncle_Fu'},
            {'id': 'soft', 'role': 'narrator_variant', 'speaker': 'Vivian'}, {'id': 'kin', 'family': 'narrator_low', 'speaker': 'Eric'},
            {'id': 'fu', 'speaker': 'uncle_fu'}, {'id': 'twin', 'speaker': 'Ryan'}, {'id': 'a', 'speaker': 'Aiden'}]
    cast = [{'id': 'narrator', 'voice_id': 'n'}, {'id': 'ivan', 'name': 'Ivan', 'voice_id': 'kin'},
            {'id': 'masha', 'name': 'Masha', 'voice_id': 'fu'}, {'id': 'petr', 'name': 'Petr', 'voice_id': 'a'}]
    lines = [line('ivan', 1, 'c1'), line('petr', 2, 'c1'), line('masha', 3, 'c2'), line('petr', 4, 'c2')]

    def shared(self, narrators=None, cast=None, **options):
        summary = cast_summary(cast or self.cast, self.pool, self.lines, narrators=narrators, **options)
        notice = next((item for item in summary['notices'] if item['code'] == 'narrator_performance_shared'), None)
        return notice and (notice['count'], notice['params']['ids'], notice['params']['chapters'])

    def test_chapter_override_sharing_a_family_or_speaker_with_a_speaker_of_that_chapter_is_noticed(self):
        self.assertEqual((1, ['ivan'], ['c1']), self.shared({'c1': 'nv'}))
        self.assertEqual((1, ['masha'], ['c2']), self.shared({'c2': 'nv'}))
        self.assertEqual((2, ['ivan', 'masha'], ['c1', 'c2']), self.shared({'c2': 'nv', 'c1': 'nv'}))
        summary = cast_summary(self.cast, self.pool, self.lines, narrators={'c1': 'nv'})
        self.assertEqual(len(summary['notices']), len(summary['warnings']))
        self.assertEqual(['Ivan'], next(item for item in summary['notices'] if item['code'] == 'narrator_performance_shared')['params']['names'])

    def test_override_of_a_chapter_where_the_character_does_not_speak_is_not_noticed(self):
        distinct = [*self.cast[:2], {**self.cast[2], 'voice_id': 'a'}, self.cast[3]]
        self.assertIsNone(self.shared({'c2': 'nv'}, distinct))
        self.assertIsNone(self.shared({'c1': 'nv'}, rendered_chapters=['c2']))

    def test_distinct_override_is_not_noticed(self):
        self.assertIsNone(self.shared({'c1': 'soft', 'c2': 'soft'}))
        twin = [self.cast[0], {**self.cast[1], 'voice_id': 'twin'}, *self.cast[2:]]
        self.assertIsNone(self.shared({'c1': 'soft'}, twin))
        self.assertEqual((1, ['ivan'], ['c1']), self.shared({'c2': 'soft'}, twin))

    def test_book_default_override_applies_to_chapters_without_their_own(self):
        self.assertEqual((2, ['ivan', 'masha'], ['c1', 'c2']), self.shared({'': 'nv'}))
        self.assertEqual((1, ['ivan'], ['c1']), self.shared({'': 'nv', 'c2': 'n'}))
        self.assertEqual((1, ['masha'], ['c2']), self.shared({'': 'soft', 'c2': 'nv'}))

    def test_without_overrides_the_cast_narrator_rule_is_unchanged(self):
        self.assertIsNone(self.shared())
        twin = [self.cast[0], {**self.cast[1], 'voice_id': 'twin'}, *self.cast[2:]]
        self.assertEqual((1, ['ivan'], ['c1']), self.shared(None, twin))
        self.assertEqual((1, ['ivan'], ['c1']), self.shared(None, twin[1:]))
        whole = cast_summary(twin, self.pool, self.lines)
        self.assertEqual(whole, cast_summary(twin, self.pool, self.lines, narrators={}))
        self.assertEqual(whole['warnings'], cast_summary(twin, self.pool, self.lines, narrators={'c1': 'n'})['warnings'])


class Startup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'library'

    def test_startup_removes_only_transient_leftovers(self):
        store = Store(self.root)
        book = store.import_data(FB2, 'fixture.fb2')
        store.close()
        leftovers = [self.root / 'books' / '.import-abc' / 'book' / 'source.fb2', self.root / 'books' / '.deleted-abc' / 'source.fb2',
                     self.root / 'work' / 'tmp123' / 'audio.wav', self.root / 'work' / 'stray.txt', self.root / 'audio' / '.pending-x1']
        kept = [self.root / 'books' / 'notes' / 'keep.txt', self.root / 'audio' / 'notes.txt', self.root / 'books' / ('f' * 32) / 'source.fb2',
                self.root / 'audio' / ('e' * 64 + '.wav'), self.root / 'audio' / ('e' * 64 + '.json')]
        for path in leftovers + kept:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x')
        store = Store(self.root, recover_interrupted=True)
        self.addCleanup(store.close)
        self.assertEqual([], [str(path) for path in leftovers if path.exists()])
        self.assertTrue(all(path.exists() for path in kept))
        self.assertTrue((self.root / 'books' / book['id'] / 'source.fb2').is_file())
        self.assertEqual([], [path for path in (self.root / 'books').iterdir() if path.name.startswith('.import-')])
        self.assertEqual([], list((self.root / 'work').iterdir()))

    def test_unreferenced_audio_is_collected_after_a_job_leaves_active(self):
        store = Store(self.root)
        self.addCleanup(store.close)
        store.update_settings({'cast_review': False})
        book = store.import_data(FB2, 'fixture.fb2')
        pack = make_pack(Path(self.temporary.name) / 'pack')
        generation = store.create_generation(book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                          'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
        stray = store.root / 'audio' / ('d' * 64 + '.wav')
        stray.write_bytes(b'unreferenced')
        jobs = Jobs(store, Path(self.temporary.name), engine_factory=lambda _: SyntheticEngine())
        self.addCleanup(jobs.shutdown)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        self.assertEqual('ready', store.generation(generation['id'])['status'])
        self.assertFalse(stray.exists())
        self.assertTrue(all((store.root / 'audio' / f'{segment["audio_hash"]}.wav').exists() for segment in store.generation(generation['id'])['segments']))


class Served(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pack = make_pack(self.root / 'pack')
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.addCleanup(self.app.close)
        self.app.store.update_settings({'pack_path': self.pack['_root'], 'device': 'cpu'})
        self.app.jobs.engine_factory = lambda _: CastEngine()
        self.book = self.app.store.import_data(FB2, 'fixture.fb2')

    def wait(self, gid, statuses):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = self.app.store.generation(gid)
            if current['status'] in statuses and not self.app.jobs.active:
                return current
            time.sleep(.02)
        self.fail('Generation did not settle.')

    def dispatch(self, op, data):
        with self.app.mutations:
            return self.app.dispatch(op, data)

    def test_review_operations_are_routed_through_the_server(self):
        real = runtime_server.health
        def ready(settings, binaries, verify=None):
            return {**real(settings, binaries, verify), 'ready': True, 'missing': []}
        with patch.object(runtime_server, 'health', ready):
            created = self.dispatch('generate', {'book_id': self.book['id']})
        review = self.wait(created['id'], ('review',))
        ivan = next(character for character in review['cast'] if character['name'] == 'Иван')
        self.assertEqual('Иван Иванович', next(character['name'] for character in self.dispatch('cast_rename', {'id': review['id'], 'character_id': ivan['id'], 'name': 'Иван Иванович'})['cast'] if character['id'] == ivan['id']))
        self.assertEqual(5, len(self.app.dispatch('pack_voices', {'id': review['id']})))
        self.dispatch('cast_voice', {'id': review['id'], 'character_id': ivan['id'], 'voice_id': 'a'})
        self.dispatch('cast_narrator', {'id': review['id'], 'voice_id': 'nv'})
        self.dispatch('start_narration', {'id': review['id']})
        done = self.wait(review['id'], ('ready', 'failed'))
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual('nv', done['analysis']['narrator_voices'][''])

    def test_generate_reports_the_runtime_error(self):
        def missing(settings, binaries, verify=None):
            return {'ready': False, 'missing': ['rimsky-infer'], 'pack_error': '', 'runtime_error': 'The bundled inference runtime is missing. Reinstall the application.'}
        with patch.object(runtime_server, 'health', missing), self.assertRaisesRegex(AppError, 'Reinstall the application'):
            self.dispatch('generate', {'book_id': self.book['id']})


if __name__ == '__main__':
    unittest.main()
