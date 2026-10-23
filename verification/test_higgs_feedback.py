from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from render_book1_higgs_half import boundary_padding, prompt_for


class HiggsFeedbackTests(unittest.TestCase):
    def test_introduction_changes_pace_at_first_list_item(self):
        row = {'id': 'c0004-p00002-0-378', 'speaker': 'narrator',
               'delivery': 'neutral',
               'spoken': 'Так представляли контакт. И сигнал из космоса. И находка корабля.'}
        prompt = prompt_for(row)
        self.assertIn('контакт. <|prosody:speed_fast|>И сигнал', prompt)
        self.assertIn('<|prosody:speed_fast|>И находка', prompt)
        self.assertFalse(prompt.startswith('<|prosody:speed_fast|>'))

    def test_name_hint_is_only_in_audio_prompt(self):
        row = {'id': 'c0004-p00004-0-358', 'speaker': 'rb1_broadcast_envoy',
               'delivery': 'neutral', 'spoken': 'Цивилизация Шихарса.'}
        self.assertIn('ШихАрса', prompt_for(row))
        self.assertEqual(row['spoken'], 'Цивилизация Шихарса.')

    def test_narochito_stress_hint_preserves_display_text(self):
        row = {'id': 'c0005-p00014-0-265', 'speaker': 'narrator',
               'delivery': 'neutral', 'spoken': 'Нарочито с шумом распахнув дверь.'}
        self.assertIn('НарочИто', prompt_for(row))
        self.assertEqual(row['spoken'], 'Нарочито с шумом распахнув дверь.')

    def test_joyful_quote_and_stunned_ellipsis_have_distinct_controls(self):
        winner = {'id': 'c0005-p00024-0-25', 'speaker': 'rb1_kirill',
                  'delivery': 'excited', 'spoken': '— Как я его! Вы видели?! '}
        pause = {'id': 'c0005-p00025-0-8', 'speaker': 'narrator',
                 'delivery': 'neutral', 'spoken': 'И замер…'}
        self.assertIn('<|emotion:elation|>', prompt_for(winner))
        self.assertIn('<|emotion:surprise|>', prompt_for(pause))
        self.assertEqual(boundary_padding(pause['id'], pause['spoken']), (0.0, 0.8))

    def test_part_heading_gets_silence_before_and_after(self):
        title = {'id': 'c0005-p00001-0-28', 'speaker': 'narrator',
                 'delivery': 'neutral', 'spoken': 'Глава первая. Сетевой турнир'}
        self.assertTrue(prompt_for(title).startswith('<|prosody:speed_slow|>'))
        self.assertEqual(boundary_padding(title['id'], title['spoken']), (0.85, 1.15))

    def test_ellipsis_inside_a_segment_gets_inline_pause(self):
        row = {'id': 'c0005-p00022-0-128', 'speaker': 'narrator',
               'delivery': 'neutral',
               'spoken': 'Я принял решение… и сделал шаг вперёд.'}
        self.assertIn('решение…<|prosody:long_pause|> и', prompt_for(row))


if __name__ == '__main__':
    unittest.main()
