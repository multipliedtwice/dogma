"""Real HTTP tests use a temporary synthetic workspace and random loopback port."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('listening', ROOT / 'scripts/serve_public_audiobook_demos.py')
listening = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listening)


class ListeningServerGates(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public = self.root / 'en-gift-of-magi/public'
        self.public.mkdir(parents=True)
        self.audio = 'a'*64+'.mp3'
        (self.public / self.audio).write_bytes(b'0123456789')
        (self.public / 'index.html').write_text('<h1>Synthetic English demo</h1>')
        (self.root / 'en-gift-of-magi/manifest.json').write_text('PRIVATE MODEL PATH')
        (self.public / 'latest.json').write_text(json.dumps({'complete': False, 'mp3': self.audio,
              'verified_units': 2, 'total_units': 10, 'private_reference': 'PRIVATE TRAINING LABELS'}))
        self.server = listening.ListeningServer(('127.0.0.1', 0), self.root, record=False)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.temporary.cleanup()

    def request(self, path, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_hub_uses_sanitized_status_and_preparing_card(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'2/10 passages', body)
        self.assertIn(b'Preparing narration', body)
        self.assertIn(b'href="/en/"', body)
        self.assertNotIn(b'href="/ru/"', body)
        self.assertNotIn(b'PRIVATE', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_single_ranges_head_and_unsatisfiable_range(self):
        for value, expected, content_range in [('bytes=2-5', b'2345', 'bytes 2-5/10'),
                                               ('bytes=-3', b'789', 'bytes 7-9/10'),
                                               ('bytes=8-', b'89', 'bytes 8-9/10')]:
            with self.subTest(value=value):
                status, headers, body = self.request('/en/'+self.audio, headers={'Range': value})
                self.assertEqual(status, 206)
                self.assertEqual(body, expected)
                self.assertEqual(headers['Content-Range'], content_range)
                self.assertIn('immutable', headers['Cache-Control'])
        status, headers, body = self.request('/en/'+self.audio, method='HEAD', headers={'Range': 'bytes=2-5'})
        self.assertEqual((status, body, headers['Content-Length']), (206, b'', '4'))
        for value in ('bytes=10-', 'bytes=5-2', 'bytes=0-1,4-5', 'bytes=-0'):
            with self.subTest(value=value):
                status, headers, body = self.request('/en/'+self.audio, headers={'Range': value})
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'], 'bytes */10')
                self.assertEqual(body, b'')

    def test_private_paths_traversals_and_mutations_are_denied(self):
        for path in ('/en/../manifest.json', '/en/%2e%2e/manifest.json', '/en/%2f..%2fmanifest.json',
                     '/en/%252e%252e/manifest.json', '/en/manifest.json', '/en-gift-of-magi/manifest.json',
                     '/en/source/story.txt', '/ru/../../en-gift-of-magi/manifest.json', '/queue/listening-server.json'):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertIn(status, (403, 404))
                self.assertNotIn(b'PRIVATE', body)
        status, _, _ = self.request('/en/latest.json', method='POST')
        self.assertEqual(status, 501)
        self.assertIn('private_reference', json.loads((self.public / 'latest.json').read_text()))

    def test_symlink_public_asset_is_denied(self):
        target = self.root / 'private.txt'
        target.write_text('PRIVATE OUTSIDE AUDIO')
        filename = 'b'*64+'.wav'
        try:
            os.symlink(target, self.public / filename)
        except OSError:
            self.skipTest('Host does not permit creating symlinks')
        status, _, body = self.request('/en/'+filename)
        self.assertEqual(status, 403)
        self.assertNotIn(b'PRIVATE', body)

    def test_indexes_and_latest_are_not_immutable(self):
        for path in ('/en/', '/en/latest.json'):
            status, headers, body = self.request(path, method='HEAD')
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(body, b'')

    def test_exact_recognition_evidence_filename_is_immutable_and_available(self):
        filename = 'recognition-'+'c'*64+'.json'
        (self.public / filename).write_text('{"human_approval":false}')
        status, headers, body = self.request('/en/'+filename)
        self.assertEqual(status, 200)
        self.assertIn('immutable', headers['Cache-Control'])
        self.assertEqual(json.loads(body), {'human_approval': False})
        self.assertEqual(self.request('/en/recognition-arbitrary.json')[0], 403)

    def test_windows_reparse_attributes_are_rejected_without_symlink_privilege(self):
        original = Path.lstat
        target = self.public / self.audio
        def attributes(path, *args, **kwargs):
            if path == target:
                return SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', attributes):
            with self.assertRaises(PermissionError):
                listening.public_path(self.root, 'en', self.audio)


if __name__ == '__main__':
    unittest.main()
