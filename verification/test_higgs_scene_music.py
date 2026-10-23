import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import music
from rimsky.util import write_json,file_digest
from rimsky.util import digest
from higgs_preview_music import attach_music
from higgs_file_checks import cached_sha


class SceneMusicBridgeTests(unittest.TestCase):
    def test_failed_vocal_seeds_are_not_repeated_and_shorter_raw_take_still_needs_a_check(self):
        from build_higgs_scene_music import run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();write_json(source/'book.json',{})
            gen=root/'generation';folder=gen/'scene-music-v2';folder.mkdir(parents=True)
            cue={'id':'bed:action:1','kind':'bed','mood':'action','seconds':22,'seed':123,
                 'style':music.compose_style('bed','action',{'genres':['industrial'],'instruments':['dark synth pad']})}
            write_json(folder/'manifest.json',{'source_sha256':file_digest(source/'book.json'),
                'module_sha256':file_digest(ROOT/'runtime/rimsky/music.py'),'layout':{'takes':[cue]}})
            pack={'_fingerprint':'fixture','generation':{'cot':'off','num_inference_steps':8}}
            requests=[]
            def generate(pack,spec,take,output,stop):
                requests.append((spec,take));n=spec['request']['semantic_max_tokens']//25*48000
                samples=np.sin(np.arange(n)*.04).astype('float32')*.1
                sf.write(output,np.stack([samples,samples],axis=1),48000)
                return output
            def verifier(command,**kwargs):
                raw=Path(command[command.index('--audio')+1]);out=Path(command[command.index('--output')+1])
                with sf.SoundFile(raw) as f:seconds=len(f)/f.samplerate
                words=['hmm'] if seconds>20 else []
                write_json(out,{'audio_sha256':file_digest(raw),'heard':' '.join(words),'heard_words':words})
            with patch('build_higgs_scene_music.music.load_music_pack',return_value=pack), \
                 patch('build_higgs_scene_music.music.generate',side_effect=generate), \
                 patch('build_higgs_scene_music.refresh_preview'), \
                 patch('build_higgs_scene_music.subprocess.run',side_effect=verifier), \
                 patch('regenerate_higgs_book_trial.wait_for_audio_resources'):
                run(gen);run(gen)
            self.assertEqual(len(requests),4)
            record=json.loads((folder/'receipts.json').read_text('utf8'))['takes'][cue['id']]
            self.assertEqual(record['recovery']['stage'],1)
            self.assertEqual(record['checks']['vocals'],'none_heard')
            self.assertEqual(record['seconds'],20)
            self.assertEqual(len(record['previous_failures']),3)
            self.assertEqual(record['requested_spec_sha256'],digest(music.cue_spec(pack,cue,'cpu')))

    def test_shared_scene_layout_mixes_and_extends_ending_without_changing_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen=Path(tmp);chapter=gen/'c0003';chapter.mkdir();page=gen/'page';page.mkdir();media=gen/'media';media.mkdir()
            folder=gen/'scene-music-v2';assets=folder/'assets';assets.mkdir(parents=True)
            voice=media/'speech.wav';sf.write(voice,np.sin(np.arange(8*24000)*.05)*.1,24000,subtype='PCM_16')
            before=voice.read_bytes();sha=file_digest(voice)
            write_json(chapter/'c0003.timeline.json',[{'id':'a','start':0,'end':4},{'id':'b','start':4,'end':8}])
            scenes=[{'id':'one','chapter_id':'c0003','anchor_segment_id':'a','bed':'calm','ambience':'none',
                'phrases':[[0,0]],'cycle':20,'mood':'calm'}]
            cue={'id':'ending:c0003','kind':'ending','anchor_segment_id':'b','start':0}
            write_json(folder/'manifest.json',{'layout':{'scenes':scenes,'takes':[{'kind':'bed'},cue]}})
            takes={}
            for identifier,seconds in [('bed:calm:0',20),('ending:c0003',24)]:
                name=identifier.replace(':','-')+'.wav';wav=assets/name
                sf.write(wav,np.full((round(seconds*48000),2),.002,dtype='float32'),48000,subtype='PCM_16')
                takes[identifier]={'audio':name,'sha256':file_digest(wav),'seconds':seconds,
                    'cue':{'kind':'ending' if identifier.startswith('ending') else 'bed'},'checks':{'vocals':'none_heard'}}
            write_json(folder/'receipts.json',{'manifest_sha256':file_digest(folder/'manifest.json'),'takes':takes})
            ready={'seconds':8,'complete':True,'stream':'/media/speech.wav','wav_sha256':sha,'audio':'speech.mp3'}
            state={'chapters':{'c0003':ready}}
            def encode(command,**kwargs):Path(command[-1]).write_bytes(b'encoded fixture')
            with patch('higgs_preview_music.subprocess.run',side_effect=encode):
                attach_music(chapter,'c0003',state,page,media)
            self.assertEqual(voice.read_bytes(),before)
            self.assertEqual([r['kind'] for r in ready['music']],['scene_bed','ending'])
            self.assertEqual(ready['music'][1]['start'],8)
            self.assertEqual(ready['music'][1]['seconds'],22)
            record=json.loads((chapter/'c0003.music-overlay.json').read_text('utf8'))
            self.assertEqual(record['timing_shift'],0)
            self.assertLess(record['combined_peak'],.999)
            for layer in record['music_spec']['placed']:self.assertEqual(layer['gain'],1.0)  # MUSIC_GAIN
            # Progressive prefixes must not play an ending or shift speech.
            ready['complete']=False
            with patch('higgs_preview_music.subprocess.run',side_effect=encode):attach_music(chapter,'c0003',state,page,media)
            self.assertEqual([r['kind'] for r in ready['music']],['scene_bed'])

    def test_requested_upgrade_waits_for_real_music_and_does_not_use_old_piano(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen=Path(tmp);chapter=gen/'c0001';chapter.mkdir()
            write_json(gen/'scene-music.request.json',{'generation':'test'})
            state={'chapters':{'c0001':{'music':[{'kind':'opening'}],'audio_with_music':'old.mp3'}}}
            attach_music(chapter,'c0001',state,gen,gen)
            self.assertEqual(state['chapters']['c0001']['music'],[])
            self.assertNotIn('audio_with_music',state['chapters']['c0001'])
            self.assertEqual(state['music']['status'],'pending')

    def test_cached_checksum_detects_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model';p.write_bytes(b'first')
            self.assertEqual(cached_sha(p),file_digest(p));self.assertEqual(cached_sha(p),file_digest(p))
            p.write_bytes(b'second')
            self.assertEqual(cached_sha(p),file_digest(p))


if __name__=='__main__':unittest.main()
