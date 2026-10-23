import copy
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'runtime'))
from build_book_fix_list import build
from render_higgs_palette_preview import carry_book_fix
from seed_iteration_generation import book_fixes_binding, production_engine_binding, frozen_pronunciation_generations

SPOKEN='Есть еще краюшка хлеба, пара яиц и с пол фляги мёда.'
PLAN={'expected_spoken':SPOKEN,'native_text':SPOKEN.replace('яиц и','яиц и,'),'recovery_kind':'missing_word_phrasing',
      'evidence':{'source':'bound deletion-only word check'}}

def row(plan,status='asr_exact_match',retake=3,**extra):
    unit={'id':'c1-u1','spoken':'x','retake_number':retake}
    if plan:unit['spoken_text_plan']=plan
    return {'unit':unit,'receipt':{'audio_sha256':'aa'},'word_check':{'status':status,'audio_sha256':'bb',**extra}}

class BookFixTests(unittest.TestCase):
    def write(self,root,generation,sub,data):
        path=root/generation/sub/'selected.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')

    def test_builder_picks_verified_plans_keyed_by_text_earliest_take(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'source').mkdir();(root/'source/book.json').write_text('{}')
            self.write(root,'audio-palette-v2','c0001/chunk-000',{'a':row(PLAN,retake=9),'b':row(None),
                'c':row(dict(PLAN,expected_spoken='other'),status='asr_mismatch')})
            self.write(root,'audio-palette-v3','c0001/chunk-000',{'a':row(PLAN,retake=2,independent_report='r.json')})
            self.write(root,'audio-palette-v3','c0001/chunk-000/word-x/take-1',{'a':row(PLAN,retake=0)})
            result=build(root)
            self.assertEqual(list(result['entries']),[SPOKEN])
            entry=result['entries'][SPOKEN]
            self.assertEqual(entry['spoken_text_plan'],PLAN)
            self.assertEqual(entry['source']['generation'],'audio-palette-v3')
            self.assertEqual(entry['source']['verified_via'],'r.json')
            self.assertEqual(entry['source']['audio_sha256'],'bb')
            self.assertTrue((root/'book-fixes.json').exists())

    def fixes(self):
        return {'sha256':'f'*64,'entries':{SPOKEN:{'spoken_text_plan':PLAN,'source':{'generation':'g'}}}}

    def test_preparation_applies_only_to_exact_text_and_records_carry(self):
        unit={'id':'u','text':SPOKEN};other={'id':'v','text':SPOKEN+' Ещё.'}
        self.assertTrue(carry_book_fix(unit,self.fixes()))
        self.assertFalse(carry_book_fix(other,self.fixes()))
        self.assertNotIn('spoken_text_plan',other)
        plan=unit['spoken_text_plan']
        self.assertEqual(plan['carried_forward'],{'book_fixes_sha256':'f'*64,'source':{'generation':'g'}})
        self.assertEqual({k:v for k,v in plan.items() if k!='carried_forward'},PLAN)

    def test_own_plan_untouched_and_invalid_carry_skipped(self):
        own={'expected_spoken':SPOKEN,'native_text':'z'};unit={'id':'u','text':SPOKEN,'spoken_text_plan':own}
        self.assertFalse(carry_book_fix(unit,self.fixes()));self.assertEqual(unit['spoken_text_plan'],own)
        bad=self.fixes();bad['entries'][SPOKEN]['spoken_text_plan']=dict(PLAN,evidence={},native_text='Совсем другое.')
        unit={'id':'u','text':SPOKEN};self.assertFalse(carry_book_fix(unit,bad));self.assertNotIn('spoken_text_plan',unit)

    def test_iteration_seed_freezes_validated_book_fixes(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);book=root/'book.json';book.write_text('{}',encoding='utf8')
            from rimsky.util import file_digest
            fixes=root/'fixes.json';fixes.write_text(json.dumps({
                'schema':1,'book_source_sha256':file_digest(book),'entries':{}},ensure_ascii=False),encoding='utf8')
            self.assertEqual(book_fixes_binding(fixes,book),
                             {'path':str(fixes.resolve()),'sha256':file_digest(fixes)})
            fixes.write_text(json.dumps({'schema':1,'book_source_sha256':'0'*64,'entries':{}}),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'another source'):
                book_fixes_binding(fixes,book)

    def test_iteration_seed_records_exact_production_engine(self):
        pin=SimpleNamespace(checkpoint='fine-tune-v1',path=Path('models/fine-tune.gguf'),sha256='a'*64)
        with patch('seed_iteration_generation.resolve_production_higgs_model',return_value=pin):
            self.assertEqual(production_engine_binding(ROOT),{
                'engine':'higgs','checkpoint':'fine-tune-v1','model':'fine-tune.gguf','model_sha256':'a'*64})

    def test_preflight_source_comes_from_frozen_homograph_request(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);answer=root/'homograph.answer.json';answer.write_text('{}',encoding='utf8')
            (root/'homograph.request.json').write_text(json.dumps({'generations':['final-units-v1']}),encoding='utf8')
            self.assertEqual(frozen_pronunciation_generations(answer),['final-units-v1'])

if __name__=='__main__':unittest.main()
