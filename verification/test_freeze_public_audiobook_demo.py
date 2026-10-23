"""Synthetic numeric surfaces and immutable preparation preservation gates."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('freeze_demo', ROOT / 'scripts/freeze_public_audiobook_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class PronunciationPreparation(unittest.TestCase):
    def test_currency_cents_and_numeric_cents(self):
        spoken, records = demo.english_numbers('Keep $2.01, then 42 cents and $1.')
        self.assertEqual(spoken, 'Keep two dollars and one cent, then forty-two cents and one dollar.')
        self.assertEqual([r['source'] for r in records], ['$2.01', '42', '$1'])

    def test_currency_modifier_and_standalone_amount(self):
        spoken, _ = demo.english_numbers('A $25 gold piece and a $9 ticket cost $34.')
        self.assertEqual(spoken, 'A twenty-five dollar gold piece and a nine dollar ticket cost thirty-four dollars.')

    def test_ordinal_and_original_words_punctuation_survive(self):
        source = '“Her 3rd choice”—a clock at 6 o’clock; nothing else.'
        spoken, changes = demo.english_numbers(source)
        self.assertEqual(spoken, '“Her third choice”—a clock at six o’clock; nothing else.')
        demo.validate_conversion(source, spoken, changes)
        with self.assertRaisesRegex(ValueError, 'outside'):
            demo.validate_conversion(source, spoken.replace('clock', 'watch'), changes)

    def test_titles_expand_only_exact_source_abbreviations(self):
        source = 'Mr. Rowan greeted Mrs. Grey at Mme. Alba’s shop.'
        spoken, records = demo.english_numbers(source)
        self.assertEqual(spoken, 'Mister Rowan greeted Missus Grey at Madame Alba’s shop.')
        self.assertEqual([r['source'] for r in records], ['Mr.', 'Mrs.', 'Mme.'])
        self.assertTrue(all(r['rule'] == demo.ABBREVIATION_POLICY for r in records))
        demo.validate_conversion(source, spoken, records)

    def test_stress_and_yo_allowed_but_no_respelling_or_omission(self):
        demo.validate_pronunciation('Идет гость.', 'Идёт го́сть.')
        for changed in ['Идёт.', 'Идёт го́сть!', 'Идёт гэсть.']:
            with self.assertRaisesRegex(ValueError, 'changed source'):
                demo.validate_pronunciation('Идет гость.', changed)

    def test_adjectival_surname_form_inherits_base_vowel(self):
        from rimsky.name_stress import build
        extra, evidence = demo.source_inflections(['К Родиным.'], {'pn:fixture': 'Ро́дин'})
        self.assertEqual(extra, {'pn-form:Родиным': 'Ро́диным'})
        self.assertEqual(evidence[0]['base'], 'Родин')
        self.assertEqual(evidence[0]['stressed_vowel_index'], 1)
        self.assertEqual(build({'pn:fixture': 'Ро́дин', **extra}, implicit_single=True)['родиным'], 1)

    def test_final_stressed_vowel_follows_stem_compatible_case_ending(self):
        from rimsky import proper_noun_stress as pn
        from rimsky.name_stress import build
        morph = SimpleNamespace(parse=lambda word: [SimpleNamespace(normal_form='лума', tag='NOUN,Geox sing,loct')])
        with patch.object(pn, '_morph', return_value=morph):
            extra, evidence = demo.source_inflections(['В Луме.'], {'pn:fixture': 'Лума́'})
            self.assertEqual(extra, {'pn-form:Луме': 'Луме́'})
            self.assertEqual(evidence[0]['stressed_vowel_ordinal'], 1)
            self.assertEqual(build({'pn:fixture': 'Лума́', **extra}, implicit_single=True)['луме'], 3)
            with self.assertRaisesRegex(ValueError, 'loses the base'):
                demo.source_inflections(['В Ламе.'], {'pn:fixture': 'Лума́'})

    def test_resume_rejects_changed_record_preserving_existing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'manifest.json'
            demo.immutable(path, {'source': 'fixture'})
            original = path.read_bytes()
            demo.immutable(path, {'source': 'fixture'})
            with self.assertRaisesRegex(ValueError, 'Immutable'):
                demo.immutable(path, {'source': 'changed'})
            self.assertEqual(path.read_bytes(), original)

    def test_internal_structure_keeps_original_parts_and_source_offsets(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            request = folder / 'analysis/structure.request.json'
            demo.immutable(request, {'task': 'synthetic structure fixture'})
            demo.immutable(folder / 'analysis/structure.answer.json', {
                'request_sha256': demo.file_digest(request),
                'answer': {'p1': [{'text': '“Go,” ', 'mode': 'speech'},
                                   {'text': 'he said.', 'mode': 'narrator'}]}})
            book = {'chapters': [{'blocks': [{'id': 'p1', 'text': '“Go,” he said.'}]}]}
            units = [{'id': 'u1', 'source_id': 'p1', 'text': '“Go,” he said.', 'start': 0, 'end': 14}]
            demo.attach_structure(folder, units, book)
            parts = units[0]['performance']['analysis_parts']
            self.assertEqual([p['mode'] for p in parts], ['speech', 'narrator'])
            self.assertEqual(''.join(p['text'] for p in parts), units[0]['text'])
            self.assertEqual(parts[1]['start'], 6)
            self.assertEqual(parts[1]['source_part_text'], 'he said.')
            units[0]['text'] = '“Go,”'
            with self.assertRaisesRegex(ValueError, 'omitted'):
                demo.attach_structure(folder, units, book)


if __name__ == '__main__':
    unittest.main()
