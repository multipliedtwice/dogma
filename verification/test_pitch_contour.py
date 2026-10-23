import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from book1_pitch_contour import pitch_contour


class PitchContourTests(unittest.TestCase):
    def test_rise_fall_and_register_independence(self):
        times=np.arange(0,4,.016)
        rising=120*2**(times/12)
        a=pitch_contour(times,rising,0,4)
        b=pitch_contour(times,rising*1.5,0,4)
        self.assertLess(a['pitch_start_offset_semitones'],0)
        self.assertGreater(a['pitch_end_offset_semitones'],0)
        self.assertGreater(a['pitch_ending_change_semitones'],0)
        self.assertEqual(a['relative_pitch_curve_semitones'],b['relative_pitch_curve_semitones'])
        self.assertAlmostEqual(b['pitch_start_hz']/a['pitch_start_hz'],1.5,places=3)
        falling=pitch_contour(times,rising[::-1],0,4)
        self.assertLess(falling['pitch_ending_change_semitones'],0)

    def test_unvoiced_edges_and_silence_are_not_invented(self):
        times=np.concatenate([np.arange(.8,1.8,.016),np.arange(2.4,3.1,.016)])
        contour=pitch_contour(times,np.full(len(times),120),0,4)
        self.assertIsNone(contour['pitch_start_hz'])
        self.assertIsNone(contour['pitch_end_hz'])
        self.assertIsNone(contour['pitch_ending_change_semitones'])
        self.assertIsNone(contour['relative_pitch_curve_semitones'][6])
        measured=[v for v in contour['relative_pitch_curve_semitones'] if v is not None]
        self.assertGreater(len(measured),4)
        self.assertTrue(all(v==0 for v in measured))


if __name__=='__main__':
    unittest.main()
