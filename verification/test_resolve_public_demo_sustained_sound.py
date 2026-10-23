"""Source-bound sustained-sound spelling tests; CPU only, no actual book writes."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_sustained_sound as adapter


class SustainedSound(unittest.TestCase):
    def fixture(self,source='Рр…',heard=('Ррр','Р-р-р.')):
        unit={'id':'u1','source_id':'p1','start':0,'end':len(source),'text':source,'spoken':source}
        evidence=[]
        for index,(model,role,family,text) in enumerate([
                ('Qwen3-ASR-1.7B','primary','qwen','Р.'),
                ('openai/whisper-small','witness','whisper',heard[0]),
                ('openai/whisper-large-v3','witness','whisper',heard[1])]):
            config={'model':model,'local_files_only':True,'expected_text_given_to_recognizer':False}
            raw=adapter.base.transcript_check(source,text,'a'*64,adapter.base.sha(config))
            evidence.append({'path':'checks/'+str(index)+'.json','sha256':str(index)*64,
                             'role':role,'family':family,'verifier':config,'raw':raw,
                             'comparison':{'passed':False}})
        decision={'passed':False,'resolution':'held','status':'unresolved','evidence':evidence,
                  'equivalences':[],'tolerated_deviations':[]}
        return unit,decision

    def test_shared_rule_accepts_only_two_repeated_same_consonant_witnesses_and_discloses_correlation(self):
        unit,old=self.fixture();snapshot=copy.deepcopy(old)
        decision,proof=adapter.spelling_decision(unit,'Russian',old,'a'*64)
        self.assertTrue(decision['passed'])
        self.assertEqual(decision['status'],'source_bound_spoken_equivalence')
        self.assertEqual(old,snapshot)
        self.assertEqual(decision['evidence'],old['evidence'])
        self.assertEqual(proof['rule'],adapter.sounds.ELONGATION_RULE)
        self.assertEqual(len({w['model'] for w in proof['witnesses']}),2)
        self.assertIn('correlated',proof['correlation'])
        self.assertFalse(proof['human_approval'])
        self.assertEqual(proof['source_span']['text'],'Рр…')
        self.assertEqual(proof['audio_sha256'],'a'*64)
        self.assertTrue(all(change['helper_sha256']==adapter.base.file_digest(Path(adapter.sounds.__file__)) for change in decision['equivalences']))

    def test_single_letter_wrong_consonant_vowels_and_mixed_words_cannot_qualify(self):
        for source in ('Р.','Ура!','Тсс…','Рр и Рр','Папа','Ааа…'):
            unit,decision=self.fixture(source)
            self.assertIsNone(adapter.spelling_decision(unit,'Russian',decision,'a'*64),source)
        for heard in [('Р.','Ррр'),('Ррр','Ттт'),('Ррр','Ура!'),('Ррр','Рр и')]:
            unit,decision=self.fixture(heard=heard)
            self.assertIsNone(adapter.spelling_decision(unit,'Russian',decision,'a'*64),heard)

    def test_same_model_greedy_and_beam_are_not_two_models(self):
        unit,decision=self.fixture()
        item=decision['evidence'][1]
        item['verifier']['model']='openai/whisper-large-v3'
        item['raw']=adapter.base.transcript_check(unit['spoken'],item['raw']['heard'],'a'*64,adapter.base.sha(item['verifier']))
        self.assertIsNone(adapter.spelling_decision(unit,'Russian',decision,'a'*64))

    def test_cued_recognizer_forged_audio_and_changed_spoken_native_source_rejected(self):
        unit,decision=self.fixture()
        for mutation in ('prompt','audio'):
            invalid=copy.deepcopy(decision)
            if mutation=='prompt':invalid['evidence'][1]['verifier']['expected_text_given_to_recognizer']=True
            else:invalid['evidence'][1]['raw']['audio_sha256']='0'*64
            with self.assertRaises(ValueError):adapter.spelling_decision(unit,'Russian',invalid,'a'*64)
        for key in ('spoken','synthesis_text'):
            changed={**unit,key:'Ррр…'}
            self.assertIsNone(adapter.spelling_decision(changed,'Russian',decision,'a'*64))
        self.assertIsNone(adapter.spelling_decision(unit,'English',decision,'a'*64))

    def test_context_source_edit_fails_closed_before_equivalence(self):
        unit,decision=self.fixture()
        ctx=adapter.Context.__new__(adapter.Context)
        ctx.assignment={'language':'Russian','units':[copy.deepcopy(unit)]};ctx.generation='frozen'
        ctx.sustained_sound_dependencies={}
        receipt={'take':0,'spec':{'source':copy.deepcopy(unit),'generation':'frozen'},'audio_sha256':'a'*64}
        proof={'decision':decision,'source_text':unit['text'],'expected_spoken':unit['spoken'],'audio_sha256':'a'*64}
        changed={**unit,'text':'Ррр…'}
        with patch.object(adapter.previous.Context,'assess',return_value=proof):
            with self.assertRaisesRegex(ValueError,'frozen source/audio'):ctx.assess(changed,receipt)
            result=ctx.assess(unit,receipt)
        self.assertTrue(result['decision']['passed'])
        self.assertIn('sustained_sound_provenance',result)

    def test_all_raw_counters_are_preserved_by_report(self):
        unit,decision=self.fixture();_,proof=adapter.spelling_decision(unit,'Russian',decision,'a'*64)
        ctx=Mock();ctx.assignment={'units':[unit]};ctx.sustained_sound_dependencies={'new':'pinned'}
        ctx.assess.return_value={'sustained_sound_provenance':proof}
        summary={'take':0};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v4','raw_mismatch_check_count':15,
               'raw_primary_mismatch_take_count':7,'disclosure':'Prior checks.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[{'take':0}]):
            result=adapter.report(ctx)
        self.assertEqual(result['raw_mismatch_check_count'],15)
        self.assertEqual(result['raw_primary_mismatch_take_count'],7)
        self.assertEqual(result['sustained_sound_resolved_units'],1)
        self.assertEqual(result['beam_witness_resolution_adapter_sha256'],'v4')
        before=adapter.base.sha(result);summary['sustained_sound_provenance']['witnesses'][0]['heard']='changed'
        self.assertNotEqual(before,adapter.base.sha(result))


if __name__=='__main__':unittest.main()
