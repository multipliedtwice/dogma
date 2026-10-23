"""CPU-only regression checks for inherited encoding and truthful resume status."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('finisher_utf8',ROOT/'scripts/finish_public_audiobook_demos.py')
finisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finisher)


class FinisherInheritance(unittest.TestCase):
    def test_plain_python_grandchild_prints_cyrillic_under_hostile_parent_encoding(self):
        sentence = 'Привет, мир.'
        grandchild = 'print('+ascii(sentence)+')'
        child = 'import subprocess,sys; subprocess.run([sys.executable,"-c",'+repr(grandchild)+'],check=True)'
        with patch.dict(os.environ,{'PYTHONUTF8':'0','PYTHONIOENCODING':'cp1252'}):
            finisher.ensure_inherited_utf8()
            result = subprocess.run([sys.executable,'-X','utf8','-c',child],capture_output=True,timeout=30,
                                    **finisher.supervision.HIDDEN)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.decode('utf-8').strip(),sentence)
            self.assertEqual(os.environ['PYTHONUTF8'],'1')
            self.assertEqual(os.environ['PYTHONIOENCODING'],'utf-8')

    def test_main_sets_encoding_before_any_internal_stage(self):
        observed = []
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ,{'PYTHONUTF8':'0','PYTHONIOENCODING':'cp1252'}), \
             patch.object(sys,'argv',['finisher','--folder',temporary,'--internal-stage','resolve']), \
             patch.object(finisher,'resolve_internal',side_effect=lambda *args:observed.append(
                 (os.environ['PYTHONUTF8'],os.environ['PYTHONIOENCODING']))):
            self.assertEqual(finisher.main(),0)
        self.assertEqual(observed,[('1','utf-8')])

    def test_resume_marks_supervisor_running_and_new_stage_clears_only_stale_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            args = SimpleNamespace(output=folder/'supervision',folder=[folder/'book'],
                                   python=Path(sys.executable),asr_python=Path(sys.executable))
            engine = finisher.Engine(args)
            engine.state['status'] = 'held'
            engine.book(folder/'book',status='held',reason='Previous process stopped',
                        counts={'verified_units':2,'open_recognition_flags':1},holds=[{'id':'u3'}])
            history = {'attempts':[{'status':'failed','exit_code':1,'log':'old.log'}]}
            engine.state['stages']['old-stage'] = history
            log = args.output/'old.log'
            log.write_bytes(b'protected old binary log')
            engine.save()
            resumed = finisher.Engine(args)
            self.assertEqual(resumed.state['status'],'running')
            self.assertEqual(resumed.state['books'][str(folder/'book')]['reason'],'Previous process stopped')
            resumed.book(folder/'book',status='running',stage='verify')
            book = resumed.state['books'][str(folder/'book')]
            self.assertNotIn('reason',book)
            self.assertEqual(book['counts'],{'verified_units':2,'open_recognition_flags':1})
            self.assertEqual(book['holds'],[{'id':'u3'}])
            self.assertEqual(resumed.state['stages']['old-stage'],history)
            self.assertEqual(log.read_bytes(),b'protected old binary log')

    def test_implementation_mismatch_keeps_old_journal_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            args = SimpleNamespace(output=folder/'supervision',folder=[folder/'book'],
                                   python=Path(sys.executable),asr_python=Path(sys.executable))
            engine = finisher.Engine(args)
            engine.state['config']['implementation_sha256'] = '0'*64
            engine.save()
            original = engine.path.read_bytes()
            with self.assertRaisesRegex(ValueError,'new output namespace'):
                finisher.Engine(args)
            self.assertEqual(engine.path.read_bytes(),original)


if __name__ == '__main__':
    unittest.main()
