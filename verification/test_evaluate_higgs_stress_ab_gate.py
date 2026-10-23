import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "runtime")]
from evaluate_higgs_stress_ab_gate import accepted_asr


class StressABGateTests(unittest.TestCase):
    def test_exact_asr_passes_without_tolerance(self):
        accepted, deviations = accepted_asr(
            {"status": "asr_exact_match", "expected_words": ["он"], "heard_words": ["он"]},
            "Он пришёл.",
        )
        self.assertTrue(accepted)
        self.assertEqual(deviations, [])

    def test_guarded_proper_noun_difference_passes_and_is_disclosed(self):
        accepted, deviations = accepted_asr(
            {
                "status": "spoken_text_mismatch",
                "expected_words": ["сказал", "гундахар"],
                "heard_words": ["сказал", "гундакар"],
            },
            "Сказал Гундахар.",
        )
        self.assertTrue(accepted)
        self.assertEqual(deviations[0]["rule"], "proper-noun-slot-v1")

    def test_non_name_word_difference_stays_held(self):
        accepted, deviations = accepted_asr(
            {
                "status": "spoken_text_mismatch",
                "expected_words": ["он", "пришёл"],
                "heard_words": ["он", "ушёл"],
            },
            "Он пришёл.",
        )
        self.assertFalse(accepted)
        self.assertEqual(deviations, [])


if __name__ == "__main__":
    unittest.main()
