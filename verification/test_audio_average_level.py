import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_book1_label_acoustics import average_level_dbfs


class AverageLevelTests(unittest.TestCase):
    def test_rms_level_and_pauses(self):
        samples = np.full(16000, .1, dtype=np.float32)
        self.assertAlmostEqual(average_level_dbfs(samples), -20, places=2)
        self.assertAlmostEqual(average_level_dbfs(np.concatenate([samples, np.zeros(16000)])), -23.01, places=2)

    def test_sine_rms_is_not_peak_level(self):
        samples = .5 * np.sin(2 * np.pi * 100 * np.arange(16000) / 16000)
        self.assertAlmostEqual(average_level_dbfs(samples), -9.03, places=2)

    def test_silence_and_invalid_audio(self):
        self.assertEqual(average_level_dbfs(np.zeros(100)), -180)
        for samples in ([], [float('nan')], [float('inf')]):
            with self.assertRaises(ValueError):
                average_level_dbfs(samples)


if __name__ == '__main__':
    unittest.main()
