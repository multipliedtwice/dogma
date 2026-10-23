from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'training'), str(ROOT / 'runtime')]
from prepare_bucket_split_plan import (  # noqa: E402
    anchored_dash_cut,
    delivery_dash_offsets,
    narrator_placeholder_spans,
    parenthetical_spans,
    plan,
    write_plan,
    structural_dash_offsets,
)
from rimsky_training.pairing import tokens  # noqa: E402


def timed_words(text, start=10.0, step=.4, pause_after=None):
    pause_after = pause_after or {}
    result = []
    cursor = start
    for index, (word, _, _) in enumerate(tokens(text)):
        end = cursor + step
        result.append((word, {'text': word, 'start': cursor, 'end': end}))
        cursor = end + pause_after.get(index, 0)
    return result


class StructuralDashOffsetsTests(unittest.TestCase):
    def test_leading_dialogue_dash_is_not_a_split(self):
        self.assertEqual(structural_dash_offsets('— Привет.'), [])

    def test_internal_dialogue_dash_is_delivery_boundary(self):
        text = 'Не помню, — признался он.'
        offset = delivery_dash_offsets(text)[0]
        self.assertEqual(text[offset:], 'признался он.')

    def test_book_style_en_dash_dialogue_boundary_is_supported(self):
        text = '\u2013 \u0418\u043c\u0435\u043d\u043d\u043e \u0442\u0430\u043a! \u2013 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0430\u043b \u044d\u043a\u0441\u043f\u0435\u0440\u0442.'
        self.assertEqual(len(structural_dash_offsets(text)), 1)
        offset = delivery_dash_offsets(text)[0]
        self.assertEqual(text[offset:], '\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0430\u043b \u044d\u043a\u0441\u043f\u0435\u0440\u0442.')

    def test_grammatical_dash_is_not_delivery_boundary(self):
        text = 'Это — губчатая платина.'
        self.assertEqual(len(structural_dash_offsets(text)), 1)
        self.assertEqual(delivery_dash_offsets(text), [])

    def test_grammatical_dash_can_still_have_a_manual_cut(self):
        text = 'Захотел спать — спи прямо в игре.'
        audio = timed_words('Захотел спать спи прямо в игре', pause_after={1: .2})
        timing = anchored_dash_cut(tokens(text), audio, structural_dash_offsets(text)[0], 9.8, 14.0)
        self.assertIsNotNone(timing)

    def test_anchor_recovery_ignores_a_substitution_away_from_cut(self):
        text = '— Меня откровенно пугают такие твои вопросы, — призналась мохнатая девушка.'
        heard = 'Меня откровенно пугают такие твои вопросы призналась махнатая девушка'
        audio = timed_words(heard, pause_after={5: .64})
        timing = anchored_dash_cut(tokens(text), audio, delivery_dash_offsets(text)[0], 9.8, 15.0)
        self.assertIsNotNone(timing)
        self.assertAlmostEqual(timing['pause_seconds'], .64)
        self.assertEqual(timing['adjacent_match'], 'exact')

    def test_short_dialogue_uses_one_word_side_and_clear_pause(self):
        text = '— Туда! — указал я на кратер.'
        audio = timed_words('Туда указал я на кратер', pause_after={0: .2})
        timing = anchored_dash_cut(tokens(text), audio, delivery_dash_offsets(text)[0], 9.8, 13.0)
        self.assertIsNotNone(timing)
        self.assertEqual(timing['anchor_words'], 4)

    def test_short_dialogue_without_pause_is_not_guessed(self):
        text = '— Тини? — моя подруга замерла.'
        audio = timed_words('Тини моя подруга замерла')
        timing = anchored_dash_cut(tokens(text), audio, delivery_dash_offsets(text)[0], 9.8, 13.0)
        self.assertIsNone(timing)

    def test_misheard_word_at_cut_is_not_fuzzily_guessed(self):
        text = 'Он спросил, — продолжила девушка.'
        audio = timed_words('Он спросил продолжало девушка', pause_after={1: .3})
        timing = anchored_dash_cut(tokens(text), audio, delivery_dash_offsets(text)[0], 9.8, 13.0)
        self.assertIsNone(timing)

    def test_spoken_placeholder_exposes_both_narrator_boundaries(self):
        text = '\u0421\u0430\u0434\u0438\u0442\u044c\u0441\u044f \u043d\u0430 \u044d\u0442\u0443 (\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u043e) \u043f\u043b\u0430\u043d\u0435\u0442\u0443.'
        spans = narrator_placeholder_spans(text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(text[spans[0]['start']:spans[0]['end']], '(\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u043e)')
        self.assertEqual(spans[0]['voice_family_id'], 'narrator_baseline')

    def test_inflected_spoken_placeholder_can_anchor_a_cut(self):
        text = '\u0421\u0430\u0434\u0438\u0442\u044c\u0441\u044f \u043d\u0430 \u044d\u0442\u0443 (\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u043e) \u043f\u043b\u0430\u043d\u0435\u0442\u0443.'
        heard = '\u0443\u0441\u0430\u0434\u0438\u0442\u044c\u0441\u044f \u043d\u0430 \u044d\u0442\u0443 \u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u0443\u044e \u043f\u043b\u0430\u043d\u0435\u0442\u0443'
        audio = timed_words(heard, pause_after={2: .2, 3: .2})
        span = narrator_placeholder_spans(text)[0]
        before = anchored_dash_cut(tokens(text), audio, span['start'], 9.8, 14.0)
        after = anchored_dash_cut(tokens(text), audio, span['end'], 9.8, 14.0)
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)

    def test_only_known_spoken_placeholders_are_detected(self):
        text = '— Возвращаем задержанных… ( НЕПОНЯТНО ). Потом (нецензурно). (тихо)'
        spans = narrator_placeholder_spans(text)
        self.assertEqual([text[s['start']:s['end']] for s in spans],
                         ['( НЕПОНЯТНО )', '(нецензурно)'])
        self.assertTrue(all(s['voice_family_id'] == 'narrator_baseline' for s in spans))

    def test_plan_suggests_placeholder_edges_with_parentheses_in_the_narrator_part(self):
        for text in ('— Возвращаем задержанных… (непонятно). ',
                     '— Садиться на эту (нецензурно) планету.',
                     '— Я… (непонятно) не могу… (непонятно)… вернуться.',
                     'Он (после паузы) тихо ответил.',
                     '— Я (даже после всего (что произошло)) вернусь.'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                board, pilot = root / 'board', root / 'source'
                board.mkdir()
                align_folder = pilot / 'recordings' / '21'
                align_folder.mkdir(parents=True)
                audio = root / 'clip.wav'
                with wave.open(str(audio), 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(24000)
                    wav.writeframes(b'\0\0' * 240000)
                words = timed_words(text, pause_after={i: .1 for i in range(len(tokens(text)))})
                alignment = {'source': {'sha256': 'recording'},
                             'windows': [{'words': [word for _, word in words]}]}
                (align_folder / 'align.json').write_text(json.dumps(alignment), encoding='utf-8')
                row = {'id': 'clip', 'source_id': 'paragraph', 'recording_id': 'rb2-21',
                       'recording_sha256': 'recording', 'source_text': text,
                       'text_start': 0, 'text_end': len(text), 'exact_audio': 'clip.wav',
                       'audio_start': 10.0, 'audio_end': 20.0,
                       'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}
                (board / 'candidates.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
                with patch('prepare_bucket_split_plan.ROOT', root):
                    result = plan(board, pilot)
                clip = result['clips']['clip']
                placeholders = narrator_placeholder_spans(text)
                spans = placeholders or parenthetical_spans(text)
                for span in spans:
                    if any(word_start < span['start'] for _, word_start, _ in tokens(text)):
                        self.assertIn(span['start'], clip['suggested'])
                    if any(word_start >= span['end'] for _, word_start, _ in tokens(text)):
                        self.assertIn(span['end'], clip['suggested'])
                edges = [0, *clip['suggested'], len(text)]
                parts = [text[start:end] for start, end in zip(edges, edges[1:])]
                self.assertEqual(''.join(parts), text)
                for span in spans:
                    self.assertTrue(any(text[span['start']:span['end']] in part for part in parts))
                if placeholders:
                    self.assertEqual(clip['voice_prefill_spans'], spans)
                    self.assertIn('speech_internal_placeholder', clip['complexity_tags'])
                else:
                    self.assertEqual(clip['delivery_review_spans'], spans)
                    self.assertNotIn('voice_prefill_spans', clip)
                    self.assertIn('parenthetical_aside', clip['complexity_tags'])
                self.assertFalse(result['training_approved'])
                if text.startswith('— Возвращаем'):
                    # Missing recognition must queue alignment instead of inventing a cut.
                    bad = json.loads(json.dumps(alignment))
                    bad['windows'][0]['words'][-1]['text'] = 'потеряно'
                    (align_folder / 'align.json').write_text(json.dumps(bad), encoding='utf-8')
                    with patch('prepare_bucket_split_plan.ROOT', root):
                        unresolved = plan(board, pilot)
                    self.assertEqual(unresolved['evaluation']['unresolved_for_forced_alignment'], ['clip'])
                    self.assertEqual(unresolved['clips']['clip']['suggested'], [])
                    (align_folder / 'align.json').write_text(json.dumps(alignment), encoding='utf-8')
                    # A listener's existing split freezes its original text/audio boundaries.
                    prior = json.loads(json.dumps(result))
                    prior['clips']['clip']['suggested'] = [clip['cuts'][0]['offset']]
                    (board / 'split-plan.json').write_text(json.dumps(prior), encoding='utf-8')
                    review = {'candidates_sha256': result['candidates_sha256'],
                              'decisions': {'clip': {'split_review': {'enabled': True,
                                            'cuts': prior['clips']['clip']['suggested']}}}}
                    review_bytes = json.dumps(review).encode('utf-8')
                    (board / 'review-draft.json').write_bytes(review_bytes)
                    with patch('prepare_bucket_split_plan.ROOT', root):
                        retained = plan(board, pilot)
                        self.assertEqual(retained['clips']['clip'], prior['clips']['clip'])
                        with self.assertRaisesRegex(ValueError, 'Saved split boundaries are frozen'):
                            write_plan(result, board, 'book', pilot)
                        write_plan(retained, board, 'book', pilot)
                    self.assertEqual((board / 'review-draft.json').read_bytes(), review_bytes)
                    # Generated proposal provenance may change before Book 3 receives
                    # its first listener decision. The split plan is then recomputed,
                    # but a review containing any decision keeps the hard hash guard.
                    row['proposal_provenance'] = 'new-frozen-model'
                    (board / 'candidates.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
                    with patch('prepare_bucket_split_plan.ROOT', root):
                        with self.assertRaisesRegex(ValueError, 'different candidate set'):
                            plan(board, pilot)
                        (board / 'review-draft.json').write_text(json.dumps({
                            'candidates_sha256': result['candidates_sha256'], 'decisions': {}}),
                            encoding='utf-8')
                        rebound = plan(board, pilot)
                    self.assertNotEqual(rebound['candidates_sha256'], result['candidates_sha256'])
                    self.assertIn('clip', rebound['clips'])

    def test_parenthetical_asides_include_nested_text_without_guessing_a_voice(self):
        text = 'Он (тихо (почти шёпотом)) ответил (после паузы). ( ) (оборвано'
        spans = parenthetical_spans(text)
        self.assertEqual([text[s['start']:s['end']] for s in spans],
                         ['(тихо (почти шёпотом))', '(после паузы)'])
        self.assertTrue(all('voice_family_id' not in span for span in spans))


if __name__ == '__main__':
    unittest.main()
