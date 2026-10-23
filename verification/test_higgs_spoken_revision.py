import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from revise_higgs_spoken_take import patch_published_prefix,validate_revision,apply_batch,retry_revision
from rimsky.audio_join import prepare_clip
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from rimsky.spoken_pronunciation import phrase_stress

class SpokenRevisionTests(unittest.TestCase):
    def test_revision_retries_keep_wording_and_record_failed_attempt(self):
        import subprocess
        with tempfile.TemporaryDirectory() as work:
            folder=Path(work);path=folder/'request.json'
            request={'native_text':'Конечно господин','seed_offset':0,'before':{'unit':{'id':'line'}}}
            write_json(path,request)
            with patch('revise_higgs_spoken_take.subprocess.run',side_effect=[subprocess.CalledProcessError(1,['test']),None]) as run:
                retry_revision(folder,path,request)
            self.assertEqual(run.call_count,2)
            candidate=json.loads(next((folder/'pronunciation-retry-inputs').glob('*.json')).read_text('utf8'))
            self.assertEqual(candidate['native_text'],request['native_text'])
            self.assertEqual(candidate['before'],request['before'])
            self.assertEqual(candidate['seed_offset'],1)
            report=json.loads(next((folder/'pronunciation-retry-reports').glob('*.json')).read_text('utf8'))
            self.assertEqual([a['status'] for a in report['attempts']],['held','applied'])

    def test_listener_comma_repair_preserves_source_and_other_sound_inputs(self):
        from rimsky.spoken_edits import apply_spoken_edit
        spoken='Конечно, господин';native='Конечно господин'
        plan={'expected_spoken':spoken,'native_text':native,'recovery_kind':'listener_comma_phrasing',
            'evidence':{'source':'explicit_listener_instruction','sha256':'instruction'}}
        self.assertEqual(apply_spoken_edit(spoken,plan),(native,spoken))
        for bad in ('Конечно', 'Конечно. Господин', 'Конечно товарищ'):
            with self.assertRaises(ValueError):apply_spoken_edit(spoken,{**plan,'native_text':bad})
        with self.assertRaises(ValueError):apply_spoken_edit(spoken,{**plan,'evidence':{'source':'model'}})
        with tempfile.TemporaryDirectory() as work:
            wav=Path(work)/'take.wav'
            sf.write(wav,np.concatenate([np.zeros(2000),np.full(2000,.03),np.zeros(2000)]),24000)
            old={'id':'line','spoken':spoken,'synthesis':{'text':spoken,'prompt':spoken,'effects_policy':'disabled','seed':7}}
            new=copy.deepcopy(old);new['synthesis'].update(text=native,prompt=native,spoken_text_plan=plan)
            key=digest(new['synthesis']);sha=file_digest(wav)
            after={'unit':new,'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},
                'word_check':{**transcript_check(spoken,spoken,sha,'asr'),'synthesis_key':key}}
            request={'native_text':native,'recovery_kind':'listener_comma_phrasing'}
            validate_revision({'unit':old},after,request)
            with self.assertRaises(ValueError):validate_revision({'unit':old},after,{'native_text':native})

    def test_batch_validates_all_inputs_before_work_and_attempts_independent_revisions(self):
        import subprocess
        with tempfile.TemporaryDirectory() as work:
            folder=Path(work);request={'generation':'g','source_sha256':'source','revisions':[
                {'generation':'g','source_sha256':'source','before':{'unit':{'id':uid}}} for uid in ('one','two')]}
            bad=copy.deepcopy(request);bad['revisions'][1]['source_sha256']='other'
            with patch('revise_higgs_spoken_take.subprocess.run') as execute:
                with self.assertRaisesRegex(ValueError,'escaped'):apply_batch(folder,bad)
                execute.assert_not_called()
            with patch('revise_higgs_spoken_take.subprocess.run',side_effect=[subprocess.CalledProcessError(1,['test']),None]) as execute:
                with self.assertRaisesRegex(ValueError,'one'):apply_batch(folder,request)
                self.assertEqual(execute.call_count,2)
                reports=list((folder/'pronunciation-batches').glob('*.json'))
                record=json.loads(reports[0].read_text('utf-8'))
                self.assertEqual([r['status'] for r in record['revisions']],['held','applied'])

    def test_stress_metadata_is_recomputed_but_other_sound_inputs_stay_fixed(self):
        with tempfile.TemporaryDirectory() as work:
            wav=Path(work)/'take.wav'
            sf.write(wav,np.concatenate([np.zeros(2000),np.full(2000,.03),np.zeros(2000)]),24000)
            spoken='Болит';native='Боли\u0301т';sha=file_digest(wav)
            old={'id':'line','spoken':spoken,'synthesis':{'text':spoken,'prompt':spoken,'effects_policy':'disabled','seed':7}}
            new=copy.deepcopy(old);new['synthesis'].update(text=native,prompt=native,phrase_pronunciation=phrase_stress(native)[1])
            def candidate():
                key=digest(new['synthesis'])
                return {'unit':new,'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},
                    'word_check':{**transcript_check(spoken,spoken,sha,'asr'),'synthesis_key':key}}
            validate_revision({'unit':old},candidate(),{'native_text':native})
            new['synthesis']['seed']=8
            with self.assertRaisesRegex(ValueError,'seed'):validate_revision({'unit':old},candidate(),{'native_text':native})
            new['synthesis']['seed']=7;new['synthesis']['phrase_pronunciation']['policy']='other'
            with self.assertRaisesRegex(ValueError,'policy'):validate_revision({'unit':old},candidate(),{'native_text':native})

    def test_revision_may_replace_old_model_only_with_the_current_pin(self):
        with tempfile.TemporaryDirectory() as work:
            wav=Path(work)/'take.wav'
            sf.write(wav,np.concatenate([np.zeros(2000),np.full(2000,.03),np.zeros(2000)]),24000)
            spoken='Болит';native='Боли\u0301т';sha=file_digest(wav)
            old={'id':'line','spoken':spoken,'synthesis':{
                'text':spoken,'prompt':spoken,'effects_policy':'disabled','model_sha256':'old'}}
            new=copy.deepcopy(old);new['synthesis'].update(
                text=native,prompt=native,model_sha256='current')
            def candidate():
                key=digest(new['synthesis'])
                return {'unit':new,'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},
                    'word_check':{**transcript_check(spoken,spoken,sha,'asr'),'synthesis_key':key}}
            validate_revision({'unit':old},candidate(),{'native_text':native},'current')
            new['synthesis']['model_sha256']='another'
            with self.assertRaisesRegex(ValueError,'pinned production model'):
                validate_revision({'unit':old},candidate(),{'native_text':native},'current')
            # A heard take predating the checkpoint label may gain only the
            # pinned production label, bound to the pinned model hash.
            pin=json.loads((ROOT/'resources/production-higgs-model.json').read_text('utf-8'))
            old['synthesis']['model_sha256']=pin['model']['sha256']
            new['synthesis'].update(model_sha256=pin['model']['sha256'],model_checkpoint=pin['checkpoint'])
            validate_revision({'unit':old},candidate(),{'native_text':native},pin['model']['sha256'])
            validate_revision({'unit':old},candidate(),{'native_text':native})
            new['synthesis']['model_checkpoint']='older-checkpoint'
            with self.assertRaisesRegex(ValueError,'model_checkpoint'):
                validate_revision({'unit':old},candidate(),{'native_text':native},pin['model']['sha256'])

    def test_single_clip_patch_preserves_other_pcm_and_moves_following_timing(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);folder=root/'generation';chapter=folder/'c0001';chapter.mkdir(parents=True)
            media=root/'media';media.mkdir();page=root/'page';page.mkdir()
            rate=24000
            original=np.concatenate([np.zeros(8000),.1*np.sin(np.arange(24000)*.04),np.zeros(8000)]).astype('float32')
            replacement=np.concatenate([np.zeros(8000),.1*np.sin(np.arange(36000)*.04),np.zeros(8000)]).astype('float32')
            old=root/'old.wav';new=root/'new.wav';sf.write(old,original,rate);sf.write(new,replacement,rate)
            a,_=prepare_clip(sf.read(old,dtype='float32')[0],rate)
            b,_=prepare_clip(sf.read(new,dtype='float32')[0],rate)
            beginning=np.zeros(12000,dtype='float32');ending=np.full(12000,.01,dtype='float32')
            wave=media/'original.wav';sf.write(wave,np.concatenate([beginning,a,ending]),rate)
            timeline=[{'id':'earlier','start':0,'end':.5},
                      {'id':'target','start':.5,'end':.5+len(a)/rate},
                      {'id':'later','start':.5+len(a)/rate,'end':1+len(a)/rate}]
            for row in timeline:row['word_status']='asr_exact_match'
            write_json(chapter/'c0001.timeline.json',timeline)
            write_json(folder/'frozen-gains.json',{'narrator:voice':1})
            write_json(folder/'progress.json',{'generation':'generation','status':'rendering','chapters':{'c0001':{
                'id':'c0001','units':3,'complete':False,'seconds':1+len(a)/rate,'wav_sha256':file_digest(wave),
                'stream':'/original.wav','audio':'old.mp3','word_flags':0}}})
            before={'unit':{'id':'target','speaker':'narrator','render_voice':'voice'},'path':str(old)}
            after={'path':str(new),'word_check':{'status':'asr_exact_match'}}
            with patch('render_higgs_palette_preview.PAGE',page),patch('render_higgs_palette_preview.MEDIA',media), \
                 patch('render_higgs_palette_preview.page'),patch('higgs_preview_music.attach_music'):
                patch_published_prefix(folder,'c0001',before,after)
                # Another publication can change the chapter's overall hash;
                # the already-installed sentence still must be recognized.
                applied=folder/'pronunciation-revisions/target/applied.json'
                proof=json.loads(applied.read_text('utf8'));proof['new_sha256']='earlier-chapter-version'
                write_json(applied,proof)
                patch_published_prefix(folder,'c0001',before,after)
                # A repeated correction proves the current patched region and
                # replaces just that region, instead of being mistaken for a rerun.
                patch_published_prefix(folder,'c0001',{**before,'path':str(new)},
                    {**after,'path':str(old)})
                patch_published_prefix(folder,'c0001',before,after)
            state=json.loads((folder/'progress.json').read_text('utf-8'));ready=state['chapters']['c0001']
            result,_=sf.read(media/Path(ready['stream']).name,dtype='int16');initial,_=sf.read(wave,dtype='int16')
            self.assertTrue(np.array_equal(result[:12000],initial[:12000]))
            self.assertTrue(np.array_equal(result[12000+len(b):],initial[12000+len(a):]))
            moved=json.loads((chapter/'c0001.timeline.json').read_text('utf-8'))
            self.assertAlmostEqual(moved[2]['start'],timeline[2]['start']+(len(b)-len(a))/rate)
            self.assertEqual(ready['pronunciation_revisions'],['target']*3)

    def test_patch_reproduces_disclosed_one_shot_edge_without_weakening_replacement_gate(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);folder=root/'generation';chapter=folder/'c0001';chapter.mkdir(parents=True)
            media=root/'media';media.mkdir();page=root/'page';page.mkdir();rate=24000
            # The diagnostic one-shot publisher keeps this active ending verbatim.
            original=np.concatenate([np.zeros(8000),np.full(24000,.04,dtype='float32')])
            replacement=np.concatenate([np.zeros(8000),np.full(24000,.03,dtype='float32'),np.zeros(8000)])
            old=root/'old.wav';new=root/'new.wav';sf.write(old,original,rate);sf.write(new,replacement,rate)
            wave=media/'original.wav';sf.write(wave,original,rate)
            timeline=[{'id':'target','start':0,'end':len(original)/rate,'word_status':'asr_exact_match'}]
            write_json(chapter/'c0001.timeline.json',timeline)
            write_json(folder/'frozen-gains.json',{'narrator:voice':1})
            write_json(folder/'progress.json',{'generation':'generation','status':'rendering','chapters':{'c0001':{
                'id':'c0001','units':1,'complete':False,'seconds':len(original)/rate,
                'wav_sha256':file_digest(wave),'stream':'/original.wav','audio':'old.mp3','word_flags':0}}})
            before={'unit':{'id':'target','speaker':'narrator','render_voice':'voice'},'path':str(old),
                    'one_shot':True,'edge':{'unsafe_start':False,'unsafe_end':True}}
            after={'path':str(new),'word_check':{'status':'asr_exact_match'}}
            with patch('render_higgs_palette_preview.PAGE',page),patch('render_higgs_palette_preview.MEDIA',media), \
                 patch('render_higgs_palette_preview.page'),patch('higgs_preview_music.attach_music'):
                patch_published_prefix(folder,'c0001',before,after)
            ready=json.loads((folder/'progress.json').read_text('utf8'))['chapters']['c0001']
            result,_=sf.read(media/Path(ready['stream']).name,dtype='int16')
            prepared,_=prepare_clip(sf.read(new,dtype='float32')[0],rate)
            expected=np.clip(np.floor(prepared*32768),-32768,32767).astype('int16')
            self.assertTrue(np.array_equal(result,expected))

if __name__=='__main__':unittest.main()
