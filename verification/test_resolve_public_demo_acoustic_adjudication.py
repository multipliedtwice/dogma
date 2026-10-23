"""CPU-only source-bound CTC/full-Whisper decision and tamper tests."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_acoustic_adjudication as adapter


class AcousticAdjudication(unittest.TestCase):
    def fixture(self,ctc_heard='HE ENFOLDED HIS DELA',full_heard='He enfolded his Dela.'):
        source='He enfolded his Della.'
        unit={'id':'u1','source_id':'p1','start':0,'end':len(source),'text':source,'spoken':source,
              'spoken_conversion':{'source':source,'expanded':source,'changes':[]}}
        policy={'allowed_names':['della'],'source_evidence':{'della':[{'source_id':'p1','start':16,'end':21,'source':'Della'}]}}
        def evidence(model,family,role,heard):
            config={'model':model,'local_files_only':True,'expected_text_given_to_recognizer':False}
            return {'path':'checks/'+family+'.json','sha256':family[0]*64,'family':family,'role':role,'verifier':config,
                    'raw':adapter.base.transcript_check(source,heard,'a'*64,adapter.base.sha(config))}
        primary=evidence('Qwen3-ASR-1.7B','qwen','primary','He unfolded his Della.')
        full=evidence('openai/whisper-large-v3','whisper','witness',full_heard)
        ctc=evidence('facebook/wav2vec2-base-960h','wav2vec2','witness',ctc_heard)
        decision={'passed':False,'status':'unresolved','resolution':'held','evidence':[primary,full],
                  'equivalences':[],'tolerated_deviations':[]}
        return unit,policy,decision,ctc

    def test_two_acoustic_families_literal_verb_with_only_existing_name_slot_can_adjudicate(self):
        unit,policy,old,ctc=self.fixture();original=copy.deepcopy(old)
        decision,proof=adapter.adjudicate(unit,old,[ctc],policy,'a'*64)
        self.assertTrue(decision['passed'])
        self.assertEqual(decision['status'],'acoustic_model_disagreement')
        self.assertEqual(decision['evidence'][:2],original['evidence'])
        self.assertEqual(decision['evidence'][0]['raw']['heard'],'He unfolded his Della.')
        self.assertEqual({item['family'] for item in proof['witnesses']},{'wav2vec2','whisper'})
        self.assertEqual(decision['equivalences'],[])
        self.assertTrue(all(change['rule']=='proper-noun-slot-v1' for change in decision['tolerated_deviations']))
        self.assertEqual(old,original)
        self.assertFalse(proof['human_approval'])
        self.assertEqual(proof['source_span']['text'],unit['text'])

    def test_ctc_exact_alone_and_two_whispers_cannot_trigger_any_exact_shortcut(self):
        unit,policy,old,ctc=self.fixture(ctc_heard='He enfolded his Della.',full_heard='He unfolded his Della.')
        decision,proof=adapter.adjudicate(unit,old,[ctc],policy,'a'*64)
        self.assertEqual(ctc['raw']['status'],'asr_exact_match')
        self.assertFalse(decision['passed']);self.assertIsNone(proof)
        invalid=copy.deepcopy(ctc);invalid['family']='whisper'
        with self.assertRaisesRegex(ValueError,'CTC evidence'):
            adapter.adjudicate(unit,old,[invalid],policy,'a'*64)

    def test_all_ordinary_word_substitutions_homophones_and_boundaries_remain_held(self):
        unit,policy,_,_=self.fixture()
        for heard in ('He unfolded his Dela.','He in folded his Dela.','He enfolded hiss Dela.','He enfolded Dela.',
                      'He enfolded his Dela now.'):
            self.assertFalse(adapter.strict_name_comparison(unit,heard,policy)['passed'],heard)
        changed=copy.deepcopy(policy);changed['source_evidence']['della'][0]['source_id']='wrong-source'
        self.assertFalse(adapter.strict_name_comparison(unit,'He enfolded his Dela.',changed)['passed'])

    def test_cued_or_changed_audio_raw_evidence_is_rejected(self):
        unit,policy,old,ctc=self.fixture()
        for field in ('audio','prompt'):
            bad=copy.deepcopy(ctc)
            if field=='audio':bad['raw']['audio_sha256']='0'*64
            else:bad['verifier']['expected_text_given_to_recognizer']=True
            with self.assertRaises(ValueError):adapter.adjudicate(unit,old,[bad],policy,'a'*64)
        bad=copy.deepcopy(old);bad['evidence'][1]['raw']['audio_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'original source/audio'):
            adapter.adjudicate(unit,bad,[ctc],policy,'a'*64)

    def test_context_appends_ctc_after_old_resolver_without_mutating_original_source(self):
        unit,policy,old,ctc=self.fixture()
        context=adapter.Context.__new__(adapter.Context)
        context.assignment={'language':'English','units':[copy.deepcopy(unit)]};context.generation='frozen'
        context.english_policy=policy;context.acoustic_adjudication_dependencies={}
        receipt={'take':0,'spec':{'source':unit,'generation':'frozen'},'audio_sha256':'a'*64}
        value={'decision':old,'source_text':unit['text'],'expected_spoken':unit['spoken'],'audio_sha256':'a'*64}
        snapshot=copy.deepcopy(receipt)
        with patch.object(adapter.previous.Context,'assess',return_value=value),patch.object(context,'ctc_evidence',return_value=[ctc]):
            result=context.assess(unit,receipt)
            changed={**unit,'text':'He unfolded his Della.'}
            with self.assertRaisesRegex(ValueError,'frozen source/audio'):context.assess(changed,receipt)
        self.assertEqual(len(result['decision']['evidence']),3)
        self.assertTrue(result['decision']['passed'])
        self.assertEqual(receipt,snapshot)
        self.assertIsNotNone(result['acoustic_adjudication_provenance'])
        self.assertEqual(result['ctc_witness_provenance']['check_count'],1)

    def test_ctc_wrapper_rejects_changed_receipt_config_model_and_wrong_family(self):
        unit,_,_,ctc=self.fixture()
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name);context=adapter.Context.__new__(adapter.Context);context.folder=folder
        path=folder/ctc['path'];adapter.base.immutable(path,{**ctc['raw'],'verifier':ctc['verifier']})
        ctc['sha256']=adapter.base.file_digest(path);ctc['verifier']['policy']='ctc-fixture'
        ctc['raw']=adapter.base.transcript_check(unit['spoken'],ctc['raw']['heard'],'a'*64,adapter.base.sha(ctc['verifier']))
        path.write_text(__import__('json').dumps({**ctc['raw'],'verifier':ctc['verifier']}),encoding='utf-8')
        ctc['sha256']=adapter.base.file_digest(path)
        module=SimpleNamespace(MODEL=ctc['verifier']['model'],POLICY='ctc-fixture',
                               evidence=Mock(return_value=[ctc]),validate_verifier_config=Mock())
        receipt={'audio_sha256':'a'*64}
        with patch.object(adapter,'ctc_module',return_value=module):
            self.assertEqual(context.ctc_evidence(unit,receipt),[ctc])
            for key,value in [('sha256','0'*64),('family','whisper'),('raw',{**ctc['raw'],'heard':'unfolded'})]:
                invalid=copy.deepcopy(ctc);invalid[key]=value;module.evidence.return_value=[invalid]
                with self.assertRaises(ValueError):context.ctc_evidence(unit,receipt)

    def test_report_discloses_acoustic_disagreement_without_doubling_raw_counts(self):
        unit,policy,old,ctc=self.fixture();_,proof=adapter.adjudicate(unit,old,[ctc],policy,'a'*64)
        context=Mock();context.assignment={'units':[unit]};context.acoustic_adjudication_dependencies={'v6':'pinned'}
        ctc_proof={'check_count':1,'evidence':[{'status':'spoken_text_mismatch','heard':ctc['raw']['heard']}]}
        context.assess.return_value={'ctc_witness_provenance':ctc_proof,'acoustic_adjudication_provenance':proof}
        summary={'take':0};row={'id':'u1','selected':summary,'takes':[summary]}
        value={'units':[row],'resolution_adapter_sha256':'v5','raw_mismatch_check_count':29,
               'resolved_with_disclosure_unit_count':2,'disclosure':'Prior failures.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[{'take':0}]):
            report=adapter.report(context)
        self.assertEqual(report['raw_mismatch_check_count'],29)
        self.assertEqual(report['ctc_witness_raw_mismatch_check_count'],1)
        self.assertEqual(report['resolved_with_disclosure_unit_count'],3)
        self.assertEqual(report['acoustic_adjudicated_units'],1)
        self.assertEqual(report['sustained_sound_resolution_adapter_sha256'],'v5')
        self.assertIn('acoustic model disagreement',report['disclosure'])

    def test_v6_dependencies_never_replace_prior_pins(self):
        context=adapter.Context.__new__(adapter.Context)
        context.dependencies={'base':'same'};context.acoustic_adjudication_dependencies={'v6':'old'}
        with patch.object(adapter.previous.Context,'recheck') as prior,patch.object(adapter,'dependencies',return_value={'v6':'changed'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
            prior.assert_called_once()
        self.assertEqual(context.dependencies,{'base':'same'})


if __name__=='__main__':unittest.main()
