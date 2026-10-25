"""Current correction dispatch keeps one book output; no native process runs."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_current_higgs_output import CurrentOutputTests, wav
import current_higgs_output as c
import current_higgs_repair as r
import process_listener_stress_queue as q


class CurrentRepairTests(CurrentOutputTests):
    # Reuse the isolated WAV/Store fixture, not any real library or profile.
    def setUp(self):
        super().setUp()
        self.book['chapters'] = [{'id': 'c', 'blocks': [
            {'id': uid, 'text': part['unit']['text']} for uid, part in self.parts.items()]}]
        self.store.source = self.book
        self.view['unit_order'] = list(self.parts)
        for uid, part in self.view['parts'].items():
            part['unit']['key'] = 'old-' + uid
        c.write(c.current_manifest(self.data, self.book_id), c._seal(self.view))
        self.queue = self.root / 'old-tab/listener-stress-queue/note.json'
        self.origin = {'id': 'note', 'book_id': self.book_id, 'audio_hash': 'heard-old-hash'}
        c.write(self.queue, {'id': 'note', 'report': self.origin, 'status': 'queued'})
        self.args = SimpleNamespace(data_dir=self.data, generation_id='g', queue_item=self.queue,
                                    result=self.root / 'result.json', configure_only=False)
        self.calls = []
        def correct(unit, *args):
            if unit['id'] != 'u0': return copy.deepcopy(unit), None
            unit = copy.deepcopy(self.new_unit)
            unit['key'] = 'new-key'
            return unit, {'human': True}
        def render(job, ready, unit):
            self.calls.append(unit['id'])
            raw = job / 'assets' / 'new.wav'
            wav(raw, [101, 102, 103, 104])
            receipt = {**self.receipt, 'audio': 'assets/new.wav'}
            return receipt, unit
        self.repair = SimpleNamespace(
            SELF=Path(q.__file__), d=SimpleNamespace(UNCHECKED={'post_processing': 'off'}),
            frozen_notes=lambda snapshot, notes: (notes, {'note'}),
            pending_reports=lambda *args: ([self.origin], {'u0': self.origin}, [
                {'path': str(self.queue), 'origin_report_sha256': c.digest(self.origin)}]),
            corrected_unit=correct, render_unit=render, write=lambda path, value, **kwargs: c.write(path, value))
        for target, replacement in [('Store', lambda *args, **kwargs: self.store),
                                    ('listener_stress.snapshot', lambda *args: {'sha256': 'snapshot'}),
                                    ('listener_stress.notes', lambda *args: [])]:
            p = patch('current_higgs_repair.' + target, replacement)
            p.start(); self.addCleanup(p.stop)

    def test_repair_only_changed_part_same_output_no_old_tree_dependency(self):
        old = self.store.generation('g')['segments'][1]
        self.assertEqual(0, r.run_if_current(self.args, self.repair))
        result = c.read(self.args.result)
        self.assertEqual(['u0'], self.calls)
        self.assertEqual('g', result['published_generation_id'])
        self.assertEqual(old, self.store.generation('g')['segments'][1])
        self.assertEqual(self.origin, c.read(self.queue)['report'])
        self.assertEqual('applied', c.read(self.queue)['status'])
        self.assertEqual('g', c.load(self.store, self.data, self.book_id)['generation_id'])
        job = next((c.output_root(self.data, self.book_id) / 'work').iterdir())
        self.assertFalse((job / 'plans').exists())
        self.assertFalse((job / 'c').exists())

    def test_failed_part_keeps_current_audio_and_does_not_claim_applied(self):
        before = self.store.generation('g')
        self.repair.render_unit = lambda *args: ({'status': 'technical_failure'}, self.new_unit)
        self.assertEqual(1, r.run_if_current(self.args, self.repair))
        self.assertEqual(before, self.store.generation('g'))
        self.assertEqual('held', c.read(self.args.result)['status'])
        self.assertEqual('queued', c.read(self.queue)['status'])

    def test_configure_only_never_runs_native_or_copies_audio(self):
        self.args.configure_only = True
        before = set(self.data.rglob('*.wav'))
        self.assertEqual(0, r.run_if_current(self.args, self.repair))
        self.assertEqual([], self.calls)
        self.assertEqual(before, set(self.data.rglob('*.wav')))
        self.assertEqual(0, c.read(self.args.result)['copied_audio'])

    def test_unsupported_internal_layout_is_held_before_native(self):
        self.view['parts']['u0']['unit']['render_parts'] = [{'pause': 1}]
        c.write(c.current_manifest(self.data, self.book_id), c._seal(self.view))
        self.assertEqual(1, r.run_if_current(self.args, self.repair))
        self.assertIn('layout-aware', c.read(self.args.result)['error'])
        self.assertEqual([], self.calls)

    def test_current_dispatch_never_falls_back_to_legacy_asr(self):
        folder = c.output_root(self.data, self.book_id)
        c.write(folder / 'ready.json', {'policy': c.POLICY})
        def command(argv, **kwargs):
            c.write(Path(argv[argv.index('--result') + 1]), {'status': 'applied', 'published_generation_id': 'g', 'revision': 5})
        with patch.object(q.subprocess, 'run', side_effect=command) as run, patch.object(q, 'build_revision', side_effect=AssertionError('legacy ASR')):
            q._process_locked(folder, self.data, 'g', self.queue)
        self.assertEqual(1, run.call_count)
        self.assertIn('repair_debug_listener_stress.py', str(run.call_args.args[0]))
        self.assertEqual('applied', c.read(self.queue)['status'])

    def test_failed_current_dispatch_keeps_previous_audio_no_legacy_fallback(self):
        folder = c.output_root(self.data, self.book_id)
        c.write(folder / 'ready.json', {'policy': c.POLICY})
        before = self.store.generation('g')
        def command(argv, **kwargs):
            c.write(Path(argv[argv.index('--result') + 1]), {'status': 'held', 'published_generation_id': 'g'})
        with patch.object(q.subprocess, 'run', side_effect=command), patch.object(q, 'build_revision', side_effect=AssertionError('legacy ASR')):
            with self.assertRaisesRegex(Exception, 'not published'):
                q._process_locked(folder, self.data, 'g', self.queue)
        self.assertEqual(before, self.store.generation('g'))
        self.assertNotEqual('applied', c.read(self.queue)['status'])


class CurrentPublicationTests(CurrentRepairTests):
    def test_progressive_publication_keeps_missing_part_and_avoids_chapter_copy(self):
        folder = self.root / 'replacement-job'
        wav(folder / 'assets/new.wav', [101, 102, 103, 104])
        row = {**self.receipt, 'audio': 'assets/new.wav'}
        c.write(folder / 'units/u0.json', row)
        with patch.object(c, 'Store', return_value=self.store):
            result = c.publish_completed(self.data, folder, [self.new_unit, self.parts['u1']['unit']], self.book_id, 'c')
        self.assertEqual(['u0'], result['published_ids'])
        self.assertEqual(1, result['retained_parts'])
        self.assertEqual('g', result['generation_id'])
        self.assertEqual(0, result['copied_audio'])
        self.assertFalse((folder / 'c').exists())

    def test_old_watcher_cannot_republish_after_current_adoption(self):
        import sync_higgs_preview_runtime as sync
        source = self.root / 'book.fb2'; source.write_bytes(b'original source')
        prepared = self.root / 'book.json'
        c.write(prepared, {'id': self.book_id, 'source_hash': 'a' * 64})
        with patch.object(sync, 'file_digest', return_value='a' * 64), patch.object(sync, 'Store', return_value=self.store):
            with self.assertRaisesRegex(ValueError, 'obsolete chapter sync'):
                sync.sync_preview(workspace=self.root, source=source, prepared_book=prepared,
                                  generation_dir=self.root / 'obsolete-output', data_dir=self.data)
        self.assertEqual([], self.store.published)

class CurrentFailureReceipts(CurrentRepairTests):
    def test_mixed_success_and_failure_terminalizes_progress_keeps_finished_clip(self):
        def correct(unit, *args):
            unit = copy.deepcopy(self.new_unit if unit['id'] == 'u0' else unit)
            unit['key'] = 'changed-' + unit['id']
            return unit, {'human': True}
        self.repair.corrected_unit = correct
        original_render = self.repair.render_unit
        def render(job, ready, unit):
            if unit['id'] == 'u1': return {'status': 'technical_failure'}, unit
            return original_render(job, ready, unit)
        self.repair.render_unit = render
        old = self.store.generation('g')['segments'][1]
        self.assertEqual(1, r.run_if_current(self.args, self.repair))
        job = next((c.output_root(self.data, self.book_id) / 'work').iterdir())
        result = c.read(self.args.result)
        self.assertEqual('held', c.read(job / 'progress.json')['status'])
        self.assertEqual(result, c.read(job / 'terminal.json'))
        self.assertEqual(1, result['rendered'])
        self.assertEqual(['u1'], result['failures'])
        self.assertEqual(old, self.store.generation('g')['segments'][1])
        self.assertEqual('queued', c.read(self.queue)['status'])


    def test_publish_exception_terminalizes_without_losing_previous_playback(self):
        before = self.store.generation('g')
        with patch.object(c, 'publish_parts', side_effect=OSError('publication unavailable')):
            self.assertEqual(1, r.run_if_current(self.args, self.repair))
        job = next((c.output_root(self.data, self.book_id) / 'work').iterdir())
        self.assertEqual('held', c.read(job / 'progress.json')['status'])
        self.assertIn('publication unavailable', c.read(job / 'terminal.json')['error'])
        self.assertEqual(before, self.store.generation('g'))
        self.assertEqual('queued', c.read(self.queue)['status'])




class CurrentStagingCleanupTests(CurrentRepairTests):
    def test_success_removes_reserved_temporary_wavs_keeps_current_playable_file(self):
        self.compact.update(session=self.receipt['session'], raw_audio_sha256=self.receipt['audio_sha256'],
                            synthesis_sha256=c.digest(self.new_unit['synthesis']))
        original = self.repair.render_unit
        def render(job, ready, unit):
            row, effective = original(job, ready, unit)
            session = job / 'native' / row['session']
            wav(session / (unit['id'] + '.wav'), [101, 102, 103, 104])
            c.write(session / 'worker.json', self.compact['worker'])
            c.write(session / 'physical-start.json', {'unit': unit, 'request_sha256': row['request_sha256']})
            c.write(session / 'physical-result.json', self.compact['terminal'])
            return row, effective
        self.repair.render_unit = render
        self.assertEqual(0, r.run_if_current(self.args, self.repair))
        result = c.read(self.args.result)
        self.assertEqual('complete', result['staging_cleanup']['status'])
        self.assertEqual(2, len(result['staging_cleanup']['files']))
        job = next((c.output_root(self.data, self.book_id) / 'work').iterdir())
        self.assertEqual([], list(job.rglob('*.wav')))
        self.assertTrue((job / 'native/physical/worker.json').is_file())
        current = self.store.generation('g')['segments'][0]
        self.assertTrue((self.data / 'audio' / (current['audio_hash'] + '.wav')).is_file())

    def test_failed_job_cleanup_is_rejected_without_deleting_any_audio(self):
        job = c.output_root(self.data, self.book_id) / 'work' / ('a' * 24)
        wav(job / 'assets/keep.wav', [1, 2])
        c.write(job / 'terminal.json', {'status': 'held'})
        with self.assertRaisesRegex(ValueError, 'incomplete correction'):
            c.cleanup_job_audio(self.store, self.data, self.view, job, {})
        self.assertTrue((job / 'assets/keep.wav').exists())


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (CurrentRepairTests, CurrentPublicationTests, CurrentFailureReceipts, CurrentStagingCleanupTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith('test_'))
    return suite


if __name__ == '__main__': unittest.main()
