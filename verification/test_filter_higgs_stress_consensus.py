import unittest
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import filter_higgs_stress_consensus as target


class FakeAccentor:
    def __init__(self, homograph="слово", marked="сл+ово"):
        self.homograph = homograph
        self.marked = marked

    def __call__(self, word, *, put_stress, **kwargs):
        return self.marked if put_stress else self.homograph


class StressConsensusTests(unittest.TestCase):
    def test_consume_all_uses_every_available_split(self):
        goals = target.consensus_goals(5.0, .1, True)
        self.assertEqual(float("inf"), goals["train"])
        self.assertFalse(target.consensus_complete({"train": 10.0, "validation": 0.0}, goals, True))
        self.assertTrue(target.consensus_complete({"train": 10.0, "validation": 1.0}, goals, True))

    def test_meter_wrapper_installs_a_pinned_local_ctc_loader(self):
        module = SimpleNamespace(load_ctc=object())
        target._install_local_ctc_loader(module, Path("some-local-ctc"))
        self.assertTrue(callable(module.load_ctc))
        self.assertEqual(
            Path(module._rimsky_local_ctc_directory),
            Path("some-local-ctc").resolve(),
        )

    def test_silero_accepts_only_one_unambiguous_mark(self):
        self.assertEqual(target.silero_dictionary_index("слово", FakeAccentor()), 0)
        self.assertIsNone(target.silero_dictionary_index("слово", FakeAccentor(homograph="сл+ово")))
        self.assertIsNone(target.silero_dictionary_index("ёлка", FakeAccentor()))
        self.assertIsNone(target.silero_dictionary_index("кто-то", FakeAccentor()))

    def test_acoustic_gate_requires_matching_word_stress_alignment_and_margin(self):
        goal = {"word_index": 1, "base_word": "слово", "correct_vowel_index": 0}
        judgement = [
            {"word": "Это", "best": 0, "align": 0.9, "margin": 0.8},
            {"word": "слово", "best": 0, "align": 0.8, "margin": 0.3},
        ]
        self.assertEqual(target.acoustic_decision(judgement, goal, min_align=.65, min_margin=.15)[1], "accepted")
        changed = [dict(item) for item in judgement]
        changed[1]["best"] = 1
        self.assertEqual(target.acoustic_decision(changed, goal, min_align=.65, min_margin=.15)[1], "meter-stress-disagreement")
        changed = [dict(item) for item in judgement]
        changed[1]["align"] = .5
        self.assertEqual(target.acoustic_decision(changed, goal, min_align=.65, min_margin=.15)[1], "meter-low-alignment")
        changed = [dict(item) for item in judgement]
        changed[1]["margin"] = .05
        self.assertEqual(target.acoustic_decision(changed, goal, min_align=.65, min_margin=.15)[1], "meter-low-margin")


if __name__ == "__main__":
    unittest.main()
