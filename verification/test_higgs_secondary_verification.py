import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from higgs_secondary_verification import apply_secondary,half_compound_expansion,proper_name_hyphen_expansion,final_devoicing_equivalence,raw_witness_coverage
from rimsky.speech_quality import transcript_check
from rimsky.util import file_digest,write_json

class IndependentVerificationTests(unittest.TestCase):
    def test_proper_name_boundary_preserves_every_letter_and_other_word(self):
        expected='На северо-запад от кратера Аз-Лаар.'
        heard,changes=proper_name_hyphen_expansion(expected,'На северо-запад от кратера Азлаар.')
        self.assertTrue(changes)
        self.assertEqual('asr_exact_match',transcript_check(expected,heard,'a','v')['status'])
        for wrong in ['На северо-запад от кратера Азлар.',
                      'На северо-запад от кратера Азлеар.',
                      'На северо-запад кратера Азлаар.',
                      'На северо-запад от кратера Азлаар лишнее.']:
            heard,_=proper_name_hyphen_expansion(expected,wrong)
            self.assertEqual('spoken_text_mismatch',transcript_check(expected,heard,'a','v')['status'])
        for source,heard in [('Колонны-ноги','колонныноги'),('Аз Лаар','Азлаар'),('неторопливо','не торопливо')]:
            _,changes=proper_name_hyphen_expansion(source,heard);self.assertFalse(changes)

    def test_raw_transcript_reuse_keeps_disagreement_and_checks_model_and_take_bindings(self):
        from audit_higgs_secondary_asr import MODEL,REVISION
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);wav=root/'audio.wav';wav.write_bytes(b'audio');weights=root/'model.safetensors';weights.write_bytes(b'weights')
            sha=file_digest(wav);check={**transcript_check('one two','one',sha,'verifier'),'unit_id':'line','synthesis_key':'key'}
            selected={'line':{'path':str(wav),'receipt':{'key':'key','audio_sha256':sha},'unit':{'spoken':'one two'},'word_check':check}}
            config={'implementation_sha256':'script','model':MODEL,'revision':REVISION,'language':'ru','task':'transcribe',
                'do_sample':False,'device':'cuda','dtype':'float16','word_comparison_sha256':'old',
                'files':{'model.safetensors':file_digest(weights)}}
            path=root/'report.json';write_json(path,{'verifier':config,'checks':{'take':check}})
            with patch('huggingface_hub.try_to_load_from_cache',return_value=str(weights)):
                self.assertEqual(raw_witness_coverage(selected,[path],'script'),{'line'})
                import copy
                first=copy.deepcopy(selected['line']);first['unit']['id']='line'
                second=copy.deepcopy(first);second['unit']['spoken']='one three'
                self.assertEqual(raw_witness_coverage({'first':first,'second':second},[path],'script'),{'first'})
                self.assertEqual(selected['line']['word_check']['status'],'spoken_text_mismatch')
                self.assertEqual(raw_witness_coverage(selected,[path],'different'),set())
                weights.write_bytes(b'changed')
                self.assertEqual(raw_witness_coverage(selected,[path],'script'),set())

    def test_final_devoicing_records_spoken_equivalence_without_guessing_names_or_missing_words(self):
        source='Отправитель: Глас Эстир'
        heard,changes=final_devoicing_equivalence(source,'Отправитель, глаз Эстир.')
        self.assertEqual(transcript_check(source,heard,'a','v')['status'],'asr_exact_match')
        self.assertEqual(changes[0]['source_word'],'глас')
        self.assertEqual(changes[0]['heard_word'],'глаз')
        for wrong in ['Отправитель.','Отправитель глаз Эстер','Отправитель глаз и Стир','Отправитель глаз Стир']:
            normalised,changes=final_devoicing_equivalence(source,wrong)
            self.assertFalse(changes)
            self.assertNotEqual(transcript_check(source,normalised,'a','v')['status'],'asr_exact_match')
        for a,b in [('лук','люк'),('не','ни'),('код','год'),('лес','лещ')]:
            _,changes=final_devoicing_equivalence(a,b);self.assertFalse(changes)

    def test_spelling_join_does_not_hide_missing_preposition(self):
        expected='Есть и с пол фляги мёда.'
        heard,edits=half_compound_expansion(expected,'Есть и с полфляги меда.')
        self.assertTrue(edits)
        self.assertEqual(transcript_check(expected,heard,'audio','verifier')['status'],'asr_exact_match')
        heard,_=half_compound_expansion(expected,'Есть и полфляги меда.')
        self.assertNotEqual(transcript_check(expected,heard,'audio','verifier')['status'],'asr_exact_match')
        heard,edits=half_compound_expansion('неторопливо','не торопливо')
        self.assertFalse(edits)
        self.assertNotEqual(transcript_check('неторопливо',heard,'a','v')['status'],'asr_exact_match')

    def test_exact_witness_is_bound_and_keeps_original_disagreement(self):
        with tempfile.TemporaryDirectory() as folder:
            wav=Path(folder)/'take.wav';wav.write_bytes(b'exact-audio');sha=file_digest(wav)
            primary={**transcript_check('one two','one',sha,'primary'),'synthesis_key':'take'}
            selected={'line':{'path':str(wav),'unit':{'spoken':'one two'},
                'receipt':{'audio_sha256':sha},'word_check':primary}}
            witness={**transcript_check('one two','one two',sha,'secondary'),'unit_id':'line','synthesis_key':'take'}
            path=Path(folder)/'report.json';write_json(path,{'verifier':{'model':'independent'},'checks':{'key':witness}})
            result=apply_secondary(selected,[path])
            self.assertEqual(result['line']['word_check']['status'],'asr_exact_match')
            self.assertEqual(result['line']['word_check']['primary_disagreement'],primary)
            self.assertFalse(result['line']['word_check']['human_approval'])
            self.assertEqual(selected['line']['word_check'],primary)
            witness['synthesis_key']='different';write_json(path,{'verifier':{},'checks':{'key':witness}})
            self.assertEqual(apply_secondary(selected,[path])['line']['word_check'],primary)
            wav.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'audio changed'):apply_secondary(selected,[path])

    def test_digit_case_ending_needs_another_witness_of_the_same_numeral(self):
        source='Прибыль с восемнадцати великих подземелий.'
        with tempfile.TemporaryDirectory() as folder:
            wav=Path(folder)/'take.wav';wav.write_bytes(b'exact-audio');sha=file_digest(wav)
            primary={**transcript_check(source,'с 18',sha,'primary'),'synthesis_key':'take'}
            selected={'line':{'path':str(wav),'unit':{'spoken':source},'receipt':{'audio_sha256':sha},'word_check':primary}}
            def report(name,heard):
                path=Path(folder)/name/'report.json'
                write_json(path,{'verifier':{'model':name,'language':'ru'},'checks':{'k':{**transcript_check(source,heard,sha,name),
                    'unit_id':'line','synthesis_key':'take'}}})
                return path
            digits=report('digits','Прибыль с 18 великих подземелий.')
            self.assertEqual(apply_secondary(selected,[digits])['line']['word_check']['status'],'spoken_text_mismatch')
            wrong=report('wrong','Прибыль с восемнадцать великих подземелей.')
            self.assertEqual(apply_secondary(selected,[digits,wrong])['line']['word_check']['status'],'spoken_text_mismatch')
            spelled=report('spelled','Прибыль с восемнадцати великих подземельях.')
            check=apply_secondary(selected,[digits,spelled])['line']['word_check']
            self.assertEqual(check['status'],'asr_exact_match')
            self.assertEqual(check['numeral_corroborations'][0]['corroborating_window'],['с','восемнадцати','великих'])
            self.assertEqual(check['heard_raw'],'Прибыль с 18 великих подземелий.')
            other=report('other','Прибыль с 19 великих подземелий.')
            self.assertEqual(apply_secondary(selected,[other,spelled])['line']['word_check']['status'],'spoken_text_mismatch')

    def test_hyphenated_sustained_sound_keeps_its_spelling_through_the_rules(self):
        source='У-у-у…'
        with tempfile.TemporaryDirectory() as folder:
            wav=Path(folder)/'take.wav';wav.write_bytes(b'audio');sha=file_digest(wav)
            primary={**transcript_check(source,'О.',sha,'primary'),'synthesis_key':'k'}
            selected={'line':{'path':str(wav),'unit':{'spoken':source},'receipt':{'audio_sha256':sha},'word_check':primary}}
            path=Path(folder)/'w'/'report.json'
            write_json(path,{'verifier':{'model':'w','language':'ru'},'checks':{'a':{**transcript_check(source,'«У-у-у!»',sha,'w'),
                'unit_id':'line','synthesis_key':'k'}}})
            self.assertEqual(apply_secondary(selected,[path])['line']['word_check']['status'],'asr_exact_match')

    def test_digit_year_matches_a_spoken_ordinal_with_a_corroborating_witness(self):
        source='Изготовленная в две тысячи пятьдесят восьмом году.'
        with tempfile.TemporaryDirectory() as folder:
            wav=Path(folder)/'take.wav';wav.write_bytes(b'audio');sha=file_digest(wav)
            primary={**transcript_check(source,'в 2058 году',sha,'primary'),'synthesis_key':'k'}
            selected={'line':{'path':str(wav),'unit':{'spoken':source},'receipt':{'audio_sha256':sha},'word_check':primary}}
            def report(name,heard):
                path=Path(folder)/name/'report.json'
                write_json(path,{'verifier':{'model':name,'language':'ru'},'checks':{'k':{**transcript_check(source,heard,sha,name),
                    'unit_id':'line','synthesis_key':'k'}}})
                return path
            digits=report('digits','Изготовленное в 2058 году.')
            spelled=report('spelled','Изготовленное в две тысячи пятьдесят восьмом году.')
            check=apply_secondary(selected,[digits,spelled])['line']['word_check']
            self.assertEqual(check['status'],'asr_exact_match')

if __name__=='__main__':unittest.main()
