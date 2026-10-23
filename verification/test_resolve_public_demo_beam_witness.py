"""Synthetic CPU-only beam adapter contracts; no model load or actual inference."""
import copy
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_beam_witness as adapter


class BeamWitnessAdapter(unittest.TestCase):
    def fixture(self,beam_heard='He enfolded his Della.'):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name)
        unit={'id':'u1','source_id':'p1','start':0,'end':21,'text':'He enfolded his Della.',
              'spoken':'He enfolded his Della.'}
        context=adapter.Context.__new__(adapter.Context)
        context.folder,context.generation=folder,'immutable-source'
        context.assignment={'language':'English','title':'Fixture','units':[unit]}
        context.blocks={'p1':unit['text']};context.dependencies={};context.extension_dependencies={'v2':'pinned'}
        context.full_witness_dependencies={'v3':'pinned'};context.beam_witness_dependencies={'v4':'pinned'}
        context.bindings={};context.english_policy=None;context.recheck=Mock()
        audio=folder/'assets/raw.wav';audio.parent.mkdir();audio.write_bytes(b'unchanged native speech fixture')
        audio_hash=adapter.base.file_digest(audio)
        spec={'source':unit,'generation':context.generation,'request':{'text':unit['spoken']}}
        receipt={'unit':unit['id'],'take':0,'quality_passed':True,'audio':'assets/raw.wav','audio_sha256':audio_hash,
                 'spec':spec,'spec_sha256':adapter.base.sha(spec)}
        adapter.base.immutable(folder/'takes/u1/take-00.json',receipt)
        helpers=[]
        for policy,directory,heard,beams in [('greedy-fixture','full-witness','He unfolded his Della.',1),
                                             ('beam-fixture','beam-witness',beam_heard,3)]:
            config={'model':'openai/whisper-large-v3','policy':policy,'num_beams':beams,'do_sample':False,
                    'expected_text_given_to_recognizer':False,'local_files_only':True}
            raw=adapter.base.transcript_check(unit['spoken'],heard,audio_hash,adapter.base.sha(config))
            path=folder/directory/'recognition.json';adapter.base.immutable(path,{**raw,'verifier':config})
            evidence={'path':path.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(path),
                      'verifier':config,'raw':raw,'family':'whisper','role':'witness'}
            helpers.append(SimpleNamespace(MODEL=config['model'],POLICY=policy,REVISION='pinned-model-revision',
                           FILES={'model':'f'*64},__file__=__file__,dependencies=Mock(return_value={}),
                           evidence=Mock(return_value=[evidence]),validate_verifier_config=Mock()))
        return context,unit,receipt,*helpers

    def patches(self,greedy,beam):
        stack=ExitStack()
        stack.enter_context(patch.object(adapter.previous.previous.Context,'evidence',return_value=[]))
        stack.enter_context(patch.object(adapter.previous,'full_module',return_value=greedy))
        stack.enter_context(patch.object(adapter,'beam_module',return_value=beam))
        stack.enter_context(patch.object(adapter.base,'english_equivalence',return_value={
            'passed':False,'canonical_words':['unfolded'],'equivalences':[],'tolerated_deviations':[]}))
        return stack

    def test_any_raw_exact_beam_uses_existing_rule_and_keeps_greedy_disagreement(self):
        context,unit,receipt,greedy,beam=self.fixture()
        original=copy.deepcopy(receipt)
        with self.patches(greedy,beam):proof=context.assess(unit,receipt)
        self.assertTrue(proof['decision']['passed'])
        self.assertEqual(proof['decision']['resolution'],'independent_witness_exact')
        self.assertEqual(len(proof['decision']['evidence']),2)
        self.assertEqual(proof['decision']['evidence'][0]['raw']['heard'],'He unfolded his Della.')
        self.assertEqual(proof['beam_witness_provenance']['evidence'][0]['status'],'asr_exact_match')
        self.assertEqual(proof['full_witness_provenance']['check_count'],1)
        self.assertEqual(proof['full_witness_provenance']['evidence'][0]['status'],'spoken_text_mismatch')
        self.assertEqual(proof['beam_witness_provenance']['check_count'],1)
        self.assertEqual(proof['beam_witness_provenance']['evidence'][0]['decoding']['num_beams'],3)
        self.assertEqual(receipt,original)

    def test_nonexact_unfolded_beam_cannot_resolve_source_verb(self):
        context,unit,receipt,greedy,beam=self.fixture('He unfolded his Della.')
        with self.patches(greedy,beam):proof=context.assess(unit,receipt)
        self.assertFalse(proof['decision']['passed'])
        self.assertTrue(all(item['raw']['status']=='spoken_text_mismatch' for item in proof['decision']['evidence']))

    def test_beam_raw_audio_hash_config_policy_or_witness_role_tamper_rejected(self):
        context,unit,receipt,greedy,beam=self.fixture()
        original=beam.evidence.return_value[0]
        for key,value in [('sha256','0'*64),('raw',{**original['raw'],'audio_sha256':'0'*64}),
                          ('role','primary'),('verifier',{**original['verifier'],'policy':'greedy-fixture'})]:
            item=copy.deepcopy(original);item[key]=value;beam.evidence.return_value=[item]
            with self.patches(greedy,beam):
                with self.assertRaises(ValueError):context.evidence(unit,receipt)

    def test_non_original_contracts_never_load_beam_evidence(self):
        context,unit,receipt,greedy,beam=self.fixture()
        variants=[]
        for marker in ('context_recovery','pronunciation_recovery'):
            value=copy.deepcopy(receipt);value['spec'][marker]={};variants.append(value)
        value=copy.deepcopy(receipt);value['take']=9;variants.append(value)
        value=copy.deepcopy(receipt);value['quality_passed']=False;variants.append(value)
        for value in variants:
            with patch.object(adapter.previous.Context,'evidence',return_value=['unchanged']),patch.object(adapter,'beam_module',return_value=beam):
                self.assertEqual(context.evidence(unit,value),['unchanged'])
        beam.evidence.assert_not_called()

    def test_all_prior_dependencies_remain_separate_and_beam_change_fails_closed(self):
        context=adapter.Context.__new__(adapter.Context)
        context.dependencies={'base':'same'};context.extension_dependencies={'v2':'same'}
        context.full_witness_dependencies={'v3':'same'};context.beam_witness_dependencies={'v4':'old'}
        with patch.object(adapter.previous.Context,'recheck') as prior,patch.object(adapter,'dependencies',return_value={'v4':'new'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
            prior.assert_called_once()
        self.assertEqual(context.dependencies,{'base':'same'})
        self.assertEqual(context.extension_dependencies,{'v2':'same'})
        self.assertEqual(context.full_witness_dependencies,{'v3':'same'})

    def test_report_preserves_raw_totals_and_separate_same_model_proofs(self):
        context,unit,receipt,greedy,beam=self.fixture('He unfolded his Della.')
        with self.patches(greedy,beam):proof=context.assess(unit,receipt)
        summary={'take':0};row={'id':'u1','takes':[summary],'selected':summary}
        report={'units':[row],'resolution_adapter_sha256':'v3-adapter','raw_mismatch_check_count':25,
                'raw_witness_mismatch_check_count':10,'full_witness_raw_mismatch_check_count':1,
                'unregistered_recovery_history':[{'heard':'preserved failure'}],'disclosure':'Existing evidence.'}
        with patch.object(adapter.previous,'report',return_value=report),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(context,'assess',return_value=proof):
            result=adapter.report(context)
        self.assertEqual(result['raw_mismatch_check_count'],25)
        self.assertEqual(result['raw_witness_mismatch_check_count'],10)
        self.assertEqual(result['full_witness_raw_mismatch_check_count'],1)
        self.assertEqual(result['beam_witness_raw_mismatch_check_count'],1)
        self.assertEqual(result['full_witness_resolution_adapter_sha256'],'v3-adapter')
        self.assertEqual(result['resolution_adapter_sha256'],adapter.base.file_digest(adapter.SELF))
        self.assertEqual(len(result['unregistered_recovery_history']),1)
        before=adapter.base.sha(result);summary['beam_witness_provenance']['evidence'][0]['receipt_sha256']='changed'
        self.assertNotEqual(before,adapter.base.sha(result))

    def test_completion_recomputes_using_v4_context_directly(self):
        context,_,_,_,_=self.fixture()
        report={'resolution_adapter_sha256':'v4','units':[]}
        with patch.object(adapter,'Context',return_value=context),patch.object(adapter,'report',return_value=report),\
             patch.object(adapter.finishing,'prefix_seconds',return_value=(1,5)),patch.object(adapter.base,'immutable'),\
             patch.object(adapter.finishing,'validate_completion',return_value=(True,'strict v4 recomputation')) as check,\
             patch.object(adapter.supervision,'write'):
            result=adapter.resolve(context.folder,complete=True)
        check.assert_called_once_with(context.folder,context,report)
        self.assertTrue(result['completion_valid'])


if __name__=='__main__':unittest.main()
