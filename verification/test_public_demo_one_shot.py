"""One-shot dispatch, crash budget, source binding and honest status; CPU fixtures only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import render_public_demo_one_shot as m


class OneShot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.output=self.root/'output';self.source.mkdir();self.output.mkdir()
        self.ref=self.root/'ref.wav';sf.write(self.ref,np.ones(2400)*.1,24000,subtype='PCM_16')
        self.runner=self.root/'native.exe';self.runner.write_bytes(b'fixture')
        self.unit={'id':'u0000','source_id':'p','start':0,'end':6,'text':'Words.','spoken':'Words.',
                   'palette_reference':{'reviewed':True,'path':str(self.ref),'audio_sha256':m.file_digest(self.ref),'text':'Reference.'}}
        self.manifest={'speech_model':{},'runner':{'path':str(self.runner),'sha256':m.file_digest(self.runner),'dlls':{}},
                       'seed':17,'music':{'bed':{},'ending':{},'ending_rms_dbfs':-30},'opening':{'sha256':'a'*64},'ffmpeg':'fixture-ffmpeg'}
        self.assignment={'language':'English','title':'Fixture','units':[self.unit]}
        self.freeze()
    def freeze(self):
        m.write(self.source/'assignment.json',self.assignment);m.write(self.source/'manifest.json',self.manifest)
        m.write(self.source/'frozen.json',{'assignment':self.assignment,'manifest':self.manifest})
    def config(self,**kw):return m.configure(self.source,self.output,**kw)
    def test_transient_windows_reader_lock_retries_atomic_write(self):
        path=self.root/'worker.json';replace=m.os.replace;attempts=[]
        def transient(source,target):
            attempts.append(source)
            if len(attempts)<3:
                error=PermissionError('reader sharing violation');error.winerror=5;raise error
            replace(source,target)
        with patch.object(m.os,'replace',side_effect=transient),patch.object(m.time,'sleep'):
            m.write(path,{'status':'running'})
        self.assertEqual(m.read(path),{'status':'running'});self.assertEqual(len(attempts),3)
        self.assertNotEqual(attempts[0],path.with_suffix('.json.tmp'))
    def test_atomic_writer_is_bounded_preserves_immutable_and_does_not_retry_disk_errors(self):
        lock=PermissionError('sharing violation');lock.winerror=32
        for error,count in ((lock,10),(OSError('disk failure'),1)):
            with patch.object(m.os,'replace',side_effect=error) as replace,patch.object(m.time,'sleep'),self.assertRaises(OSError):
                m.write(self.root/'worker.json',{})
            self.assertEqual(replace.call_count,count)
        path=self.root/'immutable.json';m.write(path,{'original':True})
        with self.assertRaises(ValueError):m.write(path,{'changed':True},immutable=True)
        self.assertEqual(m.read(path),{'original':True})
    def reserve(self,config,wav=True):
        spec=m.request_spec(config,self.unit,0);sid=m.sha({'policy':m.POLICY,'specs':[spec]});base=self.output/'sessions'/sid
        m.write(base/'request.json',{'requests':[spec['request']]})
        session={'id':sid,'specs':[spec],'sequence_sha256':m.file_digest(base/'request.json')}
        m.write(base/'physical-start.json',session)
        if wav:
            target=base/'native'/(spec['request']['id']+'.wav');target.parent.mkdir();sf.write(target,np.ones(1000)*.2,24000,subtype='PCM_16')
        return session
    def test_prepared_override_is_explicit_source_bound_and_preserves_source(self):
        path=self.root/'changes.json';value={'schema':1,'human_approval':False,'changes':[{'id':'u0000','source_text':'Words.',
            'before':'Words.','after':'Prepared words.','basis':'Explicit listener preparation instruction'}]};m.write(path,value)
        config=self.config(prepared_overrides=path);spec=m.request_spec(config,self.unit,0)
        self.assertEqual(spec['request']['text'],'Prepared words.');self.assertEqual(spec['source'],self.unit)
        self.assertEqual(config['music_volume_scale'],.9);self.assertEqual(spec['prepared_overrides_sha256'],m.file_digest(path))
        value['changes'][0]['after']='Different';m.write(path,value)
        with self.assertRaises(ValueError):m.request_spec(config,self.unit,0)
        value['changes'][0]['source_text']='Other source';m.write(path,value)
        with self.assertRaises(ValueError):self.config(prepared_overrides=path)
    def test_bad_tag_or_changed_run_mode_cannot_resume(self):
        self.unit['spoken']='<|emotion:happy|>Words.';self.freeze()
        with self.assertRaises(ValueError):self.config()
        self.unit['spoken']='Words.';self.freeze();self.config()
        with self.assertRaises(ValueError):self.config(music_volume_scale=.8)
    def test_format_check_does_not_apply_acoustic_edge_silence_or_duration_gates(self):
        path=self.root/'raw.wav'
        for samples in (np.ones(40)*.2,np.zeros(1000)):
            sf.write(path,samples,24000,subtype='PCM_16');self.assertTrue(m.basic_audio(path)['format_valid'])
        sf.write(path,np.zeros(1000),16000,subtype='PCM_16')
        with self.assertRaises(ValueError):m.basic_audio(path)
    def test_model_guard_fails_before_any_native_start(self):
        c=self.config();spec=m.request_spec(c,self.unit,0)
        with patch.object(m.assets,'wait_for_audio_resources'),patch.object(m,'model_guard',side_effect=ValueError('model pin')),patch.object(m.subprocess,'Popen') as call:
            with self.assertRaisesRegex(ValueError,'model pin'):m.run_session(self.output,c,[spec],60)
            call.assert_not_called()
        self.assertFalse(list(self.output.glob('sessions/*/physical-start.json')))
    def test_one_native_dispatch_no_recognition_quality_or_retakes(self):
        config=self.config();spec=m.request_spec(config,self.unit,0);events=[]
        def native(command,**kw):
            events.append('native');base=Path(command[command.index('--out-dir')+1]);base.mkdir()
            sf.write(base/(spec['request']['id']+'.wav'),np.ones(1000)*.2,24000,subtype='PCM_16')
            return SimpleNamespace(pid=123,returncode=0,poll=lambda:0)
        def guarded(*a):events.append('guard');return SimpleNamespace(path=self.root/'pinned.gguf'),self.runner
        with patch.object(m.assets,'wait_for_audio_resources'),patch.object(m,'model_guard',side_effect=guarded),patch.object(m.subprocess,'Popen',side_effect=native) as call,patch('psutil.Process',return_value=SimpleNamespace(create_time=lambda:10)),patch.multiple(m.assets,audit=Mock(side_effect=AssertionError('audit')),quality=Mock(side_effect=AssertionError('quality')),verify=Mock(side_effect=AssertionError('ASR')),selected=Mock(side_effect=AssertionError('retake'))):
            m.run_session(self.output,config,[spec],60)
            call.assert_called_once();self.assertEqual(events,['guard','native'])
            row=m.receipts(self.output,config)[0];self.assertEqual(row['status'],'rendered');self.assertEqual(row['take'],0)
            import psutil
            with patch('psutil.Process',side_effect=psutil.NoSuchProcess(123)):
                state=m.render(self.output,config)
            self.assertEqual(call.call_count,1);self.assertTrue(state['render_complete']);self.assertEqual(state['recognition_status'],'not_checked')
            self.assertNotIn('verified_units',state);self.assertNotIn('open_recognition_flags',state)
    def test_crash_after_native_start_reuses_wav_without_resynthesis(self):
        config=self.config();self.reserve(config)
        with patch.object(m,'run_session',side_effect=AssertionError('duplicate physical take')),patch.object(m,'publish'):
            state=m.render(self.output,config)
        self.assertTrue(state['render_complete']);self.assertEqual(m.receipts(self.output,config)[0]['format']['frames'],1000)
    def test_crash_without_wav_is_failure_not_an_automatic_retry(self):
        config=self.config();self.reserve(config,wav=False)
        with patch.object(m,'run_session',side_effect=AssertionError('retake')),patch.object(m,'publish'):
            state=m.render(self.output,config)
        self.assertEqual(state['counts']['technical_failure'],1);self.assertFalse(state['render_complete'])
    def test_tampered_reserved_request_is_rejected(self):
        config=self.config();session=self.reserve(config);path=self.output/'sessions'/session['id']/'physical-start.json'
        session['specs'][0]['request']['text']='Different words';m.write(path,session)
        with self.assertRaises(ValueError):m.render(self.output,config)
    def test_real_cli_has_no_verify_witness_or_retake_stage(self):
        for stage in ('verify','witness','recover'):
            p=subprocess.run([sys.executable,'-X','utf8',str(m.SELF),'--source',str(self.source),'--output',str(self.output),'--stage',stage],capture_output=True,text=True)
            self.assertEqual(p.returncode,2);self.assertIn('invalid choice',p.stderr)
    def test_public_status_is_unreviewed_whole_pcm_with_reduced_music(self):
        config=self.config();session=self.reserve(config);m.collect_session(self.output,session)
        intro=self.root/'intro.wav';bed=self.root/'bed.wav';ending=self.root/'ending.wav'
        sf.write(intro,np.zeros((8*24000,2)),24000,subtype='PCM_16')
        sf.write(bed,np.ones((20*24000,2))*.01,24000,subtype='PCM_16');sf.write(ending,np.ones((24*24000,2))*.01,24000,subtype='PCM_16')
        def encode(cmd,**kw):
            if cmd[-1].endswith('.mp3'):Path(cmd[-1]).write_bytes(b'fixture mp3')
        with patch.object(m.assets,'opening',return_value=intro),patch.object(m.assets,'music_asset',side_effect=lambda spec,kind:bed if kind=='bed' else ending),patch.object(m.subprocess,'run',side_effect=encode):
            status=m.publish(self.output,config)
        self.assertEqual(status['captions'][0]['text'],self.unit['text']);self.assertAlmostEqual(status['captions'][0]['end']-8,1000/24000)
        self.assertFalse(status['machine_verified']);self.assertEqual(status['recognition_status'],'not_checked')
        self.assertAlmostEqual(status['mix']['bed_rms_dbfs'],-30.9151498112)
        self.assertFalse((self.output/'complete.json').exists())
        wav,_=sf.read(self.output/'public'/status['wav'],dtype='float32',always_2d=True)
        np.testing.assert_array_equal(wav[:8*24000],np.zeros((8*24000,2)))


if __name__=='__main__':unittest.main()
