import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from rimsky.speaker_resolution import existing_character_for_name, named_attribution_speaker, IDENTITY_CONTINUITY_RULE
from rimsky.analysis import SYSTEM_PROMPT


class AliasContinuityTests(unittest.TestCase):
    def setUp(self):
        self.cast=[{'id':'husband','name':'Иван Петров','aliases':['Ваня','Петров','Шустрый']},
                   {'id':'wife','name':'Мария','aliases':['Маша']}]

    def test_full_name_nickname_and_surname_reuse_one_identity(self):
        for name in ('Иван Петров','Ваня','Петров','Шустрый','петров'):
            self.assertEqual(existing_character_for_name(name,self.cast),'husband')
        self.assertEqual(named_attribution_speaker('— ответил Шустрый.',self.cast),'husband')
        self.assertEqual(named_attribution_speaker('— сказал Петров.',self.cast),'husband')

    def test_wife_addressing_husband_does_not_make_him_speaker(self):
        text='— Петров, ты опять всё забыл! — сказала Маша.'
        attribution=text[text.index('— сказала'):]
        self.assertEqual(named_attribution_speaker(attribution,self.cast),'wife')

    def test_shared_surname_is_not_a_merge_or_deterministic_speaker(self):
        self.cast.append({'id':'brother','name':'Пётр Петров','aliases':['Петров']})
        self.assertIsNone(existing_character_for_name('Петров',self.cast))
        self.assertIsNone(named_attribution_speaker('— сказал Петров.',self.cast))
        self.assertIsNone(existing_character_for_name('Петрова',self.cast))

    def test_general_model_has_same_identity_continuity_rule(self):
        from rimsky.staged_analysis import SPEAKER_SYSTEM
        from rimsky.speaker_resolution import TIE_BREAKER_SYSTEM
        self.assertIn(IDENTITY_CONTINUITY_RULE,SYSTEM_PROMPT)
        self.assertIn(IDENTITY_CONTINUITY_RULE,SPEAKER_SYSTEM)
        self.assertIn(IDENTITY_CONTINUITY_RULE,TIE_BREAKER_SYSTEM)


if __name__=='__main__':unittest.main()
