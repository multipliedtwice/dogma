"""V17 independent final checks, append-only registration and public privacy."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import assemble_public_demo_ellipsis_recovery_v2 as helper
import resolve_public_demo_ellipsis_assembly_v2 as adapter
from verification import test_public_demo_ellipsis_assembly_v2 as fixtures
from package_public_audiobook_demos import clean_json


class CompositeAdapter(unittest.TestCase):
    def fixture(self):
        case=fixtures.EllipsisAssembly();case.setUp();self.addCleanup(case.doCleanups)
        bound,children=case.assembly_fixture();unit=case.unit;folder=case.folder
        context=adapter.Context.__new__(adapter.Context);context.folder=folder;context.generation='frozen';context.assignment={'language':'Russian','units':[unit]}
        context.blocks={'p':' '*unit['start']+unit['text']};context.manifest={'speech_model':bound['original_spec']['model'],'runner':bound['original_spec']['runner']}
        context.dependencies={};context.bindings={};context.ellipsis_assembly_v2_dependencies={};context.english_policy=None;context.recheck=Mock()
        with patch.object(helper,'campaign',return_value=bound),patch.object(helper,'validate_child',side_effect=lambda f,u,c,a:children[c-1]),\
             patch.object(helper.renderer,'quality',return_value={'duration':2.04}):
            receipt=helper.assemble(folder,unit,1,1,1)
        private=helper.receipt_path(folder,unit,1)
        def decision(target,repair,audio,kind):
            evidence=[]
            heard=target['spoken'].replace('цуцык','цуцик')
            for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
                config={'model':family,'expected_text_given_to_recognizer':False};raw=helper.transcript_check(target['spoken'],heard,audio,helper.renderer.sha(config))
                path=private.parent/(kind+'-'+recognizer+'.json');helper.immutable(path,{**raw,'verifier':config})
                evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':helper.file_digest(path),'raw':raw,'verifier':config,'role':role,'family':family})
            value=helper.words.strict_decision(target,evidence,context.blocks['p'],repair);self.assertTrue(value['passed']);return value
        parent=decision(unit,case.repair,receipt['audio_sha256'],'parent')
        for child in children:
            chosen=case.partition['children'][child['child']-1];target={'text':chosen['text'],'spoken':chosen['spoken']}
            for kind in ('full_native_decision','crop_decision'):child[kind]=decision(target,chosen['repair'],child['audio_sha256'],str(child['child'])+'-'+kind)
        receipt['spec_sha256']=helper.renderer.sha(receipt['spec'])
        helper.renderer.write(private,receipt)
        proof={'policy':helper.POLICY,'receipt_sha256':helper.file_digest(private),'audio_origin':'exact-pcm-composite',
            'assembled_audio_sha256':receipt['audio_sha256'],'physical_parent_native_calls':0,'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},
            'source_partition':case.partition,'campaign_sha256':'a'*64,'child_proofs':children,'gap':receipt['spec']['ellipsis_phrase_assembly_v2']['gap'],
            'decision':parent,'normative_evidence':{'private_data':'C:/PRIVATE/training-work/source.json'},'word_campaign_sha256':'b'*64,
            'word_proposal_sha256':'c'*64,'word_stress_sha256':'d'*64,'execution_inventory_at_assembly':[],'human_approval':False,
            'acoustic_stress_approval':False,'source_and_captions_unchanged':True,'alignment_is_approval':False,'canonical_registration':False,
            'v1_no_audio':bound['v1_no_audio'],'alternative_authorizations':[],'original_spec':bound['original_spec'],'provider_request':{'body':'PRIVATE REQUEST'},'protected_labels':'manual-labeling/private.json'}
        return context,unit,receipt,private,proof,bound,children
    def guards(self,fixture,proof=None):
        context,unit,receipt,path,original,bound,children=fixture
        proof=original if proof is None else proof
        return patch.multiple(helper,validate_ellipsis_assembly_receipt=Mock(return_value=proof),assembly_receipt=Mock(return_value=(bound,receipt)),
            validate_child=Mock(side_effect=lambda f,u,c,a:children[c-1]),blind_decision=Mock(return_value=proof['decision']),
            check_record=Mock(side_effect=lambda check,*args:{k:v for k,v in check.items() if k!='verifier'}))
    def test_independent_final_source_recompute_and_safe_public_projection(self):
        f=self.fixture();context,unit,receipt,_,proof,_,_=f;before=copy.deepcopy(proof)
        with self.guards(f):decision,public=adapter.validated_assembly(context,unit,receipt)
        self.assertTrue(decision['passed']);clean_json(public);self.assertEqual(proof,before)
        serialized=str(public)
        for private in ('C:/PRIVATE','PROTECTED REFERENCE','PRIVATE REQUEST','palette_reference','provider_request','protected_labels','manual-labeling'):
            self.assertNotIn(private,serialized)
        self.assertEqual(public['physical_parent_native_calls'],0);self.assertEqual(public['audio_origin'],'exact-pcm-composite')
        self.assertEqual(public['final_full_source_decision']['evidence'][0]['raw'],decision['evidence'][0]['raw'])
        for leak in ({'provider_request':{'body':'secret'}},{'leak':'C:/PRIVATE/models/model.gguf'}):
            with self.assertRaises(ValueError):clean_json({**public,**leak})
    def test_forged_parent_request_or_single_final_pass_rejected(self):
        f=self.fixture();context,unit,receipt,_,proof,_,_=f
        bad=copy.deepcopy(receipt);bad['spec']['request']={'text':unit['spoken']};bad['spec_sha256']=adapter.base.sha(bad['spec'])
        with self.guards(f),self.assertRaises(ValueError):adapter.validated_assembly(context,unit,bad)
        for mutate in (lambda d:d['evidence'].pop(),lambda d:d['evidence'][0]['comparison'].update(passed=False),
                       lambda d:d.update(passed=False),lambda d:d['evidence'][0]['raw'].update(heard='Свет пропал')):
            bad=copy.deepcopy(proof);mutate(bad['decision'])
            with self.guards(f,bad),self.assertRaises(ValueError):adapter.validated_assembly(context,unit,receipt)
    def test_append_only_new_composite_slots_preserve_previous_thirty(self):
        f=self.fixture();context,unit,receipt,path,_,_,_=f;protected={}
        for take in range(30):
            prior=context.folder/'takes'/unit['id']/('take-%02d.json'%take);prior.parent.mkdir(parents=True,exist_ok=True);prior.write_bytes(('prior '+str(take)).encode());protected[prior]=prior.read_bytes()
        with self.guards(f):
            result=adapter.register(context,path);target=context.folder/result['canonical'];self.assertEqual(target.read_bytes(),path.read_bytes());adapter.register(context,path)
            target.write_bytes(b'conflicting composite')
            with self.assertRaises(ValueError):adapter.register(context,path)
        for prior,data in protected.items():self.assertEqual(prior.read_bytes(),data)
    def test_all_prior_receipts_delegate_to_frozen_v16(self):
        context=adapter.Context.__new__(adapter.Context)
        with patch.object(adapter.previous.Context,'assess',return_value={'v16':'unaltered'}) as prior:
            self.assertEqual(context.assess({'id':'old'},{'spec':{}}),{'v16':'unaltered'});prior.assert_called_once()
    def test_publish_and_complete_failure_exit_nonzero(self):
        for stage in ('publish','complete'):
            with patch.object(sys,'argv',['resolver','--folder',str(ROOT),'--stage',stage]),patch.object(adapter,'resolve',return_value={
                'report_sha256':'a'*64,'prefix_units':0,'completion_valid':False,'completion_reason':'held'}):self.assertEqual(adapter.main(),1)


if __name__=='__main__':unittest.main()
