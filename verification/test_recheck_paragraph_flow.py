from __future__ import annotations
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
from test_core import epub_fixture
from test_paragraph_illustrations import png
from rimsky.illustrations import IllustrationJobs, render_codex
from rimsky.util import AppError
from rimsky.visuals import save_visual
from server import Application


class ParagraphFlowRecheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.addCleanup(self.app.close)
        self.store = self.app.store
        self.book = self.store.import_data(epub_fixture({'OEBPS/a.xhtml': b'<html><body><h1>First</h1><p>First paragraph.</p><p>Second paragraph.</p></body></html>'}), 'first.epub')
        self.sources = [b['id'] for c in self.book['chapters'] for b in c['blocks'] if b['kind'] == 'paragraph']
        self.store.update_settings({'online_images': True})
        self.calls = []
        self.app.illustrations.preflight = lambda: 'controlled-codex'
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            return png()
        self.app.illustrations.renderer = render

    def start(self, sources=None):
        return self.app.dispatch('illustration_start', {'book_id': self.book['id'], 'source_ids': sources or self.sources, 'style': 'ink', 'consent': True})

    def finish(self, run):
        self.app.illustrations.thread.join(5)
        self.assertFalse(self.app.illustrations.thread.is_alive())
        return self.app.illustrations.snapshot(run['id'])

    def test_book_deletion_cannot_remove_another_books_new_shared_image(self):
        second = self.store.import_data(epub_fixture({'OEBPS/a.xhtml': b'<html><body><h1>Other</h1><p>Other paragraph.</p></body></html>'}), 'other.epub')
        target = next(b['id'] for c in second['chapters'] for b in c['blocks'] if b['kind'] == 'paragraph')
        visual = save_visual(self.store, self.book['id'], self.sources[0], png())
        inspecting, writer_entered, written = threading.Event(), threading.Event(), threading.Event()
        errors = []
        result = []
        def writer():
            try:
                if not inspecting.wait(3):
                    raise AssertionError('Deletion never inspected shared-image references')
                writer_entered.set()
                result.append(save_visual(self.store, second['id'], target, png()))
            except BaseException as error:
                errors.append(error)
            finally:
                written.set()
        real_rows = self.store.rows
        observed = False
        def rows(query, parameters=()):
            nonlocal observed
            value = real_rows(query, parameters)
            if query == 'SELECT id FROM visuals WHERE file=?' and parameters == (visual['file'],) and not value and not observed:
                observed = True
                inspecting.set()
                self.assertTrue(writer_entered.wait(2))
                written.wait(.2)
            return value
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with patch.object(self.store, 'rows', side_effect=rows):
                deleted = self.app.dispatch('delete_book', {'id': self.book['id']})
        finally:
            inspecting.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(deleted['deleted'])
        self.assertEqual(1, len(result))
        path = self.store.root / 'visuals' / result[0]['file']
        self.assertTrue(path.is_file(), 'Newly accepted illustration lost its shared image bytes during deletion')
        self.assertEqual(png(), path.read_bytes())

    def test_last_image_completion_can_resume_after_status_write_failure(self):
        real_save = self.app.illustrations._save
        failed = False
        def save(run_id, status, state):
            nonlocal failed
            if status == 'ready' and not failed:
                failed = True
                raise OSError('controlled final-status publication failure')
            return real_save(run_id, status, state)
        with patch.object(self.app.illustrations, '_save', side_effect=save):
            run = self.finish(self.start([self.sources[0]]))
        self.assertEqual('failed', run['status'])
        self.assertEqual('ready', run['items'][0]['status'])
        self.app.illustrations.preflight = lambda: (_ for _ in ()).throw(AssertionError('No external request is needed to finish the saved run'))
        resumed = self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': True})
        if self.app.illustrations.thread:
            self.app.illustrations.thread.join(5)
        self.assertEqual('ready', self.app.illustrations.snapshot(resumed['id'])['status'])
        self.assertEqual([self.sources[0]], self.calls)
        self.assertEqual('', self.app.illustrations.snapshot(resumed['id'])['error'])

    def test_reopened_all_saved_image_run_can_finish_without_inference(self):
        run = self.finish(self.start())
        self.app.illustrations._save(run['id'], 'running', self.app.illustrations._state(run))
        restarted = IllustrationJobs(self.store, renderer=lambda *args: self.fail('Saved paragraphs must not render again'), preflight=lambda: self.fail('No provider preflight needed'))
        self.addCleanup(restarted.shutdown)
        self.assertEqual('paused', restarted.snapshot(run['id'])['status'])
        self.assertEqual('ready', restarted.resume(run['id'], True)['status'])
        self.assertEqual(len(self.sources), len(self.store.visuals(self.book['id'])))

    def test_saved_run_finalization_works_offline_without_permission_or_external_preflight(self):
        run = self.finish(self.start([self.sources[0]]))
        state = self.app.illustrations._state(run)
        state['error'] = 'Interrupted final status write'
        self.app.illustrations._save(run['id'], 'failed', state)
        originals = self.store.visuals(self.book['id'])
        self.store.update_settings({'online_images': False})
        self.app.illustrations.preflight = lambda: self.fail('Local finalization cannot start Codex')
        result = self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': False})
        self.assertEqual('ready', result['status'])
        self.assertEqual('', result['error'])
        self.assertEqual(originals, self.store.visuals(self.book['id']))
        self.assertEqual([self.sources[0]], self.calls)

    def test_unfinished_runs_still_require_online_permission(self):
        run = self.finish(self.start([self.sources[0]]))
        self.store.execute('DELETE FROM visuals')
        state = self.app.illustrations._state(run)
        state['items'][0]['status'] = 'pending'
        self.app.illustrations._save(run['id'], 'paused', state)
        self.store.update_settings({'online_images': False})
        before = self.app.illustrations.snapshot(run['id'])
        with self.assertRaisesRegex(AppError, 'Enable online'):
            self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': True})
        self.assertEqual(before, self.app.illustrations.snapshot(run['id']))

    def test_uncertain_requests_cannot_use_local_finish_to_bypass_retry_approval(self):
        run = self.finish(self.start([self.sources[0]]))
        self.store.execute('DELETE FROM visuals')
        state = self.app.illustrations._state(run)
        state['items'][0]['status'] = 'uncertain'
        self.app.illustrations._save(run['id'], 'paused', state)
        with self.assertRaisesRegex(AppError, 'explicit approval'):
            self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': True, 'retry_failed': False})
        self.assertEqual('uncertain', self.app.illustrations.snapshot(run['id'])['items'][0]['status'])
        self.assertEqual([self.sources[0]], self.calls)

    def test_invalid_completion_states_are_not_marked_successful(self):
        run = self.finish(self.start([self.sources[0]]))
        for items in ([], [{**run['items'][0], 'status': 'unrecognized'}], [{**run['items'][0], 'status': 'running'}]):
            with self.subTest(items=items):
                state = {**self.app.illustrations._state(run), 'items': items}
                self.app.illustrations._save(run['id'], 'failed', state)
                with self.assertRaisesRegex(AppError, 'completion state'):
                    self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': False})
                self.assertEqual('failed', self.app.illustrations.snapshot(run['id'])['status'])

    def test_failed_local_finalization_can_be_retried_without_regenerating(self):
        run = self.finish(self.start([self.sources[0]]))
        self.app.illustrations._save(run['id'], 'paused', self.app.illustrations._state(run))
        before = self.app.illustrations.snapshot(run['id'])
        with patch.object(self.app.illustrations, '_save', side_effect=OSError('controlled database failure')):
            with self.assertRaises(OSError):
                self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': False})
        self.assertEqual(before, self.app.illustrations.snapshot(run['id']))
        self.assertEqual('ready', self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': False})['status'])
        self.assertEqual([self.sources[0]], self.calls)

    def _cli(self, before=''):
        script = self.root / 'controlled-codex'
        script.write_text(f'''#!{sys.executable}
import pathlib,json,base64,os,sys,subprocess,time
{before}
brief=json.loads(pathlib.Path('paragraph.json').read_text())
pathlib.Path('result.png').write_bytes(base64.b64decode({base64.b64encode(png()).decode()!r}))
pathlib.Path('result.json').write_text(json.dumps({{'source_id':brief['source_id'],'status':'generated','file':'result.png','reason':''}}))
''', encoding='utf-8')
        script.chmod(0o700)
        return str(script)

    @unittest.skipIf(os.name == 'nt', 'POSIX owned-process-group check')
    def test_codex_completion_stops_owned_descendants_after_leader_exit(self):
        marker = self.root / 'descendant-alive'
        child = self.root / 'child.pid'
        ready = self.root / 'child.ready'
        command = f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(.5); pathlib.Path({str(marker)!r}).write_text('still running'); time.sleep(30)"
        before = f"p=subprocess.Popen([sys.executable,'-c',{command!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); pathlib.Path({str(child)!r}).write_text(str(p.pid))\ndeadline=time.monotonic()+5\nwhile not pathlib.Path({str(ready)!r}).exists():\n    assert time.monotonic()<deadline\n    time.sleep(.01)"
        try:
            result = render_codex({'source_id': self.sources[0]}, 'ink', threading.Event(), self.root, executable=self._cli(before))
            self.assertEqual(png(), result)
            time.sleep(.7)
            self.assertFalse(marker.exists(), 'Codex child continued after its owned request returned successfully')
        finally:
            if child.exists():
                try:
                    os.kill(int(child.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_completed_codex_output_still_respects_the_log_limit(self):
        executable = self._cli("os.write(1,b'x'*(4*1024*1024+1))")
        real_popen = subprocess.Popen
        def completed(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            process.wait(timeout=5)
            return process
        with patch('rimsky.illustrations.subprocess.Popen', side_effect=completed):
            with self.assertRaisesRegex(AppError, 'output limit'):
                render_codex({'source_id': self.sources[0]}, 'ink', threading.Event(), self.root, executable=executable)


if __name__ == '__main__':
    unittest.main()
