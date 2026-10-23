import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from audit_higgs_english_asr import english

class LanguageAuditTests(unittest.TestCase):
    def test_only_english_script_selects_the_english_recognizer(self):
        self.assertTrue(english('Wise men say'))
        self.assertTrue(english('Shall I stay?'))
        for text in ('Гундахар пересек рубку.', 'Версия 21A', 'Он сказал Hello', '—', '162'):
            self.assertFalse(english(text))

if __name__=='__main__':unittest.main()
