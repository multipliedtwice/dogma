"""Split-audit proposals must respect saved work and structural ambiguity."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_label_split_gaps import structural_events, remaining_edges, frozen_passage_text, model_events


class LabelSplitGapTests(unittest.TestCase):
    def test_speech_author_speech_has_two_edges(self):
        text = 'Тебя нигде нет, — Дмитрий обвёл рукой, — все рычат на меня!'
        events = structural_events(text, 'character')
        self.assertEqual([x['kind'] for x in events], ['speech_author_transition', 'author_speech_resumption'])

    def test_grammatical_dash_and_nested_quote_do_not_change_performer(self):
        self.assertEqual(structural_events('Светлана в «нагрузке» — на руках браслеты.', 'narration'), [])
        self.assertEqual(structural_events('— Ты говоришь: «побуду с фракцией», это как?', 'character'), [])

    def test_older_placeholder_cut_at_word_edge_is_already_split(self):
        text = 'Что это было… (нецензурно)?!'
        start = text.index('(')
        offsets = structural_events(text, 'character')[0]['offsets']
        self.assertEqual(remaining_edges(text, offsets, [start + 1]), [])

    def test_one_saved_aside_edge_still_leaves_resumption(self):
        text = 'Либо (что маловероятно), либо нет.'
        a, b = structural_events(text)[0]['offsets']
        self.assertEqual(remaining_edges(text, [a, b], [a]), [b])

    def test_full_aside_at_end_has_only_one_internal_edge(self):
        text = 'Навык (модифицированный).'
        a, b = structural_events(text)[0]['offsets']
        self.assertEqual(remaining_edges(text, [a, b], []), [a])

    def test_midword_model_boundary_is_not_a_cut(self):
        self.assertEqual(remaining_edges('Вернулся домой.', [3], []), [])

    def test_rows_recover_raw_text_without_silently_losing_parts(self):
        rows = [{'source_id': 'p', 'start': 2, 'end': 5, 'text': 'Да.'},
                {'source_id': 'p', 'start': 5, 'end': 9, 'text': ' Нет'}]
        self.assertEqual(frozen_passage_text(rows, 'p', 2, 9), 'Да. Нет')
        rows[1]['start'] = 6
        self.assertIsNone(frozen_passage_text(rows, 'p', 2, 9))

    def test_parts_before_clip_and_same_speaker_do_not_create_false_cut(self):
        parts = [{'start': 0, 'end': 10, 'speaker': 'a', 'mode': 'speech'},
                 {'start': 10, 'end': 20, 'speaker': 'narrator', 'mode': 'narration'},
                 {'start': 20, 'end': 30, 'speaker': 'a', 'mode': 'speech'}]
        self.assertEqual(model_events(parts, 20, 30, 'x' * 10, True), [])
        self.assertEqual([e['offsets'] for e in model_events(parts, 0, 30, 'x' * 30, True)], [[10], [20]])


if __name__ == '__main__':
    unittest.main()
