from __future__ import annotations

import io
import json
import secrets
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from test_core import FB2, SyntheticEngine, complete_audio, pack_fixture
from rimsky import export as export_module
from rimsky import jobs as jobs_module
from rimsky.export import build_export, captions
from rimsky.jobs import Jobs
from rimsky.packs import health
from rimsky.store import Store
from rimsky.util import AppError, EngineTransportError, append_warning, file_digest
from server import Application, handler_for
from PIL import Image


def lock_free(lock):
    acquired = []
    def probe():
        if lock.acquire(timeout=2):
            acquired.append(True)
            lock.release()
    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(5)
    return bool(acquired)


def png_bytes():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(output, format='PNG')
    return output.getvalue()


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class DyingEngine(SyntheticEngine):
    def __init__(self):
        super().__init__()
        self.process = Process()

    def call(self, method, payload, stop):
        if self.process.code is not None:
            raise EngineTransportError('Local inference process exited.', 'exit')
        if method == 'analyze':
            self.analyzed += 1
            self.process.code = -15
            raise EngineTransportError('Local inference exceeded its 1800-second operation limit.', 'timeout')
        return super().call(method, payload, stop)


class ReviewRepairsCore(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def prepared(self):
        pack = pack_fixture(self.root / 'pack')
        return self.store.create_generation(self.book['id'], {
            'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
            'pack_name': pack['name'], 'device': 'cpu', 'align_words': False,
        })

    def completed(self):
        generation = self.prepared()
        complete_audio(self.store, generation)
        return self.store.generation(generation['id'])

    def run_job(self, generation, factory):
        jobs = Jobs(self.store, self.root, engine_factory=factory)
        self.addCleanup(jobs.shutdown)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def test_srt_keeps_literal_text_and_vtt_escapes_markup(self):
        segments = [{'duration': 1, 'spoken': 'a & b < c > d', 'cues': []}]
        srt = captions(segments, srt=True)
        vtt = captions(segments)
        self.assertIn('a & b < c > d', srt)
        self.assertNotIn('&amp;', srt)
        self.assertIn('a &amp; b &lt; c &gt; d', vtt)

    def test_srt_export_file_is_not_entity_escaped(self):
        generation = self.completed()
        first = generation['segments'][0]
        self.store.execute('UPDATE segments SET spoken=? WHERE generation_id=? AND id=?', ('x < y & z > q', generation['id'], first['id']))
        # Initial synthetic ready-caption fixture, with no older current audio.
        self.store.execute('DELETE FROM audiobook_replacements WHERE generation_id=?',(generation['id'],))
        srt = build_export(self.store, generation['id'], 'srt', None, self.root)
        vtt = build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertIn('x < y & z > q', (self.store.root / 'exports' / srt['file']).read_text('utf-8'))
        self.assertIn('x &lt; y &amp; z &gt; q', (self.store.root / 'exports' / vtt['file']).read_text('utf-8'))

    def test_chained_corrections_keep_the_current_name_bounded(self):
        generation = self.completed()
        self.store.execute('UPDATE generations SET name=? WHERE id=?', ('N' * 200, generation['id']))
        segment = generation['segments'][0]['id']
        for index in range(25):
            generation = self.store.revise(generation['id'], [{'id': segment, 'spoken': f'Correction {index}'}])
            self.assertLessEqual(len(generation['name']), 200)
        self.assertEqual('N'*200,generation['name'])
        self.assertEqual(1,len(self.store.generation_list(self.book['id'])))

    def test_corrections_preserve_the_current_short_name(self):
        generation = self.completed()
        base=generation['name'];gid=generation['id']
        segment = generation['segments'][0]['id']
        names = []
        for index in range(3):
            generation = self.store.revise(generation['id'], [{'id': segment, 'spoken': f'Correction {index}'}])
            names.append(generation['name'])
        self.assertEqual([base]*3,names)
        self.assertEqual(gid,generation['id'])

    def test_warning_append_is_idempotent(self):
        existing = 'Unresolved speaker; narrator voice is used. Model note.'
        self.assertEqual(existing, append_warning(existing, 'Unresolved speaker; narrator voice is used.', 'Model note.'))
        self.assertEqual('A. B.', append_warning('A.', 'B.', 'B.'))
        self.assertEqual('B.', append_warning('', '', 'B.'))

    def test_unavailable_alignment_warning_is_not_repeated_on_resume(self):
        generation = self.completed()
        jobs = Jobs(self.store, self.root)
        for _ in range(3):
            jobs._align(generation['id'], {'_root': str(self.root)}, 'Russian', {'device': 'cpu'})
        notice = 'Word alignment is unavailable; passage subtitles are retained.'
        for segment in self.store.generation(generation['id'])['segments']:
            self.assertEqual(1, segment['warning'].count(notice))

    def test_failed_alignment_warning_is_not_repeated_on_resume(self):
        generation = self.completed()
        jobs = Jobs(self.store, self.root)
        class Aligner:
            process = Process()
            def call(self, *args, **kwargs):
                raise AppError('Alignment text does not cover the spoken passage.')
            def close(self):
                pass
        pack = {'_root': str(self.root), 'aligner': {'path': 'aligner'}, 'files': {}, '_stats': {}}
        with patch.object(jobs_module, 'resolve_binary', return_value='rimsky-align'), \
             patch.object(jobs_module, 'Engine', side_effect=lambda *args, **kwargs: Aligner()):
            for _ in range(3):
                jobs._align(generation['id'], pack, 'Russian', {'device': 'cpu'})
        for segment in self.store.generation(generation['id'])['segments']:
            self.assertEqual(1, segment['warning'].count('Alignment text does not cover'))
            self.assertEqual(1, segment['warning'].count('Passage subtitles are retained.'))

    def test_analysis_retry_restarts_exited_engine(self):
        engines = []
        def factory(_):
            engines.append(DyingEngine() if not engines else SyntheticEngine())
            return engines[-1]
        generation = self.run_job(self.prepared(), factory)
        self.assertEqual('ready', generation['status'], generation['error'])
        self.assertEqual(2, len(engines))
        self.assertEqual(1, engines[0].analyzed)

    def test_analysis_retry_reports_original_failure_when_restart_fails(self):
        engines = []
        def factory(_):
            if engines:
                raise AppError('Bundled inference runtime is missing.')
            engines.append(DyingEngine())
            return engines[-1]
        generation = self.run_job(self.prepared(), factory)
        self.assertEqual('failed', generation['status'])
        self.assertIn('operation limit', generation['error'])
        self.assertNotIn('exited', generation['error'])

    def test_file_digest_honours_stop_between_chunks(self):
        path = self.root / 'large.bin'
        path.write_bytes(b'x' * (3 * 1024 * 1024 + 7))
        stopped = threading.Event()
        stopped.set()
        with self.assertRaisesRegex(AppError, 'paused'):
            file_digest(path, stopped)
        class Later:
            checks = 0
            def is_set(self):
                self.checks += 1
                return self.checks > 2
        with self.assertRaisesRegex(AppError, 'paused'):
            file_digest(path, Later())
        self.assertEqual(64, len(file_digest(path, threading.Event())))

    def test_pack_verification_stops_without_fallback_or_cache(self):
        generation = self.prepared()
        jobs = Jobs(self.store, self.root)
        stopped = threading.Event()
        stopped.set()
        self.store.update_settings({'pack_path': str(self.root / 'elsewhere')})
        with patch.object(jobs_module, 'load_pack', wraps=jobs_module.load_pack) as loader:
            with self.assertRaisesRegex(AppError, 'paused'):
                jobs._pack(generation['config'], verify=True, stop=stopped)
        self.assertEqual(1, loader.call_count)
        cache = {}
        from rimsky.packs import verified_pack
        with patch('rimsky.packs.file_digest', side_effect=AppError('Preparation paused.')):
            with self.assertRaises(AppError):
                verified_pack(generation['config']['pack_path'], cache)
        self.assertEqual({}, cache)

    def test_export_validates_audio_without_holding_store_lock(self):
        generation = self.completed()
        real = export_module.accepted_audio
        observed = []
        def slow(path, **kwargs):
            observed.append(lock_free(self.store.lock))
            return real(path, **kwargs)
        with patch.object(export_module, 'accepted_audio', side_effect=slow):
            build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertTrue(observed)
        self.assertTrue(all(observed))

    def test_export_snapshot_survives_source_deletion_after_capture(self):
        generation = self.completed()
        real = export_module.freeze_audio
        def delete_then_freeze(snapshot):
            source = self.store.root / 'audio' / snapshot.name
            source.unlink(missing_ok=True)
            source.with_suffix('.json').unlink(missing_ok=True)
            return real(snapshot)
        with patch.object(export_module, 'freeze_audio', side_effect=delete_then_freeze):
            result = build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertTrue((self.store.root / 'exports' / result['file']).is_file())
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_export_refuses_incomplete_repair_marker(self):
        generation = self.completed()
        path = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.json'
        path.write_text(json.dumps({**json.loads(path.read_text()), 'reset_cues': True}))
        with self.assertRaisesRegex(AppError, 'repair is incomplete'):
            build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))

    def test_export_detects_snapshot_that_does_not_match_recorded_digest(self):
        generation = self.completed()
        path = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        with path.open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaises(AppError):
            build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))

    def test_repair_inspects_audio_without_holding_store_lock(self):
        generation = self.run_job(self.prepared(), lambda _: SyntheticEngine())
        self.assertEqual('ready', generation['status'], generation['error'])
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: SyntheticEngine())
        self.addCleanup(jobs.shutdown)
        real = jobs_module.accepted_audio
        observed = []
        def slow(path, **kwargs):
            observed.append(lock_free(self.store.lock))
            return real(path, **kwargs)
        with patch.object(jobs_module, 'accepted_audio', side_effect=slow):
            repaired = jobs.repair(generation['id'])
        self.assertEqual('ready', repaired['status'])
        self.assertTrue(observed)
        self.assertTrue(all(observed))

    def test_health_reports_ffprobe_separately(self):
        with patch('rimsky.packs.resolve_binary', side_effect=lambda name, _: '/bin/ffmpeg' if name == 'ffmpeg' else None):
            result = health(self.store.settings(), self.root)
        self.assertIs(True, result['ffmpeg'])
        self.assertIs(False, result['ffprobe'])


class OnlineVisualLocking(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root / 'binaries')
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.app.close)
        self.book = self.app.store.import_data(FB2, 'fixture.fb2')
        self.app.store.update_settings({'online_images': True})
        self.source = self.book['chapters'][0]['blocks'][-1]['id']

    def test_provider_call_runs_without_mutation_lock(self):
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        observed = []
        def provider(request):
            observed.append(lock_free(self.app.mutations))
            return png_bytes()
        body = json.dumps({'op': 'generate_visual', 'data': {'book_id': self.book['id'], 'source_id': self.source, 'api_key': 'key', 'consent': True}}).encode()
        try:
            with patch('rimsky.visuals.fetch_visual', side_effect=provider):
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}/api', data=body,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}), timeout=10) as response:
                    result = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual([True], observed)
        self.assertEqual('generated-online', result['kind'])
        self.assertEqual(1, len(self.app.store.visuals(self.book['id'])))

    def test_book_deleted_during_provider_call_is_not_persisted(self):
        def provider(request):
            self.app.store.delete_book(self.book['id'])
            return png_bytes()
        with patch('rimsky.visuals.fetch_visual', side_effect=provider):
            with self.assertRaisesRegex(AppError, 'not found'):
                self.app.dispatch('generate_visual', {'book_id': self.book['id'], 'source_id': self.source, 'api_key': 'key', 'consent': True})
        self.assertEqual([], self.app.store.rows('SELECT * FROM visuals'))

    def test_consent_is_checked_before_provider_call(self):
        self.app.store.update_settings({'online_images': False})
        with patch('rimsky.visuals.fetch_visual') as provider:
            with self.assertRaisesRegex(AppError, 'permission'):
                self.app.dispatch('generate_visual', {'book_id': self.book['id'], 'source_id': self.source, 'api_key': 'key', 'consent': True})
        provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()
