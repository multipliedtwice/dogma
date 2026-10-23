from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
from test_core import FB2, SyntheticEngine, complete_audio, epub_fixture, pack_fixture
from rimsky.analysis import analysis_target, batches
from rimsky.export import build_export
from rimsky.preparation import speech_sources
from rimsky.util import AppError
from server import Application


class BoundaryAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.addCleanup(self.app.close)
        self.store = self.app.store
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def generation(self, book=None):
        return self.store.create_generation((book or self.book)['id'], {'device': 'cpu'})

    def scene_book(self):
        raw = epub_fixture({'OEBPS/a.xhtml': '<html><body><p>Один разговор.</p><p>Первая реплика.</p><hr/><p>Другой разговор.</p><p>Вторая реплика.</p></body></html>'.encode()})
        return self.store.import_data(raw, 'scenes.epub')

    def test_scene_identity_reaches_analysis_targets(self):
        source = next(speech_sources(self.scene_book(), ['c0001']))
        self.assertEqual(source['scene_id'], analysis_target(source).get('scene_id'))

    def test_batches_do_not_span_explicit_scene_boundaries(self):
        sources = list(speech_sources(self.scene_book(), ['c0001']))
        grouped = list(batches(sources))
        self.assertEqual(2, len(grouped))
        self.assertEqual(sources, [source for batch in grouped for source in batch])
        self.assertTrue(all(len({s['scene_id'] for s in batch}) == 1 for batch in grouped))

    def test_real_scheduler_keeps_before_after_context_in_the_target_scene(self):
        book = self.scene_book()
        pack = pack_fixture(self.root / 'pack')
        config = {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                  'pack_name': pack['name'], 'device': 'cpu', 'align_words': False}
        generation = self.store.create_generation(book['id'], config)
        engine = SyntheticEngine()
        original = engine.call
        requests = []
        def call(method, payload, stop):
            if method == 'analyze':
                requests.append(payload)
            return original(method, payload, stop)
        engine.call = call
        self.app.jobs.engine_factory = lambda _: engine
        self.app.jobs.start(generation['id'])
        self.app.jobs.thread.join(10)
        result = self.store.generation(generation['id'])
        self.assertEqual('ready', result['status'], result['error'])
        self.assertEqual(3, len(requests))
        for request in requests:
            scene = request['segments'][0]['scene_id']
            self.assertTrue(all(s['scene_id'] == scene for s in request['segments']))
            self.assertTrue(all(s.get('scene_id') == scene for s in request['before'] + request['after']))
        expected = [source['id'] for source in speech_sources(book, config.get('chapters') or [c['id'] for c in book['chapters']])]
        self.assertEqual(expected, [s['id'] for request in requests for s in request['segments']])

    def test_delayed_position_save_cannot_revive_a_deleted_generation(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        segment = generation['segments'][0]
        value = {'chapter_id': segment['chapter_id'], 'source_id': segment['source_id'],
                 'segment_id': segment['id'], 'generation_id': generation['id'], 'offset': 0}
        captured, proceed, deletion_finished = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = self.store.rows
        def snapshot(query, parameters=()):
            result = original(query, parameters)
            if query.startswith('SELECT book_id FROM generations') and threading.current_thread().name == 'position-save':
                captured.set()
                if not proceed.wait(3):
                    raise RuntimeError('Fixture failed to release position save')
            return result
        def save():
            try:
                self.store.save_position(self.book['id'], value)
            except Exception as error:
                errors.append(error)
        def delete():
            try:
                self.store.delete_generation(generation['id'])
            except Exception as error:
                errors.append(error)
            finally:
                deletion_finished.set()
        with patch.object(self.store, 'rows', snapshot):
            saving = threading.Thread(target=save, name='position-save')
            saving.start()
            self.assertTrue(captured.wait(3))
            deleting = threading.Thread(target=delete, name='position-delete')
            deleting.start()
            deletion_finished.wait(.15)
            proceed.set()
            saving.join(3)
            deleting.join(3)
        self.assertFalse(saving.is_alive() or deleting.is_alive())
        self.assertFalse(errors, errors)
        book = self.store.book(self.book['id'])
        self.assertEqual([], book['generations'])
        self.assertNotIn('generation_id', book['position'] or {})
        self.assertNotIn('segment_id', book['position'] or {})

    def test_post_commit_locked_export_reports_logical_deletion_and_cleanup_warning(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        exported = build_export(self.store, generation['id'], 'srt', None, self.root)
        locked = self.store.root / 'exports' / exported['file']
        unlink = Path.unlink
        def remove(path, *args, **kwargs):
            if path == locked:
                raise PermissionError('controlled locked export')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', remove):
            result = self.app.dispatch('delete_generation', {'id': generation['id']})
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_warnings'])
        self.assertTrue(locked.exists())
        self.assertFalse(self.store.generation_list(self.book['id']))

    def test_post_commit_locked_audio_does_not_fail_generation_deletion(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        audio = self.store.root / 'audio'
        unlink = Path.unlink
        def remove(path, *args, **kwargs):
            if path.parent == audio and path.suffix == '.wav':
                raise PermissionError('controlled locked audio')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', remove):
            result = self.app.dispatch('delete_generation', {'id': generation['id']})
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_warnings'])
        self.assertFalse(self.store.generation_list(self.book['id']))
        self.store.collect_audio()
        self.assertFalse(list(audio.glob('*.wav')))

    def test_book_folder_cleanup_failure_is_reported_after_committed_deletion(self):
        import rimsky.store as store_module
        with patch.object(store_module.shutil, 'rmtree', side_effect=PermissionError('controlled locked directory')):
            result = self.app.dispatch('delete_book', {'id': self.book['id']})
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_warnings'])
        self.assertFalse(self.store.library())
        self.assertFalse((self.store.root / 'books' / self.book['id']).exists())
        self.assertTrue(all((path / 'source.fb2').is_file() for path in (self.store.root / 'books').iterdir()))
        self.assertEqual(['.deleted-'], [path.name[:9] for path in (self.store.root / 'books').iterdir()])

    def test_database_refusal_still_preserves_files_and_reports_failure(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        self.store.execute("CREATE TRIGGER block_delete BEFORE DELETE ON generations BEGIN SELECT RAISE(ABORT, 'denied'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.dispatch('delete_generation', {'id': generation['id']})
        self.assertEqual(1, len(self.store.book(self.book['id'])['generations']))
        self.assertTrue(list((self.store.root / 'audio').glob('*.wav')))

    def test_cleanup_keeps_shared_audio_and_unrecognized_local_files(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        audio = self.store.root / 'audio'
        kept = {path.name: path.read_bytes() for path in audio.iterdir()}
        unrelated = audio / 'user-notes.json'
        unrelated.write_text('{"keep":true}')
        orphan = audio / ('0' * 64 + '.json')
        orphan.write_text('{}')
        self.assertEqual([], self.store.collect_audio())
        self.assertTrue(unrelated.exists())
        self.assertFalse(orphan.exists())
        for name, raw in kept.items():
            self.assertEqual(raw, (audio / name).read_bytes())

    def test_healthy_deletion_has_no_cleanup_warning(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        result = self.app.dispatch('delete_generation', {'id': generation['id']})
        self.assertEqual({'deleted': True, 'cleanup_warnings': []}, result)
        self.assertFalse(list((self.store.root / 'audio').iterdir()))

    def test_scene_pause_and_resume_reuses_accepted_analysis(self):
        book = self.scene_book()
        pack = pack_fixture(self.root / 'pack')
        config = {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                  'pack_name': pack['name'], 'device': 'cpu', 'align_words': False}
        generation = self.store.create_generation(book['id'], config)
        engine = SyntheticEngine()
        original = engine.call
        seen = []
        interrupt = True
        def call(method, payload, stop):
            nonlocal interrupt
            if method == 'analyze':
                if seen and interrupt:
                    stop.set()
                    raise AppError('controlled scene-boundary interruption')
                seen.extend(s['id'] for s in payload['segments'])
            return original(method, payload, stop)
        engine.call = call
        self.app.jobs.engine_factory = lambda _: engine
        self.app.jobs.start(generation['id'])
        self.app.jobs.thread.join(10)
        self.assertEqual('paused', self.store.generation(generation['id'])['status'])
        accepted = list(seen)
        interrupt = False
        self.app.jobs.start(generation['id'])
        self.app.jobs.thread.join(10)
        result = self.store.generation(generation['id'])
        self.assertEqual('ready', result['status'], result['error'])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(accepted, seen[:len(accepted)])
        self.assertEqual([s['id'] for s in speech_sources(book, result['config']['chapters'])], seen)

    def test_legacy_sources_without_scene_metadata_retain_size_batching(self):
        sources = [{'id': str(i), 'text': 'a' * 4} for i in range(3)]
        self.assertEqual([sources[:2], sources[2:]], list(batches(sources, maximum=8)))
        self.assertNotIn('scene_id', analysis_target(sources[0]))

    def test_frozen_cast_is_not_recomputed_by_scene_request_changes(self):
        pack = pack_fixture(self.root / 'pack')
        generation = self.store.create_generation(self.book['id'], {
            'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
            'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
        engine = SyntheticEngine()
        self.app.jobs.engine_factory = lambda _: engine
        self.app.jobs.start(generation['id'])
        self.app.jobs.thread.join(10)
        ready = self.store.generation(generation['id'])
        calls = engine.analyzed
        self.store.status(generation['id'], 'paused')
        self.app.jobs.start(generation['id'])
        self.app.jobs.thread.join(10)
        resumed = self.store.generation(generation['id'])
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual(calls, engine.analyzed)
        self.assertEqual(ready['cast'], resumed['cast'])
        self.assertEqual(ready['analysis'], resumed['analysis'])

    def test_cleanup_scan_failure_is_reported_after_committed_deletion(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        original = Path.iterdir
        def denied(path):
            if path == self.store.root / 'audio':
                raise PermissionError('controlled audio-directory access refusal')
            return original(path)
        with patch.object(Path, 'iterdir', denied):
            result = self.app.dispatch('delete_generation', {'id': generation['id']})
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_warnings'])
        self.assertFalse(self.store.generation_list(self.book['id']))
        self.assertEqual([], self.store.collect_audio())
        self.assertFalse(list((self.store.root / 'audio').iterdir()))

    def test_shared_illustration_is_retained_and_last_locked_copy_reports_cleanup(self):
        from rimsky.util import now, uid
        file = 'a' * 64 + '.png'
        image = self.store.root / 'visuals' / file
        image.write_bytes(b'controlled filesystem fixture, not rendered media')
        source = self.book['chapters'][0]['blocks'][0]['id']
        ids = [uid(), uid()]
        for identity in ids:
            self.store.execute('INSERT INTO visuals VALUES(?,?,?,?,?,?,?)',
                               (identity, self.book['id'], source, '', file, 'imported', now()))
        self.assertEqual({'deleted': True, 'cleanup_warnings': []},
                         self.app.dispatch('delete_visual', {'id': ids[0]}))
        self.assertTrue(image.exists())
        original = Path.unlink
        def denied(path, *args, **kwargs):
            if path == image:
                raise PermissionError('controlled locked illustration')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            result = self.app.dispatch('delete_visual', {'id': ids[1]})
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_warnings'])
        self.assertTrue(image.exists())
        self.assertEqual([], self.store.book(self.book['id'])['visuals'])

    def visual_input(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (12, 9), (60, 90, 120)).save(buffer, 'PNG')
        return buffer.getvalue(), self.book['chapters'][0]['blocks'][0]['id']

    def test_attached_illustration_is_saved_and_returned_for_its_source(self):
        from rimsky.visuals import save_visual
        raw, source = self.visual_input()
        item = save_visual(self.store, self.book['id'], source, raw, 'Controlled illustration')
        book = self.store.book(self.book['id'])
        self.assertEqual([item], book['visuals'])
        asset, mime = self.app.asset('visual', item['id'])
        self.assertEqual('image/png', mime)
        self.assertTrue(asset.read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(source, book['visuals'][0]['source_id'])

    def test_failed_illustration_insert_removes_only_new_unreferenced_image(self):
        from rimsky.visuals import save_visual
        raw, source = self.visual_input()
        self.store.execute("CREATE TRIGGER reject_visual BEFORE INSERT ON visuals BEGIN SELECT RAISE(ABORT,'controlled insert refusal'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'controlled insert refusal'):
            save_visual(self.store, self.book['id'], source, raw)
        self.assertEqual([], self.store.book(self.book['id'])['visuals'])
        self.assertFalse(list((self.store.root / 'visuals').iterdir()))

    def test_failed_duplicate_illustration_insert_preserves_existing_shared_image(self):
        from rimsky.visuals import save_visual
        raw, source = self.visual_input()
        item = save_visual(self.store, self.book['id'], source, raw)
        path = self.store.root / 'visuals' / item['file']
        original = path.read_bytes()
        self.store.execute("CREATE TRIGGER reject_visual BEFORE INSERT ON visuals BEGIN SELECT RAISE(ABORT,'controlled insert refusal'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'controlled insert refusal'):
            save_visual(self.store, self.book['id'], source, raw)
        self.assertEqual(original, path.read_bytes())
        self.assertEqual([item], self.store.book(self.book['id'])['visuals'])

    def test_actual_http_illustration_upload_asset_and_deletion(self):
        import json
        from http.server import ThreadingHTTPServer
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from server import handler_for
        raw, source = self.visual_input()
        token = 'controlled-private-session'
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, data=None):
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path,
                         data=data, headers={'Authorization': 'Bearer ' + token}), timeout=5) as response:
                return response.status, response.read()
        try:
            status, data = request('/visual?' + urlencode({'book': self.book['id'], 'source': source}), raw)
            self.assertEqual(200, status)
            item = json.loads(data)
            status, png = request('/asset?' + urlencode({'kind': 'visual', 'id': item['id']}))
            self.assertEqual(200, status)
            self.assertEqual((self.store.root / 'visuals' / item['file']).read_bytes(), png)
            status, data = request('/api', json.dumps({'op': 'delete_visual', 'data': {'id': item['id']}}).encode())
            self.assertEqual({'deleted': True, 'cleanup_warnings': []}, json.loads(data))
            self.assertEqual([], self.store.book(self.book['id'])['visuals'])
            self.assertFalse(list((self.store.root / 'visuals').iterdir()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_core_packaging_preflight_requires_reference_decoder_dependencies(self):
        path = Path(__file__).parents[1] / 'scripts' / 'package_runtime.py'
        spec = importlib.util.spec_from_file_location('audit_packager', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for missing in ('numpy', 'soundfile'):
            with self.subTest(missing=missing), patch.object(module.importlib.util, 'find_spec',
                    side_effect=lambda name: None if name == missing else object()), patch.object(module.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, missing):
                    module.build('core', self.root / 'runtime-out')
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
