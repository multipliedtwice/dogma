from __future__ import annotations

import json
import secrets
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))
from rimsky.jobs import RENDER_FAILED, Jobs
from rimsky.store import DEFAULTS, Store
from rimsky.util import AppError
from server import Application, handler_for
from test_core import FB2, SyntheticEngine, complete_audio, pack_fixture


class HeldEngine(SyntheticEngine):
    def __init__(self, hold_after=1, failure=None):
        super().__init__()
        self.hold_after = hold_after
        self.failure = failure
        self.held = threading.Event()

    def call(self, method, payload, stop):
        if method == 'render' and self.rendered >= self.hold_after:
            if self.failure:
                raise AppError(self.failure)
            self.held.set()
            while not stop.wait(.02):
                pass
            raise AppError('Preparation paused.')
        return super().call(method, payload, stop)


class StateFeedback(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = pack_fixture(self.root / 'pack')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self):
        return self.store.create_generation(self.book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False,
        })

    def jobs(self, engine):
        jobs = Jobs(self.store, self.root, lambda _: engine)
        self.addCleanup(jobs.shutdown)
        return jobs

    def test_requested_pause_is_paused_without_an_error_and_keeps_progress(self):
        generation = self.generation()
        engine = HeldEngine(hold_after=1)
        jobs = self.jobs(engine)
        jobs.start(generation['id'])
        self.assertTrue(engine.held.wait(5))
        jobs.pause(generation['id'])
        jobs.thread.join(5)
        self.assertFalse(jobs.thread.is_alive())
        paused = self.store.generation(generation['id'])
        self.assertEqual('paused', paused['status'])
        self.assertEqual(0, paused['resume_on_launch'])
        self.assertEqual('', paused['error'])
        self.assertEqual(1, sum(segment['status'] == 'ready' for segment in paused['segments']))
        self.assertFalse(any(segment['status'] == 'rendering' for segment in paused['segments']))

    def test_shutdown_leaves_active_job_for_checkpoint_resume_on_next_launch(self):
        generation = self.generation()
        first = HeldEngine(hold_after=1)
        jobs = Jobs(self.store, self.root, lambda _: first)
        jobs.start(generation['id'])
        self.assertTrue(first.held.wait(5))
        self.assertEqual(1, self.store.generation(generation['id'])['resume_on_launch'])
        jobs.shutdown()
        self.assertEqual(('paused', 1), tuple(
            self.store.generation(generation['id'])[key] for key in ('status', 'resume_on_launch')
        ))

        resumed = Jobs(self.store, self.root, lambda _: SyntheticEngine())
        try:
            self.assertEqual(generation['id'], resumed.resume_interrupted()['id'])
            resumed.thread.join(10)
            self.assertFalse(resumed.thread.is_alive())
            finished = self.store.generation(generation['id'])
            self.assertEqual('ready', finished['status'], finished['error'])
            self.assertEqual(0, finished['resume_on_launch'])
        finally:
            resumed.shutdown()

    def test_user_pause_stays_paused_when_application_quits_immediately(self):
        generation = self.generation()
        engine = HeldEngine(hold_after=1)
        jobs = Jobs(self.store, self.root, lambda _: engine)
        jobs.start(generation['id'])
        self.assertTrue(engine.held.wait(5))
        jobs.pause(generation['id'])
        jobs.shutdown()
        paused = self.store.generation(generation['id'])
        self.assertEqual(('paused', 0), (paused['status'], paused['resume_on_launch']))

    def test_closed_scheduler_does_not_consume_restart_intent(self):
        generation = self.generation()
        self.store.execute(
            "UPDATE generations SET status='paused',resume_on_launch=1 WHERE id=?",
            (generation['id'],),
        )
        jobs = Jobs(self.store, self.root, lambda _: SyntheticEngine())
        jobs.shutdown()
        self.assertIsNone(jobs.resume_interrupted())
        self.assertEqual(1, self.store.generation(generation['id'])['resume_on_launch'])

    def test_genuine_failure_remains_visible(self):
        generation = self.generation()
        jobs = self.jobs(HeldEngine(hold_after=1, failure='Speech model crashed.'))
        jobs.start(generation['id'])
        jobs.thread.join(5)
        failed = self.store.generation(generation['id'])
        self.assertEqual('failed', failed['status'])
        self.assertEqual(RENDER_FAILED, failed['error'])
        marked = [segment for segment in failed['segments'] if segment['status'] == 'failed']
        self.assertEqual(['Speech model crashed.'], [segment['warning'] for segment in marked])

    def test_reopening_clears_legacy_pause_errors_but_keeps_interruption_notice(self):
        legacy, interrupted, failed = self.generation(), self.generation(), self.generation()
        self.store.status(legacy['id'], 'paused', 'Preparation paused.')
        self.store.status(interrupted['id'], 'rendering')
        self.store.status(failed['id'], 'failed', 'Speech model crashed.')
        self.store.close()
        self.store = Store(self.root / 'library', recover_interrupted=True)
        self.assertEqual(('paused', ''), tuple(self.store.generation(legacy['id'])[key] for key in ('status', 'error')))
        self.assertEqual('paused', self.store.generation(interrupted['id'])['status'])
        self.assertIn('interrupted', self.store.generation(interrupted['id'])['error'])
        self.assertEqual('Speech model crashed.', self.store.generation(failed['id'])['error'])

    def test_no_op_corrections_are_rejected_without_creating_a_revision(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        original = self.store.generation(generation['id'])
        first, second = original['segments'][:2]
        unchanged = [
            [{'id': first['id']}],
            [{'id': first['id'], 'spoken': first['spoken'], 'speaker': first['speaker'], 'delivery': first['delivery']}],
            [{'id': first['id'], 'spoken': first['spoken']}, {'id': second['id'], 'delivery': second['delivery']}],
        ]
        for changes in unchanged:
            with self.assertRaisesRegex(AppError, 'matches the saved passage'):
                self.store.revise(original['id'], changes)
        self.assertEqual([original['id']], [saved['id'] for saved in self.store.generation_list(self.book['id'])])
        revised = self.store.revise(original['id'], [{'id': first['id'], 'spoken': 'Changed text.'}, {'id': second['id'], 'spoken': second['spoken']}])
        self.assertEqual(original['id'], revised['parent_id'])
        by_id = {segment['id']: segment for segment in revised['segments']}
        self.assertEqual(('pending', None), (by_id[first['id']]['status'], by_id[first['id']]['audio_hash']))
        self.assertEqual(second['audio_hash'], by_id[second['id']]['audio_hash'])
        self.assertEqual('ready', self.store.generation(original['id'])['status'])

    def test_retired_subtitles_setting_is_ignored_and_cannot_be_updated(self):
        self.assertNotIn('subtitles', DEFAULTS)
        self.assertNotIn('subtitles', self.store.settings())
        with self.assertRaisesRegex(AppError, 'Unknown setting'):
            self.store.update_settings({'subtitles': True})
        existing = Store(self.root / 'existing')
        try:
            existing.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps({**DEFAULTS, 'subtitles': True}),))
        finally:
            existing.close()
        reopened = Store(self.root / 'existing')
        try:
            self.assertNotIn('subtitles', reopened.settings())
        finally:
            reopened.close()

    def test_rejected_session_token_is_reported_as_unauthorized(self):
        app = Application(self.root / 'http-library', self.root)
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, token, dev=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, auth, body=None):
            headers = {'Authorization': 'Bearer ' + auth, 'Content-Type': 'application/json'}
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}{path}', data=data, headers=headers), timeout=5) as response:
                return json.load(response)
        try:
            self.assertEqual([], request('/api', token, {'op': 'library', 'data': {}}))
            for path, body in (('/api', {'op': 'library', 'data': {}}), ('/asset?kind=audio&id=missing', None)):
                with self.assertRaises(HTTPError) as rejected:
                    request(path, 'stale-token', body)
                self.assertEqual(401, rejected.exception.code)
                self.assertIn(b'authentication failed', rejected.exception.read())
            with self.assertRaises(HTTPError) as ordinary:
                request('/api', token, {'op': 'book', 'data': {'id': 'missing'}})
            self.assertEqual(400, ordinary.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    unittest.main()
