"""Synthetic CPU tests; never downloads a model or starts recognition/synthesis."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_full_witness as adapter


class FullWitnessAdapter(unittest.TestCase):
    def fixture(self,heard='He enfolded his Della.'):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name)
        unit={'id':'u1','source_id':'p1','start':0,'end':21,'text':'He enfolded his Della.',
              'spoken':'He enfolded his Della.'}
        context=adapter.Context.__new__(adapter.Context)
        context.folder,context.generation=folder,'frozen-fixture'
        context.assignment={'language':'English','title':'Fixture','units':[unit]}
        context.blocks={'p1':unit['text']};context.dependencies={};context.extension_dependencies={'v2':'fixed'}
        context.full_witness_dependencies={'v3':'fixed'};context.bindings={};context.english_policy=None
        context.recheck=Mock()
        audio=folder/'assets/raw.wav';audio.parent.mkdir();audio.write_bytes(b'unchanged native speech fixture')
        checksum=adapter.base.file_digest(audio)
        spec={'source':unit,'generation':context.generation,'request':{'text':unit['spoken']}}
        receipt={'unit':unit['id'],'take':0,'quality_passed':True,'audio':'assets/raw.wav','audio_sha256':checksum,
                 'spec':spec,'spec_sha256':adapter.base.sha(spec)}
        adapter.base.immutable(folder/'takes/u1/take-00.json',receipt)
        config={'model':'openai/whisper-large-v3','expected_text_given_to_recognizer':False,'local_files_only':True}
        raw=adapter.base.transcript_check(unit['spoken'],heard,checksum,adapter.base.sha(config))
        path=folder/'full-witness/full.json';adapter.base.immutable(path,{**raw,'verifier':config})
        item={'path':path.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(path),
              'verifier':config,'raw':raw,'family':'whisper','role':'witness'}
        helper=SimpleNamespace(MODEL=config['model'],REVISION='pinned-fixture-revision',FILES={'model':'a'*64},
                               __file__=__file__,dependencies=Mock(return_value={}),
                               evidence=Mock(return_value=[item]),validate_verifier_config=Mock())
        return context,unit,receipt,helper,item

    def test_original_native_audio_can_be_resolved_by_unchanged_any_raw_exact_rule(self):
        context,unit,receipt,helper,item=self.fixture()
        original=copy.deepcopy(receipt)
        with patch.object(adapter,'full_module',return_value=helper),patch.object(adapter.previous.Context,'evidence',return_value=[]):
            proof=context.assess(unit,receipt)
        self.assertTrue(proof['decision']['passed'])
        self.assertEqual(proof['decision']['resolution'],'independent_witness_exact')
        self.assertEqual(proof['decision']['selected_evidence'],item['path'])
        self.assertEqual(proof['full_witness_provenance']['evidence'][0]['receipt_sha256'],item['sha256'])
        self.assertEqual(receipt,original)
        self.assertEqual(proof['dependencies'],{})
        self.assertEqual(context.extension_dependencies,{'v2':'fixed'})

    def test_unfolded_remains_held_and_raw_disagreement_is_retained(self):
        context,unit,receipt,helper,item=self.fixture('He unfolded his Della.')
        with patch.object(adapter,'full_module',return_value=helper),patch.object(adapter.previous.Context,'evidence',return_value=[]),\
             patch.object(adapter.base,'english_equivalence',return_value={'passed':False,'canonical_words':['unfolded'],
                                                                         'equivalences':[],'tolerated_deviations':[]}):
            proof=context.assess(unit,receipt)
        self.assertFalse(proof['decision']['passed'])
        self.assertEqual(proof['decision']['evidence'][0]['raw']['heard'],'He unfolded his Della.')
        self.assertEqual(proof['full_witness_provenance']['evidence'][0]['status'],'spoken_text_mismatch')

    def test_full_witness_skips_context_pronunciation_outside_original_takes(self):
        context,unit,receipt,helper,_=self.fixture()
        variants=[]
        for marker in ('context_recovery','pronunciation_recovery'):
            value=copy.deepcopy(receipt);value['spec'][marker]={'policy':'frozen'};variants.append(value)
        value=copy.deepcopy(receipt);value['take']=6;variants.append(value)
        value=copy.deepcopy(receipt);value['quality_passed']=False;variants.append(value)
        for value in variants:
            with patch.object(adapter,'full_module',return_value=helper),patch.object(adapter.previous.Context,'evidence',return_value=['old']):
                self.assertEqual(context.evidence(unit,value),['old'])
        helper.evidence.assert_not_called()
        self.assertTrue(adapter.original_take({**receipt,'take':5,'spec':{'recovery':{'policy':'fixed'}}}))

    def test_forged_audio_receipt_raw_and_nonwhisper_role_are_rejected(self):
        context,unit,receipt,helper,item=self.fixture()
        for field,value in [('sha256','0'*64),('raw',{**item['raw'],'heard':'He unfolded his Della.'}),('role','primary')]:
            bad=copy.deepcopy(item);bad[field]=value;helper.evidence.return_value=[bad]
            with patch.object(adapter,'full_module',return_value=helper),patch.object(adapter.previous.Context,'evidence',return_value=[]):
                with self.assertRaises(ValueError):context.evidence(unit,receipt)

    def test_new_dependencies_are_separate_and_fail_closed_on_change(self):
        context=adapter.Context.__new__(adapter.Context)
        context.dependencies={'base':'same'};context.extension_dependencies={'v2':'same'}
        context.full_witness_dependencies={'new':'old'}
        with patch.object(adapter.previous.Context,'recheck') as previous,patch.object(adapter,'dependencies',return_value={'new':'changed'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
            previous.assert_called_once()
        self.assertEqual(context.dependencies,{'base':'same'})
        self.assertEqual(context.extension_dependencies,{'v2':'same'})

    def test_report_provenance_is_hash_bound_without_double_counting(self):
        context,unit,receipt,_,_=self.fixture()
        summary={'take':0};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v2-adapter','raw_mismatch_check_count':19,
               'raw_witness_mismatch_check_count':8,'unregistered_recovery_history':[{'heard':'earlier raw failure'}],
               'disclosure':'Prior disclosure.'}
        proof={'rule':adapter.RULE,'evidence':[{'status':'spoken_text_mismatch','receipt_sha256':'x'}]}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(context,'assess',return_value={'full_witness_provenance':proof}):
            result=adapter.report(context)
        self.assertEqual(result['raw_mismatch_check_count'],19)
        self.assertEqual(result['raw_witness_mismatch_check_count'],8)
        self.assertEqual(result['full_witness_raw_mismatch_check_count'],1)
        self.assertEqual(result['pronunciation_resolution_adapter_sha256'],'v2-adapter')
        self.assertEqual(result['resolution_adapter_sha256'],adapter.base.file_digest(adapter.SELF))
        self.assertEqual(row['selected']['full_witness_provenance'],proof)
        before=adapter.base.sha(result);row['selected']['full_witness_provenance']['evidence'][0]['receipt_sha256']='changed'
        self.assertNotEqual(before,adapter.base.sha(result))

    def test_completion_receives_v3_context_and_augmented_report_directly(self):
        context,_,_,_,_=self.fixture()
        report={'resolution_adapter_sha256':'v3','units':[]}
        with patch.object(adapter,'Context',return_value=context),patch.object(adapter,'report',return_value=report),\
             patch.object(adapter.finishing,'prefix_seconds',return_value=(1,10)),patch.object(adapter.base,'immutable'),\
             patch.object(adapter.finishing,'validate_completion',return_value=(True,'recomputed')) as completion,\
             patch.object(adapter.supervision,'write'):
            result=adapter.resolve(context.folder,complete=True)
        completion.assert_called_once_with(context.folder,context,report)
        self.assertTrue(result['completion_valid'])


if __name__=='__main__':unittest.main()
