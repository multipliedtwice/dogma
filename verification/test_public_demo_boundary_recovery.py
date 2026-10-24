"""V14 source/native mapping and closed budgets; synthetic CPU data only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_demo_boundary_recovery as m


class BoundaryExperiment(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='boundary-test-',dir=ROOT/'training-work');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'book';self.folder.mkdir()
        text=['Он не любит споров. ','Брат ихний рад…'];self.units=[];start=0
        structure={'mode':'speech','source_part_index':1,'source_part_start':0,'source_part_end':sum(map(len,text)),
                   'source_part_text':''.join(text),'structure_request_sha256':'a'*64,'structure_answer_sha256':'b'*64}
        for index,item in enumerate(text):
            self.units.append({'id':'fixture'+str(index),'source_id':'p','start':start,'end':start+len(item),'text':item,
                'spoken':item.strip(),'synthesis_text':item.strip(),'chapter_id':'c','speaker':'person','voice':'family','mood':'calm',
                'palette_reference':{'path':'C:/PRIVATE/training-work/reference.wav','text':'PROTECTED REFERENCE'},
                'performance':{'analysis_parts':[structure]}});start+=len(item)
        self.unit=self.units[1];self.assignment={'language':'Russian','units':self.units};self.blocks={'p':''.join(text)}
        source_index=self.unit['text'].index('ихний')
        patchrow={'source_start':self.unit['start']+source_index,'source_end':self.unit['start']+source_index+5,
            'source_surface':'ихний','native_start':source_index,'native_end':source_index+5,'before':'ихний','after':'и́хний',
            'kind':'stress-only','dictionary_facts':[{'url':'https://orfo.ruslang.ru/fixture','headword':'и́хний',
                'fact':'Synthetic invariant fixture, not a quotation.','retrieval_date':'2026-10-24'}]}
        row={k:self.unit[k] for k in ('id','source_id','start','end')}
        row.update(text_sha256=m.renderer.sha(self.unit['text']),native_before=self.unit['synthesis_text'],
                   native_after='Брат и́хний рад…',patches=[patchrow])
        self.repair=m.words.repair(self.unit,row)
        boundary=self.units[0]['end']-2
        self.spec={'unit':self.unit['id'],'source_id':'p','start':self.unit['start'],'end':self.unit['end'],
            'text_sha256':m.renderer.sha(self.unit['text']),'pair_ids':[u['id'] for u in self.units],
            'boundary':{'source_id':'p','start':boundary,'end':boundary+1,'before':'.','after':','},
            'segmentation':{'source_start':patchrow['source_start'],'source_end':patchrow['source_end'],
                            'source_surface':'ихний','split_after_letters':2},'prior_supervision':'not-read-in-this-fixture'}
    def candidates(self):return m.candidates(self.assignment,self.blocks,self.unit,self.repair,self.spec)
    def bound(self):
        return {'candidates':self.candidates(),'word_repair':self.repair,'target':self.unit,'dictionary':self.repair['patches'][0]['dictionary_facts'],
                'source_paragraph':self.blocks['p'],'language':'Russian'}
    def answer(self):
        return {'candidates':[{'id':c['id'],'synthesis_text':c['synthesis_text'],'accepted':True,'reason':'Synthetic source-preserving hypothesis.',
                    'word_sequence_preserved':True,'meaning_preserved':True,'stress_preserved':True,
                    'segmentation_evaluation':'sound_preserving' if i else 'not_applicable'} for i,c in enumerate(self.candidates())],
                'audio_approval':False}

    def test_three_distinct_fixed_choices_preserve_original_source_and_all_anchors(self):
        assignment=copy.deepcopy(self.assignment);choices=self.candidates()
        self.assertEqual([c['id'] for c in choices],list(m.CHOICES));self.assertEqual(len({c['synthesis_text'] for c in choices}),3)
        self.assertEqual([c['synthesis_text'] for c in choices],['Он не любит споров, Брат и́хний рад…',
            'Он не любит споров, Брат и́х-ний рад…','Брат и́х-ний рад…'])
        for c in choices:
            for item in c['letter_digit_stress_map']:self.assertEqual(c['native_before'][item['before']],c['synthesis_text'][item['after']])
            self.assertEqual(c['spoken'],'Он не любит споров. Брат ихний рад…' if c['choice']<3 else self.unit['spoken'])
        self.assertEqual(self.assignment,assignment)

    def test_other_speaker_performance_structural_part_skipped_source_and_bad_offsets_reject(self):
        for key,value in [('speaker','another'),('voice','another'),('mood','angry'),('palette_reference',{'path':'different'})]:
            bad=copy.deepcopy(self.assignment);bad['units'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):m.candidates(bad,self.blocks,self.unit,self.repair,self.spec)
        for mutation in (lambda s:s['boundary'].update(start=1),lambda s:s['boundary'].update(before='—'),
                         lambda s:s['segmentation'].update(source_start=1),lambda s:s['segmentation'].update(split_after_letters=0),
                         lambda s:s.update(pair_ids=['fixture1','fixture0'])):
            bad=copy.deepcopy(self.spec);mutation(bad)
            with self.assertRaises(ValueError):m.candidates(self.assignment,self.blocks,self.unit,self.repair,bad)
        bad=copy.deepcopy(self.assignment);bad['units'][0]['performance']['analysis_parts'][0]['mode']='narrator'
        with self.assertRaises(ValueError):m.candidates(bad,self.blocks,self.unit,self.repair,self.spec)

    def test_only_declared_boundary_and_internal_hyphen_changes_allowed(self):
        for edit in ({'kind':'boundary','index':2,'before':'.','after':'—'},
                     {'kind':'segmentation','index':0,'before':'','after':'-'},
                     {'kind':'letter','index':0,'before':'Д','after':'Т'}):
            with self.assertRaises(ValueError):m.apply_native_edits('Да. ихний', [edit])
        after,mapping=m.apply_native_edits('И́хний 12?', [{'kind':'segmentation','index':3,'before':'','after':'-'}])
        self.assertEqual(after,'И́х-ний 12?');self.assertEqual(len(mapping),8)
        changed=copy.deepcopy(self.repair);changed['synthesis_text']='Брат и́хний друг…'
        with self.assertRaises(ValueError):m.validate_repair(self.unit,changed)

    def test_colon_candidate_is_data_bound_before_any_campaign(self):
        self.spec['boundary']['after']=':'
        self.assertIn('споров: Брат',self.candidates()[0]['synthesis_text'])
        self.spec['boundary']['after']=';'
        with self.assertRaises(ValueError):self.candidates()

    def test_either_evaluator_can_reject_each_candidate_without_substitution_or_retry(self):
        bound=self.bound();left=self.answer();right=self.answer();right['candidates'][0].update(accepted=False,reason='Meaning uncertain.',meaning_preserved=False)
        self.assertEqual(m.approved_candidates(bound,[left,right]),list(m.CHOICES)[1:])
        for row in right['candidates']:row['accepted']=False
        self.assertEqual(m.approved_candidates(bound,[left,right]),[])
        bad=self.answer();bad['candidates'][1]['synthesis_text']='Replacement hypothesis'
        with self.assertRaises(ValueError):m.approved_candidates(bound,[left,bad])
        bad=self.answer();bad['candidates'][1]['segmentation_evaluation']='uncertain'
        with self.assertRaises(ValueError):m.validate_answer(bad,bound)

    def test_provider_request_has_exact_hypotheses_but_no_private_reference_or_raw_model_request(self):
        request=m.proposal_request(self.bound());serialized=json.dumps(request,ensure_ascii=False)
        for text in ('и́х-ний','native_edits','letter_digit_stress_map','Reject','reject'): # At least one reject instruction and all concrete data.
            if text not in ('Reject','reject'):self.assertIn(text,serialized)
        self.assertIn('reject',serialized)
        self.assertNotIn('PRIVATE',serialized);self.assertNotIn('reference.wav',serialized);self.assertNotIn('palette_reference',serialized)
        self.assertNotIn('prior_supervision',serialized);self.assertNotIn('model_path',serialized)

    def test_real_evaluation_boundary_retains_rejections_without_another_provider_call(self):
        bound=self.bound();request=m.proposal_request(bound);answer=self.answer()
        answer['candidates'][0].update(accepted=False,meaning_preserved=False,reason='Independent semantic rejection.')
        def call(provider,model,effort,request,path):
            return {'request_sha256':m.file_digest(path),'answer':answer,'metadata':{'provider':provider,'model_used':model,'effort':effort},'audio_approval':False}
        with patch.object(m,'call_provider',side_effect=call) as provider:
            first=m.evaluate_provider(self.folder,self.unit,bound,request,*m.isolated.PROVIDERS[0])
            repeated=m.evaluate_provider(self.folder,self.unit,bound,request,*m.isolated.PROVIDERS[0])
        self.assertEqual(provider.call_count,1);self.assertEqual(first,repeated)
        self.assertFalse(m.read(self.folder/first['answer'])['answer']['candidates'][0]['accepted'])

    def test_malformed_answer_is_retained_and_never_retried_as_persuasion(self):
        bound=self.bound();request=m.proposal_request(bound)
        def call(provider,model,effort,request,path):
            return {'request_sha256':m.file_digest(path),'answer':{'bad':'format'},'metadata':{},'audio_approval':False}
        with patch.object(m,'call_provider',side_effect=call) as provider:
            with self.assertRaises(ValueError):m.evaluate_provider(self.folder,self.unit,bound,request,*m.isolated.PROVIDERS[0])
            with self.assertRaises(ValueError):m.evaluate_provider(self.folder,self.unit,bound,request,*m.isolated.PROVIDERS[0])
        self.assertEqual(provider.call_count,1)

    def test_raw_missing_final_consonant_split_word_and_one_correct_witness_stay_held(self):
        expected=self.unit['spoken'];bound=self.bound()
        def evidence(a,b):
            rows=[]
            for recognizer,role,family,heard in [('qwen','primary','qwen',a),('turbo','witness','whisper',b)]:
                rows.append({'path':recognizer,'sha256':'b'*64,'role':role,'family':family,'verifier':{},
                             'raw':m.transcript_check(expected,heard,'a'*64,'c'*64)})
            return rows
        self.assertTrue(m.strict_decision(self.unit,evidence(expected,expected),self.blocks['p'],self.repair)['passed'])
        for heard in ('Брат их не рад…','Брат ихни рад…','Брат ихний спит…','Брат рад…','Брат ихний рад очень…'):
            with self.subTest(heard=heard):self.assertFalse(m.strict_decision(self.unit,evidence(heard,expected),self.blocks['p'],self.repair)['passed'])

    def test_frozen_non_target_spacing_equivalence_keeps_unique_literal_target_alignment(self):
        source='Наш не охотник до борзых. Брат ихний охоч…'
        unit={'text':source,'spoken':source,'synthesis_text':'Наш не охотник до борзы́х. Брат и́хний охо́ч…'}
        repair=copy.deepcopy(self.repair);repair['patches'][0]['spoken_index']=6
        repair['source_stress_projection']=unit['synthesis_text']
        heard='Наш неохотник до борзых. Брат ихний охоч…';evidence=[]
        for role,family in [('primary','qwen'),('witness','whisper')]:
            evidence.append({'path':role,'sha256':'d'*64,'role':role,'family':family,'verifier':{},
                             'raw':m.transcript_check(source,heard,'a'*64,'b'*64)})
        result=m.strict_decision(unit,evidence,source,repair)
        self.assertTrue(result['passed']);self.assertTrue(result['equivalences'])
        self.assertEqual(result['evidence'][0]['comparison']['literal_target_alignment'],[
            {'source_index':6,'heard_index':5,'source_word':'ихний','heard_word':'ихний'}])
        for bad in ('Наш неохотник до борзых. Брат их не охоч…','Наш неохотник до борзых ихний. Брат охоч…',
                    'Наш неохотник до борзых. Брат ихний ихний охоч…'):
            self.assertIsNone(m.literal_target_alignment(unit,bad,source,repair,'a'*64,'b'*64))

    def test_extra_target_word_cannot_hide_in_an_unrelated_authorized_name_slot(self):
        source='Брат ихний рад, Ксарвик.';heard='Брат ихний рад, ихний.'
        unit={'text':source,'spoken':source,'synthesis_text':'Брат и́хний рад, Ксарвик.'}
        repair=copy.deepcopy(self.repair);repair['source_stress_projection']=unit['synthesis_text']
        evidence=[{'path':role,'sha256':'d'*64,'role':role,'family':family,'verifier':{},
                   'raw':m.transcript_check(source,heard,'a'*64,'b'*64)} for role,family in [('primary','qwen'),('witness','whisper')]]
        baseline=m.words.strict_decision(unit,evidence,source,repair)
        self.assertTrue(baseline['passed']);self.assertTrue(baseline['tolerated_deviations'])
        self.assertFalse(m.strict_decision(unit,evidence,source,repair)['passed'])
        self.assertIsNone(m.literal_target_alignment(unit,heard,source,repair,'a'*64,'b'*64))

    def test_closed_parent_requires_all_three_failed_stages_and_keeps_snapshot_immutable(self):
        journal=self.folder/'parent.json';log=self.folder/'parent.log';log.write_text('Actual failed source gate',encoding='utf-8')
        self.spec['prior_supervision']=journal.relative_to(ROOT).as_posix()
        value={'word_context_units':{self.unit['id']:{'status':'held','attempts':{}}},'stages':{}}
        for number in (1,2,3):
            value['word_context_units'][self.unit['id']]['attempts'][str(number)]={'status':'held','finished_unix':123.,'reason':'failed gate'}
            command=['python',str(Path(m.parent_driver.__file__)),'--folder',str(self.folder),'--unit',self.unit['id'],'--attempt',str(number),'--internal-gate','context']
            value['stages'][str(number)]={'name':'word-context-'+self.unit['id']+'-a'+str(number)+'-context-gate',
                'folder':str(self.folder),'status':'failed','command':command,
                'attempts':[{'status':'failed','exit_code':1,'log':str(log),'identity':{'command':command}}]}
        bad=copy.deepcopy(value);bad['word_context_units'][self.unit['id']]['attempts']['3']['status']='running';m.immutable(journal,bad)
        with self.assertRaises(ValueError):m.closed_parent_snapshot(self.folder,self.unit,self.spec)
        journal.unlink();m.immutable(journal,value);first=m.closed_parent_snapshot(self.folder,self.unit,self.spec)
        journal.write_text('{}',encoding='utf-8')
        self.assertEqual(m.closed_parent_snapshot(self.folder,self.unit,self.spec),first)

    def test_negative_native_quality_does_not_demand_asr_but_safe_native_does(self):
        def native(folder,unit,attempt):return {},{'request':{}},{'quality_passed':attempt==2}
        def execution(folder,base,spec,receipt):return None if receipt['quality_passed'] else {'failed_stage':'native-quality'}
        decision={'passed':False,'evidence':[{'raw':'retained'}]}
        for number in (1,2,3):
            base=m.predecessor.attempt_home(self.folder,self.unit,number)
            m.immutable(base/'word-controller-context-gate.json',{'controller_sha256':m.file_digest(Path(m.parent_driver.__file__)),
                'source_unit':self.unit['id'],'attempt':number,'alignment_is_approval':False,'decision':decision})
        with patch.object(m.predecessor,'native_receipt',side_effect=native),patch.object(m,'execution_proof',side_effect=execution),\
             patch.object(m.predecessor,'blind_decision',return_value=decision) as blind:
            rows=m.failed_context_attempts(self.folder,self.unit);self.assertEqual(blind.call_count,1)
            self.assertEqual([r['failed_stage'] for r in rows],['native-quality','context','native-quality'])
            (m.predecessor.attempt_home(self.folder,self.unit,2)/'word-controller-context-gate.json').unlink()
            with self.assertRaises(OSError):m.failed_context_attempts(self.folder,self.unit)

    def test_repeated_native_and_occupied_new_slot_are_not_a_new_budget(self):
        choices=self.candidates();old=self.folder/'takes'/self.unit['id']/'take-18.json';m.immutable(old,{'spec':{'request':{'text':choices[0]['synthesis_text']}}})
        with self.assertRaisesRegex(ValueError,'repeats'):m.no_repeated_native(self.folder,self.unit,choices)
        old.unlink();m.immutable(old.with_name('take-21.json'),{'spec':{'other_marker':{}}})
        with self.assertRaisesRegex(ValueError,'occupied'):m.no_repeated_native(self.folder,self.unit,choices)

    def test_registered_own_crop_revalidates_without_reclassifying_it_as_a_prior_strategy(self):
        choices=self.candidates();base=m.home(self.folder,self.unit);m.immutable(base/'campaign.json',{'fixed':'campaign'})
        spec={'source':self.unit,'request':{'id':self.unit['id']+'-boundary-context-1','text':choices[0]['synthesis_text']},
              'boundary_punctuation_context_recovery':{'policy':m.POLICY,'attempt':1,'campaign_sha256':m.file_digest(base/'campaign.json'),
                'implementation_sha256':m.file_digest(m.SELF),'context':choices[0]}}
        receipt={'unit':self.unit['id'],'take':21,'spec':spec};private=m.receipt_path(self.folder,self.unit,1);m.immutable(private,receipt)
        canonical=self.folder/'takes'/self.unit['id']/'take-21.json';canonical.parent.mkdir(parents=True);canonical.write_bytes(private.read_bytes())
        m.no_repeated_native(self.folder,self.unit,choices)
        private.write_bytes(b'different private receipt')
        with self.assertRaisesRegex(ValueError,'byte-bound'):m.no_repeated_native(self.folder,self.unit,choices)

    def test_source_blind_decoder_contract_and_new_implementation_hash(self):
        bound={'language':'Russian','qwen_files':{}}
        torch=m.importlib.metadata.version('torch');transformers=m.importlib.metadata.version('transformers')
        old=m.words.verifier_config(bound,'turbo',torch,transformers);new=m.verifier_config(bound,'turbo',torch,transformers)
        self.assertEqual({k:v for k,v in old.items() if k not in ('policy','implementation_sha256')},
                         {k:v for k,v in new.items() if k not in ('policy','implementation_sha256')})
        self.assertFalse(new['expected_text_given_to_recognizer']);self.assertEqual(new['implementation_sha256'],m.file_digest(m.SELF))
        raw=m.transcript_check(self.unit['spoken'],self.unit['spoken'],'a'*64,m.renderer.sha(new))
        with self.assertRaises(ValueError):m.check_record({**raw,'verifier':{**new,'expected_text_given_to_recognizer':True}},self.unit['spoken'],'a'*64,bound,'turbo')

    def test_actual_pcm_crop_cannot_be_changed_even_with_rewritten_hash_and_both_gates_required(self):
        import numpy as np
        import soundfile as sf
        chosen=self.candidates()[0];base=m.attempt_home(self.folder,self.unit,1)
        m.immutable(base/'native-receipt.json',{'fixture':'native'});m.immutable(base/'alignment.json',{'fixture':'coordinates'})
        words=m.word_tokens(chosen['spoken']);audio=np.zeros((len(words)+1)*1000,dtype=np.int16)
        cues=[]
        for index,word in enumerate(words):
            audio[index*1000+200:index*1000+500]=1000+index
            cues.append({'text':word,'start':index+.2,'end':index+.5})
        native_path=base/'full.wav';sf.write(native_path,audio,1000,subtype='PCM_16')
        spec={'source':self.unit,'boundary_punctuation_context_recovery':{'attempt':1,'context':chosen}}
        native={'audio':native_path.relative_to(self.folder).as_posix(),'audio_sha256':m.file_digest(native_path),'quality_passed':True}
        bound={'word_campaign_sha256':'a'*64,'word_proposal_sha256':'b'*64,'word_repair':self.repair,'word_stress':{}}
        m.immutable(m.home(self.folder,self.unit)/'campaign.json',{'fixture':'campaign'})
        quality={'fixture':'boundary checks held fixed'}
        with patch.object(m,'native_receipt',return_value=(bound,spec,native)),patch.object(m,'validate_alignment',return_value=cues),\
             patch.object(m.renderer,'quality',return_value=quality),patch.object(m,'blind_decision',return_value={'passed':True}),\
             patch.object(m.words,'campaign',return_value={'dictionary':[]}),patch.object(m.words,'validated_proposal',return_value={'providers':[]}),\
             patch.object(m,'experiment_provenance',return_value={'fixture':'review hashes'}):
            m.crop(self.folder,self.unit,1);receipt=m.read(m.receipt_path(self.folder,self.unit,1))
            self.assertEqual(receipt['take'],21)
            proof=m.validate_boundary_context_receipt(self.folder,self.unit,receipt)
            self.assertFalse(proof['alignment_is_approval']);self.assertFalse(proof['human_approval'])
            for failed in ('context','crop'):
                with patch.object(m,'blind_decision',side_effect=lambda f,u,a,k:{'passed':k!=failed}):
                    with self.assertRaisesRegex(ValueError,'recognition disagreements'):m.validate_boundary_context_receipt(self.folder,self.unit,receipt)
            child_path=self.folder/receipt['audio'];child,rate=sf.read(child_path,dtype='int16');child[len(child)//2]+=1
            sf.write(child_path,child,rate,subtype='PCM_16');receipt['audio_sha256']=m.file_digest(child_path)
            m.renderer.write(m.receipt_path(self.folder,self.unit,1),receipt)
            with self.assertRaisesRegex(ValueError,'exact unprocessed native PCM'):m.validate_boundary_context_receipt(self.folder,self.unit,receipt)


if __name__=='__main__':unittest.main()
