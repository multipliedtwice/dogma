from __future__ import annotations

import base64
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_core import FB2, complete_audio, pack_fixture, write_wave
from rimsky.integrity import accepted_audio, read_audio
from rimsky.store import Store
from rimsky.util import atomic_replace, write_json
import server
from server import Application, handler_for, parse_range

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import serve_listening_proxy as proxy

TOKEN = 'cache-token'




class RangeParsing(unittest.TestCase):
    def test_single_ranges(self):
        self.assertIsNone(parse_range(None, 100))
        self.assertEqual((0, 9), parse_range('bytes=0-9', 100))
        self.assertEqual((90, 99), parse_range('bytes=90-', 100))
        self.assertEqual((90, 99), parse_range('bytes=-10', 100))
        self.assertEqual((0, 99), parse_range('bytes=-500', 100))
        self.assertEqual((50, 99), parse_range('bytes=50-5000', 100))

    def test_unsatisfiable_and_ignored(self):
        self.assertEqual('bad', parse_range('bytes=100-', 100))
        self.assertEqual('bad', parse_range('bytes=20-10', 100))
        self.assertEqual('bad', parse_range('bytes=-0', 100))
        self.assertIsNone(parse_range('bytes=0-1,5-6', 100))
        self.assertIsNone(parse_range('items=0-1', 100))
        self.assertIsNone(parse_range('bytes=abc-', 100))


class AudioHttpCache(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        book = self.store.import_data(FB2, 'fixture.fb2')
        pack = pack_fixture(self.root / 'pack')
        generation = self.store.create_generation(book['id'], {
            'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
            'pack_name': pack['name'], 'device': 'cpu', 'align_words': False,
        })
        complete_audio(self.store, generation)
        self.key = self.store.generation(generation['id'])['segments'][0]['audio_hash']
        self.path = self.store.root / 'audio' / f'{self.key}.wav'
        self.expected = self.path.read_bytes()
        self.app = Application.__new__(Application)
        self.app.store = self.store
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, TOKEN))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.store.close()
        self.temporary.cleanup()

    def get(self, headers=None, kind='audio', ident=None):
        request = Request(f'http://127.0.0.1:{self.http.server_port}/asset?kind={kind}&id={ident or self.key}',
                          headers={'Authorization': 'Bearer ' + TOKEN, **(headers or {})})
        return urlopen(request, timeout=5)

    def test_full_response_is_immutable_and_private(self):
        with self.get() as response:
            self.assertEqual(200, response.status)
            self.assertEqual(self.expected, response.read())
            self.assertEqual('private, max-age=31536000, immutable', response.headers['Cache-Control'])
            self.assertEqual(f'"{self.key}"', response.headers['ETag'])
            self.assertEqual('bytes', response.headers['Accept-Ranges'])

    def test_conditional_request_returns_304_without_body(self):
        with self.assertRaises(HTTPError) as raised:
            self.get({'If-None-Match': f'"{self.key}"'})
        self.assertEqual(304, raised.exception.code)
        self.assertEqual(b'', raised.exception.read())
        self.assertEqual(f'"{self.key}"', raised.exception.headers['ETag'])
        with self.get({'If-None-Match': '"other"'}) as response:
            self.assertEqual(200, response.status)

    def test_conditional_request_still_requires_authorization_and_existing_audio(self):
        request = Request(f'http://127.0.0.1:{self.http.server_port}/asset?kind=audio&id={self.key}',
                          headers={'If-None-Match': f'"{self.key}"'})
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        self.assertEqual(401, raised.exception.code)
        with self.assertRaises(HTTPError) as missing:
            self.get({'If-None-Match': '"' + 'f' * 64 + '"'}, ident='f' * 64)
        self.assertEqual(404, missing.exception.code)

    def test_range_requests(self):
        size = len(self.expected)
        with self.get({'Range': 'bytes=4-19'}) as response:
            self.assertEqual(206, response.status)
            self.assertEqual(f'bytes 4-19/{size}', response.headers['Content-Range'])
            self.assertEqual('16', response.headers['Content-Length'])
            self.assertEqual(self.expected[4:20], response.read())
        with self.get({'Range': 'bytes=-10'}) as response:
            self.assertEqual(self.expected[-10:], response.read())
            self.assertEqual(f'bytes {size - 10}-{size - 1}/{size}', response.headers['Content-Range'])
        with self.get({'Range': 'bytes=0-5,8-9'}) as response:
            self.assertEqual(200, response.status)
            self.assertEqual(self.expected, response.read())
        with self.get({'Range': 'bytes=4-19', 'If-Range': '"stale"'}) as response:
            self.assertEqual(200, response.status)
        with self.get({'Range': 'bytes=4-19', 'If-Range': f'"{self.key}"'}) as response:
            self.assertEqual(206, response.status)
        with self.assertRaises(HTTPError) as raised:
            self.get({'Range': f'bytes={size}-'})
        self.assertEqual(416, raised.exception.code)
        self.assertEqual(f'bytes */{size}', raised.exception.headers['Content-Range'])

    def test_integrity_runs_once_per_unchanged_file_and_again_after_a_change(self):
        server.VERIFIED_AUDIO.items.clear()
        with patch('server.accepted_audio', wraps=accepted_audio) as check:
            for _ in range(3):
                with self.get() as response:
                    self.assertEqual(self.expected, response.read())
            self.assertEqual(1, check.call_count)
            replacement = self.path.with_suffix('.replacement')
            write_wave(replacement, seconds=.9)
            atomic_replace(replacement, self.path)
            with self.assertRaises(HTTPError) as raised:
                self.get()
            self.assertEqual(404, raised.exception.code)
            self.assertIn(b'checksum', raised.exception.read())
            self.assertEqual(2, check.call_count, 'a changed file is verified again and fails closed')
            write_json(self.path.with_suffix('.json'), {**read_audio(self.path), 'warning': '', 'reset_cues': False})
            with self.get() as response:
                self.assertEqual(self.path.read_bytes(), response.read())
            self.assertEqual(3, check.call_count)

    def test_failed_verification_is_not_remembered(self):
        server.VERIFIED_AUDIO.items.clear()
        meta = self.path.with_suffix('.json')
        original = meta.read_bytes()
        meta.write_text('{}', 'utf-8')
        for _ in range(2):
            with self.assertRaises(HTTPError):
                self.get()
        meta.write_bytes(original)
        with self.get() as response:
            self.assertEqual(200, response.status)


class Runtime(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.received.append(dict(self.headers))
        body = b'0123456789'
        status, start, end = 200, 0, len(body) - 1
        if self.headers.get('If-None-Match') == '"v1"':
            self.send_response(304)
            self.send_header('ETag', '"v1"')
            self.send_header('Cache-Control', 'private, max-age=31536000, immutable')
            self.end_headers()
            return
        if self.headers.get('Range') == 'bytes=2-5':
            status, start, end = 206, 2, 5
        self.send_response(status)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('ETag', '"v1"')
        self.send_header('Accept-Ranges', 'bytes')
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(body)}')
        if self.path.endswith('plain'):
            self.send_header('Cache-Control', 'no-store')
        elif self.path.endswith('public'):
            self.send_header('Cache-Control', 'public, max-age=60')
        else:
            self.send_header('Cache-Control', 'max-age=31536000, immutable')
        self.end_headers()
        self.wfile.write(body[start:end + 1])

    def log_message(self, *args):
        pass


class ProxyPassThrough(unittest.TestCase):
    def setUp(self):
        self.runtime = ThreadingHTTPServer(('127.0.0.1', 0), Runtime)
        self.runtime.received = []
        threading.Thread(target=self.runtime.serve_forever, daemon=True).start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), proxy.Handler)
        self.server.upstream_port = self.runtime.server_port
        self.server.runtime_port = self.runtime.server_port
        self.server.runtime_token = 't' * 48
        self.server.listener_book_id = self.server.listener_generation_id = ''
        self.server.authorization = b'Basic ' + base64.b64encode(b'user:password')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        for item in (self.server, self.runtime):
            self.addCleanup(item.server_close)
            self.addCleanup(item.shutdown)

    def get(self, suffix='audio', headers=None):
        request = Request(f'http://127.0.0.1:{self.server.server_port}{proxy.PRODUCTION_PAGE}asset?kind=audio&id={suffix}',
                          headers={'Authorization': 'Basic ' + base64.b64encode(b'user:password').decode(), **(headers or {})})
        return urlopen(request, timeout=5)

    def test_immutable_cache_policy_stays_private_and_single(self):
        with self.get() as response:
            self.assertEqual(b'0123456789', response.read())
            self.assertEqual(['private, max-age=31536000, immutable'], response.headers.get_all('Cache-Control'))
            self.assertEqual('"v1"', response.headers['ETag'])
            self.assertEqual('bytes', response.headers['Accept-Ranges'])

    def test_no_store_and_public_upstream_policies_are_never_shared_cacheable(self):
        with self.get('plain') as response:
            self.assertEqual(['private, no-store'], response.headers.get_all('Cache-Control'))
        with self.get('public') as response:
            self.assertEqual(['private, no-store'], response.headers.get_all('Cache-Control'))

    def test_range_and_conditional_statuses_pass_through(self):
        with self.get(headers={'Range': 'bytes=2-5'}) as response:
            self.assertEqual(206, response.status)
            self.assertEqual('bytes 2-5/10', response.headers['Content-Range'])
            self.assertEqual(b'2345', response.read())
        with self.assertRaises(HTTPError) as raised:
            self.get(headers={'If-None-Match': '"v1"'})
        self.assertEqual(304, raised.exception.code)
        self.assertEqual(['private, max-age=31536000, immutable'], raised.exception.headers.get_all('Cache-Control'))
        self.assertEqual('"v1"', raised.exception.headers['ETag'])

    def test_cache_control_helper(self):
        self.assertEqual('private, no-store', proxy.private_cache_control(''))
        self.assertEqual('private, max-age=5', proxy.private_cache_control('max-age=5'))
        self.assertEqual('private, no-store', proxy.private_cache_control('public, s-maxage=9'))


if __name__ == '__main__':
    unittest.main()
