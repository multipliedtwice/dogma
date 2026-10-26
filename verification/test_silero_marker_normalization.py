"""No model execution: malformed tagger markers and literal source signs."""
import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=next(p for p in Path(__file__).resolve().parents if(p/'runtime/rimsky/store.py').is_file())
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky import auto_pronunciation as auto

class SileroMarkerTests(unittest.TestCase):
    def run_marker(self,source,raw):
        accentor=Mock(return_value=raw)
        with patch.object(auto,'_accentor',return_value=accentor):
            result=auto._silero(source,['NAME'])
        self.assertEqual(source,accentor.call_args.args[0]);self.assertEqual(['name'],accentor.call_args.kwargs['words_to_ignore'])
        return result
    def test_added_consonant_marker_is_removed_without_guessing_stress(self):
        source='\u2014 \u00ab\u0414\u0435\u043d\u044c\u0433\u0438 \u0442\u0443\u0442 \u043d\u0435 \u043d\u0443\u0436\u043d\u044b\u00bb.'
        raw=source.replace('\u0414','+\u0414').replace('\u043d\u044b','\u043d+\u044b')
        self.assertEqual(source.replace('\u043d\u044b','\u043d\u044b\u0301'),self.run_marker(source,raw))
    def test_invalid_markers_in_other_positions_are_not_source_edits(self):
        for source,raw in [('Word.','+Word.+'),('\u0421\u043b\u043e\u0432\u043e.','+\u0421\u043b\u043e\u0432\u043e.+'),('a b','a+ b')]:
            with self.subTest(source=source):self.assertEqual(source,self.run_marker(source,raw))
    def test_literal_arithmetic_and_plus_before_vowel_are_preserved(self):
        for source in ('2+2=4','a+b','\u0430+\u0431','+\u0414\u0435\u043d\u044c\u0433\u0438','+\u0410','++\u0430'):
            with self.subTest(source=source):self.assertEqual(source,self.run_marker(source,source))
    def test_literal_plus_survives_adjacent_added_valid_mark(self):
        source='2+ \u0432\u043e\u0434\u0430';raw='2+ \u0432\u043e\u0434+\u0430'
        self.assertEqual('2+ \u0432\u043e\u0434\u0430\u0301',self.run_marker(source,raw))
    def test_vowel_mark_and_yo_conversion_remain_valid(self):
        self.assertEqual('\u0432\u043e\u0434\u0430\u0301',self.run_marker('\u0432\u043e\u0434\u0430','\u0432\u043e\u0434+\u0430'))
        self.assertEqual('\u0451\u0436',self.run_marker('\u0435\u0436','+\u0451\u0436'))
    def test_existing_acute_is_preserved_without_duplication(self):
        source='\u0432\u043e\u0434\u0430\u0301'
        self.assertEqual(source,self.run_marker(source,'\u0432\u043e\u0434+\u0430\u0301'))
    def test_unrelated_letter_punctuation_or_literal_plus_loss_holds(self):
        for source,raw in [('word','other'),('word.','word!'),('a+b','ab'),('a + b','a+ b')]:
            with self.subTest(source=source),self.assertRaises(ValueError):self.run_marker(source,raw)
    def test_duplicate_added_vowel_markers_hold(self):
        with self.assertRaisesRegex(ValueError,'duplicated'):self.run_marker('\u0432\u043e\u0434\u0430','\u0432\u043e\u0434++\u0430')

if __name__=='__main__':unittest.main()
