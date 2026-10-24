"""Source-exact children, honest composite receipts and strict final gates."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import assemble_public_demo_ellipsis_recovery as m


class EllipsisAssembly(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        text='Свет горит… цуцык спит…'
        self.unit={'id':'fixture','source_id':'p','start':10,'end':10+len(text),'text':text,'spoken':text,'synthesis_text':text,'chapter_id':'c',
            'palette_reference':{'path':'C:/PRIVATE/training-work/reference.wav','text':'PROTECTED REFERENCE'}}
        index=text.index('цуцык');facts=[{'url':'https://orfo.ruslang.ru/abc/fixture','headword':'цу́цик','fact':'Synthetic spelling invariant, no quotation.','retrieval_date':'2026-10-24'}]
        patchrow={'source_start':10+index,'source_end':15+index,'source_surface':'цуцык','native_start':index,'native_end':index+5,
            'before':'цуцык','after':'цу́цик','kind':'historical-ts-spelling','dictionary_facts':facts}
        row={k:self.unit[k] for k in ('id','source_id','start','end')};row.update(text_sha256=m.renderer.sha(text),native_before=text,native_after=text.replace('цуцык','цу́цик'),patches=[patchrow])
        self.repair=m.words.repair(self.unit,row);self.partition=m.partition(self.unit,self.repair)
    def test_source_offsets_ellipsis_and_approved_native_reconstruct_exactly(self):
        p=self.partition;children=p['children'];gap=p['gap']
        self.assertEqual(children[0]['text']+gap['text']+children[1]['text'],self.unit['text'])
        self.assertEqual(children[0]['synthesis_text']+gap['text']+children[1]['synthesis_text'],self.repair['synthesis_text'])
        self.assertTrue(all(c['text'].endswith('…') for c in children));self.assertEqual(children[0]['end'],gap['start']);self.assertEqual(gap['end'],children[1]['start'])
        self.assertEqual(children[1]['repair']['patches'][0]['spoken_index'],0)
        self.assertEqual(children[1]['repair']['source_stress_projection'],'цу́цык спит…')
    def test_unsupported_repair_changed_punctuation_or_empty_child_reject(self):
        for mutate in (lambda r:r.update(synthesis_text=r['synthesis_text'].replace('…',',')),
                       lambda r:r['patches'][0].update(after='су́цик'),lambda r:r['patches'][0].update(dictionary_facts=[])):
            bad=copy.deepcopy(self.repair);mutate(bad)
            with self.assertRaises(ValueError):m.partition(self.unit,bad)
        unit={**self.unit,'text':self.unit['text'].replace('…','.')}
        with self.assertRaises(ValueError):m.partition(unit,self.repair)
    def test_pcm_join_only_inserts_counted_zeros_and_preserves_all_children(self):
        import numpy as np
        left=np.zeros(1000,dtype=np.int16);right=left.copy();left[100:900]=1200;right[100:900]=-1300
        before=[x.copy() for x in (left,right)];combined,gap=m.assemble_pcm([left,right],1000,m.join_policy())
        self.assertEqual(gap['inserted_zero_samples'],40);self.assertFalse(gap['natural_silence_met_target'])
        self.assertTrue(np.array_equal(combined[:1000],left));self.assertTrue(np.array_equal(combined[1040:],right));self.assertFalse(combined[1000:1040].any())
        self.assertTrue(all(np.array_equal(a,b) for a,b in zip(before,(left,right))))
        extra=np.pad(left,(100,100));_,gap=m.assemble_pcm([extra,right],1000,m.join_policy());self.assertEqual(gap['inserted_zero_samples'],0);self.assertTrue(gap['natural_silence_met_target'])
        unsafe=left.copy();unsafe[-30:]=3000
        with self.assertRaises(ValueError):m.assemble_pcm([unsafe,right],1000,m.join_policy())
    def assembly_fixture(self):
        import numpy as np
        import soundfile as sf
        bound={'partition':self.partition,'original_spec':{'source':self.unit,'generation':'frozen','model':{'sha256':'model'},
            'runner':{'sha256':'runner'},'reference':self.unit['palette_reference'],'effects':'disabled','request':{'text':'NOT A PARENT REQUEST'}},
            'join':m.join_policy(),'word_repair':self.repair,'normative_evidence':{},'word_campaign_sha256':'a'*64,
            'word_proposal_sha256':'b'*64,'word_stress':{}}
        children=[]
        for child in (1,2):
            audio=np.zeros(1000,dtype=np.int16);audio[100:900]=1000*child;path=self.folder/('child-'+str(child)+'.wav');sf.write(path,audio,1000,subtype='PCM_16')
            children.append({'child':child,'attempt':1,'source_span':{k:self.partition['children'][child-1][k] for k in ('source_id','start','end','text','spoken')},
                'audio':path.relative_to(self.folder).as_posix(),'audio_sha256':m.file_digest(path),'sample_count':1000,'sample_rate':1000,
                'native_spec_sha256':'a'*64,'native_receipt_sha256':'b'*64,'native_audio_sha256':'c'*64,'child_crop_receipt_sha256':'d'*64,
                'bounds':{'start_sample':1,'end_sample':1001},'human_approval':False,'alignment_is_approval':False,
                'full_native_decision':{'passed':True},'crop_decision':{'passed':True}})
        m.immutable(m.home(self.folder,self.unit)/'campaign.json',{'fixture':'campaign'})
        return bound,children
    def test_honest_parent_pcm_tamper_and_both_final_checks_still_required(self):
        import soundfile as sf
        bound,children=self.assembly_fixture();quality={'duration':2.04,'fixture':'unchanged original final quality'}
        with patch.object(m,'campaign',return_value=bound),patch.object(m,'validate_child',side_effect=lambda f,u,c,a:children[c-1]),\
             patch.object(m.renderer,'quality',return_value=quality),patch.object(m,'blind_decision',return_value={'passed':True}) as gate:
            receipt=m.assemble(self.folder,self.unit,1,1,1)
            self.assertNotIn('request',receipt['spec']);self.assertEqual(receipt['spec']['audio_origin'],'exact-pcm-composite')
            self.assertEqual(receipt['spec']['ellipsis_phrase_assembly']['physical_parent_native_calls'],0)
            proof=m.validate_ellipsis_assembly_receipt(self.folder,self.unit,receipt);self.assertEqual(proof['assembled_audio_sha256'],receipt['audio_sha256'])
            gate.return_value={'passed':False}
            with self.assertRaisesRegex(ValueError,'Both final'):m.validate_ellipsis_assembly_receipt(self.folder,self.unit,receipt)
            gate.return_value={'passed':True}
            path=self.folder/receipt['audio'];audio,rate=sf.read(path,dtype='int16');audio[500]+=1;sf.write(path,audio,rate,subtype='PCM_16')
            receipt['audio_sha256']=m.file_digest(path);m.renderer.write(m.receipt_path(self.folder,self.unit,1),receipt)
            with self.assertRaisesRegex(ValueError,'Composite PCM'):m.assembly_receipt(self.folder,self.unit,1)
    def test_parent_budget_bounds_and_repeated_child_pair_cannot_reset_budget(self):
        bound,children=self.assembly_fixture()
        with patch.object(m,'campaign',return_value=bound),patch.object(m,'validate_child',side_effect=lambda f,u,c,a:children[c-1]),patch.object(m.renderer,'quality',return_value={'duration':2}):
            m.assemble(self.folder,self.unit,1,1,1)
            with self.assertRaisesRegex(ValueError,'Same child pair'):m.assemble(self.folder,self.unit,2,1,1)
            with self.assertRaises(ValueError):m.assemble(self.folder,self.unit,4,1,1)
        for child,attempt in ((3,1),(1,4),(True,1)):
            with self.assertRaises(ValueError):m.child_home(self.folder,self.unit,child,attempt)
    def test_child_quality_and_both_full_and_crop_gates_before_reuse(self):
        bound={'word_repair':self.repair};native={'quality_passed':False}
        with patch.object(m,'native_receipt',return_value=(bound,{},native)),patch.object(m,'blind_decision') as checks:
            with self.assertRaisesRegex(ValueError,'Original child native quality'):m.validate_child(self.folder,self.unit,1,1)
            checks.assert_not_called()
        native['quality_passed']=True
        with patch.object(m,'native_receipt',return_value=(bound,{},native)),patch.object(m,'blind_decision',return_value={'passed':False}):
            with self.assertRaisesRegex(ValueError,'Both full-native'):m.validate_child(self.folder,self.unit,1,1)
    def test_actual_child_pcm_crop_tamper_and_fresh_crop_gate_rejected(self):
        import soundfile as sf
        import numpy as np
        chosen=self.partition['children'][1];base=m.child_home(self.folder,self.unit,2,1);base.mkdir(parents=True)
        m.immutable(base/'native-receipt.json',{'fixture':'native'});m.immutable(base/'alignment.json',{'fixture':'coordinates'})
        audio=np.zeros(3000,dtype=np.int16);audio[200:500]=1000;audio[1200:1500]=1200
        cues=[{'text':w,'start':i+.2,'end':i+.5} for i,w in enumerate(m.word_tokens(chosen['spoken']))]
        path=base/'full.wav';sf.write(path,audio,1000,subtype='PCM_16')
        native={'audio':path.relative_to(self.folder).as_posix(),'audio_sha256':m.file_digest(path),'quality_passed':True}
        spec={'ellipsis_child_recovery':{'context':chosen}};bound={'partition':self.partition}
        with patch.object(m,'native_receipt',return_value=(bound,spec,native)),patch.object(m,'validate_alignment',return_value=cues),\
             patch.object(m.renderer,'quality',return_value={'duration':1.4}),patch.object(m,'blind_decision',return_value={'passed':True}):
            receipt=m.crop_child(self.folder,self.unit,2,1);proof=m.validate_child(self.folder,self.unit,2,1)
            self.assertEqual(proof['audio_sha256'],receipt['audio_sha256'])
            for failure in ('context','crop'):
                with patch.object(m,'blind_decision',side_effect=lambda f,u,a,k,c:{'passed':k!=failure}):
                    with self.assertRaises(ValueError):m.validate_child(self.folder,self.unit,2,1)
            child=self.folder/receipt['audio'];x,rate=sf.read(child,dtype='int16');x[len(x)//2]+=1;sf.write(child,x,rate,subtype='PCM_16')
            receipt['audio_sha256']=m.file_digest(child);m.renderer.write(base/'child-crop.json',receipt)
            with self.assertRaisesRegex(ValueError,'exact aligned native PCM'):m.validate_child(self.folder,self.unit,2,1)
    def test_parent_quality_failure_blocks_recognizer_before_call(self):
        with patch.object(m,'assembly_receipt',return_value=({}, {'quality_passed':False})):
            with self.assertRaisesRegex(ValueError,'final quality remains held'):m.verification_audio(self.folder,self.unit,1,'parent')
    def test_real_subprocess_child_and_parent_verify_argument_modes(self):
        import subprocess,os
        for flags in (['--stage','verify','--attempt','1','--kind','parent','--recognizer','qwen'],
                      ['--stage','verify','--attempt','1','--child','1','--kind','crop','--recognizer','turbo']):
            result=subprocess.run([sys.executable,'-X','utf8',str(m.SELF),'--folder',str(self.folder),'--unit','fixture',*flags],
                env={**os.environ,'CUDA_VISIBLE_DEVICES':''},capture_output=True,text=True,encoding='utf8',timeout=30)
            self.assertNotEqual(result.returncode,0);self.assertIn('FileNotFoundError',result.stderr)
            self.assertNotIn('the following arguments are required',result.stderr)


if __name__=='__main__':unittest.main()
