from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, SyntheticEngine, write_wave
from test_review_gate_1004 import VOICES, Recording, line, make_pack
from rimsky import store as store_module
from rimsky.analysis import ALIASES
from rimsky.casting import NOTICES, resolve_kind
from rimsky.jobs import NOT_IN_REVIEW, RENDER_FAILED, Jobs, legacy_key, recast, segment_pauses
from rimsky.packs import CONTEXT_ERROR, health, load_pack, require_context, verified_pack
from rimsky.store import Store
from rimsky.util import AppError, file_digest, write_json
from server import LOCK_FREE, Application

SECOND = FB2.replace(b'Verification book', 'Вторая книга'.encode())
LEGACY_DELIVERY = 'This model has no validated delivery control; neutral performance was requested.'


class Held(Recording):
    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.waiting = threading.Event()

    def call(self, method, payload, stop):
        if method == 'probe' and self.analyzed:
            self.waiting.set()
            self.gate.wait(10)
        return super().call(method, payload, stop)


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.store.update_settings({'device': 'cpu'})
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = make_pack(self.root / 'pack')

    def generation(self, pack=None, book=None):
        pack = pack or self.pack
        return self.store.create_generation((book or self.book)['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
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

    def recasted(self, generation, character_id, **changes):
        cast = [{**character, **changes} if character['id'] == character_id else character for character in generation['cast']]
        self.store.execute('UPDATE generations SET cast=? WHERE id=?', (json.dumps(cast, ensure_ascii=False), generation['id']))

    def small_pack(self, context=4096):
        root = self.root / f'small-{context}'
        make_pack(root)
        manifest = json.loads((root / 'pack.json').read_text('utf-8'))
        write_json(root / 'pack.json', {**manifest, 'text_model': {**manifest['text_model'], 'context': context}})
        return load_pack(str(root), verify=True, pinned=True)

    def legacy_audio(self, generation):
        assigned = {character['id']: character['voice_id'] for character in generation['cast']}
        keys = {}
        for segment in generation['segments']:
            voice = assigned['narrator' if segment['speaker'] in ('narrator', 'unknown') else segment['speaker']]
            keys[segment['id']] = key = legacy_key(generation['config'], self.book, segment, voice)
            path = self.store.root / 'audio' / f'{key}.wav'
            saved = write_wave(path)
            write_json(path.with_suffix('.json'), {**saved, 'sha256': file_digest(path), 'warning': LEGACY_DELIVERY, 'reset_cues': False})
        return keys

    def mixed(self, generation):
        return {segment['id']: len(segment['performance']['analysis_parts']) for segment in generation['segments']
                if segment['performance'].get('internal_transition')}

    def padded(self, generation):
        return {segment['id']: segment_pauses(segment) for segment in generation['segments'] if segment_pauses(segment)}

    def single(self, generation, keys):
        return {sid: key for sid, key in keys.items() if sid not in self.mixed(generation) and sid not in self.padded(generation)}

    def assert_padded_reuse(self, before, after, keys):
        durations = {segment['id']: segment['duration'] for segment in after['segments']}
        hashes = {segment['id']: segment['audio_hash'] for segment in after['segments']}
        for sid, pauses in self.padded(before).items():
            self.assertNotEqual(keys[sid], hashes[sid])
            self.assertAlmostEqual(.1 + sum(pauses), durations[sid], places=3)


class StartupCleanup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / 'library'

    def test_restored_older_database_keeps_unknown_book_folders_and_audio(self):
        store = Store(self.library)
        store.update_settings({'cast_review': False, 'device': 'cpu'})
        store.import_data(FB2, 'fixture.fb2')
        store.close()
        shutil.copy2(self.library / 'library.sqlite3', self.root / 'backup.sqlite3')
        store = Store(self.library)
        book = store.import_data(SECOND, 'second.fb2')
        pack = make_pack(self.root / 'pack')
        generation = store.create_generation(book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                          'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
        jobs = Jobs(store, self.root, engine_factory=lambda _: SyntheticEngine())
        jobs.start(generation['id'])
        jobs.thread.join(10)
        ready = store.generation(generation['id'])
        self.assertEqual('ready', ready['status'], ready['error'])
        audio = [store.root / 'audio' / f'{segment["audio_hash"]}.wav' for segment in ready['segments']]
        store.close()
        shutil.copy2(self.root / 'backup.sqlite3', self.library / 'library.sqlite3')
        for suffix in ('-wal', '-shm'):
            (self.library / f'library.sqlite3{suffix}').unlink(missing_ok=True)
        store = Store(self.library)
        self.addCleanup(store.close)
        self.assertEqual([], [item['id'] for item in store.library() if item['id'] == book['id']])
        self.assertTrue((self.library / 'books' / book['id'] / 'source.fb2').is_file())
        self.assertTrue(all(path.is_file() and path.with_suffix('.json').is_file() for path in audio))

    def test_fresh_database_beside_an_existing_library_deletes_nothing(self):
        store = Store(self.library)
        book = store.import_data(FB2, 'fixture.fb2')
        store.close()
        stray = self.library / 'audio' / ('c' * 64 + '.wav')
        stray.write_bytes(b'kept')
        for name in ('library.sqlite3', 'library.sqlite3-wal', 'library.sqlite3-shm'):
            (self.library / name).unlink(missing_ok=True)
        store = Store(self.library)
        self.addCleanup(store.close)
        self.assertEqual([], store.library())
        self.assertTrue((self.library / 'books' / book['id'] / 'source.fb2').is_file())
        self.assertTrue(stray.is_file())

    def test_failed_book_removal_is_finished_at_the_next_start(self):
        store = Store(self.library)
        book = store.import_data(FB2, 'fixture.fb2')
        failed = OSError('Synthetic locked file')
        with patch.object(store_module.shutil, 'rmtree', side_effect=failed):
            result = store.delete_book(book['id'])
        self.assertEqual([f'Book was removed from the library, but its stored source could not be removed: {failed}'], result['cleanup_warnings'])
        self.assertFalse((self.library / 'books' / book['id']).exists())
        trash = [path for path in (self.library / 'books').iterdir() if path.name.startswith('.deleted-')]
        self.assertEqual(1, len(trash))
        self.assertTrue((trash[0] / 'source.fb2').is_file())
        store.close()
        store = Store(self.library, recover_interrupted=True)
        self.addCleanup(store.close)
        self.assertFalse(trash[0].exists())
        again = store.import_data(FB2, 'fixture.fb2')
        self.assertEqual({'deleted': True, 'cleanup_warnings': []}, store.delete_book(again['id']))
        self.assertEqual([], list((self.library / 'books').iterdir()))


class MinimumContext(Base):
    def test_complete_analysis_on_a_pinned_4096_pack_still_renders_and_repairs(self):
        small = self.small_pack()
        jobs, engine, done = self.reviewed()
        config = {**done['config'], 'pack_path': small['_root'], 'pack_fingerprint': small['_fingerprint']}
        self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(config), done['id']))
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        rendered = engine.rendered
        (self.store.root / 'audio' / f'{ready["segments"][0]["audio_hash"]}.wav').write_bytes(b'damaged')
        for key in ready['segments'][0]['performance'].get('render_parts') or []:
            (self.store.root / 'audio' / f'{key}.wav').unlink(missing_ok=True)
        jobs.repair(done['id'])
        jobs.thread.join(10)
        repaired = self.store.generation(done['id'])
        self.assertEqual('ready', repaired['status'], repaired['error'])
        self.assertEqual(rendered + 1, engine.rendered)
        self.assertEqual(len(VOICES), len(jobs.pack_voices(done['id'])))

    def test_unfinished_analysis_on_a_small_pack_is_refused_before_it_starts(self):
        small = self.small_pack(8191)
        generation = self.generation(small)
        engine = Recording()
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            self.jobs(engine).start(generation['id'])
        self.assertEqual(('paused', 0), (self.store.generation(generation['id'])['status'], engine.analyzed))

    def test_only_pinned_loads_accept_a_4096_to_8191_context(self):
        for context in (4096, 8191):
            small = self.small_pack(context)
            with self.subTest(context=context):
                for load in (lambda: load_pack(small['_root']), lambda: verified_pack(small['_root'], {})):
                    with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
                        load()
                self.assertEqual(small['_fingerprint'], verified_pack(small['_root'], {}, pinned=True)['_fingerprint'])
                with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
                    require_context(small)
        tiny = self.small_pack(4096)
        manifest = json.loads((Path(tiny['_root']) / 'pack.json').read_text('utf-8'))
        write_json(Path(tiny['_root']) / 'pack.json', {**manifest, 'text_model': {**manifest['text_model'], 'context': 4095}})
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            load_pack(tiny['_root'], pinned=True)
        self.assertIs(self.pack, require_context(self.pack))

    def test_selection_health_and_new_generations_require_8192(self):
        small = self.small_pack()
        status = health({'pack_path': small['_root'], 'character_analysis': 'local'}, self.root / 'bin')
        self.assertEqual((False, CONTEXT_ERROR, None), (status['ready'], status['pack_error'], status['pack']))
        app = Application(self.root / 'app-library', self.root / 'bin')
        self.addCleanup(app.close)
        app.store.update_settings({'character_analysis': 'local'})
        book = app.store.import_data(FB2, 'fixture.fb2')
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            app.dispatch('select_pack', {'path': small['_root']})
        self.assertEqual('', app.store.settings()['pack_path'])
        self.assertEqual({}, app.pack_verification_cache)
        self.assertEqual(small['_root'], app.verify_pack(small['_root'], pinned=True)['_root'])
        self.assertEqual(small['_root'], app.jobs._pack({'pack_path': small['_root'], 'pack_fingerprint': small['_fingerprint']}, verify=True)['_root'])
        app.store.update_settings({'pack_path': small['_root']})
        with app.mutations, self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            app.dispatch('generate', {'book_id': book['id']})
        self.assertEqual([], app.store.book(book['id'])['generations'])
        self.assertEqual(self.pack['_root'], app.dispatch('select_pack', {'path': self.pack['_root']})['settings']['pack_path'])


class CastKind(Base):
    def setUp(self):
        super().setUp()
        self.store.update_settings({'cast_review': True})

    def test_listener_name_stress_override_keeps_the_model_guess(self):
        import json as _json
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        acute = '́'
        pronunciation = {'policy': 'auto-pronunciation-v1', 'common': [], 'names': {ivan['id']: {
            'name': 'Иван', 'stressed': 'И' + acute + 'ван', 'confidence': 'low', 'basis': 'fixture', 'source': 'model'}}}
        analysis = {**done['analysis'], 'pronunciation': pronunciation}
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (_json.dumps(analysis), done['id']))
        changed = jobs.cast_name_stress(done['id'], ivan['id'], 'Ива' + acute + 'н')
        row = changed['analysis']['pronunciation']['names'][ivan['id']]
        self.assertEqual(('Ива' + acute + 'н', 'listener', 'И' + acute + 'ван'), (row['stressed'], row['source'], row['model_stressed']))
        with self.assertRaisesRegex(AppError, 'changes the letters'):
            jobs.cast_name_stress(done['id'], ivan['id'], 'Ива' + acute + 'нн')
        with self.assertRaisesRegex(AppError, 'no automatic name stress'):
            jobs.cast_name_stress(done['id'], self.character(done, 'Маша')['id'], 'Ма' + acute + 'ша')

    def test_person_voted_mechanical_gets_a_character_voice_when_corrected(self):
        jobs, _, done = self.reviewed()
        ivan, masha = self.character(done, 'Иван'), self.character(done, 'Маша')
        self.recasted(done, ivan['id'], kind='mechanical', kind_votes={'mechanical': 3, 'person': 1}, voice_id='m')
        with self.assertRaisesRegex(AppError, 'cannot be used'):
            jobs.cast_voice(done['id'], ivan['id'], 'a')
        corrected = self.character(jobs.cast_kind(done['id'], ivan['id'], 'person'), 'Иван')
        self.assertEqual(('person', {'mechanical': 3, 'person': 4}, 'character'), (corrected['kind'], corrected['kind_votes'], corrected['role']))
        self.assertEqual('person', resolve_kind(corrected['kind_votes']))
        self.assertEqual(({'a', 'b'} - {masha['voice_id']}).pop(), corrected['voice_id'])
        self.assertIs(False, corrected['voice_reused'])
        other = 'a' if corrected['voice_id'] == 'b' else 'b'
        self.assertEqual(other, self.character(jobs.cast_voice(done['id'], ivan['id'], other), 'Иван')['voice_id'])

    def test_mechanical_correction_moves_a_person_to_a_mechanical_voice(self):
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        changed = jobs.cast_kind(done['id'], ivan['id'], 'mechanical')
        machine = self.character(changed, 'Иван')
        self.assertEqual(('mechanical', 'm', 'mechanical'), (machine['kind'], machine['voice_id'], machine['role']))
        self.assertEqual('mechanical', resolve_kind(machine['kind_votes']))
        self.assertIn('casting_notices', changed['analysis'])
        self.assertEqual(len(changed['analysis']['casting_notices']), len(changed['analysis']['casting_warnings']))

    def test_kind_change_inside_the_same_pool_keeps_the_chosen_voice(self):
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        chosen = 'a' if ivan['voice_id'] == 'b' else 'b'
        jobs.cast_voice(done['id'], ivan['id'], chosen)
        collective = self.character(jobs.cast_kind(done['id'], ivan['id'], 'collective'), 'Иван')
        self.assertEqual(('collective', chosen), (collective['kind'], collective['voice_id']))
        person = self.character(jobs.cast_kind(done['id'], ivan['id'], 'person'), 'Иван')
        self.assertEqual(('person', chosen), (person['kind'], person['voice_id']))
        self.assertEqual('person', resolve_kind(person['kind_votes']))

    def test_mechanical_kind_without_mechanical_voices_keeps_the_voice_and_reports_it(self):
        plain = make_pack(self.root / 'plain', voices=[voice for voice in VOICES if voice.get('role') != 'mechanical'])
        jobs, _, done = self.reviewed(pack=plain)
        ivan = self.character(done, 'Иван')
        changed = jobs.cast_kind(done['id'], ivan['id'], 'mechanical')
        self.assertEqual(('mechanical', ivan['voice_id']), (self.character(changed, 'Иван')['kind'], self.character(changed, 'Иван')['voice_id']))
        notice = next(item for item in changed['analysis']['casting_notices'] if item['code'] == 'no_mechanical_voice')
        self.assertEqual([ivan['id']], notice['params']['ids'])

    def test_kind_is_validated_and_only_changed_during_review(self):
        jobs, _, done = self.reviewed()
        ivan = self.character(done, 'Иван')
        for kind in ('robot', '', None, 5, ['person']):
            with self.subTest(kind=kind), self.assertRaisesRegex(AppError, '^Unknown character kind.$'):
                jobs.cast_kind(done['id'], ivan['id'], kind)
        with self.assertRaisesRegex(AppError, 'Unknown speaker'):
            jobs.cast_kind(done['id'], 'narrator', 'person')
        ready = self.finish(jobs, done, narrate=True)
        with self.assertRaisesRegex(AppError, NOT_IN_REVIEW):
            jobs.cast_kind(ready['id'], ivan['id'], 'mechanical')
        self.assertNotIn('cast_kind', LOCK_FREE)

    def test_recast_keeps_known_gender_compatibility_before_avoiding_local_collisions(self):
        def entry(source, speaker, characters=20, chapter='c1'):
            return {'chapter_id': chapter, 'source_id': source, 'ordinal': int(source[1:]), 'start': 0, 'speaker': speaker,
                    'kind': 'paragraph', 'characters': characters, 'scene_id': ''}
        female = {'value': 'female', 'support': {'female': ['p1']}}
        cast = [{'id': 'narrator', 'voice_id': 'n'}, {'id': 'x', 'kind': 'person', 'gender': female, 'voice_id': 'm'},
                {'id': 'y', 'kind': 'person', 'voice_id': 'b'}]
        together = [entry('p1', 'x'), entry('p2', 'y')]
        self.assertEqual('b', recast(cast, 'x', self.pack, together)['id'])
        apart = [entry('p1', 'x'), entry('p2', 'y', chapter='c2')]
        self.assertEqual('b', recast(cast, 'x', self.pack, apart)['id'])

    def kin_pack(self):
        return make_pack(self.root / 'kin', voices=[*VOICES[:4], {'id': 'kin', 'name': 'Kin', 'family': 'narrator_low', 'speaker': 'Eric'},
                                                   {'id': 'twin', 'name': 'Twin', 'speaker': 'Ryan'}])

    def test_recast_avoids_the_narrator_of_every_chapter_where_the_character_speaks(self):
        pack = self.kin_pack()
        cast = [{'id': 'narrator', 'voice_id': 'n'}, {'id': 'x', 'kind': 'person', 'voice_id': 'm'}, {'id': 'y', 'kind': 'person', 'voice_id': 'a'}]
        lines = [line('x', 1, 'c1'), line('x', 2, 'c2'), line('y', 3, 'c3')]
        pick = lambda inputs, narrators=None: recast(cast, 'x', pack, inputs, narrators)['id']
        self.assertEqual('kin', pick(lines[1:]))
        self.assertEqual('twin', pick(lines[1:], {'c2': 'nv'}))
        self.assertEqual('twin', pick(lines[1:], {'': 'nv'}))
        self.assertEqual('kin', pick(lines[1:], {'': 'nv', 'c2': 'n'}))
        self.assertEqual('kin', pick(lines[1:], {'c1': 'nv'}))
        self.assertEqual('a', pick(lines, {'c2': 'nv'}))
        self.assertEqual('twin', pick(lines[2:], {'': 'nv'}))

    def test_kind_correction_avoids_the_chapter_narrator_chosen_in_review(self):
        jobs, _, done = self.reviewed(pack=self.kin_pack())
        ivan, masha, second = self.character(done, 'Иван'), self.character(done, 'Маша'), self.book['chapters'][1]['id']
        self.recasted(jobs.cast_narrator(done['id'], second, 'nv'), ivan['id'], voice_id='a')
        self.recasted(self.store.generation(done['id']), masha['id'], kind='mechanical', kind_votes={'mechanical': 1}, voice_id='m')
        changed = jobs.cast_kind(done['id'], masha['id'], 'person')
        self.assertEqual(('a', 'twin'), (self.character(changed, 'Иван')['voice_id'], self.character(changed, 'Маша')['voice_id']))
        self.assertNotIn('narrator_performance_shared', {notice['code'] for notice in changed['analysis']['casting_notices']})

    def test_server_routes_cast_kind_under_the_mutation_lock(self):
        app = Application(self.root / 'app-library', self.root / 'bin')
        self.addCleanup(app.close)
        with patch.object(app.jobs, 'cast_kind', return_value={'id': 'g'}) as operation:
            self.assertEqual({'id': 'g'}, app.dispatch('cast_kind', {'id': 'g', 'character_id': 'c', 'kind': 'person'}))
        operation.assert_called_once_with('g', 'c', 'person')


class ReviewFlow(Base):
    def setUp(self):
        super().setUp()
        self.store.update_settings({'cast_review': True})

    def test_correction_during_review_creates_a_revision_that_waits_for_review(self):
        jobs, engine, done = self.reviewed()
        ivan, masha = self.character(done, 'Иван'), self.character(done, 'Маша')
        segment = next(item for item in done['segments'] if item['speaker'] == ivan['id'])
        revision = jobs.revise(done['id'], [{'id': segment['id'], 'speaker': masha['id']}])
        self.assertEqual(('review', False, True), (revision['status'], revision['analysis']['approved'], revision['analysis']['complete']))
        other = 'b' if masha['voice_id'] == 'a' else 'a'
        self.assertEqual(other, self.character(jobs.cast_voice(revision['id'], masha['id'], other), 'Маша')['voice_id'])
        self.assertEqual('review', jobs.start(revision['id'])['status'])
        ready = self.finish(jobs, revision, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertEqual('review', self.store.generation(done['id'])['status'])

    def test_queued_narration_does_not_report_the_finished_casting_stage(self):
        engine = Held()
        jobs, _, done = self.reviewed(engine)
        self.assertEqual({'stage': 'casting', 'done': 1, 'total': 1}, done['analysis']['progress'])
        jobs.start_narration(done['id'])
        self.assertTrue(engine.waiting.wait(5))
        polled = self.store.generation_poll(done['id'])['generation']
        self.assertEqual(('queued', True), (polled['status'], polled['analysis']['approved']))
        self.assertNotIn('progress', polled['analysis'])
        engine.gate.set()
        jobs.thread.join(10)
        ready = self.store.generation(done['id'])
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertNotIn('progress', ready['analysis'])

    def test_auto_approved_casting_keeps_no_finished_stage_and_resume_keeps_the_analysis(self):
        self.store.update_settings({'cast_review': False})
        engine = Held()
        engine.gate.set()
        jobs = self.jobs(engine)
        ready = self.finish(jobs, self.generation())
        self.assertEqual((True, True), (ready['analysis']['complete'], ready['analysis']['approved']))
        self.assertNotIn('progress', ready['analysis'])
        self.store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND ordinal=(SELECT min(ordinal) FROM segments WHERE generation_id=?)", (ready['id'], ready['id']))
        self.store.status(ready['id'], 'paused')
        self.assertEqual(ready['analysis'], jobs.start(ready['id'])['analysis'])
        jobs.thread.join(10)
        self.assertEqual(ready['analysis'], self.store.generation(ready['id'])['analysis'])
        unfinished = self.generation()
        progress = {'stage': 'analysis', 'done': 1, 'total': 4}
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({'complete': False, 'progress': progress}), unfinished['id']))
        engine.gate.clear()
        engine.waiting.clear()
        queued = jobs.start(unfinished['id'])
        self.assertEqual(progress, queued['analysis']['progress'])
        engine.gate.set()
        jobs.thread.join(10)

    def test_rename_and_merge_keep_user_names_when_the_alias_list_is_full(self):
        jobs, _, done = self.reviewed()
        ivan, masha = self.character(done, 'Иван'), self.character(done, 'Маша')
        full = [f'Прозвище{index}' for index in range(ALIASES)]
        self.recasted(done, ivan['id'], aliases=full)
        self.recasted(self.store.generation(done['id']), masha['id'], aliases=full)
        renamed = self.character(jobs.cast_rename(done['id'], ivan['id'], 'Иван Петрович'), 'Иван Петрович')
        self.assertEqual((ALIASES, 'Иван'), (len(renamed['aliases']), renamed['aliases'][0]))
        merged = jobs.cast_merge(done['id'], masha['id'], ivan['id'])
        target = self.character(merged, 'Иван Петрович')
        self.assertEqual(ALIASES, len(target['aliases']))
        self.assertEqual(['Маша', 'Иван'], target['aliases'][:2])


class RenderCompatibility(Base):
    def test_failed_passage_warning_keeps_the_final_worker_line(self):
        self.store.update_settings({'cast_review': False})
        message = 'Local inference process exited before completing its request. Process output: ' + 'noise ' * 600 + 'RuntimeError: CUDA error: an illegal memory access was encountered'
        class Crashing(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    raise AppError(message)
                return super().call(method, payload, stop)
        failed = self.finish(self.jobs(Crashing()), self.generation())
        self.assertEqual(('failed', RENDER_FAILED), (failed['status'], failed['error']))
        warning = next(segment['warning'] for segment in failed['segments'] if segment['status'] == 'failed')
        self.assertEqual(2000, len(warning))
        self.assertTrue(warning.startswith('Local inference process exited before completing its request. Process output: noise'))
        self.assertTrue(warning.endswith('RuntimeError: CUDA error: an illegal memory access was encountered'))

    def test_pending_passages_reuse_audio_saved_under_the_legacy_key(self):
        self.store.update_settings({'cast_review': True})
        jobs, engine, done = self.reviewed()
        keys = self.legacy_audio(done)
        mixed = self.mixed(done)
        self.assertTrue(mixed)
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertEqual(sum(mixed.values()), engine.rendered)
        hashes = {segment['id']: segment['audio_hash'] for segment in ready['segments']}
        self.assertTrue(self.padded(done))
        self.assertEqual(self.single(done, keys), {sid: key for sid, key in hashes.items() if sid not in mixed and sid not in self.padded(done)})
        self.assertFalse(any(hashes[sid] == keys[sid] for sid in mixed))
        self.assert_padded_reuse(done, ready, keys)
        self.assertFalse(any(LEGACY_DELIVERY in segment['warning'] for segment in ready['segments']))
        segment = ready['segments'][0]
        self.assertNotIn(segment['id'], mixed)
        rendered = engine.rendered
        retaken = self.finish(jobs, jobs.revise(ready['id'], [{'id': segment['id'], 'retake': True}]))
        self.assertEqual('ready', retaken['status'], retaken['error'])
        self.assertEqual(rendered + 1, engine.rendered)
        self.assertNotEqual(keys[segment['id']], retaken['segments'][0]['audio_hash'])

    def test_damaged_legacy_audio_is_synthesized_again(self):
        self.store.update_settings({'cast_review': True})
        jobs, engine, done = self.reviewed()
        keys = self.legacy_audio(done)
        damaged = done['segments'][0]['id']
        self.assertNotIn(damaged, self.mixed(done))
        (self.store.root / 'audio' / f'{keys[damaged]}.wav').write_bytes(b'damaged')
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertEqual(1 + sum(self.mixed(done).values()), engine.rendered)
        self.assertNotEqual(keys[damaged], ready['segments'][0]['audio_hash'])

    def test_revision_of_a_legacy_analysis_keeps_its_alias_warnings(self):
        self.store.update_settings({'cast_review': False})
        jobs = self.jobs(Recording())
        ready = self.finish(jobs, self.generation())
        unmerged = NOTICES['alias_unmerged_capacity'].format(count=2)
        skipped = NOTICES['alias_skipped_limit'].format(count=5, limit=256)
        legacy = {key: value for key, value in ready['analysis'].items() if key not in ('casting_notices', 'approved', 'progress')}
        legacy['casting_warnings'] = [*legacy['casting_warnings'], unmerged, skipped]
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps(legacy), ready['id']))
        revision = jobs.revise(ready['id'], [{'id': ready['segments'][0]['id'], 'spoken': 'Другое произношение.'}])
        notices = {notice['code']: notice for notice in revision['analysis']['casting_notices']}
        self.assertEqual((2, 5, 256), (notices['alias_unmerged_capacity']['count'], notices['alias_skipped_limit']['count'],
                                       notices['alias_skipped_limit']['params']['limit']))
        self.assertLessEqual({unmerged, skipped}, set(revision['analysis']['casting_warnings']))
        self.assertEqual('paused', revision['status'])


if __name__ == '__main__':
    unittest.main()
