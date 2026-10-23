import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.auto_pronunciation import auto_pronunciation,validate_name_stress
from rimsky.speech_quality import word_tokens

A='́'
NAMES={'erdamon_belar':'Эрдамо'+A+'н Бела'+A+'р','vel':'Вел (Велнари'+A+'н)'}


class AutoPronunciationTests(unittest.TestCase):
    def test_names_homographs_and_yo_only(self):
        text='Все кончено. Потом Белару дали замок, и Вел ушел.'
        out,plan=auto_pronunciation(text,NAMES)
        self.assertIn('Всё',out);self.assertIn('ушёл',out)
        self.assertIn('Бела'+A+'ру',out)
        self.assertIn('Вел ',out)                       # never 'Вёл'
        self.assertNotIn('ко'+A+'нчено',out)             # ordinary words keep Higgs's reading
        self.assertEqual(word_tokens(out.replace(A,'')),word_tokens(text))
        self.assertEqual(plan['policy'],'auto-pronunciation-v1')

    def test_names_are_per_occurrence_and_ordinary_words_keep_their_fixes(self):
        names={'vel':'Вел (А'+A+'тлас)','old_dog':'Ста'+A+'рый пёс'}
        out,_=auto_pronunciation('Вел ушел. Он вел машину, а Атлас лежал рядом. Старый дом стоял пустым.',names,['Старый','пёс'])
        self.assertTrue(out.startswith('Вел ушёл.'))     # the character, sentence-initial
        self.assertIn('он вёл'.capitalize()[0]+'н вёл',out)  # the ordinary verb
        self.assertIn('а А'+A+'тлас',out)               # the character's chosen stress
        self.assertIn('Старый дом',out)                  # ordinary prose at a sentence start

    def test_explicit_marks_win_and_unchanged_text_has_no_plan(self):
        out,_=auto_pronunciation('Кровью, по'+A+'том и немытыми задницами.',{})
        self.assertIn('по'+A+'том',out)
        self.assertEqual(auto_pronunciation('Он молчал.',{}),('Он молчал.',None))

    def test_long_tail_policy_marks_only_the_rarest_words(self):
        text='Месяц назад Белар был похож на дребезжащую рухлядь, что пугала нас громкими выхлопами и скрежещущими шестернями.'
        sparse,_=auto_pronunciation(text,NAMES)
        out,plan=auto_pronunciation(text,NAMES,rare_words=True)
        self.assertEqual(plan['policy'],'stress-rare-v1')
        self.assertEqual(len(plan['rare_words']),2)          # capped, rarest first
        added=[w for w,x in zip(out.split(' '),sparse.split(' ')) if w!=x]
        self.assertEqual(added,plan['rare_words'])
        self.assertIn('Бела'+A+'р',out)                      # names keep the provider's stress
        for common in ('Месяц','назад','похож','громкими'):self.assertIn(common+' ',out)
        self.assertEqual(word_tokens(out.replace(A,'')),word_tokens(text))
        self.assertIsNone(auto_pronunciation('Мы спим.',{},rare_words=True)[1])

    def test_name_proposals_must_keep_letters_and_stress_each_word_once(self):
        validate_name_stress('Эрдамон Белар','Эрдамо'+A+'н Бела'+A+'р')
        validate_name_stress('Вел','Вел')
        for bad in ('Эрдамо'+A+'н Белар','Эрдамо'+A+'н Беллa'+A+'р','Эрда'+A+'мо'+A+'н Бела'+A+'р'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_name_stress('Эрдамон Белар',bad)


if __name__=='__main__':unittest.main()
