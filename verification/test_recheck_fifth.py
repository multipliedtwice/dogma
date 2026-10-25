from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.client import IncompleteRead
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_core import FB2, complete_audio, pack_fixture, write_wave
from rimsky.audio_source import open_audio_pair
from rimsky.books import image_asset
from rimsky.export import build_export, concat_list, run_encoder
from rimsky.integrity import accepted_audio, read_audio
from rimsky.store import Store
from rimsky.util import AppError, atomic_replace, write_json
from server import Application, handler_for
from PIL import Image


class FifthRecheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = pack_fixture(self.root / 'pack')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self):
        generation = self.store.create_generation(self.book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False,
        })
        complete_audio(self.store, generation)
        return self.store.generation(generation['id'])

    def replace_audio(self, path, seconds=.9, publish=True):
        temporary = path.with_suffix('.replacement')
        write_wave(temporary, seconds=seconds)
        atomic_replace(temporary, path)
        if publish:
            write_json(path.with_suffix('.json'), {**read_audio(path), 'warning': '', 'reset_cues': False})

    def assert_frozen_export(self, *, during_capture=False):
        generation = self.generation()
        audio = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        from rimsky.opening import select_opening
        expected = sum(segment['duration'] for segment in generation['segments']) + select_opening(generation)['duration']
        replaced = []
        original = os.link if during_capture else concat_list
        def race(*args):
            if not replaced and (not during_capture or Path(args[0])==audio):
                replaced.append(True)
                self.replace_audio(audio)
            return original(*args)
        operation = 'rimsky.export.os.link' if during_capture else 'rimsky.export.concat_list'
        with patch(operation, side_effect=race):
            result = build_export(self.store, generation['id'], 'm4b', None, self.root)
        details = json.loads(subprocess.check_output([
            shutil.which('ffprobe'), '-v', 'error', '-show_chapters', '-show_format', '-of', 'json',
            str(self.store.root / 'exports' / result['file']),
        ]))
        self.assertAlmostEqual(expected, float(details['format']['duration']), delta=.03)
        self.assertAlmostEqual(expected, float(details['chapters'][-1]['end_time']), delta=.002)
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_export_freezes_audio_before_encoder_reads_mutable_cache_paths(self):
        self.assert_frozen_export()

    def test_export_snapshot_has_a_copy_fallback_when_hard_links_are_unavailable(self):
        with patch('rimsky.export.os.link', side_effect=OSError('hard links unavailable')):
            self.assert_frozen_export()

    def assert_no_export_published(self):
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_replacement_during_snapshot_capture_exports_only_retained_audio(self):
        # The captured pair can now survive repair. Assert that real encoded
        # audio and chapter timing still match the original, never the replacement.
        self.assert_frozen_export(during_capture=True)

    def test_failed_snapshot_copy_cleans_staging_and_does_not_publish(self):
        generation = self.generation()
        with patch('rimsky.export.os.link', side_effect=OSError('no hard links')), \
             patch('rimsky.export.shutil.copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                build_export(self.store, generation['id'], 'm4b', None, self.root)
        self.assert_no_export_published()

    def test_in_place_modification_during_encoding_refuses_the_result(self):
        generation = self.generation()
        audio = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        def race(command, stop=None, **options):
            result = run_encoder(command, stop, **options)
            with audio.open('ab') as handle:
                handle.write(b'changed')
            return result
        with patch('rimsky.export.run_encoder', side_effect=race):
            with self.assertRaisesRegex(AppError, 'checksum mismatch'):
                build_export(self.store, generation['id'], 'm4b', None, self.root)
        self.assert_no_export_published()

    def test_accepted_audio_preserves_and_rewinds_a_caller_owned_stream(self):
        generation = self.generation()
        path = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        expected = path.read_bytes()
        with path.open('rb') as source:
            source.seek(30)
            result = accepted_audio(path, stream=source)
            self.assertFalse(source.closed)
            self.assertEqual(0, source.tell())
            self.assertEqual(expected, source.read())
        self.assertEqual(hashlib.sha256(expected).hexdigest(), result['sha256'])

    def test_http_never_serves_a_replacement_that_was_not_integrity_checked(self):
        generation = self.generation()
        key = generation['segments'][0]['audio_hash']
        app = Application.__new__(Application)
        app.store = self.store
        @contextlib.contextmanager
        def race(path):
            self.replace_audio(path, publish=False)
            with open_audio_pair(path) as pair:
                yield pair
        with self.audio_request(app, key) as request, patch('server.open_audio_pair', side_effect=race):
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=3)
            self.assertEqual(404, rejected.exception.code)
            self.assertIn(b'checksum', rejected.exception.read())

    def test_http_keeps_validated_original_when_path_changes_before_response(self):
        generation = self.generation()
        key = generation['segments'][0]['audio_hash']
        path = self.store.root / 'audio' / f'{key}.wav'
        expected = path.read_bytes()
        app = Application.__new__(Application)
        app.store = self.store
        def race(path, **kwargs):
            validated = accepted_audio(path, **kwargs)
            self.replace_audio(path, publish=False)
            return validated
        with self.audio_request(app, key) as request, patch('server.accepted_audio', side_effect=race) as validate:
            with urlopen(request, timeout=3) as response:
                self.assertEqual(200, response.status)
                self.assertEqual(len(expected), int(response.headers['Content-Length']))
                self.assertEqual(expected, response.read())
        self.assertEqual(1, validate.call_count)
        self.assertNotEqual(expected, path.read_bytes())

    @contextlib.contextmanager
    def audio_request(self, app, key):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, 'fifth-audit-token'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield Request(f'http://127.0.0.1:{server.server_port}/asset?kind=audio&id={key}',
                          headers={'Authorization': 'Bearer fifth-audit-token'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_audio_response_retains_the_validated_handle_after_path_replacement(self):
        generation = self.generation()
        key = generation['segments'][0]['audio_hash']
        path = self.store.root / 'audio' / f'{key}.wav'
        expected = path.read_bytes()
        app = Application.__new__(Application)
        app.store = self.store
        original = shutil.copyfileobj
        def race(source, target, length):
            self.replace_audio(path)
            return original(source, target, length)
        with self.audio_request(app, key) as request, patch('server.shutil.copyfileobj', side_effect=race):
            with urlopen(request, timeout=3) as response:
                self.assertEqual(200, response.status)
                self.assertEqual(len(expected), int(response.headers['Content-Length']))
                self.assertEqual(expected, response.read())
        self.assertNotEqual(expected, path.read_bytes())

    def test_interrupted_audio_response_closes_without_appending_a_second_http_response(self):
        generation = self.generation()
        key = generation['segments'][0]['audio_hash']
        path = self.store.root / 'audio' / f'{key}.wav'
        expected = path.read_bytes()[:16]
        app = Application.__new__(Application)
        app.store = self.store
        handles = []
        def interrupted(source, target, length):
            handles.append(source)
            target.write(source.read(16))
            raise OSError('controlled playback read failure')
        with self.audio_request(app, key) as request, patch('server.shutil.copyfileobj', side_effect=interrupted):
            with urlopen(request, timeout=3) as response:
                self.assertEqual(200, response.status)
                with self.assertRaises(IncompleteRead) as failure:
                    response.read()
                self.assertEqual(expected, failure.exception.partial)
        self.assertEqual(1, len(handles))
        self.assertTrue(handles[0].closed)

    def test_audio_sample_validation_and_digest_use_the_same_open_file(self):
        generation = self.generation()
        path = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        import wave
        original = wave.open
        replacement = path.with_suffix('.replacement')
        write_wave(replacement, seconds=.9)
        @contextlib.contextmanager
        def race(file, mode):
            with original(file, mode) as audio:
                yield audio
            atomic_replace(replacement, path)
        with patch('rimsky.integrity.wave.open', side_effect=race):
            info = read_audio(path)
        self.assertEqual(expected, info['sha256'])
        self.assertAlmostEqual(.1, info['duration'])

    def test_palette_figures_keep_partial_transparency(self):
        image = Image.new('P', (2, 1))
        image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        image.putdata([0, 1])
        image.info['transparency'] = bytes([0, 128] + [255] * 254)
        source = io.BytesIO()
        image.save(source, 'PNG')
        folder = self.root / 'images'
        key = image_asset(source.getvalue(), folder, {})
        with Image.open(folder / key) as converted:
            pixels = converted.convert('RGBA')
            self.assertEqual((255, 0, 0, 0), pixels.getpixel((0, 0)))
            self.assertEqual((0, 0, 255, 128), pixels.getpixel((1, 0)))


if __name__ == '__main__':
    unittest.main()
