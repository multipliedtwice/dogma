import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "series_label_predictor", ROOT / "scripts/train_series_label_predictor.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SeriesLabelPredictorTests(unittest.TestCase):
    def test_supported_labels_never_use_outer_chapter_counts(self):
        rows = []
        for chapter in ("c1", "c2", "c3"):
            rows += [{"book": "b", "chapter_id": chapter, "voice": "supported"}] * 4
        rows += [{"book": "b", "chapter_id": "test", "voice": "outer_only"}] * 20
        split = {"books": {"b": {"test_chapters": ["test"]}}}
        self.assertEqual(MODULE.supported(rows, split, "voice"), ["supported"])

    def test_frozen_candidates_cannot_auto_accept(self):
        source = (ROOT / "scripts/train_series_label_predictor.py").read_text(encoding="utf-8")
        self.assertIn('"automatic_acceptance": False', source)
        self.assertIn('"production_promoted": False', source)
        self.assertIn('"next_pristine_gate": "reality-benders-3"', source)


if __name__ == "__main__":
    unittest.main()
