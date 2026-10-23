import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.auto_pronunciation import auto_pronunciation,policy_options
from rimsky.name_stress import name_spans
from rimsky.proper_noun_stress import proper_noun_lexicon

A='́'
TEXTS=['Мы ехали в Затолис. Потом в Затолисе шёл дождь.','Самый безумный игв, а рядом игвы и игва.',
       'Кроколиск напал, но кроколиска убили.','Он видел Солтмира и Солтмиру.','ХАААРРР! закричал он.']


class ProperNounStressTests(unittest.TestCase):
    def setUp(self):
        self.names={'glas':'Глас'}
        self.lexicon={**self.names,**proper_noun_lexicon(TEXTS,self.names)}

    def render(self,text):
        return auto_pronunciation(text,self.lexicon,[],**policy_options('stress-rare-v2'))[0]

    def test_one_vowel_names_keep_their_stem_stress_in_longer_forms(self):
        out=self.render('Надо передать Гласу список, а Глас ушёл.')
        self.assertIn('Гла'+A+'су',out);self.assertIn('Глас ушёл',out)   # one-vowel form stays unmarked

    def test_invented_words_share_one_stress_in_every_case(self):
        out=self.render('Он увидел игва, а игвы ушли.')
        self.assertIn('и'+A+'гва',out);self.assertIn('и'+A+'гвы',out)

    def test_families_share_the_base_form_stress(self):
        a=self.render('Кроколиск напал.');b=self.render('Кроколиска убили.')
        self.assertEqual(a.index(A),b.index(A))                           # same stem vowel

    def test_oblique_only_base_with_ending_stress_is_not_spread(self):
        self.assertFalse(any(k.casefold().endswith('солтмира') and v.endswith('а'+A) for k,v in self.lexicon.items()))

    def test_v1_policy_is_unchanged(self):
        out=auto_pronunciation('Надо передать Гласу список.',self.names,[],**policy_options('stress-rare-v1'))[0]
        self.assertNotIn('Гла'+A+'су',out)

    def test_shouting_is_not_a_name(self):
        self.assertFalse(any('ХААА' in k for k in self.lexicon))


if __name__=='__main__':unittest.main()
