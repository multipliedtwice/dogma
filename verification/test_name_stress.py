import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.name_stress import mark_names,parse

A='́'
LEX={'erdamon_belar':'Эрдамо́н Бела́р','ar_hakon':'Ар-Хако́н','marishka':'Мари́шка'}


class NameStressTests(unittest.TestCase):
    def test_every_case_form_keeps_the_character_stress(self):
        text='Белар молчал. Белару дали меч, у Белара не было слов, а с Беларом спорить не стали.'
        marked,_=mark_names(text,LEX)
        for form in ('Бела'+A+'р','Бела'+A+'ру','Бела'+A+'ра','Бела'+A+'ром'):
            self.assertIn(form,marked)

    def test_hyphenated_names_feminine_stems_and_explicit_marks(self):
        marked,_=mark_names('Ар-Хакону и Маришкой; Бе'+A+'лар уже размечен.',LEX)
        self.assertIn('Ар-Хако'+A+'ну',marked)
        self.assertIn('Мари'+A+'шкой',marked)
        self.assertIn('Бе'+A+'лар',marked)            # explicit stress in text wins
        self.assertNotIn('Бела'+A+'р уже',marked)

    def test_ordinary_words_and_source_letters_are_untouched(self):
        text='Белые стены и беларусский говор, а белару не говорили.'
        self.assertEqual(mark_names(text,LEX)[0],text)
        marked,_=mark_names('Белару',LEX)
        self.assertEqual(marked.replace(A,''),'Белару')

    def test_lexicon_requires_one_stress_after_a_vowel(self):
        for bad in ('Белар','Бе'+A+'ла'+A+'р','Б'+A+'елар'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse(bad)


if __name__=='__main__':unittest.main()
