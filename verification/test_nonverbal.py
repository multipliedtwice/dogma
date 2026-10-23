import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from rimsky.nonverbal import animal_vocalization


class NonverbalTests(unittest.TestCase):
    def test_dog_growl_is_not_spoken_letters(self):
        intent=animal_vocalization('— Р-р-р… ',{'nature':'Старый пёс, фамилиар охотника'})
        self.assertEqual(intent['kind'],'dog_growl')
        self.assertFalse(intent['spoken_words'])

    def test_human_and_real_words_are_not_silently_replaced(self):
        self.assertIsNone(animal_vocalization('Р-р-р…',{'nature':'Человек'}))
        self.assertIsNone(animal_vocalization('— Рано вставать.',{'nature':'пёс'}))
        self.assertIsNone(animal_vocalization('— Р.',{'nature':'пёс'}))
        self.assertIsNone(animal_vocalization('Р-р-р…',{'nature':'Человек, не собака'}))

    def test_event_asset_is_hash_bound_and_is_not_a_word_check_pass(self):
        import tempfile
        from pathlib import Path
        from rimsky.nonverbal import asset_plan,write_asset
        root=Path(__file__).resolve().parents[1]
        if not (root/'resources/nonverbal/dog-growl-source.json').exists():
            self.skipTest('Optional pinned event asset is not installed')
        intent=animal_vocalization('— Р-р-р…',{'nature':'пёс'})
        plan=asset_plan(intent,root)
        with tempfile.TemporaryDirectory() as folder:
            take=write_asset(plan,root,Path(folder)/'event.wav')
            self.assertEqual(take['word_status'],'nonverbal_not_spoken_text')
            self.assertFalse(take['audio_checks']['unsafe_start'])
            self.assertFalse(take['audio_checks']['unsafe_end'])
            plan['reference']['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'changed'):write_asset(plan,root,Path(folder)/'bad.wav')

    def test_generator_routes_dog_event_without_a_native_speech_request(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        root=Path(__file__).resolve().parents[1]
        if not (root/'resources/nonverbal/dog-growl-source.json').exists():
            self.skipTest('Optional event bank is not installed')
        sys.path.insert(0,str(root/'scripts'))
        import render_higgs_book_trial as renderer
        pack={'speech':{'model':'model'},'files':{'model':'a'*64},'voices':[{'id':'male'}]}
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);source=folder/'input.json'
            source.write_text(json.dumps({'cast':[{'id':'dog','gender_or_role':'animal','description':'Старый пёс'}],
                'units':[{'id':'u','chapter_id':'c','speaker':'dog','mode':'speech','voice':'male','text':'— Р-р-р…'}]}),encoding='utf-8')
            with patch.object(renderer,'make_packs',return_value=({'male':pack},{})), \
                 patch.object(renderer.subprocess,'Popen') as native,patch.object(renderer,'assemble') as publication:
                renderer.render(source,folder/'out',['c'])
                native.assert_not_called();publication.assert_not_called()
            plan=json.loads((folder/'out/render-plan.json').read_text(encoding='utf-8'))
            self.assertEqual(plan['units'][0]['synthesis']['backend'],'nonverbal_asset')
            self.assertEqual(plan['units'][0]['spoken'],'')


if __name__=='__main__':unittest.main()
