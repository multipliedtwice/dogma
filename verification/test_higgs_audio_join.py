import sys
import unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.audio_join import prepare_clip,gap_seconds,boundary_check

class JoinTests(unittest.TestCase):
    def test_active_end_is_not_masked_by_fading(self):
        x=np.r_[np.zeros(100),np.full(2000,.2)]
        self.assertTrue(boundary_check(x,1000)['unsafe_end'])
        with self.assertRaisesRegex(ValueError,'retake'):prepare_clip(x,1000)

    def test_speech_samples_and_dynamics_survive(self):
        speech=np.sin((np.arange(500)+1)*.17).astype('float32')*.08
        x=np.r_[np.full(300,.0001),speech,np.full(350,.0001)]
        y,info=prepare_clip(x,1000)
        self.assertTrue(np.array_equal(y[40:540],speech))
        self.assertEqual(len(y),580)
        self.assertEqual(info['trimmed_start_samples'],260)
        self.assertEqual(info['faded_padding'],['start','end'])

    def test_existing_padding_counts_toward_pause(self):
        prev={'source_id':'p','sentence_index':1,'scene':1,'kind':'paragraph'}
        current={**prev,'sentence_index':2}
        gap=gap_seconds(prev,current,{'trailing_padding':.07},{'leading_padding':.05})
        self.assertAlmostEqual(gap,.12)
        self.assertAlmostEqual(gap_seconds(prev,{**current,'scene':2},{'trailing_padding':.07},{'leading_padding':.05}),1.03)

    def test_faint_active_edge_is_also_held(self):
        x=np.r_[np.zeros(300),np.full(80,.007)]
        with self.assertRaises(ValueError):prepare_clip(x,1000)

if __name__=='__main__':unittest.main()
