import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.spoken_pronunciation import apply_stress,unmarked,phrase_stress
from rimsky.util import digest


class SpokenStressTests(unittest.TestCase):
    def test_exact_word_gate_rejects_mixed_identifiers_marks_and_partial_compounds(self):
        word='потом';marked='по\u0301том'
        def apply(text):
            start=text.index(word)
            return apply_stress(text,{'source_text_sha256':digest(text.encode('utf8')),'edits':[{
                'start':start,'end':start+len(word),'word':word,'marked':marked,'sense':'Fixture','evidence':text}]})
        for text in ('abcпотом','потомX','потом2','2потом','_потом','потом_',
                     'потом\u0300','a\u200dпотом','когда-потом','потом-то','потом\u2011то','когда\u2010потом'):
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'entire word'):apply(text)
        for text in ('(потом),','—потом.','- потом','-потом','потом\n'):
            with self.subTest(text=text):self.assertEqual(apply(text),text.replace(word,marked))
    def test_movement_phrase_is_shared_preserves_words_and_is_idempotent(self):
        for text in ['Охотник резко вскочил на ноги и вскинул лук.',
                     'Она встала на ноги.', 'Поставить его на ноги.', 'Поднялся наконец на ноги.']:
            native,plan=phrase_stress(text)
            self.assertIn('на́ ноги',native)
            self.assertEqual(unmarked(native),text)
            self.assertEqual(phrase_stress(native)[0],native)
            self.assertEqual(text[plan['edits'][0]['start']:plan['edits'][0]['end']],'на')
        for text in ['Он посмотрел на ноги.', 'Надел сапоги на ноги.',
                     'Он встал. Посмотрел на ноги.', 'Он встал, глядя на ноги.',
                     'Он вскочил и посмотрел на ноги.', 'Он поднял взгляд на ноги.',
                     'Вскочил на но́ги.', 'Вскочил на ноги́.', 'Вскочил на́ ноги.']:
            self.assertEqual(phrase_stress(text)[0],text)

    def test_word_gate_ignores_stress_without_hiding_omitted_words(self):
        from rimsky.speech_quality import word_tokens
        self.assertEqual(word_tokens('Вскочил на́ ноги.'),word_tokens('Вскочил на ноги.'))
        self.assertNotEqual(word_tokens('Вскочил на́ ноги.'),word_tokens('Вскочил ноги.'))
    def test_context_bound_stress_preserves_letters_and_changes_sound_key(self):
        text='Кровью, потом и немытыми задницами.';start=text.index('потом')
        plan={'source_text_sha256':digest(text.encode('utf8')),'edits':[{'start':start,'end':start+5,
              'word':'потом','marked':'по́том','sense':'sweat, instrumental case','evidence':text}]}
        marked=apply_stress(text,plan)
        self.assertEqual(marked,'Кровью, по́том и немытыми задницами.')
        self.assertEqual(unmarked(marked),text)
        self.assertNotEqual(digest(marked),digest(text))
        with self.assertRaisesRegex(ValueError,'different spoken text'):apply_stress('А потом мы ушли.',plan)

    def test_stress_does_not_change_words_or_remove_yo_and_short_i(self):
        self.assertEqual(unmarked('по́том, её, край'),'потом, её, край')
        text='потом';base={'source_text_sha256':digest(text.encode('utf8')),'edits':[]}
        for marked in ['потом́','па́том','по́то́м']:
            plan={**base,'edits':[{'start':0,'end':5,'word':text,'marked':marked,'sense':'sweat','evidence':'context'}]}
            with self.assertRaises(ValueError):apply_stress(text,plan)


if __name__=='__main__':unittest.main()
