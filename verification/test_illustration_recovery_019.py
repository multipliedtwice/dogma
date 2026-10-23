from __future__ import annotations
import json
import threading
import unittest
import test_paragraph_illustrations as fixture
from rimsky.util import AppError
from rimsky.visuals import save_visual


class IllustrationRecovery(unittest.TestCase):
    setUp = fixture.ParagraphIllustrations.setUp
    start = fixture.ParagraphIllustrations.start
    finished = fixture.ParagraphIllustrations.finished

    def visual(self, source=None, color='blue'):
        return save_visual(self.store, self.book['id'], source or self.sources[0], fixture.png(color))

    def resume(self, run, consent=True, retry=False):
        return self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': consent, 'retry_failed': retry})

    def legacy(self, run):
        state = self.app.illustrations._state(run)
        for item in state['items']:
            item.update(status='skipped', visual_id=None)
        self.store.execute('UPDATE illustration_runs SET state=? WHERE id=?', (json.dumps(state), run['id']))

    def test_reused_output_retains_identity_and_deletion_requires_explicit_resume(self):
        visual = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        self.assertEqual(visual['id'], run['items'][0]['visual_id'])
        self.assertEqual([], self.calls)
        self.app.dispatch('delete_visual', {'id': visual['id']})
        deleted = self.app.illustrations.snapshot(run['id'])
        self.assertEqual('paused', deleted['status'])
        self.assertEqual('pending', deleted['items'][0]['status'])
        self.assertIsNone(deleted['items'][0]['visual_id'])
        with self.assertRaisesRegex(AppError, 'approve sending'):
            self.resume(run, consent=False)
        self.assertEqual([], self.calls)
        completed = self.finished(self.resume(run))
        self.assertEqual('ready', completed['status'])
        self.assertEqual(self.sources[:1], self.calls)
        self.assertNotEqual(visual['id'], completed['items'][0]['visual_id'])

    def test_deletion_recovers_legacy_skipped_records_without_visual_ids(self):
        visual = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        self.legacy(run)
        self.app.dispatch('delete_visual', {'id': visual['id']})
        self.assertEqual('paused', self.app.illustrations.snapshot(run['id'])['status'])
        self.assertEqual('ready', self.finished(self.resume(run))['status'])
        self.assertEqual(self.sources[:1], self.calls)

    def test_resume_repairs_legacy_identity_without_network_permission(self):
        visual = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        self.legacy(run)
        self.store.update_settings({'online_images': False})
        self.app.illustrations.preflight = lambda: self.fail('No provider preflight is needed')
        result = self.resume(run, consent=False)
        self.assertEqual('ready', result['status'])
        self.assertEqual(visual['id'], result['items'][0]['visual_id'])
        self.assertEqual([], self.calls)

    def test_resume_checks_legacy_completed_output_before_marking_ready(self):
        visual = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        self.legacy(run)
        (self.store.root / 'visuals' / visual['file']).unlink()
        with self.assertRaisesRegex(AppError, 'approve sending'):
            self.resume(run, consent=False)
        repaired = self.app.illustrations.snapshot(run['id'])
        self.assertEqual('paused', repaired['status'])
        self.assertEqual('pending', repaired['items'][0]['status'])
        self.assertEqual([], self.calls)
        self.assertEqual('ready', self.finished(self.resume(run))['status'])
        self.assertEqual(self.sources[:1], self.calls)

    def test_resume_detects_corrupt_completed_image(self):
        run = self.finished(self.start(self.sources[:1]))
        visual = self.store.visuals(self.book['id'])[0]
        (self.store.root / 'visuals' / visual['file']).write_bytes(b'corrupt image')
        result = self.finished(self.resume(run))
        self.assertEqual('ready', result['status'])
        self.assertEqual(self.sources[:1] * 2, self.calls)
        self.assertNotEqual(visual['id'], result['items'][0]['visual_id'])

    def test_resume_uses_valid_alternative_without_dispatch(self):
        old = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        replacement = self.visual(color='green')
        (self.store.root / 'visuals' / old['file']).unlink()
        self.store.update_settings({'online_images': False})
        result = self.resume(run, consent=False)
        self.assertEqual('ready', result['status'])
        self.assertEqual(replacement['id'], result['items'][0]['visual_id'])
        self.assertEqual([], self.calls)

    def test_deleting_selected_image_rebinds_to_usable_alternative(self):
        old = self.visual()
        run = self.finished(self.start(self.sources[:1]))
        alternate = self.visual(color='green')
        self.app.dispatch('delete_visual', {'id': old['id']})
        result = self.app.illustrations.snapshot(run['id'])
        self.assertEqual('ready', result['status'])
        self.assertEqual(alternate['id'], result['items'][0]['visual_id'])
        self.assertEqual([], self.calls)
        self.app.dispatch('delete_visual', {'id': alternate['id']})
        self.assertEqual('paused', self.app.illustrations.snapshot(run['id'])['status'])

    def test_deleting_unrelated_image_does_not_invalidate_a_run(self):
        selected = self.visual()
        unrelated = self.visual(self.sources[1])
        run = self.finished(self.start(self.sources[:1]))
        self.app.dispatch('delete_visual', {'id': unrelated['id']})
        result = self.app.illustrations.snapshot(run['id'])
        self.assertEqual('ready', result['status'])
        self.assertEqual(selected['id'], result['items'][0]['visual_id'])

    def test_missing_new_version_cannot_reuse_the_replaced_image(self):
        old = self.visual()
        run = self.app.dispatch('illustration_start', {'book_id': self.book['id'], 'source_ids': self.sources[:1], 'consent': True, 'replace_existing': True})
        completed = self.finished(run)
        new_id = completed['items'][0]['visual_id']
        self.app.dispatch('delete_visual', {'id': new_id})
        self.assertEqual('paused', self.app.illustrations.snapshot(run['id'])['status'])
        self.assertEqual([old['id']], [item['id'] for item in self.store.visuals(self.book['id'])])
        resumed = self.finished(self.resume(run))
        self.assertEqual('ready', resumed['status'])
        self.assertNotEqual(old['id'], resumed['items'][0]['visual_id'])
        self.assertEqual(self.sources[:1] * 2, self.calls)

    def test_attachment_arriving_during_render_keeps_both_images_and_binds_the_paid_output(self):
        attached = []
        def render(brief, style, stop, root):
            attached.append(self.visual(brief['source_id']))
            return fixture.png()
        self.app.illustrations.renderer = render
        run = self.finished(self.start(self.sources[:1]))
        generated = [visual for visual in self.store.visuals(self.book['id']) if visual['kind'] == 'generated-codex']
        self.assertEqual('ready', run['items'][0]['status'])
        self.assertEqual([generated[0]['id']], [run['items'][0]['visual_id']])
        self.app.dispatch('delete_visual', {'id': attached[0]['id']})
        self.assertEqual(('ready', generated[0]['id']), (self.app.illustrations.snapshot(run['id'])['status'], self.app.illustrations.snapshot(run['id'])['items'][0]['visual_id']))
        self.app.dispatch('delete_visual', {'id': generated[0]['id']})
        self.assertEqual('paused', self.app.illustrations.snapshot(run['id'])['status'])

    def test_deleting_an_image_the_active_run_still_needs_is_rejected_without_stopping_it(self):
        old = self.visual()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            entered.set()
            if not release.wait(3):
                raise AssertionError('Provider fixture was not released')
            self.assertFalse(stop.is_set())
            return fixture.png()
        self.app.illustrations.renderer = render
        run = self.start(self.sources[:2])
        self.assertTrue(entered.wait(2))
        with self.assertRaises(AppError) as rejected:
            self.app.dispatch('delete_visual', {'id': old['id']})
        self.assertEqual('illustration_run_active', rejected.exception.code)
        self.assertFalse(self.app.illustrations.stop.is_set())
        release.set()
        completed = self.finished(run)
        self.assertEqual('ready', completed['status'])
        self.assertEqual(old['id'], completed['items'][0]['visual_id'])
        self.assertEqual(self.sources[1:2], self.calls)
        self.app.dispatch('delete_visual', {'id': old['id']})
        self.assertEqual('paused', self.app.illustrations.snapshot(run['id'])['status'])

    def test_active_rebind_survives_stale_worker_checkpoint(self):
        old = self.visual()
        alternate = self.visual(color='green')
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        def render(brief, style, stop, root):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Provider fixture was not released')
            self.assertFalse(stop.is_set())
            return fixture.png()
        self.app.illustrations.renderer = render
        run = self.start(self.sources[:2])
        self.assertTrue(entered.wait(2))
        selected = self.app.illustrations.snapshot(run['id'])['items'][0]['visual_id']
        surviving = old if selected == alternate['id'] else alternate
        self.app.dispatch('delete_visual', {'id': selected})
        release.set()
        completed = self.finished(run)
        self.assertEqual('ready', completed['status'])
        self.assertEqual(surviving['id'], completed['items'][0]['visual_id'])
        self.assertEqual(2, len(self.store.visuals(self.book['id'])))
