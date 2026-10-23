import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.narration_transitions import POLICY,envelope,source_boundaries,bridge_layers,segment_windows
from rimsky.util import write_json,file_digest
from higgs_transition_publication import publish_transition_pauses

class TransitionTests(unittest.TestCase):
    def test_source_separator_and_swell_are_independent_of_music_scene_labels(self):
        rows=[{'id':'a','source_id':'p1','start':0,'end':10},{'id':'b','source_id':'p3','start':14,'end':20}]
        blocks=[{'id':'p1','kind':'paragraph'},{'id':'p2','kind':'separator','text':'* * *'},{'id':'p3','kind':'paragraph'}]
        self.assertEqual(source_boundaries(rows,blocks),[{'kind':'scene','before':'a','after':'b'}])
        w={'start':10,'end':14};g=envelope(w,np.array([3,4,7,10,10.5,12,14,15]))
        self.assertTrue(np.allclose(g,[0,0,.6,1.2,2,2,0,0]))
        scenes=[{'anchor_segment_id':'a','bed':'calm'}];takes={'bed:calm:0':{'audio_key':'verified','duration':20}}
        layers=bridge_layers([],scenes,takes,{'a':0},[w]);self.assertEqual(layers[0]['at'],4)
        self.assertEqual(layers[0]['key'],'verified');self.assertEqual(layers[0]['kind'],'transition')

    def test_production_windows_stop_before_unknown_audio(self):
        rows=[{'id':'a','ordinal':0,'text':'Words','status':'ready','duration':10},
            {'id':'s','ordinal':1,'text':'* * *','status':'ready','duration':4},
            {'id':'b','ordinal':2,'text':'Words','status':'pending','duration':0},
            {'id':'c','ordinal':3,'text':'* * *','status':'ready','duration':4}]
        self.assertEqual(segment_windows(rows),[{'kind':'scene','anchor_segment_id':'s','start':10,'end':14}])

    def test_reflow_preserves_every_spoken_sample_flags_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);gen=root/'generation';source=root/'source';source.mkdir();chapter=gen/'c0001';chapter.mkdir(parents=True)
            media=root/'media';media.mkdir();page=root/'page';page.mkdir()
            blocks=[{'id':'p1','kind':'paragraph'},{'id':'p2','kind':'separator','text':'* * *'},{'id':'p3','kind':'paragraph'}]
            write_json(source/'book.json',{'chapters':[{'id':'c0001','blocks':blocks}]})
            write_json(gen/'manifest.json',{'source_sha256':file_digest(source/'book.json')})
            x=np.r_[np.full(24000,2000,dtype='int16'),np.zeros(2400,dtype='int16'),np.full(24000,-3000,dtype='int16')]
            wave=media/'old.wav';sf.write(wave,x,24000,subtype='PCM_16')
            rows=[{'id':'a','source_id':'p1','start':0,'end':1,'word_status':'asr_exact_match'},
                {'id':'b','source_id':'p3','start':1.1,'end':2.1,'word_status':'spoken_text_mismatch'}]
            write_json(chapter/'c0001.timeline.json',rows)
            ready={'id':'c0001','seconds':2.1,'complete':True,'units':2,'word_flags':1,'wav_sha256':file_digest(wave),'stream':'/old.wav','audio':'old.mp3'}
            state={'chapters':{'c0001':ready}}
            with patch('render_higgs_palette_preview.PAGE',page),patch('render_higgs_palette_preview.MEDIA',media),patch('render_higgs_palette_preview.page'),patch('higgs_preview_music.attach_music'):
                publish_transition_pauses(gen,state);once=copy.deepcopy(state);publish_transition_pauses(gen,state)
            self.assertEqual(state,once);new=state['chapters']['c0001'];self.assertEqual(new['word_flags'],1)
            self.assertAlmostEqual(new['seconds'],11.92)
            y,_=sf.read(media/Path(new['stream']).name,dtype='int16')
            self.assertTrue(np.array_equal(y[:24000],x[:24000]));self.assertTrue(np.array_equal(y[round(4.92*24000):round(5.92*24000)],x[-24000:]))
            self.assertFalse(y[round(5.92*24000):].any())

if __name__=='__main__':unittest.main()
