"""Pure explicit-source cast-family authority, without taggers/providers."""
import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.source_name_stress import source_names,explicit_mark
from rimsky.name_stress import mark_names,parse
from rimsky.series_characters import _name_overlay
from rimsky.util import digest


class SourceCastNameStressTests(unittest.TestCase):
    def evidence(self,word,source='p1'):
        plain,index=explicit_mark(word)
        return {'word':word,'plain':plain,'stress':index,'source_id':source,'source_start':5,
                'source_end':5+len(word),'source_text_sha256':digest(('Text '+word).encode())}
    def row(self,name,stressed,source='model'):
        return {'name':name,'stressed':stressed,'source':source}

    def test_explicit_source_cast_stress_overrides_model_and_propagates_other_forms(self):
        rows={'g':self.row('Гундахар (Мариша)','Гу́ндахар (Мари́ша)')};before=copy.deepcopy(rows)
        names,proof=source_names([self.evidence('Гундаха́р')],rows,{})
        self.assertEqual('Гундаха́р (Мари́ша)',names['g'])
        self.assertEqual('Гундаха́ром',mark_names('Гундахаром',names)[0])
        self.assertEqual(rows,before);self.assertEqual('source_override',proof[0]['status'])

    def test_known_dictionary_name_uses_explicit_oblique_stress_without_model_guess(self):
        names,_=source_names([self.evidence('Па́влом')],{'p':self.row('Павел','Паве́л')},{})
        self.assertEqual('Па́вел',names['p'])
        self.assertEqual('Па́влом',mark_names('Павлом',names)[0])

    def test_hybrid_human_primary_does_not_protect_untouched_model_alias(self):
        character={'id':'m','name':'Маришка','aliases':['Мозес']}
        row=_name_overlay({'source_hash':'s','chapters':[]},character,
            self.row('Маришка (Мозес)','Ма́ришка (Мо́зэс)'),{'names':{'маришка':'Мари́шка'}})
        names,proof=source_names([self.evidence('Ма́ришка'),self.evidence('Мозе́с','p2')],{'m':row},{'маришка':'Мари́шка'})
        self.assertEqual('Мари́шка (Мозэ́с)',names['m'])
        self.assertEqual({'human_precedence','source_override'},{r['status']for r in proof})
        self.assertEqual('Мари́шка (Мо́зэс)',row['stressed'])

    def test_direct_human_full_row_and_canonical_family_resolve_source_conflict(self):
        source=[self.evidence('Гундаха́р'),self.evidence('Гу́ндахар','p2')]
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            source_names(source,{'g':self.row('Гундахар','Гунда́хар')},{})
        human=self.row('Гундахар','Гунда́хар','listener')
        names,proof=source_names(source,{'g':human},{'гундахар':'Гунда́хар'})
        self.assertEqual('Гунда́хар',names['g']);self.assertTrue(all(r['status']=='human_precedence'for r in proof))

    def test_ambiguous_families_and_lost_vowels_hold_without_human_authority(self):
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            source_names([self.evidence('Але́кса')],{'a':self.row('Алекс','А́лекс'),'b':self.row('Алекса','Але́кса')},{})
        with self.assertRaisesRegex(ValueError,'retained base vowel'):
            source_names([self.evidence('Льво́м')],{'l':self.row('Лев','Лев')},{})
        names,proof=source_names([self.evidence('Льво́м')],{'l':self.row('Лев','Лев','listener')},{'лев':'Ле́в'})
        self.assertEqual({'l':'Лев'},names);self.assertEqual('human_precedence',proof[0]['status'])

    def test_source_binding_and_human_word_attribution_are_validated(self):
        rows={'g':self.row('Гундахар','Гу́ндахар')};source=self.evidence('Гундаха́р')
        for field,value in (('stress',0),('source_end',999),('source_text_sha256','invalid')):
            with self.subTest(field=field),self.assertRaises(ValueError):source_names([{**source,field:value}],rows,{})
        character={'id':'g','name':'Гундахар'}
        projected=_name_overlay({'source_hash':'s','chapters':[]},character,rows['g'],{'names':{'гундахар':'Гунда́хар'}})
        projected['series_listener_overlay']['words'][0]['stressed']='Гундаха́р'
        with self.assertRaisesRegex(ValueError,'binding'):source_names([source],{'g':projected},{})

    def test_stress_only_source_preserves_model_hard_e_respelling(self):
        names,_=source_names([self.evidence('Мозе́с')],{'m':self.row('Мозес','Мо́зэс')},{})
        self.assertEqual('Мозэ́с',names['m'])
        names,_=source_names([self.evidence('Мо́зес')],{'m':self.row('Мозес','Мозёс')},{})
        self.assertEqual('Мо́зес',names['m'],'A lower-priority model ё cannot add a second stress')

    def test_explicit_yo_source_carries_letter_and_stress_without_redundant_acute(self):
        source=self.evidence('Мозёс')
        names,proof=source_names([source],{'m':self.row('Мозес','Мо́зэс')},{})
        self.assertEqual('Мозёс',names['m']);self.assertNotIn('́',names['m'])
        self.assertEqual('Мозёс ответил. Он видел Мозёса.',mark_names('Мозёс ответил. Он видел Мозеса.',names)[0])
        self.assertEqual('Мозёс',proof[0]['sources'][0]['word'])
        protected=self.row('Мозес','Мо́зес','listener')
        with self.assertRaisesRegex(ValueError,'source ё conflicts'):
            source_names([source],{'m':protected},{'мозес':'Мо́зес'})
        with self.assertRaisesRegex(ValueError,'source ё conflicts'):
            source_names([source],{},{'мозес':'Мо́зес'})
        protected=self.row('Мозес','Мозёс','listener')
        names,proof=source_names([source],{'m':protected},{'мозес':'Мозёс'})
        self.assertEqual('Мозёс',names['m']);self.assertEqual('human_precedence',proof[0]['status'])
        names,proof=source_names([source],{},{'мозес':'Мозёс'})
        self.assertEqual({},names);self.assertEqual('human_precedence',proof[0]['status'])


if __name__=='__main__':unittest.main()
