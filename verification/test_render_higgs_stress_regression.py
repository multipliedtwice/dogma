import unittest

from scripts.render_higgs_stress_regression import apply_mark


class StressRegressionRenderTests(unittest.TestCase):
    def test_replaces_only_the_selected_word_and_existing_mark(self):
        self.assertEqual(
            apply_mark("Ар-Ха́кон затуши́л сигару.", "Ар-Хакон", "Ар-Хако́н"),
            "Ар-Хако́н затуши́л сигару.",
        )
        self.assertEqual(apply_mark("к само́й границе", "самой", "са́мой"), "к са́мой границе")
        self.assertEqual(apply_mark("Ха́нгвила с нами нет", "Хангвил", "Ха́нгвил"),
                         "Ха́нгвила с нами нет")

    def test_requires_unique_word(self):
        with self.assertRaisesRegex(ValueError, "found 2"):
            apply_mark("слово и слово", "слово", "сло́во")


if __name__ == "__main__":
    unittest.main()
