"""Reader metadata must describe the newly committed, source-bound take."""
import copy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import current_higgs_output as current
from rimsky import russian_pronunciation as phonetics


class PreparedReaderText(unittest.TestCase):
    def unit(self):
        prepared='Никого́ не ви́дно.'
        native,records=phonetics.apply(prepared)
        return {'text':'Никого не видно.','spoken':'Никого не видно.',
            'synthesis':{'text':native,'russian_phonetics':{'rule':phonetics.RULE,
                'prephonetic_source':prepared,'records':records}}}

    def test_reader_gets_final_human_marks_without_model_only_phonetic_spelling(self):
        unit=self.unit();before=copy.deepcopy(unit)
        self.assertEqual('Никого́ не ви́дно.',current.prepared_spoken(unit))
        self.assertNotEqual(unit['synthesis']['text'],current.prepared_spoken(unit))
        self.assertEqual(before,unit)

    def test_corrupt_phonetic_record_rejected(self):
        unit=self.unit();unit['synthesis']['russian_phonetics']['records'][0]['spoken']='другое'
        with self.assertRaises(ValueError):current.prepared_spoken(unit)

    def test_changed_native_text_rejected(self):
        unit=self.unit();unit['synthesis']['text']+=' Лишнее.'
        with self.assertRaises(ValueError):current.prepared_spoken(unit)

    def test_conflicting_explicit_prepared_reader_rejected(self):
        unit=self.unit();unit['prepared_spoken']='Другие слова.'
        with self.assertRaisesRegex(ValueError,'contradicts'):current.prepared_spoken(unit)

    def test_no_phonetic_proof_requires_exact_native_reader_equality(self):
        unit={'text':'Первый.','spoken':'Первый.','synthesis':{'text':'Пе́рвый.'}}
        with self.assertRaisesRegex(ValueError,'not bound'):current.prepared_spoken(unit)
        unit['prepared_spoken']='Пе́рвый.'
        self.assertEqual('Пе́рвый.',current.prepared_spoken(unit))

    def test_plain_source_fallback_only_when_native_is_identical(self):
        self.assertEqual('Plain.',current.prepared_spoken({'text':'Plain.','synthesis':{'text':'Plain.'}}))
        with self.assertRaises(ValueError):current.prepared_spoken({'text':'Plain.','synthesis':{'text':'Different.'}})

    def test_missing_prephonetic_records_cannot_bypass_binding(self):
        unit=self.unit();unit['synthesis']['russian_phonetics'].pop('records')
        with self.assertRaisesRegex(ValueError,'binding'):current.prepared_spoken(unit)

if __name__=='__main__':unittest.main()
