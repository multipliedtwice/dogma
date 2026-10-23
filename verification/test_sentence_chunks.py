from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rimsky.analysis import synthesis_units, validate_analysis
from test_core import unit_parts
from rimsky.books import sentence_spans, split_text


class SentenceChunkTests(unittest.TestCase):
    def test_never_breaks_a_long_sentence_or_word(self):
        sentence = 'Это ' + 'длинное ' * 65 + 'предложение.'
        self.assertEqual([(0, len(sentence), sentence)], split_text(sentence, limit=120))

    def test_quotes_and_attribution_remain_one_sentence(self):
        text = '— Да, мы видели, — усмехнулся мужчина. Потом он ушёл.'
        spans = sentence_spans(text)
        self.assertEqual(['— Да, мы видели, — усмехнулся мужчина. ',
                          'Потом он ушёл.'], [text[a:b] for a, b in spans])

    def test_segments_join_only_after_complete_sentences(self):
        text = 'Первая длинная фраза. Вторая длинная фраза. Третья длинная фраза.'
        chunks = split_text(text, limit=35)
        self.assertEqual(text, ''.join(chunk for _, _, chunk in chunks))
        boundaries = {0, len(text)} | {edge for span in sentence_spans(text) for edge in span}
        self.assertTrue(all(a in boundaries and b in boundaries for a, b, _ in chunks))

    def test_character_and_attribution_become_one_audio_chunk(self):
        text = '— Как я его! Вы видели?! — восторженно прокричал я.'
        cut = text.index('— восторженно')
        source = {'id': 'p', 'source_id': 'p', 'start': 0, 'end': len(text),
                  'ordinal': 0, 'chapter_id': 'c', 'text': text}
        proposal = {'characters': [], 'passages': [{'id': 'p', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'excited',
             'evidence': ['p']},
            {'start': cut, 'end': len(text), 'speaker': 'narrator',
             'delivery': 'neutral', 'evidence': []}])}]}
        cast = [{'id': 'hero', 'name': 'Hero', 'aliases': [],
                 'description': '', 'evidence': ['p']}]
        parts = synthesis_units(validate_analysis(proposal, [source], cast)[0], [source])
        self.assertEqual(text, ''.join(part['text'] for part in parts))
        self.assertEqual(['— Как я его! ', 'Вы видели?! — восторженно прокричал я.'], [part['text'] for part in parts])
        self.assertEqual([('hero', 'excited', False), ('hero', 'excited', True)],
                         [(part['speaker'], part['delivery'], part['performance']['internal_transition']) for part in parts])
        mixed = parts[1]['performance']['analysis_parts']
        self.assertEqual([('hero', 'excited'), ('narrator', 'neutral')], [(item['speaker'], item['delivery']) for item in mixed])
        self.assertIn('split review', parts[1]['warning'])

    def test_grouping_does_not_mix_context_evidence_between_parts(self):
        text = 'Первое предложение. Второе предложение.'
        cut = text.index('Второе')
        source = {'id': 'p', 'source_id': 'p', 'start': 0, 'end': len(text),
                  'ordinal': 0, 'chapter_id': 'c', 'text': text}
        def context(quote):
            return {'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose', 'confidence': 'high',
                    'evidence': [{'id': 'p', 'quote': quote}]}
        proposal = {'characters': [], 'passages': [{'id': 'p', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'narrator', 'delivery': 'neutral',
             'evidence': [], 'context': context('Первое предложение')},
            {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral',
             'evidence': [], 'context': context('Второе предложение')},
        ])}]}
        parts = synthesis_units(validate_analysis(proposal, [source], [])[0], [source])
        self.assertEqual(1, len(parts))
        analysis_parts = parts[0]['performance']['analysis_parts']
        self.assertEqual(['Первое предложение'],
                         [item['quote'] for item in analysis_parts[0]['context']['evidence']])
        self.assertEqual(['Второе предложение'],
                         [item['quote'] for item in analysis_parts[1]['context']['evidence']])


if __name__ == '__main__':
    unittest.main()
