"""Isolated control/proof fixtures; no real providers, GPU, sources or profiles."""
import copy,importlib.util,json,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('dev_selected_test',ROOT/'scripts/development_public_demo.py')
dev=importlib.util.module_from_spec(s);s.loader.exec_module(dev)

def wav(path,seconds=.2,value=0.,channels=1):
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb')as w:w.setparams((channels,2,24000,0,'NONE',''));w.writeframes((np.ones((round(seconds*24000),channels))*value*32767).astype(np.int16).tobytes())

class DevelopmentSelectedTests(unittest.TestCase):
    def spec(self):return {'policy':'first','take':0,'source':{'id':'u','text':'Текст'},'model':{'path':'model','sha256':'m'},
        'runner':{'path':'runner','sha256':'r'},'reference':{'path':'ref','sha256':'v'},
        'request':{'id':'u-one-shot','text':'Текст','voice_ref':'ref','reference_text':'Reference','seed':100,
                   'temperature':.8,'top_k':50,'top_p':.95,'repetition_penalty':1.1,'max_tokens':200}}

    def test_attempt1_changes_only_topk_sound_input_and_attempt_id(self):
        base=self.spec()['request'];actual=dev.effective_request(base,1)
        self.assertEqual(actual,{**base,'id':'u-one-shot-dev-t1','top_k':30})

    def test_attempt2_has_topk30_and_seed_plus_one_only(self):
        base=self.spec()['request'];actual=dev.effective_request(base,2)
        self.assertEqual(actual,{**base,'id':'u-one-shot-dev-t2','top_k':30,'seed':101})
        for take in(0,3):
            with self.assertRaises(ValueError):dev.effective_request(base,take)

    def test_text_reference_temperature_or_cap_change_rejected(self):
        base=self.spec()['request']
        for key,value in [('text','Другой'),('voice_ref','other'),('temperature',.75),('top_p',.8),('max_tokens',2048),('seed',101)]:
            actual=dev.effective_request(base,1);actual[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):dev.validate_request(base,actual,1)

    def fixture(self,root):
        base=root/'first';output=root/'dev';origin={'unit':'u','status':'rendered','take':0,'spec':self.spec(),
            'session':'first','audio_sha256':'a','synthetic_unsafe':True}
        dev.write(base/'units/u.json',origin)
        binding={'unit':'u','first_receipt_sha256':dev.file_digest(base/'units/u.json'),'unsafe_first':True}
        config={'source_generation':'g','frozen':{'assignment':{'units':[origin['spec']['source']]}}}
        value={'first_takes':[binding]}
        return base,output,origin,binding,config,value

    def repaired(self,output,origin,binding,take=1,unsafe=False):
        row={'unit':'u','status':'rendered','take':take,'spec':dev.effective_spec(origin['spec'],take),
            'base_receipt_sha256':binding['first_receipt_sha256'],'source_generation':'g','session':'repair',
            'audio_sha256':'new','synthetic_unsafe':unsafe}
        dev.write(output/'takes/u'/('take-'+str(take)+'.json'),row);return row

    def selected(self,output,base,origin,config,value,**kwargs):
        with patch.object(dev,'load_plan',return_value=(value,base,config)),patch.object(dev.first,'receipts',return_value=[origin]),\
             patch.object(dev,'physical',side_effect=lambda root,row:(root/'audio.wav',{'unsafe_start':False,'unsafe_end':row['synthetic_unsafe']})):
            return dev.selection(output,**kwargs)

    def test_selection_records_actual_take_and_physical_attempt_count(self):
        with tempfile.TemporaryDirectory()as t:
            base,out,origin,binding,cfg,value=self.fixture(Path(t));self.repaired(out,origin,binding)
            _,selected=self.selected(out,base,origin,cfg,value,require_safe=True)
            self.assertEqual(selected[0]['take'],1);self.assertEqual(selected[0]['physical_attempt_count'],2)

    def test_safe_first_take_is_kept_without_extra_render(self):
        with tempfile.TemporaryDirectory()as t:
            base,out,origin,binding,cfg,value=self.fixture(Path(t));origin['synthetic_unsafe']=False;dev.write(base/'units/u.json',origin)
            binding.update(first_receipt_sha256=dev.file_digest(base/'units/u.json'),unsafe_first=False)
            _,selected=self.selected(out,base,origin,cfg,value,require_safe=True)
            self.assertEqual(selected[0]['take'],0)

    def test_safe_prior_take_forbids_another_attempt(self):
        with tempfile.TemporaryDirectory()as t:
            base,out,origin,binding,cfg,value=self.fixture(Path(t));self.repaired(out,origin,binding);self.repaired(out,origin,binding,2)
            with self.assertRaisesRegex(ValueError,'Unnecessary'):self.selected(out,base,origin,cfg,value)

    def test_attempt_gap_and_third_take_rejected(self):
        for take in(2,3):
            with tempfile.TemporaryDirectory()as t:
                base,out,origin,binding,cfg,value=self.fixture(Path(t))
                if take==2:self.repaired(out,origin,binding,2)
                else:dev.write(out/'takes/u/take-3.json',{})
                with self.assertRaises(ValueError):self.selected(out,base,origin,cfg,value)

    def test_unapproved_source_or_runner_change_is_rejected(self):
        with tempfile.TemporaryDirectory()as t:
            base,out,origin,binding,cfg,value=self.fixture(Path(t));row=self.repaired(out,origin,binding)
            row['spec']['runner']['path']='other';dev.write(out/'takes/u/take-1.json',row)
            with self.assertRaisesRegex(ValueError,'source/model/reference/control'):self.selected(out,base,origin,cfg,value)

    def test_unsafe_selected_take_holds(self):
        with tempfile.TemporaryDirectory()as t:
            base,out,origin,binding,cfg,value=self.fixture(Path(t))
            with self.assertRaisesRegex(ValueError,'unsafe boundary'):self.selected(out,base,origin,cfg,value,require_safe=True)

    def physical_fixture(self,root):
        runner=root/'runner.exe';model=root/'model.gguf';runner.write_bytes(b'runner');model.write_bytes(b'model')
        spec=self.spec();spec['runner']={'path':str(runner),'sha256':dev.file_digest(runner)}
        spec['model']={'path':str(model),'sha256':dev.file_digest(model)}
        session=root/'sessions/s';sequence=session/'request.json';dev.write(sequence,{'requests':[spec['request']]})
        command=[str(runner),'--task','tts','--family','higgs_audio_tts','--model',str(model),'--backend','cuda','--device','0','--threads','4',
            '--request-sequence',str(sequence),'--out-dir',str(session/'native'),'--out-format','pcm16','--metrics']
        wav(session/'native/u-one-shot.wav');raw=session/'native/u-one-shot.wav'
        signal=np.zeros(4800,np.int16);signal[1000:2000]=(np.sin(np.arange(1000)*.1)*500).astype(np.int16)
        with wave.open(str(raw),'wb')as w:w.setparams((1,2,24000,0,'NONE',''));w.writeframes(signal.tobytes())
        row={'spec':spec,'spec_sha256':dev.sha(spec),'session':'s','audio':raw.relative_to(root).as_posix(),'audio_sha256':dev.file_digest(raw)}
        dev.write(session/'physical-start.json',{'specs':[spec],'command':command,'sequence_sha256':dev.file_digest(sequence)})
        dev.write(session/'physical-result.json',{'returncode':0,'error':None})
        dev.write(session/'worker.json',{'pid':99999999,'create_time':1,'command':command})
        return row,session

    def test_real_fixture_proves_pcm_sequence_binary_and_dead_worker(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);row,session=self.physical_fixture(root);path,edge=dev.physical(root,row)
            self.assertEqual(dev.file_digest(path),row['audio_sha256']);self.assertFalse(edge['unsafe_end'])

    def test_worker_command_mismatch_or_live_pid_rejected(self):
        for live in(False,True):
            with tempfile.TemporaryDirectory()as t:
                root=Path(t);row,session=self.physical_fixture(root);worker=dev.read(session/'worker.json')
                if live:worker.update(pid=dev.os.getpid(),create_time=dev.psutil.Process().create_time())
                else:worker['command']=worker['command']+['--unexpected']
                dev.write(session/'worker.json',worker)
                with self.assertRaises(ValueError):dev.physical(root,row)

    def test_modified_native_pcm_rejected(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);row,session=self.physical_fixture(root);wav(session/'native/u-one-shot.wav',value=.02)
            with self.assertRaisesRegex(ValueError,'audio differs'):dev.physical(root,row)

    def test_assembly_uses_honest_dev_disclosure_and_keeps_raw_speech(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);clip=root/'speech.wav';intro=root/'intro.wav';bed=root/'bed.wav';end=root/'ending.wav'
            wav(clip,1,.02);wav(intro,8,.01,2);wav(bed,20,.001,2);wav(end,1,.001,2)
            speech,speech_rate=sf.read(clip,dtype='int16');speech[:2400]=0;speech[-2400:]=0;sf.write(clip,speech,speech_rate,subtype='PCM_16')
            config={'frozen':{'assignment':{'units':[{'id':'u','source_id':'p','text':'Текст'}],'title':'Fixture','language':'Russian'},
                'manifest':{'music':{'bed':{},'ending':{}},'ffmpeg':'fake','opening':{'sha256':dev.file_digest(intro)}}},
                'source_generation':'g','music_volume_scale':.9,'player_sha256':dev.file_digest(dev.assets.PLAYER_SOURCE)}
            row={'unit':'u','status':'rendered','take':1,'physical_attempt_count':2,'audio_path':str(clip),'audio_sha256':dev.file_digest(clip)}
            def encode(command,**kwargs):
                if '-c:a'in command:Path(command[-1]).write_bytes(b'fixture mp3')
            out=root/'selected';out.mkdir()
            dev.write(out/'development-plan.json',{'diagnostic_inventory':[{'native_requests':6}]})
            with patch.object(dev,'selection',return_value=(config,[row])),patch.object(dev.assets,'opening',return_value=intro),\
                 patch.object(dev.assets,'music_asset',side_effect=lambda data,kind:bed if kind=='bed'else end),\
                 patch.object(dev.subprocess,'run',side_effect=encode):
                status=dev.assemble_selected(out)
            self.assertEqual(status['post_processing'],'development_only');self.assertEqual(status['production_default_post_processing'],'off')
            self.assertEqual(status['captions'][0]['take'],1);self.assertEqual(status['captions'][0]['physical_attempt_count'],2)
            self.assertNotIn('One-shot',status['limitation'])
            self.assertEqual(status['development_checks']['separate_diagnostic_native_requests'],6)
            self.assertEqual(status['development_checks']['total_native_requests_in_this_recheck'],8)
            audio,rate=sf.read(out/'public'/status['wav'],dtype='int16',always_2d=True)
            opening,_=sf.read(intro,dtype='int16',always_2d=True)
            self.assertTrue(np.array_equal(audio[:8*rate],opening))
            self.assertEqual(status['captions'][0]['end']-status['captions'][0]['start'],1)
            self.assertEqual(len(audio),10*rate);self.assertEqual(status['opening_seconds'],8)
            # A retry validates existing completed assets against the durable
            # identity and freshly reconstructed PCM, never mere existence.
            with patch.object(dev,'selection',return_value=(config,[row])),patch.object(dev.assets,'opening',return_value=intro),\
                 patch.object(dev.assets,'music_asset',side_effect=lambda data,kind:bed if kind=='bed'else end),\
                 patch.object(dev.subprocess,'run',side_effect=encode):
                self.assertEqual(dev.assemble_selected(out)['version'],status['version'])
            (out/'public'/status['wav']).write_bytes(b'partial')
            with patch.object(dev,'selection',return_value=(config,[row])),patch.object(dev.assets,'opening',return_value=intro),\
                 patch.object(dev.assets,'music_asset',side_effect=lambda data,kind:bed if kind=='bed'else end),\
                 patch.object(dev.subprocess,'run',side_effect=encode),self.assertRaises(ValueError):
                dev.assemble_selected(out)

    def test_lowlevel_mix_rejects_unbound_incomplete_or_unsafe_selection(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);clip=root/'speech.wav';wav(clip,1,.02)
            config={'frozen':{'assignment':{'units':[{'id':'u'}]}}}
            row={'unit':'u','status':'rendered','audio_path':str(clip),'audio_sha256':dev.file_digest(clip)}
            def proof(rows):
                return {'config_sha256':dev.sha(config),'selected_sha256':dev.sha(rows),
                        'diagnostic_count':6,'source_pcm_boundary_verified':True}
            with self.assertRaisesRegex(ValueError,'proof'):
                dev.mix_selected(root/'bad-proof',config,[row],{**proof([row]),'selected_sha256':'0'*64})
            with self.assertRaisesRegex(ValueError,'coverage'):
                dev.mix_selected(root/'missing',config,[],proof([]))
            with self.assertRaisesRegex(ValueError,'unsafe boundary'):
                dev.mix_selected(root/'unsafe',config,[row],proof([row]))

    def test_diagnostic_counts_are_actual_receipts_and_reject_duplicates(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);row,session=self.physical_fixture(root);row.update(unit='u',take=0,status='rendered')
            receipt=root/'units/u.json';dev.write(receipt,row);manifest=root/'diagnostic.json';dev.write(manifest,{'units':[row]})
            descriptor={'kind':'diagnostic','root':str(root),'manifest':str(manifest),'receipts':[str(receipt)]}
            result=dev.diagnostic_inventory([descriptor]);self.assertEqual(result[0]['native_requests'],1)
            with self.assertRaises(ValueError):dev.diagnostic_inventory([descriptor,descriptor])

if __name__=='__main__':unittest.main()
