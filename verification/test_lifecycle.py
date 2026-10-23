from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_core import FB2
from rimsky.instance import library_lease
from rimsky.util import AppError

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def runtime(root):
    command = [sys.executable, str(ROOT/'runtime/server.py'), '--data-dir', str(root), '--parent-pipe', '--dev']
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8') as process:
        messages = queue.Queue()
        threading.Thread(target=lambda: messages.put(process.stdout.readline()), daemon=True).start()
        try:
            ready = json.loads(messages.get(timeout=10))
            yield process, ready
        finally:
            if process.stdin:
                process.stdin.close()
                process.stdin = None
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise AssertionError('Local runtime did not terminate after its parent pipe closed.')


def request(session, operation, data=None, *, origin='http://127.0.0.1:1420', token=None):
    body = json.dumps({'op': operation, 'data': data or {}}).encode()
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer '+(token or session['token']), 'Origin': origin}
    with urlopen(Request(f'http://127.0.0.1:{session["port"]}/api', data=body, headers=headers), timeout=5) as response:
        return json.load(response)


class RuntimeLifecycle(unittest.TestCase):
    def test_real_dev_session_import_and_parent_pipe_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            with runtime(Path(temporary)) as (process, session):
                self.assertEqual(1, session['protocol'])
                self.assertGreaterEqual(len(session['token']), 32)
                self.assertEqual([], request(session, 'library'))
                headers = {'Authorization': 'Bearer '+session['token'], 'Content-Type': 'application/octet-stream', 'Origin': 'http://127.0.0.1:1420'}
                with urlopen(Request(f'http://127.0.0.1:{session["port"]}/import?name=book.fb2', data=FB2, headers=headers), timeout=5) as response:
                    book = json.load(response)
                self.assertEqual(book['id'], request(session, 'library')[0]['id'])
                with self.assertRaises(HTTPError):
                    request(session, 'library', token='wrong-session')
                with self.assertRaises(HTTPError):
                    request(session, 'library', origin='https://untrusted.example')
            self.assertEqual(0, process.returncode)
            with runtime(Path(temporary)) as (_, second):
                self.assertEqual(book['id'], request(second, 'library')[0]['id'])

    def test_second_process_cannot_open_active_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            with runtime(Path(temporary)) as (_, session):
                result = subprocess.run([sys.executable, str(ROOT/'runtime/server.py'), '--data-dir', temporary], input='', text=True, encoding='utf-8', capture_output=True, timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('already open', result.stderr)
                self.assertEqual({'protocol': 1, 'error': 'This library is already open in another Dogma process.'}, json.loads(result.stdout))
                self.assertEqual([], request(session, 'library'))

    def test_library_lease_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'fixture'):
                with library_lease(root):
                    with self.assertRaises(AppError):
                        with library_lease(root):
                            self.fail('Second lease must not be granted.')
                    raise ValueError('fixture')
            with library_lease(root):
                self.assertTrue((root/'.runtime.lock').is_file())
