import copy
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'));sys.path.insert(0,str(ROOT/'scripts'))
from rimsky.speech_quality import transcript_check,spoken_word_tokens,word_tokens
from rimsky.spoken_edits import apply_spoken_edit
from rimsky.util import write_json,file_digest
from render_higgs_palette_preview import saved_retry,retry_units,phrasing_retry
from higgs_opus_word_recovery import frozen_recovery_matches,recovery_take


class WordRecoveryTests(unittest.TestCase):
    def test_frozen_opus_retry_keeps_bound_old_evidence_only_for_same_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            generation=Path(folder)/'generation';out=generation/'c0001'/'chunk-000'
            diagnosis=out/'opus-diagnosis-v2-old';diagnosis.mkdir(parents=True)
            write_json(generation/'manifest.json',{'id':'test'})
            request=diagnosis/'request.json';write_json(request,{'request':{'units':[]}})
            source_evidence='the source sentence'
            answer=diagnosis/'answer.json'
            write_json(answer,{'request_sha256':file_digest(request),'answer':{'diagnoses':[{
                'unit_id':'u1','native_candidates':['candidate text'],'source_evidence':source_evidence}]}})
            old_digest=file_digest(answer)
            saved={'id':'u1','source_id':'p1','start':0,'end':11,'text':'source text',
                   'speaker':'speaker','voice':'voice','mood':'neutral','analysis_parts':[],
                   'palette_reference':{'audio_sha256':'reference'},
                   'palette_assignment':{'allowed_reference_sha256':['reference']},
                   'render_seed':7,'spoken_text_plan':{'expected_spoken':'source text',
                       'native_text':'candidate text','recovery_kind':'opus_word_recovery',
                       'evidence':{'diagnosis_sha256':old_digest,'source_evidence':source_evidence}}}
            proposed=copy.deepcopy(saved)
            proposed['spoken_text_plan']['evidence']['diagnosis_sha256']='new-diagnosis'
            self.assertTrue(frozen_recovery_matches(out,saved,proposed))

            for path,value in [(('text',),'changed source'),(('source_id',),'changed source id'),
                               (('speaker',),'changed speaker'),(('voice',),'changed voice'),
                               (('palette_reference','audio_sha256'),'changed reference'),
                               (('palette_assignment','allowed_reference_sha256'),['changed reference']),
                               (('spoken_text_plan','expected_spoken'),'changed spoken text'),
                               (('spoken_text_plan','native_text'),'changed candidate')]:
                changed=copy.deepcopy(proposed);target=changed
                for key in path[:-1]:target=target[key]
                target[path[-1]]=value
                self.assertFalse(frozen_recovery_matches(out,saved,changed),path)

            write_json(request,{'request':{'units':['binding changed']}})
            self.assertFalse(frozen_recovery_matches(out,saved,proposed))
            request.unlink();answer.unlink()
            self.assertFalse(frozen_recovery_matches(out,saved,proposed))

    def test_refreshed_diagnosis_uses_versioned_take_without_overwriting_frozen_one(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);base=out/'word-opus-recovery'/'take-0';base.mkdir(parents=True)
            assignment={'units':[{'id':'u1'}]}
            old={'units':[{'id':'u1','text':'same','spoken_text_plan':{'native_text':'old'}}]}
            candidate={'units':[{'id':'u1','text':'same','spoken_text_plan':{'native_text':'new'}}]}
            write_json(base/'assignment.json',old);before=(base/'assignment.json').read_bytes()
            take,path,resolved=recovery_take(out,0,candidate,assignment)
            self.assertNotEqual(take,base)
            self.assertEqual(path,take/'assignment.json')
            self.assertEqual(resolved,candidate)
            self.assertEqual((base/'assignment.json').read_bytes(),before)

    def test_elongation_is_one_word_but_compounds_and_repetitions_are_retained(self):
        expected='Ох-х-х… чую жопу.'
        self.assertEqual(spoken_word_tokens(expected),word_tokens('Ох, чую жопу.'))
        self.assertEqual(transcript_check(expected,'Ох, чую жопу.','audio','verifier',True)['status'],'asr_exact_match')
        for text in ['Кто-то','Да-да','чуть-чуть','из-за','Ох-хм']:
            self.assertEqual(spoken_word_tokens(text),word_tokens(text))
        self.assertEqual(spoken_word_tokens('А-а-а! Нет-т-т!'),['а','нет'])
        self.assertNotEqual(spoken_word_tokens('Ох-х-х, чую жопу'),spoken_word_tokens('Ох, жопу'))

    def test_verifier_fix_replays_saved_extra_targets_without_changing_native_inputs(self):
        original=[{'id':key,'render_seed':123} for key in ['one','two']]
        saved={'units':retry_units(original,1,{})}
        candidate={'units':retry_units(original[:1],1,{})}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'assignment.json';write_json(path,saved)
            self.assertEqual(saved_retry(path,candidate,original,1,{}),saved)
            saved['units'][1]['render_seed']=999;write_json(path,saved)
            with self.assertRaisesRegex(ValueError,'assignment changed'):
                saved_retry(path,candidate,original,1,{})

    def test_phrasing_recovery_preserves_words_and_does_not_replace_listener_recipe(self):
        text='Есть хлеб и с пол фляги мёда.'
        unit={'id':'one','text':text}
        check={'expected_words':word_tokens(text),'heard_words':word_tokens('Есть хлеб и пол фляги мёда.'),
               'audio_sha256':'audio','synthesis_key':'key','verifier_sha256':'verifier'}
        selected={'one':{'unit':{'spoken':text},'word_check':check}}
        candidate=phrasing_retry([unit],selected,0,{})[0]
        native,expected=apply_spoken_edit(text,candidate['spoken_text_plan'])
        self.assertEqual(word_tokens(native),word_tokens(text))
        self.assertIn('и, с',native)
        self.assertNotIn('spoken_text_plan',unit)
        protected={**unit,'spoken_text_plan':{'native_text':text}}
        self.assertEqual(phrasing_retry([protected],selected,0,{}),[])
        invalid=copy.deepcopy(candidate['spoken_text_plan']);invalid['native_text']='Есть хлеб и, пол фляги мёда.'
        with self.assertRaisesRegex(ValueError,'Unsupported'):apply_spoken_edit(text,invalid)


if __name__=='__main__':unittest.main()
