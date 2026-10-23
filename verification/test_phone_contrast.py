import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import audit_higgs_phone_contrast as pc
from rimsky.speech_quality import spoken_word_tokens


class PhoneContrastTests(unittest.TestCase):
    SOURCE='Надо было передать Гласу список деталей и сундук.'

    def verdict(self,audio_text,heard):
        with patch.object(pc,'decode',return_value=pc.reference(spoken_word_tokens(audio_text))):
            return pc.judge('unused.wav',self.SOURCE,heard)

    def test_audio_matching_the_source_beats_a_multi_phone_recognizer_rewrite(self):
        verdict=self.verdict(self.SOURCE,'Надо было передать глазу список деталей и сундуков.')
        self.assertEqual([r['recognizer'] for r in verdict['spans']],['глазу','сундуков'])
        self.assertEqual(verdict['spans'][0]['margin'],1)       # single-phone contrast: below threshold
        self.assertFalse(verdict['supported'])                 # every span must exceed the threshold

    def test_audio_matching_the_recognizer_is_never_supported(self):
        heard='Надо было передать Гласу список деталей и сундуками.'
        self.assertFalse(self.verdict(heard,heard)['supported'])
        self.assertTrue(self.verdict(self.SOURCE,heard)['supported'])

    def test_homophone_differences_are_not_disputed(self):
        self.assertIsNone(self.verdict(self.SOURCE,'Надо было передать Гласу список деталей и сундук'))


if __name__=='__main__':unittest.main()
