"""Critical CPU-only pronunciation campaign tests. Never invoke CLI/native/ASR."""
import copy
import importlib.metadata
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'),str(ROOT/'runtime')]
import pronounce_public_audiobook_recovery as module
from rimsky.speech_quality import transcript_check,word_tokens


class PronunciationRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.unit = {'id':'u-fixture','text':'He enfolded his Della.','spoken':'He enfolded his Della.',
                     'source_id':'p-fixture','start':0,'end':21,'palette_reference':{'path':'PRIVATE_SENTINEL'}}
        self.unit['spoken_conversion'] = {'source':self.unit['text'],'expanded':self.unit['text'],'changes':[]}
        self.bound = {'target':self.unit,'repair':module.repair(self.unit),'language':'English','qwen_files':{'fixture':'a'*64},
                      'phoneme_evidence':{'phones':{'enfolded':'ɛ_n_f_ˈoʊ_l_d_ᵻ_d','in-folded':'ɪ_n_f_ˈoʊ_l_d_ᵻ_d','unfolded':'ʌ_n_f_ˈoʊ_l_d_ᵻ_d'}},'parents':[], 'context_failures':[],
                      'original_spec':{'request':{'text':self.unit['spoken'],'seed':123,'max_tokens':316,
                                                 'voice_ref':'PRIVATE_SENTINEL','reference_text':'PRIVATE_SENTINEL'},
                                       'source':self.unit,'model':{'path':'model.gguf'},'runner':{'path':'runner.exe'}}}

    def evidence(self,qwen,turbo):
        return [{'path':name+'.json','sha256':'a'*64,'raw':transcript_check(self.unit['spoken'],heard,'b'*64,'c'*64),
                 'verifier':{'expected_text_given_to_recognizer':False},'role':role,'family':family}
                for name,heard,role,family in [('qwen',qwen,'primary','qwen'),('turbo',turbo,'witness','whisper')]]

    def test_exact_single_token_repair_preserves_source_and_every_other_native_character(self):
        before = copy.deepcopy(self.unit)
        result = module.repair(self.unit)
        self.assertEqual(result['synthesis_text'],'He in-folded his Della.')
        self.assertEqual(self.unit,before)
        for text in ('He unfolded his Della.','He enfolded and enfolded.','He Enfolded his Della.'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                module.repair({**self.unit,'spoken':text})

    def test_two_proposals_must_agree_on_exact_only_authorized_repair(self):
        expected = {'source_token':'enfolded','native_token':'in-folded','synthesis_text':'He in-folded his Della.',
                    'dictionary_headword_IPA':'ɪnˈfoʊld','derived_past_IPA':'ɪnˈfoʊldɪd',
                    'accepted':True,'reason':'Candidate preserves prefix, meaning and stress.','audio_approval':False}
        module.validate_answer(expected,self.bound)
        for key,value in [('synthesis_text','He in-folded his dear Della.'),('native_token','unfolded'),
                          ('derived_past_IPA','ʌnˈfoʊldɪd'),('audio_approval',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                module.validate_answer({**expected,key:value},self.bound)
        request = str(module.proposal_request(self.bound))
        self.assertNotIn('PRIVATE_SENTINEL',request);self.assertNotIn('palette_reference',request)

    def test_request_exposes_candidate_phonemes_failures_and_both_rejection_options(self):
        raw = transcript_check(self.unit['spoken'],'He unfolded his Della.','b'*64,'c'*64)
        self.bound['parents'] = [{'receipt':{'take':0},'checks':[{'raw':raw,'sha256':'d'*64}]}]
        request = module.proposal_request(self.bound)
        self.assertEqual(request['request']['candidate']['synthesis_text'],'He in-folded his Della.')
        self.assertEqual(request['request']['phonemizer_evidence']['phones']['in-folded'],'ɪ_n_f_ˈoʊ_l_d_ᵻ_d')
        self.assertEqual(request['request']['recorded_failed_recognition_examples'][0]['heard'],'He unfolded his Della.')
        self.assertEqual(request['request']['dictionary']['derivation']['derived_enfolded_IPA'],'ɪnˈfoʊldɪd')
        self.assertEqual(request['schema']['properties']['accepted'],{'type':'boolean'})
        for provider,_,_ in module.isolated.PROVIDERS:
            rejected = {'accepted':False,'reason':'Independent uncertainty about suitability for this synthesizer.',
                        'source_token':'enfolded','native_token':'in-folded','synthesis_text':'He in-folded his Della.',
                        'dictionary_headword_IPA':'/ɪnˈfoʊld/','derived_past_IPA':'/ɪnˈfoʊldɪd/','audio_approval':False}
            with self.subTest(provider=provider),self.assertRaises(module.ProposalRejected):
                module.validate_answer(rejected,self.bound)

    def test_rejection_is_preserved_and_never_retried_to_persuade(self):
        rejected = {'accepted':False,'reason':'Not convinced.', 'source_token':'enfolded','native_token':'in-folded',
                    'synthesis_text':'He in-folded his Della.','dictionary_headword_IPA':'ɪnˈfoʊld',
                    'derived_past_IPA':'ɪnˈfoʊldɪd','audio_approval':False}
        request = module.proposal_request(self.bound)
        def answer(*args):
            return {'request_sha256':module.file_digest(args[-1]),'answer':rejected,'metadata':{},'audio_approval':False}
        with patch.object(module,'call_provider',side_effect=answer) as call:
            with self.assertRaises(module.ProposalRejected):
                module.evaluate_provider(self.folder,self.unit,self.bound,request,*module.isolated.PROVIDERS[0])
            self.assertEqual(call.call_count,1)
            saved = module.home(self.folder,self.unit)/'codex_cli-attempt-1.answer.json'
            before = saved.read_bytes()
            with self.assertRaisesRegex(ValueError,'no persuasion retry'):
                module.evaluate_provider(self.folder,self.unit,self.bound,request,*module.isolated.PROVIDERS[0])
            self.assertEqual(call.call_count,1);self.assertEqual(saved.read_bytes(),before)
            self.assertFalse((saved.parent/'codex_cli-attempt-2.request.json').exists())

    def test_transport_retry_is_bounded_to_two_and_old_namespace_untouched(self):
        old = self.folder/'pronunciation-recovery'/self.unit['id']/'codex_cli.answer.json'
        old.parent.mkdir(parents=True);old.write_bytes(b'protected v1 answer')
        with patch.object(module,'call_provider',side_effect=OSError('temporary network outage')) as call:
            with self.assertRaisesRegex(ValueError,'two transport attempts'):
                module.evaluate_provider(self.folder,self.unit,self.bound,module.proposal_request(self.bound),*module.isolated.PROVIDERS[0])
            self.assertEqual(call.call_count,2)
            self.assertEqual(old.read_bytes(),b'protected v1 answer')
            failures = list(module.home(self.folder,self.unit).glob('*.failure.json'))
            self.assertEqual(len(failures),2)
            self.assertFalse((old.parent/'codex_cli-attempt-1.request.json').exists())

    def test_one_exact_recognizer_does_not_hide_other_unfolded(self):
        for a,b in [('He unfolded his Della.',self.unit['spoken']),
                    (self.unit['spoken'],'He unfolded his Della.'),
                    ('He enfolded Della.',self.unit['spoken']),
                    ('He enfolded his dear Della.',self.unit['spoken'])]:
            with self.subTest(a=a,b=b):
                result = module.strict_decision(self.unit,self.evidence(a,b),None)
                self.assertFalse(result['passed'])
                self.assertEqual(result['evidence'][0]['raw']['heard'],a)
        self.assertTrue(module.strict_decision(self.unit,self.evidence(self.unit['spoken'],self.unit['spoken']),None)['passed'])

    def test_guarded_name_may_pass_but_both_comparisons_required(self):
        policy = {'fixture':True}
        # Isolate the already-tested English name adapter. This test exercises
        # aggregation: an exact witness cannot conceal/erase the other guard.
        def allowed(unit,heard,policy):
            return {'passed':True,'canonical_words':word_tokens(unit['spoken']),
                    'tolerated_deviations':[{'rule':'proper-noun-slot-v1','source_tokens':['della'],'heard_tokens':['dela']}],
                    'equivalences':[]}
        import resolve_public_demo_recognition as resolver
        with patch.object(resolver,'english_equivalence',side_effect=allowed):
            result = module.strict_decision(self.unit,self.evidence('He enfolded his Dela.',self.unit['spoken']),policy)
            self.assertTrue(result['passed']);self.assertEqual(result['status'],'permitted_tolerance')
            self.assertEqual(result['tolerated_deviations'][0]['rule'],'proper-noun-slot-v1')
            # Even a misbehaving name adapter cannot waive the protected verb.
            self.assertFalse(module.strict_decision(self.unit,self.evidence('He unfolded his Dela.',self.unit['spoken']),policy)['passed'])

    def test_profiles_are_three_distinct_requests_with_unchanged_ref_cap_source(self):
        base = module.home(self.folder,self.unit);base.mkdir(parents=True)
        module.immutable(base/'campaign.json',{'fixture':1});module.immutable(base/'proposal.json',{'fixture':2})
        specs = [module.native_spec(self.folder,self.unit,self.bound,{},n) for n in (1,2,3)]
        self.assertEqual(len({s['request']['seed'] for s in specs}),3)
        self.assertEqual(len({s['request']['id'] for s in specs}),3)
        for spec in specs:
            self.assertEqual(spec['source'],self.unit)
            for key in ('max_tokens','voice_ref','reference_text'):
                self.assertEqual(spec['request'][key],self.bound['original_spec']['request'][key])
            self.assertEqual(spec['request']['text'],'He in-folded his Della.')
        with self.assertRaises(ValueError):module.native_spec(self.folder,self.unit,self.bound,{},4)

    def test_saved_proposal_requires_distinct_exact_provider_and_answer_provenance(self):
        base = module.home(self.folder,self.unit);base.mkdir(parents=True)
        module.immutable(base/'campaign.json',{'fixture':True})
        request = module.proposal_request(self.bound);module.immutable(base/'proposal-request.json',request)
        answer = {'source_token':'enfolded','native_token':'in-folded','synthesis_text':'He in-folded his Della.',
                  'dictionary_headword_IPA':'ɪnˈfoʊld','derived_past_IPA':'ɪnˈfoʊldɪd',
                  'accepted':True,'reason':'Candidate preserves prefix, meaning and stress.','audio_approval':False}
        rows = []
        for provider,model,effort in module.isolated.PROVIDERS:
            cli = base/(provider+'.exe');cli.write_bytes(provider.encode())
            request_path = base/(provider+'-attempt-1.request.json');answer_path = base/(provider+'-attempt-1.answer.json')
            module.immutable(request_path,{**request,'provider':provider,'model':model,'effort':effort})
            module.immutable(answer_path,{'request_sha256':module.file_digest(request_path),'answer':answer,'audio_approval':False,
                                         'metadata':{'provider':provider,'model_used':model,'effort':effort,'cli':str(cli),
                                                     'cli_sha256':module.file_digest(cli),'version':'synthetic1','fallback':False}})
            rows.append({'request':request_path.relative_to(self.folder).as_posix(),'request_sha256':module.file_digest(request_path),
                         'answer':answer_path.relative_to(self.folder).as_posix(),'answer_sha256':module.file_digest(answer_path),'attempt':1,'failures':[]})
        proposal = {'policy':module.POLICY,'campaign_sha256':module.file_digest(base/'campaign.json'),
                    'request_sha256':module.file_digest(base/'proposal-request.json'),'providers':rows,
                    'synthesis_text':answer['synthesis_text'],'audio_approval':False}
        module.immutable(base/'proposal.json',proposal)
        self.assertEqual(module.validated_proposal(self.folder,self.unit,self.bound),proposal)
        for field,value in [('model_used','different-model'),('provider','codex_cli')]:
            target = base/'claude_cli-attempt-1.answer.json';original = module.read(target)
            changed = copy.deepcopy(original);changed['metadata'][field]=value
            module.renderer.write(target,changed)
            changed_proposal = copy.deepcopy(proposal);changed_proposal['providers'][1]['answer_sha256']=module.file_digest(target)
            module.renderer.write(base/'proposal.json',changed_proposal)
            with self.subTest(field=field),self.assertRaises(ValueError):
                module.validated_proposal(self.folder,self.unit,self.bound)
            module.renderer.write(target,original);module.renderer.write(base/'proposal.json',proposal)

    def test_forged_verifier_module_config_or_transcript_hash_rejected(self):
        config = module.verifier_config(self.bound,'qwen',importlib.metadata.version('torch'),importlib.metadata.version('transformers'))
        check = transcript_check(self.unit['spoken'],self.unit['spoken'],'b'*64,module.renderer.sha(config))
        check['verifier'] = config
        module.check_record(check,self.unit['spoken'],'b'*64,self.bound,'qwen')
        for mutate in ('module','blind','hash','extra'):
            changed = copy.deepcopy(check)
            if mutate=='module':changed['verifier']['implementation_sha256']='0'*64
            elif mutate=='blind':changed['verifier']['expected_text_given_to_recognizer']=True
            elif mutate=='hash':changed['audio_sha256']='0'*64
            else:changed['verifier']['prompt']='source text'
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):
                module.check_record(changed,self.unit['spoken'],'b'*64,self.bound,'qwen')

    def test_context_plan_requires_all_full_checks_and_explicit_failed_gate(self):
        def native(folder,unit,attempt):
            return {},{}, {'quality_passed':True,'audio_sha256':str(attempt)*64}
        failed = {'passed':False,'evidence':[]};passed = {'passed':True,'evidence':[]}
        for n in (1,2,3):
            base = module.previous.attempt_home(self.folder,self.unit,n)
            module.immutable(base/'driver-context-gate.json',{'source_unit':self.unit['id'],'attempt':n,'decision':failed})
        with patch.object(module.previous,'native_receipt',side_effect=native),patch.object(module.previous,'blind_decision',return_value=failed):
            self.assertEqual(len(module.context_failures(self.folder,self.unit)),3)
            (module.previous.attempt_home(self.folder,self.unit,3)/'driver-context-gate.json').unlink()
            with self.assertRaises(FileNotFoundError):module.context_failures(self.folder,self.unit)
        with patch.object(module.previous,'native_receipt',side_effect=native),patch.object(module.previous,'blind_decision',return_value=passed):
            with self.assertRaisesRegex(ValueError,'already passes'):module.context_failures(self.folder,self.unit)

    def test_native_receipt_reproduces_request_session_audio_and_quality(self):
        root = module.home(self.folder,self.unit);root.mkdir(parents=True)
        module.immutable(root/'campaign.json',{'fixture':1});module.immutable(root/'proposal.json',{'fixture':2})
        base = module.attempt_home(self.folder,self.unit,1);base.mkdir()
        spec = module.native_spec(self.folder,self.unit,self.bound,{},1)
        sequence = base/'native-request.json';module.immutable(sequence,{'requests':[spec['request']]})
        command = ['runner.exe','--task','tts','--family','higgs_audio_tts','--model','model.gguf','--backend','cuda',
                   '--device','0','--threads','4','--request-sequence',str(sequence),'--out-dir',str(base/'native'),
                   '--out-format','pcm16','--metrics']
        session = {'command':command,'spec':spec,'sequence_sha256':module.file_digest(sequence)}
        module.immutable(base/'native-session.json',session)
        audio = base/'fixture.wav';audio.write_bytes(b'synthetic audio')
        receipt = {'unit':self.unit['id'],'take':9,'spec':spec,'spec_sha256':module.renderer.sha(spec),
                   'quality_passed':True,'quality':{'duration':2},'audio':audio.relative_to(self.folder).as_posix(),
                   'audio_sha256':module.file_digest(audio)}
        module.immutable(module.receipt_path(self.folder,self.unit,1),receipt)
        with patch.object(module,'campaign',return_value=self.bound),patch.object(module,'validated_proposal',return_value={}),\
             patch.object(module.renderer,'quality',return_value={'duration':2}):
            self.assertEqual(module.native_receipt(self.folder,self.unit,1)[2],receipt)
            changed = copy.deepcopy(receipt);changed['spec']['request']['text']='He unfolded his Della.'
            module.renderer.write(module.receipt_path(self.folder,self.unit,1),changed)
            with self.assertRaisesRegex(ValueError,'request/source/attempt'):module.native_receipt(self.folder,self.unit,1)
            module.renderer.write(module.receipt_path(self.folder,self.unit,1),receipt)
            module.renderer.write(sequence,{'requests':[{**spec['request'],'text':'He unfolded his Della.'}]})
            with self.assertRaisesRegex(ValueError,'request/session'):module.native_receipt(self.folder,self.unit,1)
            module.renderer.write(sequence,{'requests':[spec['request']]})
            audio.write_bytes(b'tampered')
            with self.assertRaises(ValueError):module.native_receipt(self.folder,self.unit,1)


if __name__=='__main__':unittest.main()
