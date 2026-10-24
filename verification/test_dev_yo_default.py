"""CPU regression for the historical v4 dense-letter crash and fresh-only default."""
from pathlib import Path
import subprocess,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import auto_pronunciation as ap
import seed_iteration_generation as seed


class YoDevelopmentDefault(unittest.TestCase):
    def test_dense_silero_yo_cannot_crash_or_override_frozen_e_letters(self):
        sparse='С черных скал, после возней.'
        dense='С чёрных ска\u0301л, по\u0301сле вознёй.'
        with patch.object(ap,'_zipf',return_value=0):
            result,marks=ap._long_tail(sparse,dense,yo_layer=True)
        self.assertIn('черных',result);self.assertIn('возней',result)
        self.assertNotIn('чёрных',result);self.assertNotIn('вознёй',result)
        self.assertIn('ска\u0301л',marks)
        with self.assertRaises(ValueError):ap._long_tail(sparse,dense) # historical crash condition
        with self.assertRaises(ValueError):ap._long_tail(sparse,dense.replace('чёрных','других'),yo_layer=True)
    def test_v4_letter_authority_does_not_allow_other_letter_changes(self):
        self.assertEqual(ap._keep_layer_letters('черных возней','чёрных вознёй'),'черных возней')
        with self.assertRaises(ValueError):ap._keep_layer_letters('черных','чернов')
    def test_fresh_generation_defaults_v4_and_missing_answers_fail_before_writes(self):
        self.assertEqual(seed.DEFAULT_PRONUNCIATION_POLICY,ap.POLICY_RARE4)
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'fresh'
            with patch.object(seed,'FOLDER',Path(directory)),patch.object(sys,'argv',['seed','--name','fresh','--page','fixture.html']):
                with self.assertRaises(SystemExit) as error:seed.main()
            self.assertEqual(error.exception.code,2);self.assertFalse(folder.exists())
    def test_existing_manifest_is_refused_before_any_default_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'old';folder.mkdir();manifest=folder/'manifest.json';manifest.write_bytes(b'{"pronunciation":{"policy":"stress-rare-v3"}}')
            original=manifest.read_bytes()
            with patch.object(seed,'FOLDER',Path(directory)),patch.object(sys,'argv',['seed','--name','old','--page','fixture.html']):
                with self.assertRaisesRegex(SystemExit,'already exists'):seed.main()
            self.assertEqual(manifest.read_bytes(),original)


if __name__=='__main__':unittest.main()
