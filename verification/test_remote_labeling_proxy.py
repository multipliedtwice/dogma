"""Private tunnel authentication and save forwarding, using fake label data only."""
import base64
import http.client
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_remote_labeling import Handler, PAGE


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, dict(self.headers), None))
        body = b'wave' if self.headers.get('Range') else b'board'
        self.send_response(206 if self.headers.get('Range') else 200)
        self.send_header('Content-Length', str(len(body)))
        if self.headers.get('Range'):
            self.send_header('Content-Range', 'bytes 0-3/100')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        response = b'{"saved":true}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class RemoteLabelingTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.upstream.requests = []
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.proxy.credentials = b'rimsky:rimsky'
        self.proxy.expires_at = time.time() + 3600
        self.proxy.upstream_port = self.upstream.server_port
        self.threads = []
        for server in (self.upstream, self.proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self):
        for server in (self.proxy, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()

    def request(self, method='GET', path=PAGE, authenticated=True, body=None, **headers):
        if authenticated:
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'rimsky:rimsky').decode()
        headers.setdefault('Host', 'private.example')
        connection = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_password_prompt_and_unauthenticated_rejection(self):
        for path in (PAGE, PAGE + 'review-draft.json'):
            status, headers, _ = self.request(path=path, authenticated=False)
            self.assertEqual(status, 401)
            self.assertIn('Basic realm=', headers['WWW-Authenticate'])
        for value in ('Basic wrong', 'Basic ' + base64.b64encode(b'rimsky:wrong').decode()):
            self.assertEqual(self.request(authenticated=False, Authorization=value)[0], 401)
        self.assertEqual(self.upstream.requests, [])

    def test_allowed_board_audio_range_and_no_credentials_forwarding(self):
        self.assertEqual(self.request()[0], 200)
        status, headers, body = self.request(path='/character-rb2/full/clips/test.wav', Range='bytes=0-3')
        self.assertEqual((status, body), (206, b'wave'))
        self.assertEqual(headers['Content-Range'], 'bytes 0-3/100')
        self.assertNotIn('Authorization', self.upstream.requests[-1][2])

    def test_unrelated_paths_and_traversal_are_blocked(self):
        for path in ('/manual-labeling/reality-benders-2-full/review.json',
                     PAGE + '../secret.json', PAGE + '%2e%2e/secret.json',
                     PAGE + '%5csecret.json', '/character-rb1/fine-tune-all/'):
            self.assertEqual(self.request(path=path)[0], 404)
        self.assertEqual(self.upstream.requests, [])

    def test_same_origin_save_rewrites_local_origin_and_keeps_body(self):
        body = json.dumps({'decisions': {'fake': {'corrected_mood': 'calm'}}}).encode()
        result = self.request('POST', PAGE + 'patch-review', body=body,
                              Origin='https://private.example', **{'Content-Type': 'application/json'})
        self.assertEqual(result[0], 200)
        method, path, headers, forwarded = self.upstream.requests[-1]
        self.assertEqual((method, path, forwarded), ('POST', PAGE + 'patch-review', body))
        self.assertEqual(headers['Origin'], f'http://127.0.0.1:{self.upstream.server_port}')

    def test_cross_origin_missing_origin_unauthenticated_and_oversize_saves_blocked(self):
        for origin in ('https://evil.example', 'http://private.example', ''):
            self.assertEqual(self.request('POST', PAGE + 'patch-review', body=b'{}', Origin=origin)[0], 403)
        self.assertEqual(self.request('POST', PAGE + 'patch-review', authenticated=False,
                                      body=b'{}', Origin='https://private.example')[0], 401)
        self.assertEqual(self.request('POST', PAGE + 'patch-review', body=b'',
                                      Origin='https://private.example',
                                      **{'Content-Length': '15000001'})[0], 400)
        self.assertEqual(self.upstream.requests, [])

    def test_expiration_blocks_existing_credentials(self):
        self.proxy.expires_at = time.time() - 1
        self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.upstream.requests, [])


if __name__ == '__main__':
    unittest.main()
