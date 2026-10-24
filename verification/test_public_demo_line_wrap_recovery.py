"""V16 typographic reconstruction, immutable evidence and trigger-only stress."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_line_wrap_recovery as m
from verification import test_public_demo_boundary_recovery as fixtures


class LineWrap(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.BoundaryExperiment();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.folder=self.case.folder;self.unit=self.case.unit;self.repair=self.case.repair
    def chosen(self):return m.candidate(self.unit,self.repair,self.case.spec)
    def answer(self):
        return {'candidate_id':m.CANDIDATE,'synthesis_text':self.chosen()['synthesis_text'],'accepted':True,'reason':'General transfer inference only.',
            'word_sequence_preserved':True,'meaning_preserved':True,'stress_marks_preserved':True,'orthographic_line_wrap_legal':True,
            'tts_interpretation':'unknown','audio_approval':False}
    def test_real_lf_is_retained_and_all_original_anchors_reconstruct(self):
        before=copy.deepcopy(self.unit);chosen=self.chosen()
        self.assertEqual(chosen['synthesis_text'],'Брат и́х-\nний рад…')
        self.assertEqual(chosen['synthesis_text'].replace('-\n',''),self.repair['synthesis_text'])
        self.assertNotEqual(chosen['synthesis_text'],'Брат и́х-ний рад…')
        self.assertEqual(chosen['spoken'],self.unit['spoken']);self.assertEqual(self.unit,before)
        for row in chosen['letter_digit_stress_map']:
            self.assertEqual(chosen['native_before'][row['before']],chosen['synthesis_text'][row['after']])
        self.assertNotIn('\n',chosen['source_stress_projection'])
        self.assertEqual(m.stress_witness.marked_words(chosen['source_stress_projection']),[('ихний',0,1)])
    def test_unknown_sound_is_explicit_and_both_reviewers_can_reject(self):
        bound={'candidates':[self.chosen()]};answer=self.answer();m.validate_answer(answer,bound)
        for accepted in (False,True):
            row={**answer,'accepted':accepted};m.validate_answer(row,bound)
        for key,value in [('tts_interpretation','safe'),('audio_approval',True),('synthesis_text',answer['synthesis_text'].replace('\n','')),
                          ('orthographic_line_wrap_legal',False)]:
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_answer({**answer,key:value},bound)
    def test_source_offset_and_unsupported_transfer_reject(self):
        for key,value in [('source_start',0),('split_after_letters',1),('split_after_letters',4)]:
            spec=copy.deepcopy(self.case.spec);spec['segmentation'][key]=value
            with self.assertRaises(ValueError):m.candidate(self.unit,self.repair,spec)
    def test_capture_has_only_general_authority_and_bounded_quote(self):
        directory=ROOT/'training-work/public-demos-20261023/diagnostics/line-wrap-primary-v1/20261023T173138Z'
        facts=m.primary_evidence(directory)
        self.assertFalse(facts['lemma_specific_claim']);self.assertFalse(facts['tts_authority'])
        self.assertLessEqual(facts['quote_word_count'],25);self.assertEqual(facts['raw_sha256'],m.PRIMARY_RAW_SHA)
        for name in ('source.html','visible.txt','retrieval.json'):m.immutable_bytes(self.folder/name,(directory/name).read_bytes())
        with self.assertRaises(ValueError):m.immutable_bytes(self.folder/'source.html',b'changed')
        self.assertEqual((self.folder/'source.html').read_bytes(),(directory/'source.html').read_bytes())
    def test_registered_own_receipt_revalidates_but_foreign_marker_does_not(self):
        chosen=self.chosen();base=m.home(self.folder,self.unit);m.immutable(base/'campaign.json',{'fixed':'campaign'})
        spec={'request':{'text':chosen['synthesis_text']},'line_wrap_recovery':{'policy':m.POLICY,'attempt':1,
            'campaign_sha256':m.file_digest(base/'campaign.json'),'implementation_sha256':m.file_digest(m.SELF),'context':chosen}}
        value={'take':27,'unit':self.unit['id'],'spec':spec};private=m.receipt_path(self.folder,self.unit,1);m.immutable(private,value)
        canonical=self.folder/'takes'/self.unit['id']/'take-27.json';canonical.parent.mkdir(parents=True);canonical.write_bytes(private.read_bytes())
        m.no_conflicting_attempts(self.folder,self.unit,{'candidates':[chosen]})
        m.immutable(canonical.with_name('take-05.json'),value)
        with self.assertRaises(ValueError):m.no_conflicting_attempts(self.folder,self.unit,{'candidates':[chosen]})
    def test_native_profiles_keep_identical_lf_and_distinct_bounded_requests(self):
        chosen=self.chosen();base=m.home(self.folder,self.unit);m.immutable(base/'campaign.json',{});m.immutable(base/'proposal.json',{})
        bound={'candidates':[chosen],'attempts':[{'context_id':m.CANDIDATE,'profile':n} for n in m.PROFILES],
            'original_spec':{'request':{'seed':10,'text':self.repair['synthesis_text']},'effects':'disabled'},'word_proposal_sha256':'a'*64,'word_repair':self.repair}
        with patch.object(m,'validated_proposal',return_value={'approved_candidates':[m.CANDIDATE]}):
            values=[m.native_spec(self.folder,self.unit,bound,n) for n in (1,2,3)]
            self.assertEqual(len({v['request']['seed'] for v in values}),3)
            self.assertTrue(all(v['request']['text']==chosen['synthesis_text'] for v in values))
            self.assertTrue(all(v['effects']=='disabled' for v in values))
            with self.assertRaises(ValueError):m.native_spec(self.folder,self.unit,bound,4)
    def test_stress_is_only_a_bounded_trigger_never_a_hold_or_approval(self):
        result={'words':[{'word':'fixture'}],'suspect':True}
        first=m.stress_outcome(result,1);last=m.stress_outcome(result,3)
        self.assertTrue(first['retake_trigger']);self.assertFalse(last['retake_trigger']);self.assertTrue(last['exhausted_suspicion'])
        for value in (first,last,m.stress_outcome(None,1),m.stress_outcome(None,1,'failure')):
            self.assertFalse(value['independent_hold']);self.assertFalse(value['stress_approval']);self.assertFalse(value['audio_approval'])
    def test_stress_requires_both_word_gates_before_model_or_crop_reads(self):
        for kind in ('context','crop'):
            with patch.object(m,'blind_decision',side_effect=lambda f,u,a,k:{'passed':k!=kind}),patch.object(m,'native_receipt') as native:
                with self.assertRaises(ValueError):m.stress_inputs(self.folder,self.unit,1,{})
                native.assert_not_called()
    def test_suspect_first_valid_take_is_retained_when_later_profiles_fail_words(self):
        first={'attempt':1,'word_pcm_valid':True,'stress':m.stress_outcome({'words':[{}],'suspect':True},1)}
        later=[{'attempt':n,'word_pcm_valid':False,'stress':{}} for n in (2,3)]
        self.assertEqual(m.select_word_valid_candidate([first,*later]),first)
        error={**first,'stress':m.stress_outcome(None,1,'Missing witness model')}
        self.assertEqual(m.select_word_valid_candidate([error,*later]),error)
        clean={'attempt':2,'word_pcm_valid':True,'stress':m.stress_outcome({'words':[{}],'suspect':False},2)}
        self.assertEqual(m.select_word_valid_candidate([first,clean]),clean)
    def test_cpu_internal_worker_argparse_subprocess_reaches_semantic_validation(self):
        import subprocess,os
        environment={**os.environ,'CUDA_VISIBLE_DEVICES':'','PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
        command=[sys.executable,'-X','utf8',str(m.SELF),'--folder',str(self.folder),'--unit','fixture',
                 '--stage','stress','--attempt','1','--stress-worker']
        result=subprocess.run(command,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('FileNotFoundError',result.stderr)
        self.assertNotIn('the following arguments are required',result.stderr)
        self.assertNotIn('unrecognized arguments',result.stderr)
    def test_prior_rejection_and_malformed_answer_bytes_remain_immutable(self):
        base=m.previous.home(self.folder,self.unit);m.immutable(base/'campaign.json',{'old':'fixed'})
        cli=self.folder/'reviewer.exe';cli.write_bytes(b'pinned synthetic CLI executable')
        request={'system':'old fixed request','request':{'candidate':'old inline hyphen'}};protected={}
        for provider,model,effort in m.isolated.PROVIDERS:
            rp=base/(provider+'-attempt-1.request.json');ap=base/(provider+'-attempt-1.answer.json');fp=base/(provider+'-attempt-1.failure.json')
            m.immutable(rp,{**request,'provider':provider,'model':model,'effort':effort})
            m.immutable(ap,{'request_sha256':m.file_digest(rp),'answer':{'accepted':False,'malformed':'Retain without rewriting'},
                'metadata':{'provider':provider,'model_used':model,'effort':effort,'version':'fixture','fallback':False,
                            'cli':str(cli),'cli_sha256':m.file_digest(cli)},'audio_approval':False})
            m.immutable(fp,{'request_sha256':m.file_digest(rp),'answer_sha256':m.file_digest(ap),'kind':'answer_format_or_fact_failure'})
            for path in (rp,ap,fp):protected[path]=path.read_bytes()
        with patch.object(m.previous,'proposal_request',return_value=request):
            history=m.old_review_evidence(self.folder,self.unit,{})
            self.assertEqual(len(history['reviews']),2)
            self.assertTrue(all(r['prior_decisions']['accepted'] is False for r in history['reviews']))
            for path,data in protected.items():self.assertEqual(path.read_bytes(),data)
            first=next(base.glob('*.failure.json'));value=m.read(first);value['answer_sha256']='wrong';m.renderer.write(first,value)
            with self.assertRaises(ValueError):m.old_review_evidence(self.folder,self.unit,{})
    def test_missing_witness_input_records_error_without_inference_or_lexical_hold(self):
        bound={'stress_witness':{'enabled':True}};inputs={'configuration':bound['stress_witness'],'projection':'Брат и́хний рад…'}
        with patch.object(m,'campaign',return_value=bound),patch.object(m,'stress_inputs',return_value=inputs),\
             patch.object(m,'stress_configuration',side_effect=FileNotFoundError('missing calibration')),\
             patch.dict(m.os.environ,{'CUDA_VISIBLE_DEVICES':''}),patch.object(m.stress_witness,'judge') as judge,\
             patch.object(m.stress_witness,'release') as release:
            value=m.measure_stress(self.folder,self.unit,1,worker=True)
            judge.assert_not_called();release.assert_called_once()
            self.assertEqual(value['status'],'error');self.assertFalse(value['independent_hold']);self.assertFalse(value['stress_approval'])
            self.assertIsNotNone(m.select_word_valid_candidate([{'attempt':1,'word_pcm_valid':True,'stress':value}]))
    def test_cpu_worker_failure_is_unmeasured_and_command_propagates_all_internal_args(self):
        import subprocess
        bound={'stress_witness':{'enabled':True}};inputs={'configuration':bound['stress_witness'],'projection':'Брат и́хний рад…'}
        with patch.object(m,'campaign',return_value=bound),patch.object(m,'stress_inputs',return_value=inputs),\
             patch.object(m.subprocess,'run',side_effect=subprocess.TimeoutExpired('witness',900)) as launch:
            value=m.measure_stress(self.folder,self.unit,2)
            command=launch.call_args.args[0];options=launch.call_args.kwargs
            self.assertEqual(command[command.index('--unit')+1],self.unit['id'])
            self.assertEqual(command[command.index('--attempt')+1],'2');self.assertIn('--stress-worker',command)
            self.assertEqual(options['env']['CUDA_VISIBLE_DEVICES'],'')
            self.assertEqual(value['status'],'error');self.assertFalse(value['independent_hold']);self.assertFalse(value['stress_approval'])
    def test_frozen_literal_target_gate_rejects_missing_final_consonant(self):
        for heard in ('Брат их не рад','Брат ихни рад','Брат рад ихний','Брат ихний ихний рад'):
            self.assertIsNone(m.literal_target_alignment(self.unit,heard,self.case.blocks['p'],self.repair,'a'*64,'b'*64))
        self.assertIsNotNone(m.literal_target_alignment(self.unit,self.unit['spoken'],self.case.blocks['p'],self.repair,'a'*64,'b'*64))

    def test_actual_pcm_crop_cannot_be_changed_even_with_rewritten_hash_and_both_gates_required(self):
        import numpy as np
        import soundfile as sf
        chosen=self.chosen();base=m.attempt_home(self.folder,self.unit,1)
        m.immutable(base/'native-receipt.json',{'fixture':'native'});m.immutable(base/'alignment.json',{'fixture':'coordinates'})
        words=m.word_tokens(chosen['spoken']);audio=np.zeros((len(words)+1)*1000,dtype=np.int16)
        cues=[]
        for index,word in enumerate(words):
            audio[index*1000+200:index*1000+500]=1000+index
            cues.append({'text':word,'start':index+.2,'end':index+.5})
        native_path=base/'full.wav';sf.write(native_path,audio,1000,subtype='PCM_16')
        spec={'source':self.unit,'line_wrap_recovery':{'attempt':1,'context':chosen}}
        native={'audio':native_path.relative_to(self.folder).as_posix(),'audio_sha256':m.file_digest(native_path),'quality_passed':True}
        bound={'word_campaign_sha256':'a'*64,'word_proposal_sha256':'b'*64,'word_repair':self.repair,'word_stress':{},'stress_witness':{'enabled':False}}
        m.immutable(m.home(self.folder,self.unit)/'campaign.json',{'fixture':'campaign'})
        quality={'fixture':'boundary checks held fixed'}
        with patch.object(m,'native_receipt',return_value=(bound,spec,native)),patch.object(m,'validate_alignment',return_value=cues),\
             patch.object(m.renderer,'quality',return_value=quality),patch.object(m,'blind_decision',return_value={'passed':True}),\
             patch.object(m.words,'campaign',return_value={'dictionary':[]}),patch.object(m.words,'validated_proposal',return_value={'providers':[]}),\
             patch.object(m,'experiment_provenance',return_value={'fixture':'review hashes'}):
            m.crop(self.folder,self.unit,1);receipt=m.read(m.receipt_path(self.folder,self.unit,1))
            self.assertEqual(receipt['take'],27)
            proof=m.validate_line_wrap_receipt(self.folder,self.unit,receipt)
            self.assertFalse(proof['alignment_is_approval']);self.assertFalse(proof['human_approval'])
            for failed in ('context','crop'):
                with patch.object(m,'blind_decision',side_effect=lambda f,u,a,k:{'passed':k!=failed}):
                    with self.assertRaisesRegex(ValueError,'recognition disagreements'):m.validate_line_wrap_receipt(self.folder,self.unit,receipt)
            child_path=self.folder/receipt['audio'];child,rate=sf.read(child_path,dtype='int16');child[len(child)//2]+=1
            sf.write(child_path,child,rate,subtype='PCM_16');receipt['audio_sha256']=m.file_digest(child_path)
            m.renderer.write(m.receipt_path(self.folder,self.unit,1),receipt)
            with self.assertRaisesRegex(ValueError,'exact unprocessed native PCM'):m.validate_line_wrap_receipt(self.folder,self.unit,receipt)


if __name__=='__main__':unittest.main()
