"""CPU-only RU acoustic adjudication; diagnostic files are never promoted."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_ru_acoustic_adjudication as adapter
import witness_public_demo_wav2vec2_ru as helper


class RuAcousticAdjudication(unittest.TestCase):
    def fixture(self,ctc_heard='это генеральлого брата что намеднись приехал',full_heard='Это генералово брата, что на Меднись приехал.'):
        source='— Это генералова брата, что намеднись приехал. '
        unit={'id':'u1','source_id':'p1','start':36,'end':36+len(source),'text':source,
              'spoken':source.strip(),'synthesis_text':'— Это генералова брата, что наме́днись приехал.'}
        def evidence(model,family,role,heard,path):
            config={'model':model,'local_files_only':True,'expected_text_given_to_recognizer':False}
            return {'path':path,'sha256':family[0]*64,'family':family,'role':role,'verifier':config,
                    'raw':adapter.base.transcript_check(unit['spoken'],heard,'a'*64,adapter.base.sha(config))}
        primary=evidence('Qwen3-ASR-1.7B','qwen','primary','Это генералового брата, что намедниць приехал.','checks/qwen.json')
        full=evidence('openai/whisper-large-v3','whisper','witness',full_heard,'full-witness/receipt.json')
        ctc=evidence(helper.MODEL,'wav2vec2','witness',ctc_heard,'ru-ctc-witness/receipt.json')
        decision={'passed':False,'status':'unresolved','resolution':'held','evidence':[primary,full],
                  'equivalences':[],'tolerated_deviations':[]}
        return unit,decision,ctc

    def test_existing_phonology_with_two_architectures_resolves_and_keeps_raw_primary_wrong(self):
        unit,old,ctc=self.fixture();original=copy.deepcopy(old)
        decision,proof=adapter.adjudicate(unit,old,[ctc],unit['text'],'a'*64)
        self.assertTrue(decision['passed']);self.assertEqual(decision['status'],'acoustic_model_disagreement')
        self.assertEqual(old,original)
        self.assertEqual(decision['evidence'][0]['raw']['heard'],old['evidence'][0]['raw']['heard'])
        self.assertEqual({item['family'] for item in proof['witnesses']},{'whisper','wav2vec2'})
        self.assertTrue(all(item['comparison']['tolerated_deviations']==[] for item in proof['witnesses']))
        self.assertTrue(all(change['rule']=='russian-reduction-homophone-v1' for change in decision['equivalences']))
        self.assertEqual(decision['tolerated_deviations'],[]);self.assertFalse(proof['human_approval'])
        self.assertEqual(proof['audio_sha256'],'a'*64)

    def test_ctc_exact_alone_or_whisper_only_cannot_resolve(self):
        unit,old,ctc=self.fixture(full_heard='Это генералового брата, что намеднись приехал сюда.')
        ctc['raw']=adapter.base.transcript_check(unit['spoken'],unit['spoken'],'a'*64,adapter.base.sha(ctc['verifier']))
        decision,proof=adapter.adjudicate(unit,old,[ctc],unit['text'],'a'*64)
        self.assertEqual(ctc['raw']['status'],'asr_exact_match');self.assertFalse(decision['passed']);self.assertIsNone(proof)
        decision,proof=adapter.adjudicate(unit,old,[],unit['text'],'a'*64)
        self.assertFalse(decision['passed']);self.assertIsNone(proof)
        wrong=copy.deepcopy(ctc);wrong['family']='whisper'
        with self.assertRaises(ValueError):adapter.adjudicate(unit,old,[wrong],unit['text'],'a'*64)

    def test_either_comparison_name_or_interjection_waiver_blocks_new_adjudication(self):
        unit,old,ctc=self.fixture();wanted=adapter.base.word_tokens(unit['spoken'])
        for blocked_heard in (ctc['raw']['heard'],old['evidence'][1]['raw']['heard']):
            def comparison(unused,heard,*args):
                return {'passed':True,'canonical_words':wanted,'equivalences':[],
                        'tolerated_deviations':[{'rule':'proper-noun-slot-v1','source_tokens':['генералова']}] if heard==blocked_heard else []}
            with patch.object(adapter.base,'russian_equivalence',side_effect=comparison):
                decision,proof=adapter.adjudicate(unit,old,[ctc],unit['text'],'a'*64)
            self.assertFalse(decision['passed']);self.assertIsNone(proof)

    def test_changed_audio_or_cued_configuration_is_rejected(self):
        unit,old,ctc=self.fixture()
        for field in ('audio','prompt'):
            bad=copy.deepcopy(ctc)
            if field=='audio':bad['raw']['audio_sha256']='0'*64
            else:bad['verifier']['expected_text_given_to_recognizer']=True
            with self.assertRaises(ValueError):adapter.adjudicate(unit,old,[bad],unit['text'],'a'*64)

    def test_context_appends_after_prior_resolver_and_rejects_source_edit(self):
        unit,old,ctc=self.fixture();context=adapter.Context.__new__(adapter.Context)
        context.assignment={'language':'Russian','units':[copy.deepcopy(unit)]};context.generation='frozen'
        context.blocks={'p1':unit['text']};context.ru_acoustic_adjudication_dependencies={}
        receipt={'take':0,'spec':{'source':unit,'generation':'frozen'},'audio_sha256':'a'*64}
        value={'decision':old,'source_text':unit['text'],'expected_spoken':unit['spoken'],'audio_sha256':'a'*64}
        with patch.object(adapter.previous.Context,'assess',return_value=value),patch.object(context,'ru_ctc_evidence',return_value=[ctc]):
            result=context.assess(unit,receipt)
            with self.assertRaisesRegex(ValueError,'frozen source/audio'):
                context.assess({**unit,'text':'Changed source.'},receipt)
        self.assertTrue(result['decision']['passed'])
        self.assertEqual(len(result['decision']['evidence']),3)
        self.assertEqual(result['ru_ctc_witness_provenance']['check_count'],1)

    def test_production_namespace_config_hash_and_model_are_required(self):
        unit,_,ctc=self.fixture();temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name);context=adapter.Context.__new__(adapter.Context);context.folder=folder
        ctc['verifier']['policy']=helper.POLICY
        ctc['raw']=adapter.base.transcript_check(unit['spoken'],ctc['raw']['heard'],'a'*64,adapter.base.sha(ctc['verifier']))
        path=folder/ctc['path'];adapter.base.immutable(path,{**ctc['raw'],'verifier':ctc['verifier']});ctc['sha256']=adapter.base.file_digest(path)
        module=SimpleNamespace(MODEL=helper.MODEL,POLICY=helper.POLICY,evidence=Mock(return_value=[ctc]),validate_verifier_config=Mock())
        with patch.object(adapter,'ru_ctc_module',return_value=module):
            self.assertEqual(context.ru_ctc_evidence(unit,{'audio_sha256':'a'*64}),[ctc])
            for key,value in [('path','diagnostics/take-00.json'),('sha256','0'*64),('family','whisper'),
                              ('verifier',{**ctc['verifier'],'policy':'diagnostic-policy'})]:
                bad=copy.deepcopy(ctc);bad[key]=value;module.evidence.return_value=[bad]
                with self.assertRaises(ValueError):context.ru_ctc_evidence(unit,{'audio_sha256':'a'*64})

    def test_raw_counts_and_prior_failures_remain_once_in_report(self):
        unit,old,ctc=self.fixture();_,proof=adapter.adjudicate(unit,old,[ctc],unit['text'],'a'*64)
        context=Mock();context.assignment={'units':[unit]};context.ru_acoustic_adjudication_dependencies={}
        ctc_proof={'check_count':1,'evidence':[{'status':'spoken_text_mismatch','heard':ctc['raw']['heard']}]}
        context.assess.return_value={'ru_ctc_witness_provenance':ctc_proof,'ru_acoustic_adjudication_provenance':proof}
        summary={'take':0};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v7','raw_mismatch_check_count':31,
               'resolved_with_disclosure_unit_count':2,'unregistered_recovery_history':[{'heard':'previous failure'}],'disclosure':'Previous evidence.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[{'take':0}]):
            report=adapter.report(context)
        self.assertEqual(report['raw_mismatch_check_count'],31)
        self.assertEqual(report['ru_ctc_witness_raw_mismatch_check_count'],1)
        self.assertEqual(report['resolved_with_disclosure_unit_count'],3)
        self.assertEqual(report['ru_pronunciation_resolution_adapter_sha256'],'v7')
        self.assertEqual(len(report['unregistered_recovery_history']),1)
        before=adapter.base.sha(report);summary['ru_acoustic_adjudication_provenance']['audio_sha256']='changed'
        self.assertNotEqual(before,adapter.base.sha(report))

    def test_new_dependencies_are_separate_and_fail_closed(self):
        context=adapter.Context.__new__(adapter.Context)
        context.dependencies={'base':'same'};context.ru_pronunciation_dependencies={'v7':'same'}
        context.ru_acoustic_adjudication_dependencies={'v8':'old'}
        with patch.object(adapter.previous.Context,'recheck') as prior,patch.object(adapter,'dependencies',return_value={'v8':'changed'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
            prior.assert_called_once()
        self.assertEqual(context.dependencies,{'base':'same'});self.assertEqual(context.ru_pronunciation_dependencies,{'v7':'same'})


if __name__=='__main__':unittest.main()
