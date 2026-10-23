import unittest

from scripts.evaluate_higgs_generated_stress import intended_target


class GeneratedStressEvaluationTests(unittest.TestCase):
    def test_intended_word_and_vowel_index(self):
        self.assertEqual(intended_target("Ар-Хакон", "Ар-Хако́н"), ("Хакон", 1))
        self.assertEqual(intended_target("самой", "са́мой"), ("самой", 0))
        self.assertEqual(intended_target("во плоти", "во плоти́"), ("плоти", 1))


if __name__ == "__main__":
    unittest.main()
