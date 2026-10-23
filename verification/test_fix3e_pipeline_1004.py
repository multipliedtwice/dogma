from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, SyntheticEngine, pack_fixture
from test_fix3_pipeline_1004 import Base
from test_review_gate_1004 import Recording, make_pack
from rimsky.jobs import legacy_language
from rimsky.packs import CONTEXT_ERROR, load_pack
from rimsky.preparation import speech_language
from rimsky.store import Store
from rimsky.util import AppError, write_json
from server import Application

UND = FB2.replace(b'<lang>ru</lang>', b'<lang>und</lang>')
LEGACY = ('approved', 'progress', 'casting_notices', 'speech_language')


class LegacyLanguage(Base):
    def setUp(self):
        super().setUp()
        self.store.update_settings({'cast_review': True})
        self.book = self.store.import_data(UND, 'und.fb2')
        self.assertEqual(('Russian', 'Auto'), (speech_language(self.book), legacy_language(self.book)))

    def legacy(self, generation, status='paused'):
        analysis = {key: value for key, value in generation['analysis'].items() if key not in LEGACY}
        self.store.execute('UPDATE generations SET status=?,analysis=? WHERE id=?', (status, json.dumps(analysis), generation['id']))

    def languages(self, engine):
        return {request['language'] for request in engine.requests}

    def test_new_generation_does_not_reuse_base_audio_of_another_language(self):
        jobs, engine, done = self.reviewed()
        keys = self.legacy_audio(done)
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual(('ready', 'Russian'), (ready['status'], ready['analysis']['speech_language']), ready['error'])
        extra = sum(self.mixed(done).values()) - len(self.mixed(done))
        self.assertEqual((len(keys) + extra, {'Russian'}), (engine.rendered, self.languages(engine)))
        self.assertFalse(set(keys.values()) & {segment['audio_hash'] for segment in ready['segments']})

    def test_auto_language_packs_reuse_base_audio_whatever_the_pin(self):
        jobs, engine, done = self.reviewed(pack=make_pack(self.root / 'auto-pack', language_mode='auto'))
        keys = self.legacy_audio(done)
        mixed = self.mixed(done)
        ready = self.finish(jobs, done, narrate=True)
        self.assertEqual(('ready', 'Russian', sum(mixed.values())), (ready['status'], ready['analysis']['speech_language'], engine.rendered), ready['error'])
        self.assertEqual(self.single(done, keys), {segment['id']: segment['audio_hash'] for segment in ready['segments'] if segment['id'] not in mixed and segment['id'] not in self.padded(done)})
        self.assert_padded_reuse(done, ready, keys)

    def test_repair_of_a_pre_upgrade_generation_without_ready_audio_keeps_the_base_language(self):
        jobs, engine, done = self.reviewed()
        keys = self.legacy_audio(done)
        self.legacy(done, 'ready')
        for sid, key in keys.items():
            self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=.1 WHERE generation_id=? AND id=?", (key, done['id'], sid))
            for suffix in ('.wav', '.json'):
                (self.store.root / 'audio' / f'{key}{suffix}').unlink()
        jobs.repair(done['id'])
        jobs.thread.join(10)
        repaired = self.store.generation(done['id'])
        self.assertEqual(('ready', 'Auto'), (repaired['status'], repaired['analysis']['speech_language']), repaired['error'])
        extra = sum(self.mixed(done).values()) - len(self.mixed(done))
        self.assertEqual((len(keys) + extra, {'Auto'}), (engine.rendered, self.languages(engine)))
        hashes = {segment['id']: segment['audio_hash'] for segment in repaired['segments']}
        self.assertEqual(self.single(done, keys), {sid: key for sid, key in hashes.items() if sid not in self.mixed(done) and sid not in self.padded(done)})
        self.assertTrue(all(hashes[sid] != keys[sid] and (self.store.root / 'audio' / f'{hashes[sid]}.wav').is_file() for sid in [*self.mixed(done), *self.padded(done)]))

    def test_unrendered_pre_upgrade_generation_reuses_base_audio_in_the_base_language(self):
        jobs, engine, done = self.reviewed()
        keys = self.legacy_audio(done)
        missing = done['segments'][-1]['id']
        mixed = self.mixed(done)
        self.assertNotIn(missing, mixed)
        removed = keys.pop(missing)
        for suffix in ('.wav', '.json'):
            (self.store.root / 'audio' / f'{removed}{suffix}').unlink()
        self.legacy(done)
        ready = self.finish(jobs, done)
        self.assertEqual(('ready', 'Auto'), (ready['status'], ready['analysis']['speech_language']), ready['error'])
        self.assertEqual((1 + sum(mixed.values()), {'Auto'}), (engine.rendered, self.languages(engine)))
        self.assertNotIn(missing, self.padded(done))
        self.assertEqual(self.single(done, keys), {segment['id']: segment['audio_hash'] for segment in ready['segments']
                                                   if segment['id'] != missing and segment['id'] not in mixed and segment['id'] not in self.padded(done)})
        self.assert_padded_reuse(done, ready, keys)

    def test_pre_upgrade_generation_without_any_audio_uses_the_detected_language(self):
        jobs, engine, done = self.reviewed()
        self.legacy(done)
        ready = self.finish(jobs, done)
        self.assertEqual(('ready', 'Russian'), (ready['status'], ready['analysis']['speech_language']), ready['error'])
        self.assertEqual({'Russian'}, self.languages(engine))


class RelocationCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.library = self.root / 'library'
        environment = patch.dict(os.environ, {'RIMSKY_RESOURCE_DIR': str(self.root / 'resources')})
        environment.start()
        self.addCleanup(environment.stop)
        (self.root / 'resources' / 'packs').mkdir(parents=True)
        self.bundled = pack_fixture(self.root / 'resources' / 'packs' / 'demo')
        self.custom = self.root / 'usb-drive' / 'my-pack'
        self.custom.parent.mkdir()
        self.copy = pack_fixture(self.custom)
        manifest = json.loads((self.custom / 'pack.json').read_text('utf-8'))
        write_json(self.custom / 'pack.json', {**manifest, 'name': 'My edited voices'})
        self.edited = load_pack(str(self.custom))
        self.assertEqual(self.bundled['_fingerprint'], self.copy['_fingerprint'])

    def pinned(self, *fingerprints, stamped=False):
        store = Store(self.library)
        book = store.import_data(FB2, 'fixture.fb2')
        made = []
        for day, fingerprint in enumerate(fingerprints, 1):
            stamp = {'pinned_at': f'2026-01-0{day}T00:00:00.000000+00:00'} if stamped else {}
            generation = store.create_generation(book['id'], {'pack_path': str(self.custom), 'pack_fingerprint': fingerprint, 'pack_name': 'Pack',
                                                              'device': 'cpu', 'align_words': False, **stamp})
            store.execute('UPDATE generations SET created=?,analysis=? WHERE id=?', (f'2026-01-0{day}T00:00:00Z', json.dumps({'complete': True}), generation['id']))
            made.append(generation)
        store.update_settings({'pack_path': str(self.custom)})
        store.close()
        return made

    def selected(self):
        app = Application(self.library, self.root / 'bin')
        try:
            if app.relocation:
                app.relocation.join(10)
            return app.store.settings()['pack_path']
        finally:
            app.close()

    def unplug(self):
        (self.root / 'usb-drive').rename(self.root / 'unplugged')


class Relocation(RelocationCase):
    def test_newest_custom_generation_keeps_a_missing_folder_with_an_older_bundled_copy(self):
        older, _ = self.pinned(self.copy['_fingerprint'], self.edited['_fingerprint'])
        self.unplug()
        self.assertEqual(str(self.custom), self.selected())
        store = Store(self.library)
        revision = store.revise(older['id'], [{'id': older['segments'][0]['id'], 'spoken': 'Другое чтение.'}])
        store.execute('UPDATE generations SET created=? WHERE id=?', ('2026-01-09T00:00:00Z', revision['id']))
        store.close()
        self.assertEqual(str(self.custom), self.selected())
        (self.root / 'unplugged').rename(self.root / 'usb-drive')
        self.assertEqual(str(self.custom), self.selected())

    def test_newest_generation_with_a_bundled_fingerprint_still_relocates(self):
        self.pinned(self.edited['_fingerprint'], self.copy['_fingerprint'], stamped=True)
        self.unplug()
        self.assertEqual(self.bundled['_root'], self.selected())


class SmallPackSelection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        environment = patch.dict(os.environ, {'RIMSKY_RESOURCE_DIR': str(self.root / 'resources')})
        environment.start()
        self.addCleanup(environment.stop)

    def small(self, root, name):
        make_pack(root)
        manifest = json.loads((root / 'pack.json').read_text('utf-8'))
        write_json(root / 'pack.json', {**manifest, 'name': name, 'text_model': {**manifest['text_model'], 'context': 4096}})
        return load_pack(str(root), verify=True, pinned=True)

    def open(self, engine):
        app = Application(self.root / 'library', self.root / 'bin')
        self.addCleanup(app.close)
        if app.relocation:
            app.relocation.join(10)
        app.jobs.engine_factory = lambda _: engine
        return app

    def test_moved_small_context_pack_is_selectable_only_for_its_pinned_generations(self):
        app = self.open(Recording())
        app.store.update_settings({'device': 'cpu', 'cast_review': True, 'character_analysis': 'local'})
        book = app.store.import_data(FB2, 'fixture.fb2')
        pack = make_pack(self.root / 'pack')
        generation = app.store.create_generation(book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                              'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
        app.jobs.start(generation['id'])
        app.jobs.thread.join(10)
        done = app.store.generation(generation['id'])
        self.assertEqual('review', done['status'], done['error'])
        small = self.small(self.root / 'old-drive' / 'small', 'Small pack')
        config = {**done['config'], 'pack_path': small['_root'], 'pack_fingerprint': small['_fingerprint']}
        analysis = {key: value for key, value in done['analysis'].items() if key not in LEGACY}
        app.store.execute("UPDATE generations SET status='paused',config=?,analysis=? WHERE id=?", (json.dumps(config), json.dumps(analysis), done['id']))
        app.store.update_settings({'pack_path': small['_root']})
        app.close()
        (self.root / 'old-drive').rename(self.root / 'new-drive')
        moved = str(self.root / 'new-drive' / 'small')
        other = self.small(self.root / 'other-small', 'Other small pack')
        app = self.open(SyntheticEngine())
        self.assertEqual(small['_root'], app.store.settings()['pack_path'])
        with app.mutations, self.assertRaisesRegex(AppError, 'does not contain a valid pack.json'):
            app.dispatch('resume', {'id': done['id']})
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            app.dispatch('select_pack', {'path': other['_root']})
        self.assertEqual(small['_root'], app.store.settings()['pack_path'])
        selected = app.dispatch('select_pack', {'path': moved})
        self.assertEqual(moved, selected['settings']['pack_path'])
        self.assertEqual((False, CONTEXT_ERROR, None), (selected['health']['ready'], selected['health']['pack_error'], selected['health']['pack']))
        with app.mutations, self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            app.dispatch('generate', {'book_id': book['id']})
        with app.mutations:
            app.dispatch('resume', {'id': done['id']})
        app.jobs.thread.join(10)
        resumed = app.store.generation(done['id'])
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual(1, len(app.store.book(book['id'])['generations']))


if __name__ == '__main__':
    unittest.main()
