import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
spec=importlib.util.spec_from_file_location('preparation_package_runtime',ROOT/'scripts/package_runtime.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
from rimsky.russian_preparation import RUACCENT_REQUIRED_FILES
from rimsky import homograph_stress


class PreparationPackagingTests(unittest.TestCase):
    def test_every_runtime_required_asset_is_required_at_package_time(self):
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary)
            for relative in RUACCENT_REQUIRED_FILES:
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture only')
            self.assertEqual(package.require_preparation_assets(root),root)
            for relative in RUACCENT_REQUIRED_FILES:
                with self.subTest(missing=relative):
                    path=root/relative;path.unlink()
                    with self.assertRaisesRegex(RuntimeError,'missing:')as held:package.require_preparation_assets(root)
                    self.assertIn(relative,str(held.exception));path.write_bytes(b'fixture only')

    def test_partial_silero_install_missing_accentor_blocks_before_pyinstaller(self):
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary);models=root/'training-work/models/ruaccent-1.5.8.3'
            for relative in RUACCENT_REQUIRED_FILES:
                path=models/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture only')
            silero=root/'site-packages/silero_stress/__init__.py'
            silero.parent.mkdir(parents=True);silero.write_text('# synthetic partial install',encoding='utf8')
            # Package discovery succeeds, but the actual required model data is
            # absent. Exercise runtime validation rather than mocking its error.
            with patch.object(package,'ROOT',root),patch.object(package,'app_version',return_value='fixture'),\
                 patch.object(package,'source_digest',return_value='fixture'),\
                 patch.object(package.importlib.util,'find_spec',side_effect=lambda name:SimpleNamespace(origin=str(silero))),\
                 patch.object(homograph_stress,'MODEL_DIR',models),patch.object(package.subprocess,'run')as launch:
                with self.assertRaisesRegex(Exception,'installed local Silero'):
                    package.build('infer',root/'output')
                launch.assert_not_called()


if __name__=='__main__':unittest.main()
