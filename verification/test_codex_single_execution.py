"""Audited one-execution CLI boundary; no real subprocess/provider."""
import json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rimsky import codex_analysis as m
from rimsky.util import AnalysisInvalidError,ProviderTransportError


class SingleExecution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def fake(self,text):
        def run(command,**kwargs):
            Path(command[command.index('--output-last-message')+1]).write_text(text,encoding='utf8')
            return SimpleNamespace(returncode=0,stdout='cli stdout',stderr='cli stderr')
        return run
    def test_malformed_raw_is_preserved_and_single_execution_never_retries(self):
        out=self.root/'audit'
        with patch.object(m,'codex_executable',return_value='fixture-codex'),patch.object(m,'run_cli',side_effect=self.fake('{bad')) as call:
            with self.assertRaises(AnalysisInvalidError):m.structured_call('system',{'items':[]},{},model='gpt-6-luna',max_attempts=1,evidence_dir=out)
            call.assert_called_once()
        self.assertEqual((out/'attempt-0/raw-answer.json').read_text('utf8'),'{bad')
        self.assertEqual((out/'attempt-0/stdout.txt').read_text('utf8'),'cli stdout')
        proof=json.loads((out/'attempt-0/request.json').read_text('utf8'))
        self.assertEqual(proof['max_attempts'],1);self.assertNotIn('env',proof)
        manifest=json.loads((out/'manifest.json').read_text('utf8'))
        self.assertIn('attempt-0/raw-answer.json',manifest['files'])
    def test_legacy_default_keeps_two_attempts_and_valid_once_returns_same_object(self):
        count=0
        def run(command,**kwargs):
            nonlocal count
            count+=1;return self.fake('{bad' if count==1 else '{"ok":true}')(command,**kwargs)
        with patch.object(m,'codex_executable',return_value='fixture-codex'),patch.object(m,'run_cli',side_effect=run):
            self.assertEqual(m.structured_call('system',{},{}),{'ok':True});self.assertEqual(count,2)
        with patch.object(m,'codex_executable',return_value='fixture-codex'),patch.object(m,'run_cli',side_effect=self.fake('{"ok":true}')) as call:
            self.assertEqual(m.structured_call('system',{},{},max_attempts=1,evidence_dir=self.root/'valid'),{'ok':True});call.assert_called_once()
    def test_transport_once_has_durable_failure_manifest(self):
        out=self.root/'failed'
        with patch.object(m,'codex_executable',return_value='fixture-codex'),patch.object(m,'run_cli',side_effect=subprocess.TimeoutExpired('fixture',1)) as call:
            with self.assertRaises(ProviderTransportError):m.structured_call('system',{},{},max_attempts=1,evidence_dir=out)
            call.assert_called_once()
        manifest=json.loads((out/'manifest.json').read_text('utf8'))
        self.assertIn('attempt-0/transport-error.json',manifest['files'])


if __name__=='__main__':unittest.main()
