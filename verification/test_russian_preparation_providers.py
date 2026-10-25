import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky import russian_preparation_providers as providers
from rimsky.util import file_digest,write_json


class PreparationProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.binary=self.root/'fixture-cli';self.binary.write_bytes(b'never-executed')

    def test_offline_preflight_never_discovers_or_calls_online_cli(self):
        with patch.object(providers,'executable',side_effect=AssertionError('offline must never probe a provider')):
            result=providers.preflight(False)
        self.assertTrue(result['ready']);self.assertFalse(result['online_allowed'])
        self.assertIn('never sends',result['disclosure'])

    def test_both_exact_providers_required_for_online_preparation(self):
        with patch.object(providers,'executable',side_effect=[self.binary,OSError('missing Claude/Codex')])as lookup:
            result=providers.preflight(True)
        self.assertFalse(result['ready']);self.assertEqual(lookup.call_count,2)
        self.assertEqual([p['model']for p in result['required_providers']],['claude-opus-5-5','gpt-6-luna'])

    def metadata(self,actual='claude-opus-5-5'):
        folder=self.root/'cli';folder.mkdir()
        write_json(folder/'envelope.json',{'modelUsage':{actual:{'inputTokens':1}}})
        write_json(folder/'manifest.json',{'max_attempts':1,'files':{'envelope.json':file_digest(folder/'envelope.json')}})
        return dict(zip(('provider','model','effort'),providers.STRESS_PIN))|{
            'requested_model':'claude-opus-5-5','model_provenance':'explicit-cli-model-selection','cli_version':'fixture',
            'executable':str(self.binary.resolve()),'executable_sha256':file_digest(self.binary),
            'cli_evidence':str(folder),'cli_evidence_sha256':file_digest(folder/'manifest.json'),
            'model_usage':{actual:{'inputTokens':1}}}

    def test_actual_model_usage_must_match_pinned_opus(self):
        metadata=self.metadata('claude-other')
        with self.assertRaisesRegex(ValueError,'Actual stress model'):providers.validate(metadata,providers.STRESS_PIN)

    def test_executable_replacement_and_raw_usage_mismatch_hold(self):
        metadata=self.metadata();providers.validate(metadata,providers.STRESS_PIN)
        self.binary.write_bytes(b'changed-executable')
        with self.assertRaisesRegex(ValueError,'provenance'):providers.validate(metadata,providers.STRESS_PIN)


if __name__=='__main__':unittest.main()
