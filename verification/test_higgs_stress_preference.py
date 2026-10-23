import math
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_higgs_stress_preference as target


class StressPreferenceSummaryTests(unittest.TestCase):
    def test_percentile_interpolates_and_empty_is_none(self):
        self.assertIsNone(target.percentile([], 0.5))
        self.assertEqual(target.percentile([0.0, 10.0], 0.25), 2.5)

    def test_summary_uses_strict_preference_and_configured_margin(self):
        report = target.summarize([
            {"gap_nats": -1.0}, {"gap_nats": 0.0}, {"gap_nats": 5.0}, {"gap_nats": 8.0}
        ], 5.0)
        self.assertEqual(report["pairs"], 4)
        self.assertEqual(report["correct_preferred_fraction"], 0.5)
        self.assertEqual(report["margin_pass_fraction"], 0.5)
        self.assertEqual(report["median_gap_nats"], 2.5)

    def test_strata_are_derived_only_from_bound_target_metadata(self):
        rows = [
            {"gap_nats": 6.0, "target": {"vowel_count": 3, "correct_vowel_index": 2, "stress_from_end": 1}},
            {"gap_nats": -2.0, "target": {"vowel_count": 3, "correct_vowel_index": 0, "stress_from_end": 3}},
        ]
        result = target.stratify(rows, 5.0)
        self.assertEqual(result["vowels:3"]["pairs"], 2)
        self.assertEqual(result["stress_from_end:1"]["margin_pass_fraction"], 1.0)
        self.assertEqual(result["correct_vowel:0"]["correct_preferred_fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
