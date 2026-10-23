"""No worker launches: synthetic adoption/liveness and delivery proof checks."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('supervisor', ROOT / 'scripts/supervise_public_audiobook_demos.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisionGates(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def fake_process(self):
        process = Mock(pid=42)
        process.name.return_value = 'python.exe'
        process.create_time.return_value = 123.0
        process.cmdline.return_value = ['python.exe', str(supervisor.RENDERER), '--folder', str(self.folder), '--stage', 'all']
        process.cwd.return_value = str(ROOT)
        process.is_running.return_value = True
        process.status.return_value = 'running'
        return process

    def test_adoption_rejects_native_child_wrong_folder_stage_and_reused_pid(self):
        process = self.fake_process()
        self.assertEqual(supervisor.validate_adoption(process, self.folder)['pid'], 42)
        process.name.return_value = 'audiocpp_cli.exe'
        with self.assertRaisesRegex(ValueError, 'never a native'):
            supervisor.validate_adoption(process, self.folder)
        process.name.return_value = 'python.exe'
        with self.assertRaisesRegex(ValueError, 'creation time'):
            supervisor.validate_adoption(process, self.folder, 124)
        with self.assertRaisesRegex(ValueError, 'exact requested folder'):
            supervisor.validate_adoption(process, self.folder / 'other')
        process.cmdline.return_value[-1] = 'render'
        with self.assertRaisesRegex(ValueError, 'exact requested folder'):
            supervisor.validate_adoption(process, self.folder)

    def test_liveness_never_treats_reused_or_stopped_pid_as_running(self):
        process = self.fake_process()
        identity = supervisor.validate_adoption(process, self.folder)
        self.assertTrue(supervisor.live(process, identity))
        process.create_time.return_value = 999
        self.assertFalse(supervisor.live(process, identity))
        process.create_time.return_value = 123
        process.is_running.return_value = False
        self.assertFalse(supervisor.live(process, identity))

    def complete_fixture(self):
        assignment = {'units': [{'id': 'unit-one'}, {'id': 'unit-two'}]}
        manifest = {'opening': {'sha256': 'fixture'}, 'music': {'bed': {'fixture': True}, 'ending': {'fixture': True}}}
        frozen = {'assignment': assignment, 'manifest': manifest}
        generation = supervisor.digest(frozen)
        state = {'generation': generation, 'counts': {'verified': 2, 'held': 0},
                 'units': [{'id': u['id'], 'status': 'verified', 'selected_audio': 'assets/fixture.wav'} for u in assignment['units']]}
        proof = {'generation': generation, 'complete': True, 'music_ready': True, 'verified_units': 2,
                 'total_units': 2, 'open_recognition_flags': 0, 'opening_seconds': 8,
                 'captions': [{'id': u['id']} for u in assignment['units']]}
        public = self.folder / 'public'
        public.mkdir()
        for extension in ('wav', 'mp3'):
            path = public / ('fixture.'+extension)
            path.write_bytes(b'synthetic export fixture')
            proof[extension] = path.name
            proof[extension+'_sha256'] = supervisor.file_hash(path)
        for name, value in [('assignment', assignment), ('manifest', manifest), ('frozen', frozen), ('state', state), ('complete', proof)]:
            supervisor.write(self.folder / (name+'.json'), value)
        return state, proof

    def test_complete_requires_stopped_worker_all_units_music_and_unchanged_exports(self):
        state, proof = self.complete_fixture()
        self.assertFalse(supervisor.completion(self.folder, False)[0])
        self.assertTrue(supervisor.completion(self.folder, True)[0])
        state['units'][0]['status'] = 'held'
        supervisor.write(self.folder / 'state.json', state)
        self.assertFalse(supervisor.completion(self.folder, True)[0])
        self.assertTrue(supervisor.held(self.folder))
        state['units'][0]['status'] = 'verified'
        supervisor.write(self.folder / 'state.json', state)
        proof['music_ready'] = False
        supervisor.write(self.folder / 'complete.json', proof)
        self.assertFalse(supervisor.completion(self.folder, True)[0])
        proof['music_ready'] = True
        supervisor.write(self.folder / 'complete.json', proof)
        (self.folder / 'public/fixture.mp3').write_bytes(b'changed')
        self.assertFalse(supervisor.completion(self.folder, True)[0])

    def test_log_wait_updates_and_native_wavs_are_visible_progress(self):
        native = self.folder / 'native/session'
        native.mkdir(parents=True)
        (native / 'unit.wav').write_bytes(b'partial native audio')
        log = self.folder / 'adopted.log'
        log.write_text('WAITING for independent audio job\n')
        before = supervisor.progress(self.folder, [log])
        log.write_text(log.read_text()+'WAITING again\n')
        after = supervisor.progress(self.folder, [log])
        self.assertEqual(before['native_wav_count'], 1)
        self.assertNotEqual(supervisor.digest(before), supervisor.digest(after))


if __name__ == '__main__':
    unittest.main()
