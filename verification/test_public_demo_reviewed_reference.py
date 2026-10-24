"""Private reference eligibility uses synthetic source/audio/review fixtures."""
import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import public_demo_reviewed_reference as m
import context_public_demo_reviewed_reference_recovery as helper

class Eligibility(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.bucket={'voice_family':'masculine_3','mood':'neutral'}
        self.decision={'rejected':False,'voice_verdict':'no','corrected_voice_id':'masculine_3','mood_verdict':'yes'}
        self.label={'id':'fixture','decision':self.decision,'voice_family':'masculine_3','mood':'neutral','training_acceptance_implied':False}
        self.review={'decisions':{'fixture':self.decision}}
    def test_human_correction_and_confirmed_mood_never_imply_training(self):
        proof=m.human_row(self.review,self.label,self.bucket)
        self.assertTrue(proof['same_human_bucket']);self.assertFalse(proof['training_acceptance_implied'])
    def test_wrong_bucket_unreviewed_prediction_or_rejection_cannot_supply_voice(self):
        for changed in ({'voice_verdict':'yes'},{'corrected_voice_id':'masculine_5'},{'mood_verdict':'no'},{'rejected':True}):
            decision={**self.decision,**changed};label={**self.label,'decision':decision}
            with self.subTest(changed=changed),self.assertRaises(ValueError):m.human_row({'decisions':{'fixture':decision}},label,self.bucket)
        with self.assertRaises(ValueError):m.human_row(self.review,{**self.label,'decision':{}},self.bucket)
    def audio(self):
        data=np.zeros(4000,dtype=np.int16);data[1100:1300]=1000;data[2000:2300]=-1500
        parent=self.root/'parent.wav';bank=self.root/'bank.wav';sf.write(parent,data,24000,subtype='PCM_16');sf.write(bank,data[500:3000],24000,subtype='PCM_16')
        span={'sample_rate_hz':24000,'start_sample':500,'end_sample_exclusive':3000,'archive_sample_count':4000,'bank_sample_count':2500}
        return bank,parent,span,data
    def test_alternate_must_be_exact_native_archive_samples(self):
        bank,parent,span,data=self.audio();self.assertEqual(m.pcm_slice(bank,parent,span)['kind'],'exact-archive-pcm16-slice')
        for edit in ({'start_sample':501},{'end_sample_exclusive':2999},{'sample_rate_hz':16000}):
            with self.assertRaises(ValueError):m.pcm_slice(bank,parent,{**span,**edit})
        child=data[500:3000].copy();child[700]+=1;sf.write(bank,child,24000,subtype='PCM_16')
        with self.assertRaises(ValueError):m.pcm_slice(bank,parent,span)
    def test_no_gain_fade_resampling_or_different_audio_format_relation(self):
        bank,parent,span,data=self.audio();sf.write(bank,data[500:3000],16000,subtype='PCM_16')
        with self.assertRaises(ValueError):m.pcm_slice(bank,parent,span)
        sf.write(bank,data[500:3000],24000,subtype='PCM_24')
        with self.assertRaises(ValueError):m.pcm_slice(bank,parent,span)
    def test_private_path_containment_and_hash_checks(self):
        with patch.object(m,'ROOT',self.root):
            self.assertEqual(m.local('reference.wav'),self.root/'reference.wav')
            with self.assertRaises(ValueError):m.local('../outside.wav')
            p=self.root/'reference.wav';p.write_bytes(b'private exact media')
            with self.assertRaises(ValueError):m.checked(p,'0'*64)
    def test_native_request_replaces_actual_reference_but_not_frozen_source(self):
        unit={'id':'u0071','palette_reference':{'path':'OLD','text':'PRIVATE OLD','audio_sha256':'a'*64}}
        chosen={'id':'fixture','synthesis_text':'Он с цига́ркой. Она светится.'}
        reference={'path':'PRIVATE ALTERNATIVE.wav','text':'PRIVATE REFERENCE WORDS','audio_sha256':'b'*64,'voice_family':'masculine_3','mood':'neutral'}
        bound={'original_spec':{'request':{'seed':7,'text':'original','voice_ref':'OLD','voice_ref_text':'PRIVATE OLD'},'source':copy.deepcopy(unit),'reference':copy.deepcopy(unit['palette_reference']),'effects':'disabled'},
            'attempts':[{'context_id':'fixture','profile':p} for p in helper.PROFILES],'candidates':[chosen],
            'reference_override':{'actual_reference':reference},'word_proposal_sha256':'p','word_repair':{},'normative_evidence':{}}
        before=copy.deepcopy(bound);helper.immutable(helper.home(self.root,unit)/'campaign.json',{})
        spec=helper.native_spec(self.root,unit,bound,1)
        self.assertEqual(spec['source']['palette_reference'],unit['palette_reference']);self.assertEqual(spec['reference'],reference)
        self.assertEqual(spec['request']['voice_ref_text'],reference['text']);self.assertEqual(spec['request']['voice_ref'],reference['path'])
        self.assertEqual(spec['request']['text'],chosen['synthesis_text']);self.assertEqual(bound,before)
        changed=copy.deepcopy(bound);changed['reference_override']['actual_reference']['text']='DIFFERENT PRIVATE WORDS'
        self.assertNotEqual(helper.renderer.sha(spec),helper.renderer.sha(helper.native_spec(self.root,unit,changed,1)))
        with self.assertRaises(ValueError):helper.native_spec(self.root,unit,bound,4)

if __name__=='__main__':unittest.main()
