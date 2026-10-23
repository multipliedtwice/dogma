from __future__ import annotations
import sys
import threading
import unittest
from unittest.mock import patch
import test_paragraph_illustrations as fixture
from rimsky.illustrations import render_codex
from rimsky.util import AppError
from rimsky.visuals import save_visual

REFUSAL = 'Wait for the running illustration preparation to finish before deleting this paragraph’s last image; deleting or stopping now would discard a paid request.'


class IllustrationFixes(unittest.TestCase):
    setUp = fixture.ParagraphIllustrations.setUp
    start = fixture.ParagraphIllustrations.start
    finished = fixture.ParagraphIllustrations.finished

    def visual(self, source=None, color='blue'):
        return save_visual(self.store, self.book['id'], source or self.sources[0], fixture.png(color))

    def resume(self, run, consent=True, retry=False):
        return self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': consent, 'retry_failed': retry})

    def held(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            if brief['source_id'] == self.sources[1]:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Provider fixture was not released')
            return fixture.png()
        self.app.illustrations.renderer = render
        return entered, release

    def test_codex_output_is_saved_when_a_manual_image_arrives_during_the_request(self):
        attached = []
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            attached.append(self.visual(brief['source_id']))
            return fixture.png('red')
        self.app.illustrations.renderer = render
        run = self.finished(self.start(self.sources[:1]))
        visuals = self.store.visuals(self.book['id'])
        self.assertEqual(['imported', 'generated-codex'], [visual['kind'] for visual in visuals])
        self.assertEqual(attached[0]['id'], visuals[0]['id'])
        self.assertEqual(('ready', 'ready', visuals[1]['id']), (run['status'], run['items'][0]['status'], run['items'][0]['visual_id']))

    def test_codex_output_is_saved_when_an_images_api_result_arrives_during_the_request(self):
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            with patch('rimsky.visuals.fetch_visual', return_value=fixture.png('green')):
                self.app.dispatch('generate_visual', {'book_id': self.book['id'], 'source_id': brief['source_id'], 'api_key': 'key', 'consent': True})
            return fixture.png('red')
        self.app.illustrations.renderer = render
        run = self.finished(self.start(self.sources[:1]))
        visuals = self.store.visuals(self.book['id'])
        self.assertEqual(['generated-online', 'generated-codex'], [visual['kind'] for visual in visuals])
        self.assertEqual(('ready', visuals[1]['id']), (run['items'][0]['status'], run['items'][0]['visual_id']))

    def test_protected_ids_match_the_delete_rule_while_a_run_is_active(self):
        entered, release = self.held()
        run = self.start(self.sources[:2])
        self.assertTrue(entered.wait(2))
        only = self.app.illustrations.snapshot(run['id'])['items'][0]['visual_id']
        status = self.app.dispatch('illustration_status', {'id': run['id']})
        self.assertEqual([only], status['protected_visual_ids'])
        history = self.app.dispatch('illustration_history', {'book_id': self.book['id']})
        self.assertEqual([only], history[0]['protected_visual_ids'])
        with self.assertRaises(AppError) as rejected:
            self.app.dispatch('delete_visual', {'id': only})
        self.assertEqual(('illustration_run_active', REFUSAL), (rejected.exception.code, str(rejected.exception)))
        self.assertFalse(self.app.illustrations.stop.is_set())
        extra = self.visual(color='green')
        self.assertEqual([], self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids'])
        self.app.dispatch('delete_visual', {'id': only})
        self.assertFalse(self.app.illustrations.stop.is_set())
        self.assertEqual([extra['id']], self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids'])
        unrelated = self.visual(self.sources[2])
        self.assertNotIn(unrelated['id'], self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids'])
        self.app.dispatch('delete_visual', {'id': unrelated['id']})
        release.set()
        completed = self.finished(run)
        self.assertEqual(('ready', extra['id']), (completed['status'], completed['items'][0]['visual_id']))
        self.assertEqual([], self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids'])
        self.assertNotIn('protected_visual_ids', self.app.dispatch('illustration_history', {'book_id': self.book['id']})[0])

    def test_the_in_flight_paragraph_keeps_its_older_images_deletable(self):
        old = self.visual(self.sources[1])
        entered, release = self.held()
        run = self.app.dispatch('illustration_start', {'book_id': self.book['id'], 'source_ids': self.sources[1:2], 'consent': True, 'replace_existing': True})
        self.assertTrue(entered.wait(2))
        self.assertEqual([], self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids'])
        self.app.dispatch('delete_visual', {'id': old['id']})
        self.assertFalse(self.app.illustrations.stop.is_set())
        release.set()
        self.assertEqual('ready', self.finished(run)['status'])

    def test_invalid_sole_image_of_a_completed_item_stays_protected(self):
        entered, release = self.held()
        run = self.start(self.sources[:2])
        self.assertTrue(entered.wait(2))
        first = self.app.illustrations.snapshot(run['id'])['items'][0]['visual_id']
        broken = self.visual(color='green')
        (self.store.root / 'visuals' / broken['file']).write_bytes(b'corrupt')
        (self.store.root / 'visuals' / self.store.rows('SELECT file FROM visuals WHERE id=?', (first,))[0]['file']).write_bytes(b'corrupt')
        self.assertEqual({first, broken['id']}, set(self.app.dispatch('illustration_status', {'id': run['id']})['protected_visual_ids']))
        for visual_id in (first, broken['id']):
            with self.assertRaises(AppError):
                self.app.dispatch('delete_visual', {'id': visual_id})
        release.set()
        self.finished(run)

    def test_manual_image_after_deletion_finishes_saved_runs_locally(self):
        first = self.finished(self.start(self.sources[:1]))
        second = self.finished(self.start(self.sources[:2]))
        self.assertEqual(self.sources[:1] + self.sources[1:2], self.calls)
        self.app.dispatch('delete_visual', {'id': first['items'][0]['visual_id']})
        for run in (first, second):
            self.assertEqual(('paused', 'pending'), (self.app.illustrations.snapshot(run['id'])['status'], self.app.illustrations.snapshot(run['id'])['items'][0]['status']))
        manual = self.visual(color='green')
        self.store.update_settings({'online_images': False})
        self.app.illustrations.preflight = lambda: self.fail('No provider preflight is needed')
        for run in (first, second):
            result = self.resume(run, consent=False)
            self.assertEqual(('ready', '', 'skipped', manual['id']), (result['status'], result['error'], result['items'][0]['status'], result['items'][0]['visual_id']))
            self.assertIsNone(self.app.illustrations.active)
        self.assertEqual(self.sources[:1] + self.sources[1:2], self.calls)

    def test_failed_and_uncertain_items_with_a_saved_image_need_no_retry_approval(self):
        for status in ('failed', 'uncertain'):
            with self.subTest(status=status):
                run = self.finished(self.start(self.sources[:1]))
                state = self.app.illustrations._state(run)
                state['items'][0].update(status=status, visual_id=None, error='Controlled interruption')
                self.app.illustrations._save(run['id'], 'failed', state)
                calls = list(self.calls)
                result = self.resume(run, consent=False)
                self.assertEqual(('ready', 'skipped'), (result['status'], result['items'][0]['status']))
                self.assertEqual(calls, self.calls)

    def test_remaining_items_without_images_still_need_consent_after_reconciliation(self):
        run = self.finished(self.start(self.sources[:2]))
        self.store.execute('DELETE FROM visuals WHERE source_id=?', (self.sources[1],))
        state = self.app.illustrations._state(run)
        for item in state['items']:
            item.update(status='pending', visual_id=None)
        self.app.illustrations._save(run['id'], 'paused', state)
        with self.assertRaisesRegex(AppError, 'approve sending'):
            self.resume(run, consent=False)
        repaired = self.app.illustrations.snapshot(run['id'])
        self.assertEqual(['skipped', 'pending'], [item['status'] for item in repaired['items']])
        self.assertEqual('ready', self.finished(self.resume(run))['status'])
        self.assertEqual(self.sources[:2] + self.sources[1:2], self.calls)

    def test_stop_before_the_process_spawns_leaves_the_item_pending(self):
        marker = self.root / 'spawned'
        script = self.root / 'controlled-codex'
        script.write_text(f'#!{sys.executable}\nimport pathlib\npathlib.Path({str(marker)!r}).write_text("spawned")\n')
        script.chmod(0o700)
        self.app.illustrations.renderer = lambda brief, style, stop, root: render_codex(brief, style, stop, root, executable=str(script))
        original = self.app.illustrations._save
        def save(run_id, status, state, merge=True):
            original(run_id, status, state, merge)
            if any(item['status'] == 'running' for item in state['items']):
                self.app.illustrations.stop.set()
        self.app.illustrations._save = save
        run = self.finished(self.start(self.sources[:1]))
        self.assertFalse(marker.exists())
        self.assertEqual(('paused', 'Illustration preparation stopped before dispatch.'), (run['status'], run['error']))
        self.assertEqual(('pending', ''), (run['items'][0]['status'], run['items'][0]['error']))
        self.app.illustrations._save = original
        self.app.illustrations.renderer = lambda brief, style, stop, root: self.calls.append(brief['source_id']) or fixture.png()
        self.assertEqual('ready', self.finished(self.resume(run, retry=False))['status'])
        self.assertEqual(self.sources[:1], self.calls)

    def test_a_second_images_api_request_for_the_same_paragraph_is_refused(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def provider(request):
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise AssertionError('Provider fixture was not released')
            return fixture.png()
        request = lambda source: self.app.dispatch('generate_visual', {'book_id': self.book['id'], 'source_id': source, 'api_key': 'key', 'consent': True})
        results = []
        with patch('rimsky.visuals.fetch_visual', side_effect=provider):
            worker = threading.Thread(target=lambda: results.append(request(self.sources[0])))
            worker.start()
            self.assertTrue(entered.wait(2))
            with self.assertRaises(AppError) as refused:
                request(self.sources[0])
            self.assertEqual('visual_request_active', refused.exception.code)
            self.assertEqual('An Images API request for this paragraph is still running. Wait for it to finish before requesting another image.', str(refused.exception))
            self.assertEqual('generated-online', request(self.sources[1])['kind'])
            release.set()
            worker.join(5)
            self.assertEqual(1, len(results))
            self.assertEqual(self.sources[0], request(self.sources[0])['source_id'])
        with patch('rimsky.visuals.fetch_visual', side_effect=AppError('Controlled provider failure')):
            with self.assertRaisesRegex(AppError, 'Controlled provider failure'):
                request(self.sources[2])
        with patch('rimsky.visuals.fetch_visual', return_value=fixture.png()):
            self.assertEqual(self.sources[2], request(self.sources[2])['source_id'])
        self.assertEqual(4, len(self.store.visuals(self.book['id'])))


if __name__ == '__main__':
    unittest.main()
