"""Explicit source stress in a proper name is transparent to token discovery."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky import proper_noun_stress as proper,russian_preparation as preparation
from rimsky.name_stress import mark_names
from rimsky.util import digest


class ExplicitProperNounTests(unittest.TestCase):
    def setUp(self):
        dictionary=SimpleNamespace(word_is_known=lambda word:not word.startswith('гунда'))
        patches=[patch.object(proper,'_morph',return_value=dictionary),
            patch('rimsky.name_stress.dictionary_name_forms',return_value=()),
            patch.object(proper,'_stress',side_effect=AssertionError('explicit source stress must not be guessed'))]
        for item in patches:self.addCleanup(item.stop);item.start()

    def inventory(self,text):
        book={'source_hash':'s'*64,'chapters':[{'id':'c1','blocks':[{'id':'p1','text':text}]}]}
        return preparation.proper_inventory(book)

    def lexicon(self,text,names=None):
        inventory=self.inventory(text)
        result=preparation.prepare_names({'inventory':inventory,'names':names or{},'book_sha256':'b'*64},model_pins={})
        return inventory,result

    def test_full_marked_base_controls_unmarked_inflection_and_keeps_offsets(self):
        text='Встретил Гундаха́р. Он говорил с Гундахаром.'
        inventory,result=self.lexicon(text)
        self.assertEqual(inventory['capitalized'],['Гундахар','Гундахаром'])
        self.assertNotIn('Гунда',inventory['capitalized'])
        self.assertEqual(result['proper_nouns'],{'pn:Гундахар':'Гундаха́р'})
        note=inventory['explicit_stress'][0]
        self.assertEqual(text[note['source_start']:note['source_end']],note['word'])
        self.assertEqual(note['source_text_sha256'],digest(text.encode()))
        self.assertEqual(note['plain'],'Гундахар');self.assertEqual(note['stress'],6)
        self.assertEqual(result['explicit_source_families'],[
            {'family':'pn:Гундахар','stressed':'Гундаха́р','sources':[note]}])
        self.assertEqual(mark_names(text,result['proper_nouns'])[0],
            'Встретил Гундаха́р. Он говорил с Гундаха́ром.')

    def test_unmarked_base_can_inherit_source_oblique_stem_stress(self):
        _,result=self.lexicon('Пришел Гундахар. Он говорил с Гундаха́ром.')
        self.assertEqual(result['proper_nouns'],{'pn:Гундахар':'Гундаха́р'})

    def test_conflicting_marks_hold_instead_of_last_wins(self):
        with self.assertRaisesRegex(ValueError,'Conflicting explicit'):
            self.lexicon('Встретил Гундаха́р. Он говорил с Гу́ндахаром.')

    def test_stress_on_a_lost_oblique_ending_holds(self):
        with self.assertRaisesRegex(ValueError,'loses its vowel'):
            self.lexicon('Пришел Гундахар. Он говорил с Гундахаро́м.')

    def test_known_character_lexicon_keeps_priority_over_source_family_guess(self):
        _,result=self.lexicon('Встретил Гундаха́р. Он говорил с Гундахаром.',{'character':'Гу́ндахар'})
        self.assertEqual(result['proper_nouns'],{})
        self.assertEqual(result['explicit_source_families'],[])

    def test_ordinary_lowercase_context_stress_never_becomes_name_family(self):
        inventory=self.inventory('Он ничего не стои́т. Чужой дом.')
        self.assertEqual(inventory['explicit_stress'],[])

    def test_legacy_frozen_lexicon_mode_retains_its_original_interpretation(self):
        text='Встретил Гундаха́р. Он говорил с Гундахаром.'
        with patch.object(proper,'_stress',side_effect=lambda word,*args:word)as guess:
            legacy=proper.proper_noun_lexicon([text],{},True)
        self.assertIn('pn:Гундаха',legacy);self.assertIn('pn:Гундахаром',legacy)
        self.assertEqual(guess.call_count,2)


if __name__=='__main__':unittest.main()
