import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]
import supervise_debug_higgs_one_shot as supervisor


class DebugSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'generation'
        self.output.mkdir()
        self.adapter = self.root / 'adapter.py'
        self.adapter.write_text('# frozen adapter\n')
        self.unit = {'id': 'u0001', 'chapter_id': 'c0001', 'synthesis': {'backend': 'higgs_audio_cpp', 'prompt': 'Source'}}
        supervisor.write(self.output / 'assignment.json', {'units': [self.unit]})
        supervisor.write(self.output / 'plan.json', {'units': [self.unit]})
        self.ready = {'policy': 'development-private-higgs-one-shot-v1', 'source': str(self.root / 'old'),
                      'chapters': ['c0001'], 'unit_count': 1, 'chapter_counts': {'c0001': 1},
                      'implementation_sha256': supervisor.sha(self.adapter), **supervisor.UNCHECKED,
                      'plans': [{'assignment': 'assignment.json', 'sha256': supervisor.sha(self.output / 'assignment.json'),
                                 'plan': 'plan.json', 'plan_sha256': supervisor.sha(self.output / 'plan.json')}]}
        supervisor.write(self.output / 'ready.json', self.ready)
        self.args = argparse.Namespace(output=self.output, data_dir=self.root / 'library', source=self.root / 'book.fb2',
                                       demo_output=[], batch_size=32, session_timeout=7200, poll_seconds=.25,
                                       max_restarts=2, stall_seconds=8100)

    def rendered(self, music=False, failure=False):
        audio = self.output / 'assets/speech.wav'
        audio.parent.mkdir(exist_ok=True)
        with wave.open(str(audio), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
            stream.writeframes(b'\0\0' * 240)
        receipt = {'unit': 'u0001', 'take': 0, 'status': 'technical_failure' if failure else 'rendered',
                   'synthesis_sha256': supervisor.digest(self.unit['synthesis']), 'audio': 'assets/speech.wav',
                   'audio_sha256': supervisor.sha(audio), **supervisor.UNCHECKED}
        supervisor.write(self.output / 'units/u0001.json', receipt, immutable=True)
        row = {'complete': True, 'units': 1, **supervisor.UNCHECKED}
        if music:
            row['music'] = [{'kind': 'scene_bed'}]
        else:
            row['music_pending'] = 'Cached track needs publication'
        supervisor.write(self.output / 'progress.json', {'generation': self.output.name, 'status': 'complete',
                                                         'chapters': {'c0001': row}, **supervisor.UNCHECKED})

    def run_mocked(self, action):
        def launch(command, **kwargs):
            action(command, kwargs)
            return Mock(pid=12345, wait=Mock(return_value=0))
        with patch.object(supervisor, 'ROOT', self.root), patch.object(supervisor, 'ADAPTER', self.adapter), \
                patch.object(supervisor, 'find_adapter', return_value=None), \
                patch.object(supervisor, 'native_workers', return_value=[]), \
                patch.object(supervisor, 'synchronize') as sync, \
                patch.object(supervisor, 'identity', return_value={'pid': 12345}), \
                patch('psutil.Process'), patch.object(supervisor.subprocess, 'Popen', side_effect=launch) as popen, \
                patch.object(supervisor.time, 'sleep'), \
                patch('sync_higgs_preview_runtime._music_publications', return_value=[{}]):
            code = supervisor.run(self.args)
            return code, popen, sync

    def test_exit_zero_music_pending_resumes_cached_only_then_completes_unchecked(self):
        calls = []
        original = []
        def action(command, kwargs):
            calls.append(command)
            self.assertEqual('render', command[command.index('--stage') + 1])
            self.assertNotIn('--retake', command)
            self.assertEqual('1', kwargs['env']['PYTHONUTF8'])
            self.assertEqual('utf-8', kwargs['env']['PYTHONIOENCODING'])
            if len(calls) == 1:
                supervisor.write(self.output / 'native/session/physical-start.json', {'unit': 'u0001', 'take': 0})
            self.rendered(music=len(calls) == 2)
            if len(calls) == 1: original.append(supervisor.sha(self.output / 'units/u0001.json'))
        code, popen, sync = self.run_mocked(action)
        self.assertEqual(0, code)
        self.assertEqual(2, popen.call_count)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(original[0], supervisor.sha(self.output / 'units/u0001.json'))
        status = supervisor.read(self.output / 'one-shot-supervision/status.json')
        self.assertEqual('complete', status['status'])
        self.assertTrue(status['launches'][1]['cached_publish_only'])
        self.assertEqual(1, len(status['launches'][1]['physical_before']))
        self.assertTrue(status['music_registered'])
        for k, value in supervisor.UNCHECKED.items(): self.assertEqual(value, status[k])
        self.assertGreaterEqual(sync.call_count, 2)

    def test_music_exhaustion_is_bounded_and_store_publication_not_complete(self):
        code, popen, _ = self.run_mocked(lambda *args: self.rendered(music=False))
        self.assertEqual(1, code)
        self.assertEqual(3, popen.call_count)
        status = supervisor.read(self.output / 'one-shot-supervision/status.json')
        self.assertEqual('held', status['status'])
        self.assertEqual(['c0001'], status['progress']['music_pending'])
        progress = supervisor.read(self.output / 'progress.json')
        self.assertEqual('technical_failure', progress['status'])
        self.assertFalse(progress['machine_verified'])
        # Re-running the same durable owner cannot reset its spent budget.
        code, popen, _ = self.run_mocked(lambda *args: self.fail('Budget reset'))
        self.assertEqual(1, code)
        popen.assert_not_called()

    def test_native_failure_is_preserved_without_retry(self):
        code, popen, _ = self.run_mocked(lambda *args: self.rendered(failure=True))
        self.assertEqual(1, code)
        self.assertEqual(1, popen.call_count)
        status = supervisor.read(self.output / 'one-shot-supervision/status.json')
        self.assertEqual(['u0001'], status['progress']['technical_failures'])

    def test_waiting_for_demo_queue_does_not_spend_resume_budget(self):
        calls = []
        def gate(outputs):
            calls.append(1)
            return (len(calls) > 2, 'demo running', [])
        with patch.object(supervisor, 'demo_gate', side_effect=gate):
            code, popen, _ = self.run_mocked(lambda *args: self.rendered(music=True))
        self.assertEqual(0, code)
        self.assertEqual(1, popen.call_count)
        status = supervisor.read(self.output / 'one-shot-supervision/status.json')
        self.assertEqual(1, len(status['launches']))

    def test_audio_tamper_and_changed_frozen_plan_fail_closed(self):
        self.rendered(music=True)
        (self.output / 'assets/speech.wav').write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            supervisor.assess(self.output, self.ready, [self.unit], hash_audio=True)
        (self.output / 'plan.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            supervisor.load_units(self.output, self.ready)

    def test_partial_chapter_and_any_music_pending_prevent_completion(self):
        self.rendered(music=True)
        path = self.output / 'progress.json'
        progress = supervisor.read(path)
        progress['chapters']['c0001']['complete'] = False
        progress['chapters']['c0001']['music_pending'] = 'still not registered'
        supervisor.write(path, progress)
        result = supervisor.assess(self.output, self.ready, [self.unit])
        self.assertFalse(result['complete'])
        self.assertEqual(['c0001'], result['incomplete_chapters'])
        self.assertEqual(['c0001'], result['music_pending'])

    def test_expected_final_ending_is_required_and_cache_manifest_is_pinned(self):
        self.rendered(music=True)
        path = self.output / 'scene-music-v2/manifest.json'
        supervisor.write(path, {'layout': {'takes': [{'kind': 'ending'}]}})
        self.ready['music_files'] = {'manifest.json': supervisor.sha(path)}
        result = supervisor.assess(self.output, self.ready, [self.unit])
        self.assertFalse(result['complete'])
        self.assertEqual(['c0001'], result['music_pending'])
        progress = supervisor.read(self.output / 'progress.json')
        progress['chapters']['c0001']['music'].append({'kind': 'ending'})
        supervisor.write(self.output / 'progress.json', progress)
        self.assertTrue(supervisor.assess(self.output, self.ready, [self.unit])['complete'])
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            supervisor.assess(self.output, self.ready, [self.unit])

    def test_demo_dependency_waits_without_opening_active_worker(self):
        demo = self.root / 'demo'
        supervisor.write(demo / 'public/latest.json', {'render_complete': False, 'rendered_units': 1, 'total_units': 2})
        with patch.object(supervisor, 'read', wraps=supervisor.read) as reader:
            allowed, reason, _ = supervisor.demo_gate([demo])
        self.assertFalse(allowed)
        self.assertIn('still rendering', reason)
        self.assertEqual([demo / 'public/latest.json'], [c.args[0] for c in reader.call_args_list])
        supervisor.write(demo / 'public/latest.json', {'render_complete': True, 'rendered_units': 2, 'total_units': 2})
        supervisor.write(demo / 'worker.json', {'status': 'stopped'})
        self.assertTrue(supervisor.demo_gate([demo])[0])

    def test_live_pid_requires_matching_time_and_exact_command(self):
        process = Mock()
        process.is_running.return_value = True
        process.status.return_value = 'running'
        process.create_time.return_value = 12
        process.cmdline.return_value = ['python', 'adapter']
        with patch('psutil.Process', return_value=process):
            self.assertTrue(supervisor.live({'pid': 1, 'create_time': 12, 'command': ['python', 'adapter']}))
            self.assertFalse(supervisor.live({'pid': 1, 'create_time': 11, 'command': ['python', 'adapter']}))
            self.assertFalse(supervisor.live({'pid': 1, 'create_time': 12, 'command': ['other']}))

    def test_windows_launcher_core_chain_adopted_but_unrelated_duplicates_rejected(self):
        def process(pid, parents):
            value = Mock(pid=pid, info={'cmdline': ['python', 'adapter']})
            value.parents.return_value = parents
            value.cmdline.return_value = ['python', 'adapter']
            value.create_time.return_value = 12 + pid
            return value
        launcher = process(1, [])
        core = process(2, [launcher])
        with patch('psutil.process_iter', return_value=[launcher, core]):
            self.assertEqual(2, supervisor.find_adapter(['python', 'adapter'])['pid'])
        unrelated = process(3, [])
        with patch('psutil.process_iter', return_value=[launcher, core, unrelated]):
            with self.assertRaisesRegex(ValueError, 'unrelated'):
                supervisor.find_adapter(['python', 'adapter'])

    def test_demo_config_must_bind_public_generation_source_and_unit_count(self):
        demo = self.root / 'demo'
        config = {'source_generation': 'source-hash', 'frozen': {'assignment': {'units': [{}]}}}
        supervisor.write(demo / 'one-shot.json', config)
        latest = {'render_complete': True, 'total_units': 1, 'rendered_units': 1,
                  'generation': supervisor.digest(config), 'source_generation': 'source-hash'}
        supervisor.write(demo / 'public/latest.json', latest)
        supervisor.write(demo / 'worker.json', {'status': 'stopped'})
        self.assertTrue(supervisor.demo_gate([demo])[0])
        latest['generation'] = 'forged'
        supervisor.write(demo / 'public/latest.json', latest)
        with self.assertRaisesRegex(ValueError, 'frozen config'):
            supervisor.demo_gate([demo])

    def test_real_parser_help_no_jobs_and_invalid_budget_rejected(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(supervisor.SELF), '--help'], capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        self.assertIn('--demo-output', result.stdout)
        result = subprocess.run([sys.executable, '-X', 'utf8', str(supervisor.SELF), '--output', str(self.output),
                                 '--data-dir', str(self.root / 'store'), '--demo-output', str(self.root / 'demo'),
                                 '--max-restarts', '99'], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertFalse((self.output / 'one-shot-supervision').exists())


if __name__ == '__main__': unittest.main()
