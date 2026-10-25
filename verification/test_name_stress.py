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

    def test_provider_requires_exactly_one_decision_per_requested_character(self):
        from rimsky.name_stress_analysis import validator,lexicon
        names={'one':'Маша','two':'Иван'}
        row=lambda cid,name,marked:{'character_id':cid,'name':name,'stressed':marked,'confidence':'high','basis':'Fixture'}
        first=row('one','Маша','Ма'+A+'ша');second=row('two','Иван','Ива'+A+'н')
        valid={'names':[first,second]};validator(names)(valid)
        self.assertEqual({'one':first['stressed'],'two':second['stressed']},lexicon(valid))
        for rows in ([first,first,second],[first,row('one','Маша','Маша'+A),second],[first],
                     [first,row('unknown','Иван','Ива'+A+'н')]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):validator(names)({'names':rows})

    def test_dictionary_irregular_case_keeps_the_retained_base_vowel(self):
        marked,edits=mark_names('Павел пришёл с Павлом. Павло'+A+'м уже отмечен.',{'pavel':'Па'+A+'вел'})
        self.assertIn('Па'+A+'вел',marked);self.assertIn('Па'+A+'влом',marked)
        self.assertIn('Павло'+A+'м уже',marked,'Explicit source stress still wins')
        self.assertEqual(marked.replace(A,''),'Павел пришёл с Павлом. Павлом уже отмечен.')

    def test_lost_or_ambiguously_aligned_vowels_abstain(self):
        from rimsky.name_stress import aligned_stress_index
        self.assertEqual(1,aligned_stress_index('Павел','Павлом',1))
        self.assertEqual(1,aligned_stress_index('Павлом','Павел',1))
        self.assertIsNone(aligned_stress_index('Лев','Львом',1))
        self.assertIsNone(aligned_stress_index('Павла','Павел',4))
        self.assertIsNone(aligned_stress_index('аа','а',0))
        self.assertEqual([],mark_names('Львом',{'lev':'Ле'+A+'в'})[1])

    def test_conflicting_families_never_resolve_by_answer_order(self):
        from rimsky.name_stress import name_spans
        names={'alex':'А'+A+'лекс','alexa':'Але'+A+'кса'}
        self.assertEqual([],name_spans('Алекса',names))
        self.assertEqual([],name_spans('Алекса',dict(reversed(list(names.items())))))
        self.assertEqual('Мари'+A+'шкой',mark_names('Маришкой',LEX)[0])

    def test_dictionary_paradigm_expansion_is_cached_and_does_not_guess_unknown_words(self):
        from unittest.mock import patch
        from rimsky.name_stress import dictionary_name_forms
        from rimsky.yo_restoration import _dictionary_parses
        dictionary_name_forms.cache_clear()
        with patch('rimsky.yo_restoration._dictionary_parses',wraps=_dictionary_parses)as lookup:
            forms=dictionary_name_forms('Павел');self.assertIn('павлом',forms)
            self.assertEqual(forms,dictionary_name_forms('Павел'));self.assertEqual(1,lookup.call_count)
        self.assertEqual((),dictionary_name_forms('Эрдамон'))


if __name__=='__main__':unittest.main()
