"""CPU-only tests of turbo witness provenance and shared GPU scheduling guards."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('turbo_demo', ROOT / 'scripts/witness_public_demo_turbo.py')
turbo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(turbo)


class TurboWitness(unittest.TestCase):
    def test_verifier_is_source_blind_full_audio_and_pins_snapshot(self):
        config = turbo.verifier_config('en', 'test-torch', 'test-transformers')
        self.assertTrue(turbo.validate_verifier_config(config))
        self.assertFalse(config['expected_text_given_to_recognizer'])
        self.assertEqual(config['max_audio_seconds'], 30)
        self.assertEqual(config['truncation'], 'forbidden')
        self.assertEqual(config['revision'], turbo.REVISION)
        self.assertEqual(config['files'], turbo.FILES)
        self.assertNotIn('expected_text', config)

    def test_configuration_rejects_prompt_or_module_forgery(self):
        config = turbo.verifier_config('ru', 'test-torch', 'test-transformers')
        for changed in [{**config, 'prompt': 'source text'},
                        {**config, 'expected_text_given_to_recognizer': True},
                        {**config, 'implementation_sha256': '0'*64}]:
            with self.assertRaises(ValueError):
                turbo.validate_verifier_config(changed)

    def test_own_venv_chain_is_ignored_but_independent_turbo_and_recovery_wait(self):
        def proc(pid, parent, command):
            return dict(ProcessId=pid, ParentProcessId=parent, Name='python.exe', CommandLine=command)
        processes = [proc(11, 10, 'witness_public_demo_turbo.py --stage witness'),
                     proc(10, 1, 'witness_public_demo_turbo.py --stage witness'),
                     proc(20, 1, 'witness_public_demo_turbo.py --stage witness'),
                     proc(30, 1, 'recover_public_audiobook_demo.py --stage render'),
                     proc(40, 1, 'witness_public_demo_turbo.py --stage plan')]
        self.assertEqual([p['ProcessId'] for p in turbo.competing_extra_jobs(processes, 11)], [20,30])

    def test_full_overlong_audio_is_held_without_truncation_or_witness(self):
        import numpy as np
        import soundfile as sf
        import resolve_public_demo_recognition as resolver
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            audio = folder / 'synthetic.wav'
            sf.write(audio, np.zeros(31*24000, dtype=np.float32), 24000, subtype='PCM_16')
            checksum = turbo.file_digest(audio)
            context = SimpleNamespace(folder=folder, assignment={'language':'English'}, generation='synthetic-generation')
            u = {'id':'u1'}
            receipt = {'take':0, 'audio':'synthetic.wav', 'audio_sha256':checksum}
            with patch.object(resolver, 'report', return_value={'units':[]}), patch.object(turbo, 'choose_pending', return_value=[(u,receipt)]):
                pending, value = turbo.plan(context)
            self.assertEqual(pending, [])
            self.assertEqual(value['holds'][0]['frames'], 31*24000)
            self.assertFalse(value['holds'][0]['truncated'])
            self.assertEqual(turbo.file_digest(audio), checksum)
            self.assertFalse((folder/'witnesses').exists())

    def test_pending_uses_latest_quality_take_and_skips_approved_equivalence(self):
        units = [{'id':'u1'}, {'id':'u2'}]
        context = SimpleNamespace(folder=Path('.'), assignment={'units':units},
                                  assess=lambda u,r:{'decision':{'passed':False}})
        report = {'units':[{'id':'u1','selected':None},
                           {'id':'u2','selected':{'status':'source_bound_spoken_equivalence'}}]}
        receipts = [{'take':0,'quality_passed':True}, {'take':1,'quality_passed':True},
                    {'take':2,'quality_passed':False}]
        with patch.object(turbo.renderer, 'takes', return_value=receipts):
            pending = turbo.choose_pending(context, report)
            self.assertEqual([(u['id'],r['take']) for u,r in pending], [('u1',1)])
            all_pending = turbo.choose_pending(context, report, all_takes=True)
            self.assertEqual([(u['id'],r['take']) for u,r in all_pending], [('u1',0),('u1',1)])


if __name__ == '__main__':
    unittest.main()
