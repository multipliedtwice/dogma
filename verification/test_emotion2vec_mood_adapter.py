import importlib.util
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mood_adapter", ROOT / "scripts/train_emotion2vec_mood_adapter.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MoodAdapterPolicyTests(unittest.TestCase):
    def test_supported_labels_use_only_development_chapters(self):
        rows = []
        for chapter in ("c1", "c2", "c3"):
            rows += [{"book": "b", "chapter_id": chapter, "mood": "calm"}] * 4
        rows += [{"book": "b", "chapter_id": "test", "mood": "leaked"}] * 20
        split = {"books": {"b": {"test_chapters": ["test"]}}}
        self.assertEqual(MODULE.supported_labels(rows, split), ["calm"])

    def test_outer_partition_is_book_specific(self):
        split = {"books": {"a": {"test_chapters": ["c1"]}, "b": {"test_chapters": ["c2"]}}}
        self.assertEqual(MODULE.outer_partition({"book": "a", "chapter_id": "c1"}, split), "test")
        self.assertEqual(MODULE.outer_partition({"book": "b", "chapter_id": "c1"}, split), "development")

    def test_book2_weight_is_explicitly_lower(self):
        # The default is part of the anti-bias contract, not an incidental CLI detail.
        source = (ROOT / "scripts/train_emotion2vec_mood_adapter.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--book2-weight", type=float, default=.5)', source)

    def test_voice_conditioning_has_unknown_family_fallback(self):
        source = (ROOT / "scripts/train_emotion2vec_mood_adapter.py").read_text(encoding="utf-8")
        self.assertIn("voice_to_index.get(row[\"voice\"], 0)", source)
        self.assertIn("nn.Embedding(voices + 1, voice_dim, padding_idx=0)", source)
        self.assertIn('"voice_vocabulary_source": "inner training partition only"', source)

    def test_voice_conditioning_does_not_hard_mask_moods(self):
        # An unobserved pair may still be correct in a later chapter. The
        # palette is conditioning evidence, never an allow-list.
        source = (ROOT / "scripts/train_emotion2vec_mood_adapter.py").read_text(encoding="utf-8")
        self.assertNotIn("palette_mask", source)

    def test_relative_reference_uses_neutral_with_documented_fallbacks(self):
        rows = [
            {"voice": "a", "mood": "neutral"},
            {"voice": "a", "mood": "neutral"},
            {"voice": "a", "mood": "neutral"},
            {"voice": "a", "mood": "calm"},
            {"voice": "b", "mood": "calm"},
            {"voice": "b", "mood": "warm"},
            {"voice": "b", "mood": "soft"},
            {"voice": "c", "mood": "calm"},
        ]
        summaries = {i: np.asarray([float(i)]) for i in range(len(rows))}
        refs, basis = MODULE.family_references(rows, list(range(len(rows))), summaries)
        self.assertEqual(basis["a"]["kind"], "family_neutral")
        self.assertAlmostEqual(float(refs["a"][0]), 1.0)
        self.assertEqual(basis["b"]["kind"], "family_all_moods_fallback")
        self.assertAlmostEqual(float(refs["b"][0]), 5.0)
        self.assertEqual(basis["c"]["kind"], "global_neutral_fallback")


if __name__ == "__main__":
    unittest.main()
