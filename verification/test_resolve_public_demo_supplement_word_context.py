"""V13 adapter lineage, independent-gate and append-only invariants; CPU only."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_supplement_word_recovery as helper
import resolve_public_demo_supplement_word_context as adapter


class SupplementContextAdapter(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);folder=Path(temporary.name)
        prefix='Ранее. ';source='Он цуцык добрый.'
        neighbor={'id':'left','source_id':'p','start':0,'end':len(prefix),'text':prefix,'spoken':prefix.strip(),'synthesis_text':prefix.strip()}
        unit={'id':'u0071','source_id':'p','start':len(prefix),'end':len(prefix)+len(source),'text':source,'spoken':source,
              'synthesis_text':source,'palette_reference':{'path':'C:/PRIVATE/training-work/voice.wav'}}
        context=adapter.Context.__new__(adapter.Context);context.folder=folder;context.generation='frozen'
        context.assignment={'language':'Russian','units':[neighbor,unit]};context.blocks={'p':prefix+source}
        context.manifest={'speech_model':{'sha256':'model'},'runner':{'sha256':'runner'}}
        context.dependencies={};context.bindings={};context.supplement_word_context_dependencies={'v13':'pinned'}
        context.english_policy=None;context.recheck=Mock()
        facts=[{'url':'https://orfo.ruslang.ru/abc/fixture','headword':'цу́цик',
                'fact':'Synthetic invariant fixture, not a dictionary quotation.','retrieval_date':'2026-10-23'}]
        start=source.index('цуцык')
        row={k:unit[k] for k in ('id','source_id','start','end')}
        row.update(text_sha256=adapter.base.sha(source),native_before=source,native_after='Он цу́цик добрый.',
                   patches=[{'source_start':len(prefix)+start,'source_end':len(prefix)+start+5,'source_surface':'цуцык',
                             'native_start':start,'native_end':start+5,'before':'цуцык','after':'цу́цик',
                             'kind':'historical-ts-spelling','dictionary_facts':facts}])
        repair=helper.words.repair(unit,row);normative={'dictionary_sha256':adapter.base.sha(facts),'providers':['luna','haiku'],
                                                     'stress_sha256':'stress','audio_approval':False}
        spans=[{k:u[k] for k in ('id','source_id','start','end','text','spoken','synthesis_text')} for u in [neighbor,unit]]
        chosen={'source_spans':spans,'spoken':prefix+source,'source_text':prefix+source,
                'synthesis_text':'Ранее. Он цу́цик добрый.','target_word_start':1,'target_word_end':4}
        native_path=folder/'native.wav';native_path.write_bytes(b'full native PCM fixture')
        native_sha=adapter.base.file_digest(native_path)
        crop_path=folder/'crop.wav';crop_path.write_bytes(b'cropped PCM fixture')
        crop_sha=adapter.base.file_digest(crop_path)
        spec={'source':unit,'generation':'frozen','model':context.manifest['speech_model'],'runner':context.manifest['runner'],
              'reference':unit['palette_reference'],'effects':'disabled','request':{'text':chosen['synthesis_text']},
              'supplement_word_context_recovery':{'policy':helper.POLICY,'attempt':1,'limit':3,'context':chosen,
                                                   'normative_evidence_sha256':adapter.base.sha(normative)}}
        receipt={'unit':unit['id'],'take':24,'spec':spec,'spec_sha256':adapter.base.sha(spec),'quality_passed':True,
                 'audio':crop_path.relative_to(folder).as_posix(),'audio_sha256':crop_sha}
        private=helper.receipt_path(folder,unit,1);adapter.base.immutable(private,receipt)
        decisions={}
        for kind,expected,heard,audio in [('context',prefix+source,'Ранее. Он цуцик добрый.',native_sha),
                                          ('crop',source,'Он цуцик добрый.',crop_sha)]:
            evidence=[]
            for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
                config={'model':family,'expected_text_given_to_recognizer':False,'local_files_only':True}
                raw=adapter.base.transcript_check(expected,heard,audio,adapter.base.sha(config))
                path=private.parent/(kind+'-'+recognizer+'.json');adapter.base.immutable(path,{**raw,'verifier':config})
                evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(path),'raw':raw,
                                 'verifier':config,'role':role,'family':family})
            candidate=copy.deepcopy(repair)
            target=unit;paragraph=context.blocks['p']
            if kind=='context':
                target={'text':prefix+source,'spoken':prefix+source}
                for p in candidate['patches']:p['spoken_index']+=1
                candidate['source_stress_projection']='Ранее. Он цу́цык добрый.'
            decisions[kind]=helper.words.strict_decision(target,evidence,paragraph,candidate)
            self.assertTrue(decisions[kind]['passed'])
        proof={'policy':helper.POLICY,'receipt_sha256':adapter.base.file_digest(private),'native_audio_sha256':native_sha,
               'crop_audio_sha256':crop_sha,'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},
               'alignment_is_approval':False,'human_approval':False,'source_spans':spans,'crop':{'policy':'exact PCM'},
               'campaign_sha256':'campaign','word_provenance':{'repair':repair,'normative_evidence':normative},
               'full_context_decision':decisions['context'],'target_decision':decisions['crop']}
        bound={'word_repair':repair,'normative_evidence':normative}
        return context,unit,receipt,private,proof,bound,{'audio_sha256':native_sha},normative

    def guards(self,fixture,proof=None,decision=None):
        context,unit,receipt,path,saved,bound,native,normative=fixture
        proofs=proof or saved
        def audio(folder,target,attempt,kind):
            expected=unit['spoken'] if kind=='crop' else receipt['spec']['supplement_word_context_recovery']['context']['spoken']
            return bound,expected,'audio',receipt['audio_sha256'] if kind=='crop' else native['audio_sha256']
        return patch.multiple(helper,validate_supplement_word_context_receipt=Mock(return_value=proofs),
             native_receipt=Mock(return_value=(bound,receipt['spec'],native)),normative_evidence=Mock(return_value=normative),
             verification_audio=Mock(side_effect=audio),
             check_record=Mock(side_effect=lambda check,*args:{k:v for k,v in check.items() if k!='verifier'}),
             blind_decision=Mock(side_effect=lambda f,u,a,k:(decision or proofs['full_context_decision' if k=='context' else 'target_decision'])))

    def word_guards(self):
        from contextlib import ExitStack
        stack=ExitStack();stack.enter_context(patch.multiple(helper,approved_campaign=Mock(return_value={}),approved_proposal=Mock(return_value={})))
        stack.enter_context(patch.object(helper.words,'validated_stress',return_value={}))
        return stack

    def test_reproduced_historical_full_and_crop_pair_has_safe_lineage(self):
        fixture=self.fixture();context,unit,receipt,_,_,_,_,_=fixture
        with self.guards(fixture),self.word_guards():decision,proof=adapter.validated_supplement_word_context(context,unit,receipt)
        self.assertTrue(decision['passed']);self.assertEqual(decision['tolerated_deviations'],[])
        self.assertNotIn('PRIVATE',str(proof));self.assertNotIn('palette_reference',str(proof))
        self.assertFalse(proof['acoustic_stress_approval'])

    def test_forged_normative_evidence_or_either_recognition_gate_rejects(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_,_=fixture
        cases=[]
        bad=copy.deepcopy(proof);bad['word_provenance']['normative_evidence']={'different':'facts'};cases.append(bad)
        for key in ('full_context_decision','target_decision'):
            bad=copy.deepcopy(proof);bad[key]['evidence'][0]['comparison']['passed']=False;cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad),self.guards(fixture,bad),self.word_guards(),self.assertRaises(ValueError):
                adapter.validated_supplement_word_context(context,unit,receipt)
        with self.guards(fixture,decision={'passed':False}),self.word_guards(),self.assertRaisesRegex(ValueError,'strict original-source'):
            adapter.validated_supplement_word_context(context,unit,receipt)

    def test_source_model_effects_marker_limit_and_audio_hash_tamper_reject(self):
        fixture=self.fixture();context,unit,receipt,_,_,_,_,_=fixture;cases=[]
        for key,value in [('source',{**unit,'text':'Different'}),('model',{'sha256':'wrong'}),('effects','enabled')]:
            bad=copy.deepcopy(receipt);bad['spec'][key]=value;cases.append(bad)
        for attempt in (0,4):
            bad=copy.deepcopy(receipt);bad['spec']['supplement_word_context_recovery']['attempt']=attempt;cases.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['word_context_recovery']={};cases.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['supplement_word_context_recovery']['normative_evidence_sha256']='wrong';cases.append(bad)
        for bad in cases:
            bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with self.subTest(bad=bad),self.guards(fixture),self.word_guards(),self.assertRaises(ValueError):
                adapter.validated_supplement_word_context(context,unit,bad)
        evidence=fixture[4]['target_decision']['evidence'][0]
        (context.folder/evidence['path']).write_bytes(b'forged transcript file')
        with self.guards(fixture),self.word_guards(),self.assertRaises(ValueError):
            adapter.validated_supplement_word_context(context,unit,receipt)

    def test_append_only_registration_keeps_older_evidence_byte_exact(self):
        fixture=self.fixture();context,unit,receipt,path,_,_,_,_=fixture;protected={}
        for take in range(24):
            old=context.folder/'takes'/unit['id']/('take-%02d.json'%take);old.parent.mkdir(parents=True,exist_ok=True)
            old.write_bytes(('preserved old evidence '+str(take)).encode());protected[old]=old.read_bytes()
        with self.guards(fixture),self.word_guards():
            result=adapter.register(context,path);canonical=context.folder/result['canonical']
            self.assertEqual(canonical.read_bytes(),path.read_bytes());adapter.register(context,path)
            canonical.write_bytes(b'conflicting canonical take')
            with self.assertRaisesRegex(ValueError,'Never replace'):adapter.register(context,path)
        for old,raw in protected.items():self.assertEqual(old.read_bytes(),raw)


if __name__=='__main__':unittest.main()
