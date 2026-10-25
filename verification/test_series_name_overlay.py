"""Partial human name families preserve a complete source-bound model answer."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from rimsky.store import Store
from rimsky.listener_stress import record
from rimsky.name_stress_analysis import validator
from rimsky.series_characters import apply_pronunciations,pronunciation_display
from rimsky.respelling import names_sources,respell_text
from test_series_character_state import fb2


class SeriesNameOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'library',correction_profile=self.root/'profile.sqlite3')
        self.addCleanup(self.store.close)
        self.book=self.store.import_data(fb2('Synthetic IV',4,'Он увидел Маришку. Мариша улыбнулась. Мозес ответил.'),'fixture.fb2')
    def human(self,word,marked,base):
        passage='Он увидел '+word+'.'
        report={'id':'human-'+word,'book_id':self.book['id'],'generation_id':'fixture','kind':'stress',
            'source_id':'fixture-p','selected_text':word,'intended_stress':marked,'passage_text':passage,
            'text_offset':passage.index(word),'created':'2026-10-25T00:00:00Z'}
        record(self.store,self.book,report,{'status':'updated','canonical_name':base,'pronunciations':{base.casefold():marked}})
    def apply(self,character,marked,name=None):
        row={'name':name or pronunciation_display(character),'stressed':marked,'source':'model','confidence':'low','basis':'Synthetic model'}
        return apply_pronunciations(self.store,self.book,[character],{'names':{character['id']:row}})
    def validate(self,character,row):
        answer={**row,'character_id':character['id']}
        validator({character['id']:pronunciation_display(character)})({'names':[answer]})

    def test_primary_family_keeps_full_alias_answer_and_repeated_overlay_is_identical(self):
        character={'id':'m','name':'Маришка','aliases':['Мариша']}
        self.human('Маришку','Мари́шку','Маришка')
        result=self.apply(character,'Ма́ришка (Мари́ша)')
        row=result['names']['m'];self.validate(character,row)
        self.assertEqual('Маришка (Мариша)',row['name'])
        self.assertEqual('Мари́шка (Мари́ша)',row['stressed'])
        again=apply_pronunciations(self.store,self.book,[character],result)
        self.assertEqual(row,again['names']['m']);self.validate(character,again['names']['m'])
        self.assertEqual('Ма́ришка (Мари́ша)',row['series_listener_baseline']['row']['stressed'])

    def test_multiword_primary_and_inline_alias_keep_unmatched_automatic_marks(self):
        character={'id':'m','name':'Маришка Иванова (Мариша)','aliases':[]}
        self.human('Маришку','Мари́шку','Маришка')
        result=self.apply(character,'Ма́ришка Ивано́ва (Мари́ша)')
        self.assertEqual('Мари́шка Ивано́ва (Мари́ша)',result['names']['m']['stressed'])
        self.validate(character,result['names']['m'])

    def test_unmatched_model_alias_respelling_keeps_model_attribution(self):
        character={'id':'m','name':'Маришка','aliases':['Мозес']}
        self.human('Маришку','Мари́шку','Маришка')
        result=self.apply(character,'Ма́ришка (Мо́зэс)')
        row=result['names']['m'];self.validate(character,row)
        self.assertEqual({'мозес':'model'},names_sources(result['names']))
        text,proof=respell_text('Он встретил Мозеса.',{'m':row['stressed']},sources=names_sources(result['names']))
        self.assertEqual('Он встретил Мозэса.',text);self.assertEqual('model',proof[0]['source'])
        self.human('Мозес','Мозёс','Мозес')
        later=apply_pronunciations(self.store,self.book,[character],result)
        self.assertEqual('Мари́шка (Мозёс)',later['names']['m']['stressed'])
        self.assertEqual({'мозес':'listener'},names_sources(later['names']))

    def test_missing_authority_restores_only_exact_bound_automatic_baseline(self):
        character={'id':'m','name':'Маришка','aliases':['Мариша']}
        self.human('Маришку','Мари́шку','Маришка')
        result=self.apply(character,'Ма́ришка (Мари́ша)')
        with patch('rimsky.listener_stress.snapshot',return_value={'names':{}}):
            restored=apply_pronunciations(self.store,self.book,[character],result)
        self.assertEqual('model',restored['names']['m']['source'])
        self.assertEqual('Ма́ришка (Мари́ша)',restored['names']['m']['stressed'])
        self.assertNotIn('series_listener_overlay',restored['names']['m'])

    def test_corrupt_baseline_and_changed_cast_cannot_reuse_stored_alias_marks(self):
        character={'id':'m','name':'Маришка','aliases':['Мариша']}
        self.human('Маришку','Мари́шку','Маришка')
        original=self.apply(character,'Ма́ришка (Мари́ша)')
        corrupt=copy.deepcopy(original)
        corrupt['names']['m']['series_listener_baseline']['row']['stressed']='Ма́ришка (Ма́риша)'
        held=apply_pronunciations(self.store,self.book,[character],corrupt)
        self.assertNotIn('m',held['names']);self.assertFalse(held['listener_projection_holds'][0]['refresh_name_analysis'])
        changed=apply_pronunciations(self.store,self.book,[dict(character,aliases=['Марина'])],original)
        self.assertNotIn('m',changed['names']);self.assertTrue(changed['listener_projection_holds'][0]['refresh_name_analysis'])

    def test_legacy_primary_only_projection_needs_alias_analysis_and_unbacked_one_holds(self):
        character={'id':'m','name':'Маришка','aliases':['Мариша']}
        old={'names':{'m':{'name':'Маришка','stressed':'Ма́ришка','source':'series_listener'}}}
        with patch('rimsky.listener_stress.snapshot',return_value={'names':{}}):
            held=apply_pronunciations(self.store,self.book,[character],old)
        self.assertNotIn('m',held['names']);self.assertFalse(held['listener_projection_holds'][0]['refresh_name_analysis'])
        self.human('Маришку','Мари́шку','Маришка')
        held=apply_pronunciations(self.store,self.book,[character],old)
        self.assertNotIn('m',held['names']);self.assertTrue(held['listener_projection_holds'][0]['refresh_name_analysis'])

    def test_word_attribution_cannot_be_reused_after_display_or_sound_mutation(self):
        character={'id':'m','name':'Маришка','aliases':['Мозес']}
        self.human('Маришку','Мари́шку','Маришка')
        result=self.apply(character,'Ма́ришка (Мо́зэс)')
        result['names']['m']['stressed']='Мари́шка (Мозе́с)'
        with self.assertRaisesRegex(ValueError,'binding'):names_sources(result['names'])

    def test_foreign_source_or_character_baseline_is_an_integrity_hold_not_refresh(self):
        character={'id':'m','name':'Маришка','aliases':['Мариша']}
        self.human('Маришку','Мари́шку','Маришка')
        original=self.apply(character,'Ма́ришка (Мари́ша)')
        from rimsky.util import digest
        for field,value in (('book_source_sha256','foreign-source'),('character_id','different-character')):
            modified=copy.deepcopy(original);baseline=modified['names']['m']['series_listener_baseline']
            baseline['binding'][field]=value
            baseline['sha256']=digest({k:v for k,v in baseline.items()if k!='sha256'})
            with self.subTest(field=field):
                result=apply_pronunciations(self.store,self.book,[character],modified)
                self.assertNotIn('m',result['names'])
                self.assertFalse(result['listener_projection_holds'][0]['refresh_name_analysis'])


if __name__=='__main__':unittest.main()
