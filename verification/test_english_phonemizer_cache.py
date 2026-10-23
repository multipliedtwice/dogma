"""Integrity-preserving provenance cache: file mutations invalidate full hashes."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'runtime')]
import public_demo_english_agreement as agreement


class EnglishProvenanceCache(unittest.TestCase):
    def test_unchanged_package_reuses_hashes_but_changed_added_missing_files_invalidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder/'__init__.py').write_bytes(b'synthetic package fixture')
            dll_folder = folder/'espeak-ng'
            dll_folder.mkdir()
            pins = {}
            for name, raw in [('espeak-ng-x64.dll',b'synthetic64'),('espeak-ng-x86.dll',b'synthetic86')]:
                (dll_folder/name).write_bytes(raw)
                pins[name] = hashlib.sha256(raw).hexdigest()
            data = folder/'dictionary'
            data.write_bytes(b'vowels-one')
            fake = SimpleNamespace(__file__=str(folder/'__init__.py'))
            agreement._PROVENANCE_CACHE.clear()
            try:
                with patch.dict(sys.modules, {'espeak_phonemizer':fake}), \
                     patch.object(agreement.importlib.metadata,'version',return_value=agreement.VERSION), \
                     patch.object(agreement,'DLLS',pins), \
                     patch.object(agreement,'file_digest',wraps=agreement.file_digest) as hashes:
                    original = agreement.engine_provenance()
                    initial_reads = hashes.call_count
                    self.assertEqual(initial_reads,4)
                    self.assertEqual(agreement.engine_provenance(),original)
                    self.assertEqual(hashes.call_count,initial_reads)
                    returned = agreement.engine_provenance()
                    returned['dlls']['espeak-ng-x64.dll'] = 'mutated caller copy'
                    self.assertEqual(agreement.engine_provenance(),original)
                    before = data.stat()
                    data.write_bytes(b'vowels-two')
                    os.utime(data,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000_000))
                    changed = agreement.engine_provenance()
                    self.assertNotEqual(changed['package_files_sha256'],original['package_files_sha256'])
                    self.assertEqual(hashes.call_count,initial_reads+4)
                    added = folder/'extra-dictionary'
                    added.write_bytes(b'extra source')
                    with_added = agreement.engine_provenance()
                    self.assertEqual(with_added['file_count'],5)
                    self.assertNotEqual(with_added['package_files_sha256'],changed['package_files_sha256'])
                    data.unlink()
                    with_missing = agreement.engine_provenance()
                    self.assertEqual(with_missing['file_count'],4)
                    self.assertNotEqual(with_missing['package_files_sha256'],with_added['package_files_sha256'])
                    (dll_folder/'espeak-ng-x64.dll').write_bytes(b'changed DLL')
                    with self.assertRaisesRegex(ValueError,'DLL pin mismatch'):
                        agreement.engine_provenance()
                    (dll_folder/'espeak-ng-x64.dll').unlink()
                    with self.assertRaisesRegex(ValueError,'DLL pin mismatch'):
                        agreement.engine_provenance()
            finally:
                agreement._PROVENANCE_CACHE.clear()


if __name__ == '__main__':
    unittest.main()
