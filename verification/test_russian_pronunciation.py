import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.russian_pronunciation import RULE, apply, validate


class RussianPronunciationTests(unittest.TestCase):
    def test_exact_pronouns_at_real_boundaries_keep_source_and_offsets(self):
        source = '«кого?»\nникого; (некого). кого'
        spoken, records = apply(source)
        self.assertEqual(spoken, '«каво?»\nникаво; (некого). каво')
        self.assertEqual(len(records), 3)
        self.assertEqual(len(spoken), len(source))
        for record in records:
            self.assertEqual(source[record['start']:record['end']], record['original'])
            self.assertEqual(spoken[record['start']:record['end']], record['spoken'])
            self.assertEqual(record['rule'], RULE)
        validate(source, spoken, records)

    def test_stress_and_case_survive_only_approved_vowel_reduction(self):
        source = 'Кого́ НИКОГО́ Не́кого ко́го нико́го'
        spoken, records = apply(source)
        self.assertEqual(spoken, 'Каво́ НИКАВО́ Не́кого ка́во ника́во')
        self.assertEqual(source.count('\u0301'), spoken.count('\u0301'))
        validate(source, spoken, records)

    def test_only_known_particle_compounds_allow_pronoun_components(self):
        source = 'кого-то кого-либо кого-нибудь кое-кого КОЕ-КОГО́ кого‐то кого‑нибудь'
        spoken, records = apply(source)
        self.assertEqual(spoken, 'каво-то каво-либо каво-нибудь кое-каво КОЕ-КАВО́ каво‐то каво‑нибудь')
        self.assertEqual([r['original'].replace('\u0301', '').lower() for r in records], ['кого'] * 7)
        validate(source, spoken, records)

    def test_unknown_words_names_suffixes_and_compounds_are_untouched(self):
        source = ('коготь коготки когтями когорта погоны Коготин Когоев '
                  'дорогого строгого синего чего ничего некогошний '
                  'abcкого когоX xкого когоx 1кого кого2 _кого кого_ некого '
                  'чудо-кого кого-неизвестный никого-то кое-некого '
                  'кое-кого-то кого\u0483 кого\u200dто')
        self.assertEqual(apply(source), (source, []))
        validate(source, source, [])

    def test_validator_rejects_vowel_stress_case_and_other_word_changes(self):
        source = 'Кого́, никого и коготь'
        expected, records = apply(source)
        for bad in ('Ково́, никово и коготь', 'Каво, никаво и коготь',
                    'каво́, никаво и коготь', 'Каво́, никаво и ковоть',
                    'Каво́, никава и коготь',
                    expected + '!', source):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(source, bad, records)

    def test_validator_rejects_forged_missing_or_reordered_provenance(self):
        source = 'кого никого'
        spoken, records = apply(source)
        variants = [[], list(reversed(records)), records + [records[0]]]
        for field, value in (('start', 1), ('start', False), ('end', 4.0),
                             ('end', 2), ('original', 'кого-то'),
                             ('spoken', 'ково'), ('rule', 'untrusted')):
            changed = copy.deepcopy(records)
            changed[0][field] = value
            variants.append(changed)
        for forged in variants:
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                validate(source, spoken, forged)
        validate(source, spoken)

    def test_empty_and_already_phonetic_inputs_are_stable(self):
        for source in ('', 'ково никово неково', '— ...\n'):
            self.assertEqual(apply(source), (source, []))
            validate(source, source, [])


if __name__ == '__main__':
    unittest.main()
