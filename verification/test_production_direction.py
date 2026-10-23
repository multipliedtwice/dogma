import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'));sys.path.insert(0,str(ROOT/'scripts'))
from rimsky.scene_direction import request,validate,direct
from rimsky.chapter_gate import report,require,audio_binding,measure,CHECKS
from rimsky.util import digest,file_digest,write_json,SpeechHoldError
from rimsky.control_qualification import select
from write_higgs_control_approvals import write_approvals

class ProductionDirectionTests(unittest.TestCase):
    def payload(self):
        return request([{'id':'u','mode':'speech','speaker':'actor','source_id':'p','text':'Хороший…'}],
            [{'id':'p','text':'Хороший… Он ласково чесал пса за ушами.'},
             {'id':'q','text':'Где-то птица громко кричала.'}],{'actor':{'nature':'human'}})
    def answer(self,payload,register='warm'):
        return {'turns':[{'turn_id':t['turn_id'],'register':register,'technique':'ordinary',
            'warmth':'reassuring' if register=='warm' else 'none',
            'audible_stance':'gentle address to the dog','addressee':'dog',
            'evidence':'ласково чесал пса за ушами','needs_adjudication':False,'audio_kind':'speech'} for t in payload['turns']]}

    def test_evidence_in_another_paragraph_cannot_direct_this_turn(self):
        body=self.payload();value=self.answer(body)
        value['turns'][0]['evidence']='птица громко кричала'
        with self.assertRaisesRegex(ValueError,'another turn'):validate(value,body)

    def test_blind_critic_and_targeted_adjudication_keep_both_proposals(self):
        body=self.payload();calls=[];saved=[]
        def infer(system,payload,schema):
            calls.append(copy.deepcopy(payload))
            return self.answer(payload,'ordinary' if len(calls)==2 else 'warm')
        result=direct(body,infer,save=saved.append)
        self.assertEqual(len(calls),3)
        self.assertNotIn('primary_proposal',calls[1]);self.assertNotIn('proposals',calls[1])
        self.assertEqual(result['primary']['turns'][0]['register'],'warm')
        self.assertEqual(result['critic']['turns'][0]['register'],'ordinary')
        self.assertEqual(result['by_unit']['u']['register'],'warm')
        self.assertEqual(len(result['disagreements']),1)
        self.assertEqual(direct(body,lambda *args:self.fail('Cached stage was rerun'),result),result)

    def test_local_consensus_cannot_self_qualify(self):
        body=self.payload();result=direct(body,lambda system,payload,schema:self.answer(payload),local=True)
        self.assertTrue(result['by_unit']['u']['needs_adjudication'])

    def test_same_warm_register_with_different_relationships_requires_adjudication(self):
        body=self.payload();calls=[]
        def infer(system,payload,schema):
            calls.append(payload);answer=self.answer(payload)
            if len(calls)==2:answer['turns'][0]['warmth']='intimate'
            return answer
        result=direct(body,infer)
        self.assertEqual(len(calls),3)
        self.assertEqual(result['critic']['turns'][0]['warmth'],'intimate')
        self.assertEqual(result['by_unit']['u']['warmth'],'reassuring')
        answer=self.answer(body);answer['turns'][0].pop('warmth')
        with self.assertRaisesRegex(ValueError,'fields'):validate(answer,body)

    def test_gate_missing_measurements_and_stale_hashes_block_publication(self):
        checks={name:[{'status':'pass'}] for name in CHECKS}
        gate=report('a',checks);require(gate,'a')
        with self.assertRaises(SpeechHoldError):require(gate,'changed')
        checks.pop('scene_loudness');gate=report('a',checks)
        self.assertEqual(gate['checks']['scene_loudness']['counts']['unavailable'],1)
        with self.assertRaisesRegex(SpeechHoldError,'scene_loudness'):require(gate,'a')
        checks['scene_loudness']=[{'status':'bypassed'}]
        with self.assertRaises(ValueError):report('a',checks,True)

    def test_evaluation_bypass_disclosed_and_does_not_bypass_audio_failures(self):
        checks={name:[{'status':'pass'}] for name in CHECKS};checks['qualification']=[{'status':'bypassed'}]
        value=report('a',checks,True);require(value,'a');self.assertTrue(value['qualification_bypassed'])
        checks['words']=[{'status':'fail'}]
        with self.assertRaisesRegex(SpeechHoldError,'words'):require(report('a',checks,True),'a')
        self.assertNotEqual(audio_binding('p',{'u':{'key':'k','audio_sha256':'a'}}),
                            audio_binding('p',{'u':{'key':'k','audio_sha256':'b'}}))

    def test_assembler_refuses_to_touch_served_directory_without_gate(self):
        from render_higgs_book_trial import assemble
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);write_json(root/'render-plan.json',{'units':[]})
            with patch('render_higgs_book_trial.publish') as published:
                with self.assertRaises(SpeechHoldError):assemble(root,{'units':[]},{'takes':{}})
                published.assert_not_called()
            self.assertEqual(list(root.iterdir()),[root/'render-plan.json'])

    def test_bounded_approvals_require_unchanged_audio_and_explicit_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'case.wav').write_bytes(b'qualification take')
            case={'id':'warm','model_sha256':'m','voice_family':'male','reference_sha256':'r',
                  'tags':['affection'],'register':'warm','technique':'ordinary','pace':'normal',
                  'warmth':'reassuring',
                  'audio':'case.wav','audio_sha256':file_digest(root/'case.wav'),
                  'serves':[{'register':'warm','technique':'ordinary','pace':'deliberate','warmth':'reassuring'}]}
            write_json(root/'cases.json',{'model_sha256':'m','cases':[case]})
            answer={'case_id':'warm','case_sha256':digest(case),'verdict':'approved','source':'explicit_listener'}
            write_json(root/'answers.json',{'cases_sha256':file_digest(root/'cases.json'),'answers':[answer]})
            review=write_approvals(root/'cases.json',root/'answers.json',root/'review.json')
            pack={'_root':root,'speech':{'listening_review':'review.json','model':'model','effects_policy':'qualified'},
                  'files':{'review.json':file_digest(root/'review.json'),'ref':'r','model':'m'}}
            voice={'family':'male','id':'male','references':{'neutral':{'audio':'ref'}},
                'higgs_controls':{'combinations':[{'id':'warm','reference':'neutral',
                    **{k:case[k] for k in ('register','technique','pace','tags','serves','warmth')}}]}}
            direction={'register':'warm','warmth':'reassuring','technique':'ordinary','audio_kind':'speech'}
            chosen=select(pack,voice,direction,'deliberate',{'reference':'neutral'})
            self.assertTrue(chosen['substituted']);self.assertEqual(chosen['applied']['pace'],'normal')
            with self.assertRaises(SpeechHoldError):select(pack,voice,{**direction,'warmth':'intimate'},'normal',{'reference':'neutral'})
            with self.assertRaises(SpeechHoldError):select(pack,voice,{**direction,'warmth':None},'normal',{'reference':'neutral'})
            legacy={**case};legacy.pop('warmth')
            write_json(root/'legacy.json',{'model_sha256':'m','cases':[legacy]})
            write_json(root/'legacy-answer.json',{'cases_sha256':file_digest(root/'legacy.json'),
                'answers':[{**answer,'case_sha256':digest(legacy)}]})
            with self.assertRaisesRegex(ValueError,'relationship intent'):
                write_approvals(root/'legacy.json',root/'legacy-answer.json',root/'legacy-review.json')
            write_json(root/'take-answer.json',{'cases_sha256':file_digest(root/'legacy.json'),
                'answers':[{**answer,'case_sha256':digest(legacy),'approval_scope':'take'}]})
            accepted=write_approvals(root/'legacy.json',root/'take-answer.json',root/'take-review.json')
            self.assertEqual(accepted['reviews'][0]['control_type'],'take')
            self.assertEqual(accepted['reviews'][0]['verdict'],'approved')
            with self.assertRaises(SpeechHoldError):select(pack,voice,{**direction,'register':'intense'})
            (root/'case.wav').write_bytes(b'changed take')
            with self.assertRaisesRegex(ValueError,'audio changed'):
                write_approvals(root/'cases.json',root/'answers.json',root/'review.json')

    def test_measurement_cannot_claim_voice_pairs_or_scene_level_are_qualified(self):
        from rimsky.speech_quality import transcript_check
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'u.wav'
            sf.write(path,np.r_[np.zeros(1200),np.sin(np.arange(12000)*.08)*.08,np.zeros(1200)],24000)
            hashed=file_digest(path);direction={'register':'subdued','technique':'ordinary','needs_adjudication':False}
            base={'path':path,'audio_sha256':hashed,'scene':'campfire','direction':direction,
                  'word_check':transcript_check('Да.','Да.',hashed,'v')}
            rows=[{**base,'id':actor,'synthesis':{'text':'Да.','voice_family':'male','reference':{'sha256':actor},
                   'voice_signature':{'character_id':actor,'gain':1.},'qualification_bypassed':True}} for actor in ('one','two')]
            checks=measure(rows,model_sha256='m')
            self.assertEqual([r['status'] for r in checks['words']],['pass','pass'])
            self.assertEqual(checks['voice_distinctness'][0]['status'],'unavailable')
            self.assertTrue(all(r['status']=='unavailable' for r in checks['scene_loudness']))
            self.assertFalse(report('a',checks,True)['passed'])

    def test_voice_pair_qualification_is_reusable_across_character_names(self):
        from rimsky.chapter_gate import voice_pair_binding
        first={'family':'male','reference':'a','signature':{'character_id':'old_name','gain':1.}}
        second={'family':'male','reference':'b','signature':{'character_id':'friend','gain':1.}}
        renamed={**first,'signature':{**first['signature'],'character_id':'new_alias'}}
        self.assertEqual(voice_pair_binding(first,second,'m'),voice_pair_binding(renamed,second,'m'))
        changed={**renamed,'signature':{**renamed['signature'],'permanent_tags':['pitch_low']}}
        self.assertNotEqual(voice_pair_binding(first,second,'m'),voice_pair_binding(changed,second,'m'))

    def test_pack_builder_imports_only_current_explicit_qualification(self):
        from prepare_higgs_voice_pack import apply_qualification
        voices=[{'id':'male','family':'male','higgs_controls':{'moods':{'neutral':{'tags':[],'reviewed':False},
            'fear':{'tags':['fear'],'reviewed':False}},'paces':{'normal':{'tags':[],'reviewed':False}}}}]
        approved={'control_id':'base','control_type':'combination','verdict':'approved','voice_family':'male',
                  'reference_sha256':'r','register':'ordinary','technique':'ordinary','pace':'normal','tags':[]}
        apply_qualification(voices,{'model_sha256':'m','reviews':[approved]},'m',{'male':'r'})
        self.assertTrue(voices[0]['higgs_controls']['moods']['neutral']['reviewed'])
        self.assertNotIn('fear',voices[0]['higgs_controls']['moods'])
        self.assertEqual(len(voices[0]['higgs_controls']['combinations']),1)
        with self.assertRaisesRegex(ValueError,'checkpoint'):
            apply_qualification(voices,{'model_sha256':'other','reviews':[approved]},'m',{'male':'r'})

    def test_persistent_trait_requires_an_available_signature_not_a_named_tag_guess(self):
        from rimsky.character_direction import signature_for
        voice={'id':'male','family':'male','signatures':[
            {'id':'plain','reference':'neutral','permanent_tags':[],'gain':1.,'vocal_traits':{'pitch':'ordinary'}},
            {'id':'bass','reference':'neutral','permanent_tags':['pitch_low'],'gain':.8,
             'vocal_traits':{'pitch':'low','baseline_technique':'hoarse'}}]}
        frozen=signature_for(voice,{'pitch':'low','baseline_technique':'hoarse'},'new_character','pack')
        self.assertEqual(frozen['signature_id'],'bass');self.assertEqual(frozen['gain'],.8)
        self.assertEqual(frozen['permanent_tags'],['pitch_low'])
        with self.assertRaises(SpeechHoldError):signature_for({'id':'male','family':'male'}, {'pitch':'low'},'actor','p')

    def test_nonverbal_inventory_routes_several_sounds_without_spoken_letters(self):
        from rimsky.sound_events import plan,render
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bank=[];files={}
            for kind in ('dog_growl','dog_whine','dog_bark','human_laughter'):
                for variant in range(2):
                    name=kind+str(variant)+'.wav'
                    sf.write(root/name,np.r_[np.zeros(1200),np.sin(np.arange(12000)*(.08+.01*variant))*.05,np.zeros(1200)],24000)
                    files[name]=file_digest(root/name)
                    bank.append({'id':name,'kind':kind,'register':'subdued','audio':name,'gain':.8,
                                 'character_id':'actor' if kind.startswith('human_') else '*'})
            pack={'_root':root,'_fingerprint':'p','files':files,'sound_events':bank,'speech':{'purpose':'evaluation_only'}}
            for kind in ('dog_growl','dog_whine','dog_bark','human_laughter'):
                direction={'turn_id':kind,'register':'subdued','audio_kind':'nonverbal','event_kind':kind}
                persona={'sound_role':'human' if kind.startswith('human_') else 'dog'}
                selected=plan(pack,direction,persona,{'character_id':'actor'},1)
                self.assertEqual(selected['variant_count'],2)
                result=render(pack,selected,root/'out.wav')
                self.assertEqual(result['word_check']['status'],'nonverbal_not_spoken_text')
                self.assertEqual(result['word_check']['event_kind'],kind)
                self.assertTrue(selected['qualification_bypassed'])
            with self.assertRaisesRegex(SpeechHoldError,'species'):
                plan(pack,{'register':'subdued','event_kind':'dog_bark'},{'sound_role':'human'},{'character_id':'actor'})
            with self.assertRaisesRegex(SpeechHoldError,'No inventory'):
                plan(pack,{'register':'intense','event_kind':'dog_bark'},{'sound_role':'dog'},{'character_id':'actor'})

    def test_later_book_evidence_reaches_personas_before_casting(self):
        from rimsky.character_direction import request as profiles_request,direct as profiles_direct
        book={'source_hash':'h','chapters':[{'blocks':[{'id':'early','text':'Генерал приказал остановиться.'}]},
                  {'blocks':[{'id':'late','text':'Генерал был мёртвым игвом с низким голосом.'}]}]}
        body=profiles_request(book,[{'id':'general','name':'Генерал'}]);calls=[]
        def infer(system,payload,schema):
            calls.append(payload)
            return {'profiles':[{'character_id':'general','nature':'undead','pitch':'low',
                'baseline_register':'low rough','baseline_technique':'hoarse','sound_role':'human','aliases':[],
                'evidence':{'source_id':'late','quote':'мёртвым игвом с низким голосом'},'needs_adjudication':False}]}
        result=profiles_direct(body,infer)
        self.assertEqual(result['profiles']['general']['pitch'],'low')
        self.assertIn('late',{s['id'] for s in calls[0]['sources']})
        self.assertNotIn('proposals',calls[1])

    def test_music_waits_for_gate_and_is_revoked_when_audio_changes(self):
        from rimsky.chapter_gate import music_released,generation_binding
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'audio').mkdir();(root/'audio/k.wav').write_bytes(b'accepted take')
            segment={'id':'s','chapter_id':'c','source_id':'p','start':0,'end':3,'spoken':'Да.',
                     'speaker':'a','performance':{},'audio_hash':'k','status':'ready'}
            generation={'segments':[segment],'config':{'pack_fingerprint':'p'},'analysis':{'scene_direction':{'c':{}}}}
            self.assertFalse(music_released(generation,'c',root))
            value=report(generation_binding([segment],'p',root),{k:[{'status':'pass'}] for k in CHECKS})
            generation['analysis']['chapter_acceptance']={'c':value}
            self.assertTrue(music_released(generation,'c',root))
            (root/'audio/k.wav').write_bytes(b'changed take')
            self.assertFalse(music_released(generation,'c',root))

if __name__=='__main__':unittest.main()
