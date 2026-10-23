import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.voice_separation import separation_report, unit_vector


class VoiceSeparationTests(unittest.TestCase):
    def test_conflicting_casts_are_visible_against_within_voice_spread(self):
        report = separation_report({'a': [[1, .2], [1, -.2]], 'b': [[1, .1], [1, -.1]]})
        pair = report['pairs'][0]
        self.assertTrue(pair['within_spread_overlap_warning'])
        self.assertGreater(pair['leave_one_utterance_out_confusions'], 0)
        self.assertEqual(pair['evaluated'], 4)

    def test_distinct_voices_and_insufficient_examples(self):
        report = separation_report({'a': [[1, 0], [1, .01]], 'b': [[0, 1], [.01, 1]]})
        self.assertFalse(report['pairs'][0]['within_spread_overlap_warning'])
        self.assertEqual(report['pairs'][0]['leave_one_utterance_out_confusions'], 0)
        sparse = separation_report({'a': [[1, 0]], 'b': [[0, 1]]})
        self.assertEqual(sparse['pairs'][0]['evaluated'], 0)
        self.assertIsNone(sparse['characters']['a']['within_p95_cosine_distance'])

    def test_invalid_vectors_are_not_similarity_scores(self):
        for value in ([0, 0], [float('nan'), 1], [[1, 2]]):
            with self.assertRaises(ValueError):
                unit_vector(value)
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            separation_report({'a': [[1, 2]], 'b': [[1, 2, 3]]})


if __name__ == '__main__':
    unittest.main()
