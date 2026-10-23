import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from rimsky.scene_direction import request,validate,bind,require_realization


class SceneDirectionTests(unittest.TestCase):
    def payload(self):
        texts=['— Хороший… хороший… ','— ласково чесал пса за ушами ','— Красавец…']
        units=[{'id':str(i),'source_id':'p','speaker':'narrator' if i==1 else 'god',
                'mode':'narrator' if i==1 else 'speech','text':text} for i,text in enumerate(texts)]
        return request(units,[{'text':''.join(texts)}],{'god':{'nature':'man'},'narrator':{'nature':'narrator'}})

    def answer(self,payload):
        return {'turns':[{'turn_id':turn['turn_id'],'register':'warm','technique':'ordinary',
                          'warmth':'reassuring',
                          'audible_stance':'gentle affectionate address to an old dog','addressee':'dog',
                          'evidence':'ласково чесал пса за ушами','needs_adjudication':False,'audio_kind':'speech'}
                         for turn in payload['turns']]}

    def test_sparse_omission_cannot_silently_mean_ordinary(self):
        payload=self.payload();answer=self.answer(payload)
        validate(answer,payload)
        with self.assertRaisesRegex(ValueError,'omitted'):validate({'turns':answer['turns'][:1]},payload)
        with self.assertRaisesRegex(ValueError,'duplicate'):validate({'turns':answer['turns']*2},payload)
        self.assertEqual(set(bind(answer,payload)),{'0','1','2'})

    def test_warm_direction_cannot_be_lost_between_llm_and_synthesis(self):
        direction=self.answer(self.payload())['turns'][0]
        for tags in ([],['affection'],['affection','expressive_low']):
            with self.assertRaisesRegex(ValueError,'qualified performance palette'):
                require_realization(direction,{'technique_tags':tags})

    def test_audio_event_and_unknown_style_are_not_baseline_speech(self):
        direction=self.answer(self.payload())['turns'][0]
        with self.assertRaisesRegex(ValueError,'sound-event'):require_realization({**direction,'audio_kind':'nonverbal'}, {})
        with self.assertRaisesRegex(ValueError,'Unresolved'):require_realization({**direction,'needs_adjudication':True}, {})

    def test_legacy_compiler_cannot_invent_a_warm_control_mapping(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from higgs_character_direction import scene_delivery_profile
        direction={**self.answer(self.payload())['turns'][0],'request_sha256':'a'*64}
        with self.assertRaisesRegex(ValueError,'qualified performance palette'):
            scene_delivery_profile('god',direction,{'baseline_technique':'ordinary'},'b'*64)

    def test_display_quotes_are_source_checked_not_semantically_rewritten(self):
        from rimsky.scene_direction import literal_evidence
        payload=self.payload();answer=self.answer(payload)
        answer['turns'][0]['evidence']='«Ласково чесал пса за ушами»'
        fixed,trace=literal_evidence(answer,payload)
        self.assertEqual(fixed['turns'][0]['evidence'],'ласково чесал пса за ушами')
        self.assertEqual(len(trace),1)
        answer['turns'][0]['evidence']='«грубо толкнул пса»'
        fixed,_=literal_evidence(answer,payload)
        with self.assertRaisesRegex(ValueError,'exact source'):validate(fixed,payload)

    def test_state_types_and_unimplemented_techniques_cannot_pass_as_ready(self):
        payload=self.payload();answer=self.answer(payload);answer['turns'][0]['needs_adjudication']='false'
        with self.assertRaisesRegex(ValueError,'state'):validate(answer,payload)
        direction=self.answer(payload)['turns'][0]
        with self.assertRaisesRegex(ValueError,'no implemented'):
            require_realization(direction,{'realization_status':'unmapped'})


if __name__=='__main__':unittest.main()
