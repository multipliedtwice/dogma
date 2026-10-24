"""V14 adapter gates, raw provenance and public projection; no synthesis/ASR."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_demo_line_wrap_recovery as helper
import resolve_public_demo_line_wrap as adapter
from verification import test_public_demo_boundary_recovery as helper_tests
from package_public_audiobook_demos import clean_json


class BoundaryAdapter(unittest.TestCase):
    def fixture(self):
        case=helper_tests.BoundaryExperiment();case.setUp();self.addCleanup(case.doCleanups)
        unit=case.unit;folder=case.folder;chosen=helper.candidate(case.unit,case.repair,case.spec);repair=case.repair
        context=adapter.Context.__new__(adapter.Context);context.folder=folder;context.generation='frozen'
        context.assignment=case.assignment;context.blocks=case.blocks;context.manifest={'speech_model':{'sha256':'model'},'runner':{'sha256':'runner'}}
        context.dependencies={};context.bindings={};context.line_wrap_dependencies={'new':'pinned'};context.english_policy=None;context.recheck=Mock()
        native_path=folder/'native.wav';native_path.write_bytes(b'synthetic native PCM fixture');native_sha=adapter.base.file_digest(native_path)
        crop_path=folder/'crop.wav';crop_path.write_bytes(b'synthetic cropped PCM fixture');crop_sha=adapter.base.file_digest(crop_path)
        experiment={k:'f'*64 for k in ('campaign_sha256','proposal_sha256','word_campaign_sha256','word_proposal_sha256',
            'dictionary_sha256','stress_sha256','prior_context_campaign_sha256','closed_parent_sha256')}
        experiment.update(primary_evidence={'source_url':helper.PRIMARY_URL,'quote':helper.PRIMARY_QUOTE,'raw_sha256':helper.PRIMARY_RAW_SHA,'inference':'General transfer rule, no TTS claim.'},tts_interpretation='unknown',policy=helper.POLICY,candidate_id=chosen['id'],candidate_choice=1,native_before=chosen['native_before'],
            native_after=chosen['synthesis_text'],native_edits=chosen['native_edits'],letter_digit_stress_map=chosen['letter_digit_stress_map'],
            source_text_unchanged=True,original_spoken_unchanged=True,human_approval=False,acoustic_stress_approval=False,
            audio_approval=False,scope='Synthetic reviewed native hypothesis.',providers=[])
        for provider,model,effort in helper.isolated.PROVIDERS:
            experiment['providers'].append({'provider':provider,'model':model,'effort':effort,'request_sha256':'d'*64,'answer_sha256':'e'*64,
                                           'accepted':True,'audio_approval':False,'provider_request':{'PRIVATE':'C:/PRIVATE/training-work/raw.txt'}})
        spec={'source':unit,'generation':'frozen','model':context.manifest['speech_model'],'runner':context.manifest['runner'],
              'reference':unit['palette_reference'],'effects':'disabled','request':{'text':chosen['synthesis_text']},
              'line_wrap_recovery':{'policy':helper.POLICY,'attempt':1,'limit':3,'context':chosen,
                                                       'experiment_proposal_sha256':experiment['proposal_sha256']}}
        receipt={'unit':unit['id'],'take':27,'spec':spec,'spec_sha256':adapter.base.sha(spec),'quality_passed':True,
                 'audio':crop_path.relative_to(folder).as_posix(),'audio_sha256':crop_sha}
        private=helper.receipt_path(folder,unit,1);adapter.base.immutable(private,receipt)
        decisions={}
        for kind,expected,audio in [('context',chosen['spoken'],native_sha),('crop',unit['spoken'],crop_sha)]:
            evidence=[]
            for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
                config={'model':family,'expected_text_given_to_recognizer':False,'local_files_only':True}
                raw=adapter.base.transcript_check(expected,expected,audio,adapter.base.sha(config))
                path=private.parent/(kind+'-'+recognizer+'.json');adapter.base.immutable(path,{**raw,'verifier':config})
                evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(path),'raw':raw,
                                 'verifier':config,'role':role,'family':family})
            candidate=copy.deepcopy(repair)
            target=unit;paragraph=context.blocks[unit['source_id']]
            if kind=='context':
                target={'text':chosen['source_text'],'spoken':expected}
                for p in candidate['patches']:p['spoken_index']+=chosen['target_word_start']
                candidate['source_stress_projection']=chosen['source_stress_projection'];paragraph=chosen['source_text']
            decisions[kind]=helper.strict_decision(target,evidence,paragraph,candidate);self.assertTrue(decisions[kind]['passed'])
        proof={'policy':helper.POLICY,'receipt_sha256':adapter.base.file_digest(private),'native_audio_sha256':native_sha,
            'crop_audio_sha256':crop_sha,'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},
            'alignment_is_approval':False,'human_approval':False,'source_spans':chosen['source_spans'],'crop':{'start_sample':1,'end_sample':20},
            'crop_is_entire_native_pcm':False,
            'campaign_sha256':'f'*64,'word_provenance':{'repair':repair},'experiment_provenance':experiment,
            'full_context_decision':decisions['context'],'target_decision':decisions['crop'],
            'original_spec':{'reference_text':'PROTECTED REFERENCE','model_path':'C:/PRIVATE/models/model.gguf'},
            'provider_request':{'system':'PRIVATE CLI REQUEST'},'protected_labels':{'review_json':'manual-labeling/private.json'}}
        bound={'word_repair':repair,'candidates':[chosen],'stress_witness':{'enabled':False}}
        return context,unit,receipt,private,proof,bound,{'audio_sha256':native_sha},experiment

    def guards(self,fixture,proof=None,decision=None):
        context,unit,receipt,path,saved,bound,native,experiment=fixture;proof=proof or saved
        def audio(folder,target,attempt,kind):
            expected=unit['spoken'] if kind=='crop' else bound['candidates'][0]['spoken']
            return bound,expected,'audio',receipt['audio_sha256'] if kind=='crop' else native['audio_sha256']
        return patch.multiple(helper,validate_line_wrap_receipt=Mock(return_value=proof),
            native_receipt=Mock(return_value=(bound,receipt['spec'],native)),experiment_provenance=Mock(return_value=experiment),
            verification_audio=Mock(side_effect=audio),check_record=Mock(side_effect=lambda check,*args:{k:v for k,v in check.items() if k!='verifier'}),
            blind_decision=Mock(side_effect=lambda f,u,a,k:decision or proof['full_context_decision' if k=='context' else 'target_decision']))

    def test_bound_source_and_all_four_raw_checks_with_public_only_projection(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_,_=fixture
        before=copy.deepcopy(proof)
        with self.guards(fixture):decision,public=adapter.validated_line_wrap(context,unit,receipt)
        self.assertTrue(decision['passed']);clean_json(public)
        serialized=str(public)
        for private in ('C:/PRIVATE','PROTECTED REFERENCE','PRIVATE CLI REQUEST','palette_reference','model_path','provider_request','protected_labels','manual-labeling'):
            self.assertNotIn(private,serialized)
        self.assertEqual(public['full_context_evidence']['evidence'][0]['raw'],proof['full_context_decision']['evidence'][0]['raw'])
        self.assertEqual(public['experiment_provenance']['native_after'],receipt['spec']['request']['text'])
        self.assertEqual(proof,before);self.assertFalse(public['human_approval']);self.assertFalse(public['acoustic_stress_approval'])
        for leak in ({'provider_request':{'body':'data'}},{'leak':'C:/PRIVATE/models/model.gguf'}):
            with self.assertRaises(ValueError):clean_json({**public,**leak})

    def test_false_context_crop_or_forged_literal_alignment_cannot_register(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_,_=fixture
        for key in ('full_context_decision','target_decision'):
            bad=copy.deepcopy(proof);bad[key]['evidence'][0]['comparison']['passed']=False
            with self.guards(fixture,bad),self.assertRaises(ValueError):adapter.validated_line_wrap(context,unit,receipt)
            bad=copy.deepcopy(proof);bad[key]['evidence'][0]['comparison']['literal_target_alignment']=[]
            with self.guards(fixture,bad),self.assertRaises(ValueError):adapter.validated_line_wrap(context,unit,receipt)
        with self.guards(fixture,decision={'passed':False}),self.assertRaisesRegex(ValueError,'strict original-source'):
            adapter.validated_line_wrap(context,unit,receipt)

    def test_original_source_model_effects_and_exact_review_binding_are_required(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_,_=fixture;badrows=[]
        for key,value in [('source',{**unit,'text':'Changed'}),('model',{'sha256':'wrong'}),('effects','enabled')]:
            bad=copy.deepcopy(receipt);bad['spec'][key]=value;badrows.append(bad)
        for attempt in (0,4):
            bad=copy.deepcopy(receipt);bad['spec']['line_wrap_recovery']['attempt']=attempt;badrows.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['historical_word_context_recovery']={};badrows.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['line_wrap_recovery']['experiment_proposal_sha256']='wrong';badrows.append(bad)
        for bad in badrows:
            bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with self.subTest(bad=bad),self.guards(fixture),self.assertRaises(ValueError):adapter.validated_line_wrap(context,unit,bad)
        evidence=proof['target_decision']['evidence'][0];(context.folder/evidence['path']).write_bytes(b'forged raw transcript')
        with self.guards(fixture),self.assertRaises(ValueError):adapter.validated_line_wrap(context,unit,receipt)

    def test_append_only_registration_never_replaces_prior_twenty_one_slots(self):
        fixture=self.fixture();context,unit,_,path,_,_,_,_=fixture;protected={}
        for take in range(27):
            old=context.folder/'takes'/unit['id']/('take-%02d.json'%take);old.parent.mkdir(parents=True,exist_ok=True)
            old.write_bytes(('retained old evidence '+str(take)).encode());protected[old]=old.read_bytes()
        with self.guards(fixture):
            result=adapter.register(context,path);canonical=context.folder/result['canonical']
            self.assertEqual(canonical.read_bytes(),path.read_bytes());adapter.register(context,path)
            canonical.write_bytes(b'conflicting canonical receipt')
            with self.assertRaisesRegex(ValueError,'Never replace'):adapter.register(context,path)
        for old,raw in protected.items():self.assertEqual(old.read_bytes(),raw)

    def test_old_receipt_assessment_delegates_to_unchanged_v15_context(self):
        context=adapter.Context.__new__(adapter.Context)
        with patch.object(adapter.previous.Context,'assess',return_value={'older':'proof'}) as old:
            self.assertEqual(context.assess({'id':'old'},{'spec':{}}),{'older':'proof'});old.assert_called_once()

    def test_publish_cli_failure_is_nonzero_as_well_as_complete(self):
        for stage in ('publish','complete'):
            with patch.object(sys,'argv',['resolver','--folder',str(ROOT),'--stage',stage]),\
                 patch.object(adapter,'resolve',return_value={'report_sha256':'a'*64,'prefix_units':1,'completion_valid':False,'completion_reason':'held'}):
                self.assertEqual(adapter.main(),1)


if __name__=='__main__':unittest.main()
