import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'));sys.path.insert(0,str(ROOT/'scripts'))
from higgs_character_direction import attribution_directions,delivery_profile
from direct_higgs_book_revision import validate_scene,literal_scene_evidence

def parts(text,speaker):
    points=[0]+[i for i,c in enumerate(text) if c=='—' and i>0]+[len(text)]
    units=[]
    for j,(a,b) in enumerate(zip(points,points[1:])):
        units.append({'id':str(j),'source_id':'p','text':text[a:b],'start':a,'end':b,
                      'mode':'narrator' if j==1 else 'speech','speaker':'narrator' if j==1 else speaker})
    return units,[{'id':'p','text':text}]

class CharacterDirectionTests(unittest.TestCase):
    def test_hoarse_applies_to_character_not_author_remark(self):
        units,blocks=parts('— Оставь меня в покое, трутень… — хрипло отозвался тот, отдернув шубу — Иди, спи.','terek')
        result=attribution_directions(units,blocks)
        self.assertEqual(result['0']['technique'],'hoarse')
        self.assertNotIn('1',result)
        self.assertEqual(result['2']['technique'],'hoarse')

    def test_muttering_is_a_technique_not_fear(self):
        units,blocks=parts('— Крест, крест, крест… — бормотал Герман, ступая по острым камням — Как-то слишком уж примитивно.','german')
        result=attribution_directions(units,blocks)
        self.assertEqual(result['0']['technique'],'muttering')
        profile=delivery_profile('german','muttering')
        self.assertNotIn('fear',profile['technique_tags'])
        self.assertNotIn('whispering',profile['technique_tags'])

    def test_finished_turn_does_not_spread_to_next_speaker(self):
        units,blocks=parts('— Стой! — крикнул Иван. — Что такое?','ivan')
        units[-1]['speaker']='masha'
        self.assertEqual(set(attribution_directions(units,blocks)),{'0'})

    def test_shouting_attribution_covers_multi_sentence_remark_and_continuation(self):
        texts=['- Ладно-ладно. ', 'Я потерплю. ', 'Только заткнись! ',
               '— проорал генерал ', '— Это озерным тварям стоит меня бояться, а не наоборот!']
        full=''.join(texts);units=[];offset=0
        for i,text in enumerate(texts):
            units.append({'id':str(i),'source_id':'p','text':text,'start':offset,'end':offset+len(text),
                          'speaker':'narrator' if i==3 else 'gundahar',
                          'mode':'narrator' if i==3 else 'speech','sentence_index':min(i,2)})
            offset+=len(text)
        result=attribution_directions(units,[{'id':'p','text':full}])
        self.assertEqual(set(result),{'0','1','2','4'})
        self.assertTrue(all(c['technique']=='shouting' for c in result.values()))
        units[-1]['speaker']='german'
        self.assertEqual(set(attribution_directions(units,[{'id':'p','text':full}])),{'0','1','2'})

    def test_undead_register_survives_temporary_emotion(self):
        persona={'pitch':'low','baseline_technique':'hoarse'}
        a=delivery_profile('undead_general','ordinary',persona=persona)
        b=delivery_profile('undead_general','laughing',persona=persona)
        self.assertEqual(a['baseline_tags'],b['baseline_tags'])
        self.assertIn('pitch_low',b['baseline_tags'])
        self.assertFalse(b['listening_approved'])

    def test_persistent_rough_voice_does_not_switch_on_attribution(self):
        persona={'baseline_technique':'hoarse','baseline_register':'mature rough voice'}
        profiles=[delivery_profile('hunter',technique,persona=persona) for technique in ('ordinary','hoarse','whispering')]
        self.assertEqual({p['reference_index'] for p in profiles},{1})
        self.assertEqual(delivery_profile('other','ordinary')['reference_index'],
                         delivery_profile('other','hoarse')['reference_index'])
        general=delivery_profile('unseen_general','shouting',persona={**persona,'pitch':'low'})
        self.assertEqual(general['reference_index'],1)
        self.assertIn('pitch_low',general['baseline_tags'])
        self.assertIn('shouting',general['technique_tags'])

    def test_mastering_separates_characters_sharing_a_reference_not_their_moods(self):
        from render_higgs_book_trial import mastering_voice_key
        base={'speaker':'hunter','synthesis':{'voice_family':'male','reference':{'sha256':'a'*64}},
              'delivery_profile':{'technique':'hoarse'}}
        self.assertEqual(mastering_voice_key(base),mastering_voice_key({**base,'delivery_profile':{'technique':'shouting'}}))
        self.assertNotEqual(mastering_voice_key(base),mastering_voice_key({**base,'speaker':'undead_general'}))

    def test_assembly_rejects_unexplained_voice_change_in_same_route(self):
        from render_higgs_book_trial import validate_voice_continuity
        unit={'speaker':'hunter','synthesis':{'voice_family':'male','reference':{'sha256':'a'*64}}}
        changed={'speaker':'hunter','synthesis':{'voice_family':'male','reference':{'sha256':'b'*64}}}
        with self.assertRaisesRegex(ValueError,'reference switch'):
            validate_voice_continuity([unit,changed])
        other_family={'speaker':'hunter','synthesis':{'voice_family':'another','reference':{'sha256':'a'*64}}}
        with self.assertRaisesRegex(ValueError,'reference switch'):
            validate_voice_continuity([unit,other_family])
        opening={'speaker':'narrator','synthesis':{'voice_family':'opening','reference':{'sha256':'a'*64}}}
        ordinary={'speaker':'narrator','synthesis':{'voice_family':'normal','reference':{'sha256':'b'*64}}}
        self.assertEqual(len(validate_voice_continuity([opening,ordinary])),2)

    def test_required_delivery_cannot_be_silently_rendered_as_baseline(self):
        import render_higgs_book_trial as renderer
        profile=delivery_profile('gundahar','shouting')
        profile['blocked_controls']=['shouting'];profile['technique_tags']=[]
        with self.assertRaisesRegex(ValueError,'blocked'):
            renderer.profile_pack({}, {'id':'u','delivery_profile':profile})
        profile=delivery_profile('german','unmapped')
        with self.assertRaisesRegex(ValueError,'no implementation'):
            renderer.profile_pack({}, {'id':'u','delivery_profile':profile})

    def test_unmapped_attribution_requires_adjudication(self):
        answer={'corrections':[{'unit_id':'u','technique':'unmapped','evidence':'пропукал он',
                                'needs_adjudication':False}]}
        with self.assertRaisesRegex(ValueError,'adjudication'):
            validate_scene(answer,[{'id':'u','mode':'speech'}],'пропукал он')
        answer['corrections'][0]['needs_adjudication']=True
        validate_scene(answer,[{'id':'u','mode':'speech'}],'пропукал он')

    def test_scene_correction_cannot_apply_a_character_technique_to_narrator(self):
        u={'id':'n','mode':'narrator'}
        answer={'corrections':[{'unit_id':'n','technique':'hoarse','evidence':'хрипло сказал он'}]}
        with self.assertRaisesRegex(ValueError,'narrator'):validate_scene(answer,[u],'хрипло сказал он')

    def test_changed_character_register_changes_key_and_does_not_mutate_family(self):
        import copy
        from unittest.mock import patch
        import render_higgs_book_trial as renderer
        from rimsky.util import digest
        reference={'audio':'audio/ref.wav','text':'Точная речь.','audio_sha256':'a'*64,'reviewed':True}
        pack={'_root':str(ROOT),'_fingerprint':'old','files':{'old.wav':'b'*64},
              'voices':[{'references':{'neutral':{'audio':'old.wav','text':'Старая речь.'}},
                         'higgs_controls':{'moods':{'baseline':{'tags':[]},'amusement':{'tags':['amusement']}}}}]}
        original=copy.deepcopy(pack)
        with patch.object(renderer,'read',return_value={'masculine_6':[reference,reference]}), \
             patch.object(renderer,'file_digest',return_value='a'*64):
            selected=renderer.profile_pack(pack,{'voice':'masculine_6','delivery_profile':delivery_profile('undead_general',persona={'pitch':'low','baseline_technique':'hoarse'})})
        self.assertEqual(pack,original)
        self.assertIn('pitch_low',selected['voices'][0]['higgs_controls']['moods']['amusement']['tags'])
        self.assertIn('amusement',selected['voices'][0]['higgs_controls']['moods']['amusement']['tags'])
        self.assertNotEqual(selected['_fingerprint'],pack['_fingerprint'])

    def test_source_evidence_compiler_removes_display_quotes_without_paraphrasing(self):
        answer={'corrections':[{'unit_id':'u','evidence':'«Проорал генерал».'}]}
        fixed,trace=literal_scene_evidence(answer,{'chapter_context':'— Стой! — проорал генерал.'})
        self.assertEqual('проорал генерал',fixed['corrections'][0]['evidence'])
        self.assertEqual('«Проорал генерал».',trace[0]['original_evidence'])
        bad,_=literal_scene_evidence({'corrections':[{'unit_id':'u','evidence':'«Шептал генерал».'}]},
                                    {'chapter_context':'— Стой! — проорал генерал.'})
        self.assertEqual('«Шептал генерал».',bad['corrections'][0]['evidence'])
        short={'corrections':[{'unit_id':'u','evidence':'«— ДА!».'}]}
        context={'chapter_context':'— ДА!','units':[{'id':'u','text':'— ДА!'}]}
        fixed,_=literal_scene_evidence(short,context)
        self.assertEqual('— ДА!',fixed['corrections'][0]['evidence'])
        context['units'][0]['text']='Нет.'
        fixed,_=literal_scene_evidence(short,context)
        self.assertEqual('«— ДА!».',fixed['corrections'][0]['evidence'])

if __name__=='__main__':unittest.main()
