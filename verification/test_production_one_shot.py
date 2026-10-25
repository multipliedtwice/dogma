"""Default-off exercises the real normal Jobs render/verification boundary."""
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from rimsky.jobs import Jobs
from rimsky.higgs_backend import synthesis_plan
from rimsky.post_processing import enabled,mode
from rimsky.speech_quality import transcript_check
from rimsky.util import AppError,SpeechHoldError,GenerationCapError,EngineTransportError,digest,file_digest
from test_higgs_backend import fixture


class ProductionOneShotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.pack=fixture(self.root/'pack')
        self.resolved={'backend':'higgs_audio_cpp','device':'cpu','device_index':0,'precision':'q8_0',
            'device_name':'cpu','runner_version':'fixture runner','implementation_sha256':'e'*64}
        self.request={'pack':self.pack,'text':'Hello there.','voice_id':'n','delivery':'neutral',
            'language':'English','resolved_speech':self.resolved,'take':7}
        self.wav=self.root/'candidate.wav';self.calls=[]
        self.jobs=Jobs.__new__(Jobs);self.jobs.store=types.SimpleNamespace(root=self.root)
        self.jobs._check=Mock();self.jobs._intact=Mock();self.jobs._replace=Mock(side_effect=AssertionError('no retry'))
        self.jobs._guarded=self.guarded
    def guarded(self,method,payload):
        self.calls.append((method,copy.deepcopy(payload)))
        if method=='render':
            sf.write(Path(payload['output']),np.r_[np.zeros(1200),np.sin(np.arange(24000)*.05)*.1,np.zeros(1200)],24000,subtype='PCM_16')
            plan=synthesis_plan(self.pack,self.pack['voices'][0],payload['text'],'neutral',payload.get('controls'),self.resolved,payload.get('take',0))
            return {'warning':'','synthesis':{**plan,'plan_sha256':digest(plan)},'audio_checks':{'clipped_sample_fraction':0.}}
        if method=='verify_audio'and payload.get('allow_test_asr'):
            raise AssertionError('unexpected test protocol')
        raise AssertionError('No recognition/recovery call allowed: '+method)

    def test_default_off_one_requested_take_and_no_recognition_or_recovery(self):
        result=self.jobs._synthesize('g',{},self.request,self.wav)
        self.assertEqual(['render'],[method for method,_ in self.calls])
        self.assertEqual(7,self.calls[0][1]['take'])
        self.assertNotIn('automatic_retake',result)
        self.assertEqual({'status':'not_checked','audio_sha256':file_digest(self.wav)},result['word_check'])
        self.assertEqual('not_checked',result['audio_checks']['spoken_words'])
        self.assertEqual('not_checked',result['post_processing']['recognition_status'])
        self.assertEqual(0.,result['audio_checks']['clipped_sample_fraction'])
        self.jobs._intact.assert_called_once()
        self.jobs._chapter_acceptance('g',self.pack,{}, {}) # must not access Store/measure
        from rimsky.chapter_gate import music_released
        self.assertTrue(music_released({'config':{},'analysis':{'scene_direction':{'c':{}}}},'c',self.root))

    def test_technical_failures_do_not_advance_seed_or_retry(self):
        for error in (SpeechHoldError('edge held'),GenerationCapError('cap'),EngineTransportError('worker stopped')):
            with self.subTest(error=type(error).__name__):
                self.jobs._guarded=Mock(side_effect=error)
                with self.assertRaises(type(error)):self.jobs._synthesize('g',{},self.request,self.wav)
                self.assertEqual(1,self.jobs._guarded.call_count)
                self.assertEqual(7,self.jobs._guarded.call_args.args[1]['take'])
                self.jobs._replace.assert_not_called()

    def test_off_rejects_automatic_repair_input_before_native_dispatch(self):
        for fields in ({'_decoding_profile':'careful_full'},{'_spoken_repair':{'native_text':'changed'}}):
            with self.subTest(fields=fields),self.assertRaisesRegex(SpeechHoldError,'disabled'):
                self.jobs._synthesize('g',{},dict(self.request,**fields),self.wav)
        self.assertEqual([],self.calls)

    def test_explicit_on_runs_recognition_and_recomputes_the_verdict(self):
        real=self.guarded
        def checked(method,payload):
            if method!='verify_audio':return real(method,payload)
            self.calls.append((method,payload))
            prefix='verifier/'
            verifier=digest({k:v for k,v in self.pack['files'].items()if k.startswith(prefix)})
            return transcript_check(payload['text'],payload['text'],file_digest(self.wav),verifier)
        self.jobs._guarded=checked
        result=self.jobs._synthesize('g',{},dict(self.request,post_processing='on'),self.wav)
        self.assertEqual(['render','verify_audio'],[method for method,_ in self.calls])
        self.assertEqual('asr_exact_match',result['word_check']['status'])
        self.assertEqual(7,result['automatic_retake']['selected_take'])

    def test_mode_is_explicit_and_fail_closed(self):
        from rimsky.post_processing import disclosure
        self.assertEqual('off',mode({}));self.assertFalse(enabled({}))
        self.assertTrue(enabled({'post_processing':'on'}))
        self.assertEqual('not_checked',disclosure({'post_processing':'on'})['acoustic_stress_status'])
        for value in (None,True,'ON','auto','disabled',{}):
            with self.subTest(value=value),self.assertRaises(AppError):mode({'post_processing':value})

    def test_normal_render_publishes_off_and_ignores_historical_automatic_takes(self):
        from rimsky.store import Store
        source=b'''<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
          <author><nickname>Fixture author</nickname></author><book-title>Fixture</book-title><lang>en</lang>
          </title-info></description><body><section><p>Hello there.</p><p>Another passage.</p></section></body></FictionBook>'''
        store=Store(self.root/'library');self.addCleanup(store.close)
        book=store.import_data(source,'fixture.fb2')
        generation=store.create_generation(book['id'],{'device':'cpu','resolved_speech':self.resolved})
        take_map={s['id']:7 for s in generation['segments']}
        automatic={s['id']:{'0':{'requested_take':7,'selected_take':9,'decoding_profile':'careful_full',
                                'spoken_text_plan':{'native_text':'Wrong words.'}}}for s in generation['segments']}
        analysis={'takes':take_map,'automatic_takes':automatic,'narrator_voices':{'':'n'}}
        store.execute('UPDATE generations SET analysis=?,cast=? WHERE id=?',
            (json.dumps(analysis),json.dumps([{'id':'narrator','voice_id':'n'}]),generation['id']))
        store.execute("UPDATE segments SET speaker='narrator',delivery='neutral' WHERE generation_id=?",(generation['id'],))
        self.jobs.store=store;self.jobs._holds=lambda *args:set()
        self.jobs._chapter_acceptance=Mock(side_effect=AssertionError('off must not run quality gate'))
        config=store.generation(generation['id'])['config']
        self.jobs._render(generation['id'],self.pack,config,book,'English')
        rendered=store.generation(generation['id'])
        self.assertTrue(all(s['status']=='ready'and s['audio_hash']for s in rendered['segments']))
        self.assertEqual(len(rendered['segments']),len(self.calls))
        self.assertTrue(all(method=='render'and payload['take']==7 for method,payload in self.calls))
        self.assertEqual('not_checked',rendered['analysis']['post_processing']['recognition_status'])
        self.assertNotIn('chapter_acceptance',rendered['analysis'])
        self.calls.clear()
        self.jobs._render(generation['id'],self.pack,config,book,'English')
        self.assertEqual([],self.calls,'Exact first-take PCM should resume without ASR or native calls')


if __name__=='__main__':unittest.main()
