import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'));sys.path.insert(0,str(ROOT/'scripts'))
import finalize_higgs_trial_cast as casting


class PersonaCastTests(unittest.TestCase):
    def test_later_book_facts_reach_first_chapter_cast_before_it_is_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'book';(folder/'source').mkdir(parents=True)
            (folder/'analysis/c1').mkdir(parents=True)
            def save(path,value):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
            save(folder/'analysis/c1/cast.json',[{'id':'general','name':'Генерал','voice':'masculine_1'}])
            save(folder/'source/book.json',{'chapters':[{'id':'c1','blocks':[{'text':'— Я генерал.'}]},
                {'id':'c13','blocks':[{'text':'Генерал был мёртвым игвом.'}]}]})
            save(root/'training-work/higgs-production/datasets/reviewed-20261012-v1/references.json',{'masculine_6':[{}]})
            save(root/'manual-labeling/reality-benders-2-full/metadata/voice-families.json',{'families':[{'id':'masculine_6'}]})
            personas={'general':{'nature':'undead','pitch':'low','evidence':['Генерал был мёртвым игвом.']}}
            def model(target,name,system,payload,schema,validate):
                self.assertEqual(payload['whole_book_character_profiles']['general']['nature'],'undead')
                self.assertIn('Генерал был мёртвым игвом.',payload['source_context'])
                answer={'general':{'voice':'masculine_6','gender_or_role':'male','reason':'low undead register',
                                   'evidence':'Генерал был мёртвым игвом.'}}
                validate(answer);return answer
            with patch.object(casting,'ROOT',root),patch.object(casting,'call',side_effect=model) as calls:
                result=casting.finalize(folder,'c1','test',personas)
                self.assertEqual(result['general']['voice'],'masculine_6')
                self.assertEqual(casting.finalize(folder,'c1','test',personas),result)
                self.assertEqual(calls.call_count,1)
                with self.assertRaisesRegex(ValueError,'profiles changed'):
                    casting.finalize(folder,'c1','test',{'general':{**personas['general'],'pitch':'high'}})
                self.assertFalse((folder/'final-cast.json').exists())


if __name__=='__main__':unittest.main()
