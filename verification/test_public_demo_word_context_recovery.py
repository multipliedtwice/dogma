"""Combined approved-word/context invariants; synthetic CPU fixtures only."""
import copy
import importlib.metadata
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_word_recovery as m
from rimsky.speech_quality import transcript_check,word_tokens


class WordContextRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        texts=['Они пришли. ','Там ихний дом. ','Тут светло.']
        self.units=[];offset=0
        for i,text in enumerate(texts):
            self.units.append({'id':'s'+str(i),'source_id':'p','start':offset,'end':offset+len(text),
                               'text':text,'spoken':text.strip(),'synthesis_text':text.strip(),'chapter_id':'c'})
            offset+=len(text)
        self.unit=self.units[1];self.assignment={'language':'Russian','units':self.units};self.blocks={'p':''.join(texts)}
        start=self.unit['synthesis_text'].index('ихний')
        self.repair={'synthesis_text':self.unit['synthesis_text'].replace('ихний','и́хний'),
                     'source_stress_projection':self.unit['synthesis_text'].replace('ихний','и́хний'),
                     'patches':[{'kind':'stress-only','spoken_index':1,'source_surface':'ихний','before':'ихний','after':'и́хний',
                                 'source_start':self.unit['start']+start,'source_end':self.unit['start']+start+5}]}

    def bound(self):
        windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        return {'language':'Russian','word_repair':self.repair,'source_paragraph':self.blocks['p'],'candidates':windows,
                'attempts':[{'context_id':windows[i%len(windows)]['id'],'profile':list(m.PROFILES)[i]} for i in range(3)],
                'original_spec':{'request':{'text':self.unit['synthesis_text'],'seed':12,'voice_ref':'unchanged-ref'},
                                 'source':self.unit,'model':{'path':'model.gguf'},'runner':{'path':'runner.exe'},'effects':'disabled'},
                'word_proposal_sha256':'proposal','qwen_files':{},'aligner_files':{}}

    def test_preferred_compact_window_exact_target_once_neighbors_frozen(self):
        before=copy.deepcopy(self.assignment);windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        self.assertEqual([s['id'] for s in windows[0]['source_spans']],['s0','s1','s2'])
        self.assertEqual(windows[0]['synthesis_text'],'Они пришли. Там и́хний дом. Тут светло.')
        self.assertEqual(windows[0]['spoken'],'Они пришли. Там ихний дом. Тут светло.')
        self.assertEqual(windows[0]['source_text'],self.blocks['p'])
        self.assertEqual(windows[0]['target_word_start'],2);self.assertEqual(windows[0]['target_word_end'],5)
        self.assertEqual(self.assignment,before)
        self.assertTrue(all(sum(s['id']=='s1' for s in c['source_spans'])==1 for c in windows))

    def test_no_neighbor_patch_or_historical_letter_operation(self):
        self.units[0]['synthesis_text']='Они при́шли.'
        self.units[2]['synthesis_text']='Тут све́тло.'
        windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        self.assertEqual(windows[0]['synthesis_text'],'Они при́шли. Там и́хний дом. Тут све́тло.')
        wrong=copy.deepcopy(self.repair);wrong['patches'][0]['kind']='historical-ts-spelling'
        with self.assertRaisesRegex(ValueError,'only previously approved stress'):m.candidates(self.assignment,self.blocks,self.unit,wrong)

    def test_neighbor_source_offset_or_nonadjacent_lexical_gap_is_rejected(self):
        self.units[0]['start']=1
        with self.assertRaisesRegex(ValueError,'source span'):m.candidates(self.assignment,self.blocks,self.unit,self.repair)

    def execution(self,number,passed=False,error='Unsafe edge'):
        base=self.folder/('word-attempt-'+str(number));base.mkdir()
        spec={'request':{'id':'native-'+str(number),'text':'original stress-bound input','max_tokens':256},
              'model':{'path':'model.gguf'},'runner':{'path':'runner.exe'}}
        m.immutable(base/'native-request.json',{'requests':[spec['request']]})
        m.immutable(base/'native-session.json',{'command':m.command_for(base,spec),'spec':spec,
                                             'sequence_sha256':m.file_digest(base/'native-request.json')})
        (base/'native.log').write_bytes(b'Synthetic native execution evidence')
        output=base/'native'/(spec['request']['id']+'.wav');output.parent.mkdir();output.write_bytes(b'synthetic raw audio')
        native={'spec':spec,'spec_sha256':m.renderer.sha(spec),'quality_passed':passed,'audio':output.relative_to(self.folder).as_posix(),
                'audio_sha256':m.file_digest(output),'audio_approval':False}
        if passed:native['quality']={'duration':1}
        else:native['quality_error']=error
        m.immutable(base/'native-receipt.json',native)
        return base,spec,native

    def test_three_exhausted_words_include_unsafe_third_without_asr(self):
        records={i:self.execution(i,passed=i<3) for i in (1,2,3)}
        def quality(path,*args):
            if 'word-attempt-3' in str(path):raise ValueError('Unsafe edge')
            return {'duration':1}
        with patch.object(m.words,'native_receipt',side_effect=lambda f,u,a:({},records[a][1],records[a][2])),\
             patch.object(m.words,'attempt_home',side_effect=lambda f,u,a:records[a][0]),\
             patch.object(m.words,'blind_decision',return_value={'passed':False,'evidence':[]}) as blind,\
             patch.object(m.renderer,'quality',side_effect=quality):
            rows=m.failed_word_attempts(self.folder,self.unit)
            self.assertEqual([r['failed_stage'] for r in rows],['native-recognition','native-recognition','native-quality'])
            self.assertEqual(blind.call_count,2)
            (records[3][0]/'native-session.json').unlink()
            with self.assertRaises(FileNotFoundError):m.failed_word_attempts(self.folder,self.unit)

    def test_false_negative_parent_or_prior_passing_words_cannot_authorize_context(self):
        base,spec,native=self.execution(1,False)
        with patch.object(m.renderer,'quality',return_value={'duration':1}):
            with self.assertRaisesRegex(ValueError,'quality label'):m.execution_proof(self.folder,base,spec,native)
        with patch.object(m.words,'native_receipt',return_value=({},spec,native)),\
             patch.object(m.words,'attempt_home',return_value=base),patch.object(m,'execution_proof',return_value=None),\
             patch.object(m.words,'blind_decision',return_value={'passed':True}):
            with self.assertRaisesRegex(ValueError,'already passes'):m.failed_word_attempts(self.folder,self.unit)

    def test_windows_failed_command_proof_is_exact_and_missing_output_guarded(self):
        base,spec,native=self.execution(1,False);(base/'native'/spec['request']['id']).with_suffix('.wav').unlink()
        native={k:v for k,v in native.items() if k not in ('audio','audio_sha256','quality_error')}
        native['generation_error']=str(subprocess.CalledProcessError(1,m.command_for(base,spec)))
        self.assertEqual(m.execution_proof(self.folder,base,spec,native)['failed_stage'],'native-generation')
        with self.assertRaisesRegex(ValueError,'failed-command'):
            m.execution_proof(self.folder,base,spec,{**native,'generation_error':'native failed'})

    def test_native_request_changes_only_closed_context_sampling_seed_and_id(self):
        bound=self.bound();m.immutable(m.home(self.folder,self.unit)/'campaign.json',{'fixture':True})
        old=copy.deepcopy(bound['original_spec']);specs=[m.native_spec(self.folder,self.unit,bound,i) for i in (1,2,3)]
        self.assertEqual(len({s['request']['seed'] for s in specs}),3)
        self.assertEqual(len({s['word_context_recovery']['profile'] for s in specs}),3)
        for spec in specs:
            self.assertEqual(spec['source'],self.unit);self.assertEqual(spec['model'],old['model'])
            self.assertEqual(spec['request']['voice_ref'],old['request']['voice_ref']);self.assertEqual(spec['effects'],'disabled')
            self.assertIn('и́хний',spec['request']['text'])
        self.assertEqual(bound['original_spec'],old)
        with self.assertRaises(ValueError):m.native_spec(self.folder,self.unit,bound,4)

    def write_checks(self,kind,expected,heard_a,heard_b,checksum='a'*64):
        base=m.attempt_home(self.folder,self.unit,1)
        for name,heard in [('qwen',heard_a),('turbo',heard_b)]:
            m.immutable(base/(kind+'-'+name+'.json'),{'heard':heard,'verifier':{'recognizer':name}})
        return lambda check,e,a,b,r:transcript_check(expected,check['heard'],checksum,'b'*64)

    def test_both_full_context_checks_reject_missing_target_and_missing_final_j(self):
        bound=self.bound();chosen=bound['candidates'][0];expected=chosen['spoken']
        original='Они пришли. Там ихний дом. Тут светло.'
        for a,b in [('Они пришли. Тут светло.',original),('Они пришли. Там ихни дом. Тут светло.',original)]:
            # Separate temporary attempts to preserve immutable evidence.
            with tempfile.TemporaryDirectory() as tmp:
                previous=self.folder;self.folder=Path(tmp)
                try:
                    check=self.write_checks('context',expected,a,b)
                    with patch.object(m,'verification_audio',return_value=(bound,expected,'audio','a'*64)),\
                         patch.object(m,'check_record',side_effect=check):
                        self.assertFalse(m.blind_decision(self.folder,self.unit,1,'context')['passed'])
                finally:self.folder=previous

    def test_full_exact_both_and_crop_exact_both_are_separate_gates(self):
        bound=self.bound();expected=bound['candidates'][0]['spoken'];check=self.write_checks('context',expected,expected,expected)
        with patch.object(m,'verification_audio',return_value=(bound,expected,'audio','a'*64)),patch.object(m,'check_record',side_effect=check):
            self.assertTrue(m.blind_decision(self.folder,self.unit,1,'context')['passed'])
        check=self.write_checks('crop',self.unit['spoken'],'Там ихни дом.',self.unit['spoken'])
        with patch.object(m,'verification_audio',return_value=(bound,self.unit['spoken'],'audio','a'*64)),patch.object(m,'check_record',side_effect=check):
            self.assertFalse(m.blind_decision(self.folder,self.unit,1,'crop')['passed'])

    def test_shared_pcm_boundary_function_keeps_exact_samples_without_fades(self):
        self.assertIs(m.crop_bounds,m.context.crop_bounds)
        audio=np.zeros(2000,dtype=np.int16);audio[300:600]=1000;audio[1000:1300]=2000
        cues=[{'text':'Они','start':.3,'end':.6},{'text':'там','start':1,'end':1.3}]
        chosen={'spoken':'Они там','target_word_start':1,'target_word_end':2}
        bounds=m.crop_bounds(audio,1000,cues,chosen)
        self.assertGreaterEqual(bounds['start_sample'],600);self.assertLessEqual(bounds['start_sample'],1000)
        self.assertGreaterEqual(bounds['end_sample'],1300)
        self.assertEqual(bounds['policy']['processing'],'Exact contiguous PCM16 sample slice; no fades, gain or resampling')

    def test_source_blind_config_pins_new_module_and_preserves_decoder(self):
        bound=self.bound();torch=importlib.metadata.version('torch');transformers=importlib.metadata.version('transformers')
        old=m.words.verifier_config(bound,'turbo',torch,transformers);new=m.verifier_config(bound,'turbo',torch,transformers)
        self.assertEqual({k:v for k,v in old.items() if k not in ('policy','implementation_sha256')},
                         {k:v for k,v in new.items() if k not in ('policy','implementation_sha256')})
        self.assertEqual(new['policy'],m.POLICY);self.assertFalse(new['expected_text_given_to_recognizer'])
        raw=transcript_check(self.unit['spoken'],self.unit['spoken'],'a'*64,m.renderer.sha(new))
        with patch.object(m.words,'checked_inventory',return_value={}):
            with self.assertRaisesRegex(ValueError,'source-blind'):
                m.check_record({**raw,'verifier':{**new,'expected_text_given_to_recognizer':True}},self.unit['spoken'],'a'*64,bound,'turbo')


if __name__=='__main__':unittest.main()
