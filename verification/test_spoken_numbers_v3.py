import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.spoken_numbers import expand


class OrdinalSuffixTests(unittest.TestCase):
    def test_v3_inflects_ordinal_suffixes_by_suffix_and_preposition(self):
        cases={'в 2058-м':'в две тысячи пятьдесят восьмом','к 5-му':'к пятому','на 3-й улице':'на третьей улице',
               'в 90-х':'в девяностых','в 1990-е':'в тысяча девятьсот девяностые','5-е место':'пятое место',
               '2-я рота':'вторая рота','в 21-м веке':'в двадцать первом веке','с 5-м уровнем':'с пятым уровнем',
               'с 1-го':'с первого','было 300 человек':'было триста человек'}
        for text,spoken in cases.items():self.assertEqual(expand(text,'grouped-digits-v3'),spoken,text)

    def test_v2_is_unchanged_for_frozen_generations(self):
        self.assertEqual(expand('в 2058-м','grouped-digits-v2'),'в две тысячи пятьдесят восемь-м')


if __name__=='__main__':unittest.main()

