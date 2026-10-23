"""CPU-only Russian normative spelling contingency invariants; no synthesis/CLI."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import pronounce_public_audiobook_ru_recovery as module
from rimsky.speech_quality import transcript_check,word_tokens


class RussianPronunciationRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        self.unit={'id':'u-fixture','text':'Из дровяного склада бежит собака.','spoken':'Из дровяного склада бежит собака.',
                   'synthesis_text':'Из дровяно́го склада бежит собака.','source_id':'p-fixture','start':100,'end':131}
        self.bound={'target':self.unit,'repair':module.repair(self.unit),'phoneme_evidence':module.phoneme_evidence(),
                    'language':'Russian','parents':[],'context_failures':[],
                    'original_spec':{'request':{'text':self.unit['synthesis_text'],'max_tokens':400,'seed':123,
                                               'voice_ref':'PRIVATE_SENTINEL','reference_text':'PRIVATE_SENTINEL'},
                                     'source':self.unit,'model':{'path':'model.gguf'},'runner':{'path':'runner.exe'}}}

    def accepted(self):
        return {'accepted':True,'reason':'Normative adjective ending; frozen stress retained.',
                'source_token':'дровяно́го','native_token':'дровяно́во','synthesis_text':self.bound['repair']['synthesis_text'],
                'normative_rule':'adjectival-genitive-ogo-pronounced-vo','stress_retained':True,'audio_approval':False}

    def evidence(self,a,b):
        return [{'path':name+'.json','sha256':'a'*64,'raw':transcript_check(self.unit['spoken'],heard,'b'*64,'c'*64),
                 'role':role,'family':family,'verifier':{}}
                for name,heard,role,family in [('qwen',a,'primary','qwen'),('turbo',b,'witness','whisper')]]

    def test_unique_one_letter_native_repair_exact_source_offsets_and_all_stress_indices(self):
        before=copy.deepcopy(self.unit);repair=module.repair(self.unit)
        self.assertEqual(repair['synthesis_text'],'Из дровяно́во склада бежит собака.')
        self.assertEqual(self.unit,before)
        self.assertEqual([i for i,c in enumerate(repair['synthesis_text']) if c=='́'],repair['stress_indices'])
        self.assertEqual(self.unit['text'][repair['source_unit_start']:repair['source_unit_end']],'дровяного')
        self.assertEqual(repair['absolute_source_start'],103)
        self.assertEqual(sum(a!=b for a,b in zip(self.unit['synthesis_text'],repair['synthesis_text'])),1)
        for value in ('Из дровя́ного склада бежит собака.','Из дровяного склада бежит собака.',
                      'Из дровяно́го дровяно́го склада бежит собака.'):
            with self.subTest(value=value),self.assertRaises(ValueError):module.repair({**self.unit,'synthesis_text':value})

    def test_review_request_has_candidate_normative_facts_and_failure_but_no_palette_paths(self):
        raw=transcript_check(self.unit['spoken'],'Из дровенога склада бежит собака.','b'*64,'c'*64)
        self.bound['parents']=[{'receipt':{'take':0},'checks':[{'raw':raw,'sha256':'d'*64}]}]
        request=module.proposal_request(self.bound)
        self.assertEqual(request['request']['candidate'],self.bound['repair'])
        self.assertEqual(request['request']['recorded_failed_recognition_examples'][0]['heard'],raw['heard'])
        self.assertIn('gramota.ru',str(request['request']['dictionary']))
        self.assertNotIn('PRIVATE_SENTINEL',str(request))
        self.assertEqual(request['schema']['properties']['accepted'],{'type':'boolean'})
        self.assertIs(request['request']['audio_approval'],False)

    def test_acceptance_requires_same_candidate_stress_and_explicit_normative_rule_both_can_reject(self):
        answer=self.accepted();module.validate_answer(answer,self.bound)
        for key,value in [('native_token','дровенога'),('stress_retained',False),('audio_approval',True),
                          ('normative_rule','unrelated'),('synthesis_text','Из дровя́ново склада бежит собака.')]:
            with self.subTest(key=key),self.assertRaises(ValueError):module.validate_answer({**answer,key:value},self.bound)
        for provider,_,_ in module.isolated.PROVIDERS:
            with self.subTest(provider=provider),self.assertRaises(module.ProposalRejected):
                module.validate_answer({**answer,'accepted':False,'reason':'Unsure this is appropriate.'},self.bound)

    def test_native_profiles_are_bounded_to_take12_14_with_unchanged_model_ref_cap_and_original_source(self):
        base=module.home(self.folder,self.unit);base.mkdir(parents=True)
        module.immutable(base/'campaign.json',{'fixture':1});module.immutable(base/'proposal.json',{'fixture':2})
        specs=[module.native_spec(self.folder,self.unit,self.bound,{},n) for n in (1,2,3)]
        self.assertEqual(len({s['request']['seed'] for s in specs}),3)
        self.assertEqual(len({s['request']['id'] for s in specs}),3)
        for number,spec in enumerate(specs,1):
            self.assertEqual(spec['ru_pronunciation_recovery']['attempt'],number)
            self.assertEqual(spec['source'],self.unit);self.assertEqual(spec['model'],self.bound['original_spec']['model'])
            self.assertEqual(spec['request']['text'],self.bound['repair']['synthesis_text'])
            for key in ('max_tokens','voice_ref','reference_text'):
                self.assertEqual(spec['request'][key],self.bound['original_spec']['request'][key])
        with self.assertRaises(ValueError):module.native_spec(self.folder,self.unit,self.bound,{},4)

    def test_original_ordinary_wrong_word_is_not_waived_by_one_correct_witness(self):
        source=self.unit['spoken'];wrong='Из дровенога склада бежит собака.'
        for a,b in [(source,wrong),(wrong,source),(source,'Из дровяного склада бежит.')]:
            with self.subTest(a=a,b=b):
                result=module.strict_decision(self.unit,self.evidence(a,b),source)
                self.assertFalse(result['passed'])
                self.assertEqual(result['evidence'][0]['raw']['heard'],a)
        self.assertTrue(module.strict_decision(self.unit,self.evidence(source,source),source)['passed'])

    def test_ordinary_target_can_never_receive_proper_noun_waiver_even_if_helper_misclassifies(self):
        import resolve_public_demo_recognition as resolver
        def wrong_guard(*args):
            return {'passed':True,'canonical_words':word_tokens(self.unit['spoken']),'equivalences':[],
                    'tolerated_deviations':[{'rule':'proper-noun-slot-v1','source_tokens':['дровяного'],'heard_tokens':['дровенога']}]}
        with patch.object(resolver,'russian_equivalence',side_effect=wrong_guard):
            self.assertFalse(module.strict_decision(self.unit,self.evidence(self.unit['spoken'],self.unit['spoken']),self.unit['spoken'])['passed'])

    def test_rejection_is_immutable_and_never_retried_to_persuade(self):
        request=module.proposal_request(self.bound)
        answer={**self.accepted(),'accepted':False,'reason':'Not convinced.'}
        def reply(*args):return {'request_sha256':module.file_digest(args[-1]),'answer':answer,'metadata':{},'audio_approval':False}
        with patch.object(module,'call_provider',side_effect=reply) as call:
            with self.assertRaises(module.ProposalRejected):module.evaluate_provider(self.folder,self.unit,self.bound,request,*module.isolated.PROVIDERS[0])
            saved=module.home(self.folder,self.unit)/'codex_cli-attempt-1.answer.json';original=saved.read_bytes()
            with self.assertRaisesRegex(ValueError,'no persuasion retry'):module.evaluate_provider(self.folder,self.unit,self.bound,request,*module.isolated.PROVIDERS[0])
            self.assertEqual(call.call_count,1);self.assertEqual(saved.read_bytes(),original)

    def test_all_three_contexts_need_actual_checks_and_failed_gate(self):
        failed={'passed':False,'evidence':[]};passed={'passed':True,'evidence':[]}
        for n in (1,2,3):
            base=module.previous.attempt_home(self.folder,self.unit,n)
            module.immutable(base/'driver-context-gate.json',{'source_unit':self.unit['id'],'attempt':n,'decision':failed})
        with patch.object(module,'validate_context_native_failure',return_value=None),\
             patch.object(module.previous,'native_receipt',return_value=({}, {},{'quality_passed':True,'audio_sha256':'a'*64})),\
             patch.object(module.previous,'blind_decision',return_value=failed):
            self.assertEqual(len(module.context_failures(self.folder,self.unit)),3)
            (module.previous.attempt_home(self.folder,self.unit,3)/'driver-context-gate.json').unlink()
            with self.assertRaises(FileNotFoundError):module.context_failures(self.folder,self.unit)
        with patch.object(module,'validate_context_native_failure',return_value=None),\
             patch.object(module.previous,'native_receipt',return_value=({}, {},{'quality_passed':True,'audio_sha256':'a'*64})),\
             patch.object(module.previous,'blind_decision',return_value=passed):
            with self.assertRaisesRegex(ValueError,'already passes'):module.context_failures(self.folder,self.unit)

    def test_two_proven_negative_quality_attempts_and_one_full_asr_mismatch_are_exhausted(self):
        records={}
        failed={'passed':False,'evidence':[]}
        for number in (1,2,3):
            base=module.previous.attempt_home(self.folder,self.unit,number);base.mkdir(parents=True)
            spec={'request':{'id':'native-'+str(number),'text':'unchanged context','max_tokens':400},
                  'model':{'path':'model.gguf'},'runner':{'path':'runner.exe'}}
            sequence=base/'native-request.json';module.immutable(sequence,{'requests':[spec['request']]})
            command=['runner.exe','--task','tts','--family','higgs_audio_tts','--model','model.gguf','--backend','cuda',
                     '--device','0','--threads','4','--request-sequence',str(sequence),'--out-dir',str(base/'native'),
                     '--out-format','pcm16','--metrics']
            module.immutable(base/'native-session.json',{'command':command,'spec':spec,'sequence_sha256':module.file_digest(sequence)})
            (base/'native.log').write_bytes(b'actual synthetic execution evidence')
            audio=base/'audio.wav';audio.write_bytes(str(number).encode())
            native=base/'native'/(spec['request']['id']+'.wav');native.parent.mkdir();native.write_bytes(audio.read_bytes())
            receipt={'quality_passed':number==2,'audio':audio.relative_to(self.folder).as_posix(),'audio_sha256':module.file_digest(audio)}
            if number!=2:receipt['quality_error']='Silent take cannot be joined' if number==1 else 'Active audio edge requires a retake'
            records[number]=({},spec,receipt)
            module.immutable(base/'native-receipt.json',receipt)
            if number==2:module.immutable(base/'driver-context-gate.json',{'source_unit':self.unit['id'],'attempt':2,'decision':failed})
        def quality(path,*args):
            number=int(Path(path).parent.name[-2:])
            if number!=2:raise ValueError(records[number][2]['quality_error'])
            return {'duration':1}
        with patch.object(module.previous,'native_receipt',side_effect=lambda f,u,a:records[a]),\
             patch.object(module.previous,'blind_decision',return_value=failed) as blind,\
             patch.object(module.renderer,'quality',side_effect=quality):
            results=module.context_failures(self.folder,self.unit)
            self.assertEqual([r['failed_stage'] for r in results],['native-quality','context','native-quality'])
            self.assertEqual(blind.call_count,1)
            self.assertTrue(all(r['files'] and r['validation_failed'] for r in results))
            # A negative label alone is insufficient when actual audio passes.
            with patch.object(module.renderer,'quality',return_value={'duration':1}):
                with self.assertRaisesRegex(ValueError,'actual audio passes'):module.context_failures(self.folder,self.unit)
            # Missing provenance blocks even when all failure labels exist.
            (module.previous.attempt_home(self.folder,self.unit,1)/'native-session.json').unlink()
            with self.assertRaises(FileNotFoundError):module.context_failures(self.folder,self.unit)


if __name__=='__main__':unittest.main()
