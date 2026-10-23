import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / 'scripts' / 'check_release.py'
spec = importlib.util.spec_from_file_location('review_repairs_release_check', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

BINARIES = [('core', 'rimsky-runtime'), ('infer', 'rimsky-infer'), ('media', 'ffmpeg'), ('media', 'ffprobe')]


class ReleaseExecutableNames(unittest.TestCase):
    def test_branding_is_required_and_checksum_checked(self):
        def branding_issues():
            return [issue for issue in module.release_issues(self.root, require_acceptance=False, execute=False)
                    if 'Dogma branding' in issue]
        self.assertTrue(branding_issues())
        shutil.copytree(SOURCE.parents[1] / 'resources/branding', self.root / 'resources/branding')
        self.assertEqual([], branding_issues())
        manifest = self.root / 'resources/branding/dogma-opening-v1/manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b' ')
        self.assertTrue(branding_issues())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.runtime = self.root / 'resources' / 'runtime'

    def tearDown(self):
        self.directory.cleanup()

    def place(self, suffix, mode=0o755):
        for folder, name in BINARIES:
            path = self.runtime / folder / (name + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'placeholder')
            path.chmod(mode)

    def executable_issues(self, target):
        issues = module.release_issues(self.root, require_acceptance=False, target=target, execute=False)
        return [issue for issue in issues if 'executable' in issue]

    def test_windows_requires_exe_names(self):
        self.place('')
        issues = self.executable_issues('windows')
        self.assertEqual(len(issues), 4)
        self.assertIn('Missing packaged executable: runtime/core/rimsky-runtime.exe', issues)
        self.place('.exe')
        self.assertEqual(self.executable_issues('windows'), [])

    def test_posix_targets_reject_exe_names(self):
        self.place('.exe')
        for target in ('macos', 'linux'):
            issues = self.executable_issues(target)
            self.assertEqual(len(issues), 4)
            self.assertIn('Missing packaged executable: runtime/media/ffprobe', issues)

    def test_posix_targets_accept_executable_plain_names(self):
        self.place('')
        for target in ('macos', 'linux'):
            self.assertEqual(self.executable_issues(target), [])

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits are not available on Windows hosts')
    def test_posix_targets_require_executable_bit(self):
        self.place('', mode=0o644)
        for target in ('macos', 'linux'):
            issues = self.executable_issues(target)
            self.assertEqual(len(issues), 4)
            self.assertIn('Packaged executable is not executable: runtime/infer/rimsky-infer', issues)

    def test_default_target_follows_host(self):
        expected = module.host_target()
        self.assertIn(expected, module.TARGETS)
        self.assertEqual(module.release_issues(self.root, require_acceptance=False),
                         module.release_issues(self.root, require_acceptance=False, target=expected))

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            module.release_issues(self.root, target='solaris')


if __name__ == '__main__':
    unittest.main()
