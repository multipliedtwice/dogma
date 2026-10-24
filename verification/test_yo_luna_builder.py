"""New book YO policy: all ambiguous occurrences, isolated Luna and immutable evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import build_yo_restoration as m


class LunaYo(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        self.exe=self.out/'codex.exe';self.exe.write_bytes(b'fixture-cli')
        self.metadata=dict(zip(('provider','model','effort'),m.PIN))|{
            'policy':m.POLICY,'requested_model':'gpt-6-luna','model_provenance':'explicit-cli-model-selection',
            'cli_version':'fixture 1','executable':str(self.exe),'executable_sha256':m.file_digest(self.exe),
            'structured_call_sha256':m.file_digest(Path(m.codex_analysis.__file__))}
        self.req={'task':'yo_restoration_adjudication','items':[{'id':'y0000','sentence':'Туман осел.',
            'word':'осел','candidate_e':'осел','candidate_yo':'осёл'}]}
        self.answer={'decisions':[{'id':'y0000','choice':'e','word':'осел','basis':'Verb, not donkey.'}]}
    def test_consensus_is_promoted_without_changing_legacy_analysis(self):
        args=('Туман осел.',[],['Туман','осел'],['Туман','осе\u0301л'],{('осел',0):'осел'})
        old=m.yo.analyze(*args);new=m.yo.analyze(*args,force_all=True)
        self.assertEqual(old['decisions'][0]['basis'],'auto-agree');self.assertFalse(old['rows'])
        self.assertFalse(new['decisions']);self.assertEqual(new['rows'][0]['word'],'осел')
        self.assertEqual(new['rows'][0]['witnesses'],old['decisions'][0]['witnesses'])
        with patch('rimsky.auto_pronunciation.yo_witnesses',return_value=args[1:4]):
            decisions,rows,stats=m.analyse_texts({'Туман осел.':'c1'}, {}, [],ruaccent=lambda t:args[4])
        self.assertFalse(decisions);self.assertEqual(rows[0]['word'],'осел');self.assertEqual(stats['ambiguous_auto'],0)
    def test_call_uses_exact_luna_low_and_existing_isolation_without_fallback(self):
        with patch.object(m.codex_analysis,'codex_executable',return_value=str(self.exe)),\
             patch.object(m,'run_cli',return_value=Mock(returncode=0,stdout='fixture 1')),\
             patch.object(m.codex_analysis,'structured_call',return_value=self.answer) as call:
            answer,metadata=m.call('system',self.req,m.yo.schema(['y0000']),self.out,'test')
        self.assertEqual(answer,self.answer);self.assertEqual(metadata,self.metadata)
        self.assertEqual(call.call_args.kwargs,{'model':'gpt-6-luna','reasoning_effort':'low'})
        self.assertNotIn('model_usage',metadata)  # No server model metadata is exposed by this CLI API.
    def test_multi_surface_ambiguity_is_not_silently_skipped(self):
        item={'start':0,'end':4,'parts':[0],'key':'небо','occ':0,'word':'небо','cls':'multi_yo','yo':None}
        with patch.object(m.yo,'eligible',return_value=[item]):
            with self.assertRaisesRegex(ValueError,'Multiple YO candidates'):
                m.yo.analyze('небо',[],['небо'],['небо'],{},force_all=True)
    def test_provider_rejects_opus_wrong_effort_missing_or_changed_cli(self):
        m.validate_provider(self.metadata)
        for changed in ({'model':'claude-opus-5-5'},{'effort':'high'},{'cli_version':''},{'requested_model':'other'},
                        {'policy':'old-auto-agreement'},{'structured_call_sha256':'0'*64}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):m.validate_provider({**self.metadata,**changed})
        self.exe.write_bytes(b'changed')
        with self.assertRaises(ValueError):m.validate_provider(self.metadata)
    def test_cache_schema_provider_and_request_binding_rechecked(self):
        with patch.object(m,'call',return_value=(self.answer,self.metadata)) as call:
            first=m.adjudicate(self.out,self.req);self.assertEqual(first,m.adjudicate(self.out,self.req));call.assert_called_once()
        done=next((self.out/'batches').glob('batch-*.answer.json'));original=json.loads(done.read_text('utf8'))
        for change in ('provider','schema','binding'):
            value=copy.deepcopy(original)
            if change=='provider':value['metadata']['model']='claude-opus-5-5'
            if change=='schema':value['answer']['decisions'][0]['extra']='ignored?'
            if change=='binding':value['request_sha256']='0'*64
            done.write_text(json.dumps(value),encoding='utf8')
            with self.subTest(change=change),patch.object(m,'call') as call,self.assertRaises(ValueError):m.adjudicate(self.out,self.req)
            call.assert_not_called()
    def test_policy_in_keys_prevents_old_auto_or_opus_cache_reuse(self):
        with patch.object(m,'call',return_value=(self.answer,self.metadata)) as call:m.adjudicate(self.out,self.req)
        request=next((self.out/'batches').glob('*.request.json'));bound=m.read(request)
        self.assertEqual([bound[k] for k in ('policy','provider','model','effort')],[m.POLICY,*m.PIN])
        self.assertNotEqual(m.digest({k:v for k,v in bound.items() if k not in ('policy','provider','model','effort')}),
                            m.read(next((self.out/'batches').glob('batch-*.answer.json')))['request_sha256'])
    def test_transport_failures_have_bounded_immutable_child_evidence(self):
        with patch.object(m,'call',side_effect=RuntimeError('transport')) as call:
            with self.assertRaises(RuntimeError):m.adjudicate(self.out,self.req)
            self.assertEqual(call.call_count,3)
        self.assertEqual(len(list((self.out/'batches').glob('*.failure.json'))),3)
        before={p.name:p.read_bytes() for p in (self.out/'batches').iterdir()}
        with patch.object(m,'call') as call,self.assertRaisesRegex(ValueError,'exhausted'):m.adjudicate(self.out,self.req)
        call.assert_not_called();self.assertEqual(before,{p.name:p.read_bytes() for p in (self.out/'batches').iterdir()})
    def test_unfinished_attempt_is_not_silently_reissued(self):
        with patch.object(m,'call',return_value=(self.answer,self.metadata)):m.adjudicate(self.out,self.req)
        for p in (self.out/'batches').glob('*.answer.json'):p.unlink()
        with patch.object(m,'call') as call,self.assertRaisesRegex(ValueError,'Unfinished immutable'):m.adjudicate(self.out,self.req)
        call.assert_not_called()


if __name__=='__main__':unittest.main()
