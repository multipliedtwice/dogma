from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.speech_structure import (SPEECH_STRUCTURE_SCHEMA,
                                     checked_speech_structure,
                                     grouped_structure,
                                     needs_structure_analysis,
                                     speech_structure_request,
                                     structure_problem,
                                     structure_chunks,
                                     structure_matches_answer)
from rimsky.boundaries import unit_range
from rimsky.staged_analysis import staged_proposal_with_details
from rimsky.util import AppError
from rimsky.staged_analysis import speaker_schema
from rimsky.jobs import speaker_review_holds
from rimsky.speaker_resolution import (attribution_name_hints,
                                       named_attribution_speaker,
                                       resolution_metrics,
                                       resumes_after_insertion)


def source(ident: str, text: str) -> dict:
    return {'id': ident, 'source_id': ident, 'start': 0, 'end': len(text),
            'text': text, 'ordinal': 0, 'chapter_id': 'c', 'kind': 'paragraph'}


class SpeechStructureBoundaryTests(unittest.TestCase):
    def assert_chunks(self, text: str, expected: list[str]) -> None:
        chunks = structure_chunks(text)
        self.assertEqual([chunk['text'] for chunk in chunks], expected)
        self.assertEqual(''.join(chunk['text'] for chunk in chunks), text)
        self.assertEqual(chunks[0]['start'], 0)
        self.assertEqual(chunks[-1]['end'], len(text))
        for left, right in zip(chunks, chunks[1:]):
            self.assertEqual(left['end'], right['start'])

    def test_leading_dash_is_kept_in_direct_speech(self):
        self.assert_chunks('— Привет.', ['— Привет.'])
        self.assertTrue(needs_structure_analysis('— Привет.'))

    def test_speech_attribution_and_resumed_speech_are_three_chunks(self):
        self.assert_chunks(
            '— Привет, — сказал он. — Пока.',
            ['— Привет, ', '— сказал он. ', '— Пока.'])

    def test_sentence_ending_dash_starts_a_new_speech_turn(self):
        text = '— Кто там? — Я.'
        chunks = structure_chunks(text)
        groups = grouped_structure(chunks, ['speech', 'speech'])
        self.assertEqual(groups, [
            {'start': 0, 'end': text.index('—', 1), 'mode': 'speech'},
            {'start': text.index('—', 1), 'end': len(text), 'mode': 'speech'},
        ])

    def test_explanatory_dash_is_a_candidate_not_an_assumed_speaker_change(self):
        self.assert_chunks(
            'Захотел спать — спи прямо в игре.',
            ['Захотел спать ', '— спи прямо в игре.'])

    def test_guillemets_and_curly_quotes_preserve_contiguous_sides(self):
        self.assert_chunks(
            'Он сказал: «Да». Потом ушёл.',
            ['Он сказал: ', '«Да». ', 'Потом ушёл.'])
        self.assert_chunks(
            'Он сказал: “Да”. Потом ушёл.',
            ['Он сказал: ', '“Да”. ', 'Потом ушёл.'])

    def test_interrupted_word_is_not_split_but_following_dash_is(self):
        self.assert_chunks(
            '— Я не хо— — Он осёкся.',
            ['— Я не хо— ', '— Он осёкся.'])

    def test_en_dash_dialogue_marker_is_structural(self):
        self.assert_chunks(
            '– Привет, – сказала она.',
            ['– Привет, ', '– сказала она.'])

    def test_ascii_hyphen_attribution_is_structural(self):
        self.assert_chunks(
            '— Привет, - сказал он.',
            ['— Привет, ', '- сказал он.'])

    def test_numeric_ranges_and_word_internal_dashes_are_not_split(self):
        for text in ('В 1941–1945 годах.', 'Нужно 5–10 попыток.',
                     'Кто—то постучал.'):
            self.assert_chunks(text, [text])


class SpeechStructureContractTests(unittest.TestCase):
    def test_nested_reported_quote_cannot_change_the_outer_speaker(self):
        text = ('\u2014 \u0411\u044b\u043b\u043e \u0436\u0443\u0442\u043a\u043e! \u2014 \u043f\u0440\u0438\u0437\u043d\u0430\u043b\u0430\u0441\u044c \u0441\u0432\u043e\u0438\u043c\u0438 \u0433\u043b\u0430\u0437\u0430\u043c\u0438 '
                '\u043d\u0430\u0431\u043b\u044e\u0434\u0430\u0432\u0448\u0430\u044f \u044d\u0442\u0443 \u043a\u0430\u0440\u0442\u0438\u043d\u0443 \u0418\u0440\u0438\u0448\u043a\u0430. \u2014 \u041d\u0430\u0448 \u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440 \u0441\u043b\u043e\u0432\u043d\u043e '
                '\u043f\u043e\u0441\u0442\u0430\u0440\u0435\u043b \u043b\u0435\u0442 \u043d\u0430 \u0434\u0435\u0441\u044f\u0442\u044c. \u041e\u043d \u0441\u043a\u0440\u044e\u0447\u0438\u043b\u0441\u044f, \u0434\u0435\u0440\u0436\u0430\u043b\u0441\u044f \u0440\u0443\u043a\u043e\u0439 \u0437\u0430 \u0433\u0440\u0443\u0434\u044c \u0438 '
                '\u0435\u0434\u0432\u0430 \u0440\u0430\u0437\u0431\u043e\u0440\u0447\u0438\u0432\u043e \u0431\u043e\u0440\u043c\u043e\u0442\u0430\u043b \u043e\u0434\u043d\u043e \u0438 \u0442\u043e\u0436\u0435: \u00ab\u041d\u0430\u043c \u043f\u043e\u0441\u0442\u0430\u0432\u0438\u043b\u0438 \u043c\u0430\u0442\u2026 '
                '\u201e\u0422\u0451\u043c\u043d\u0430\u044f \u0424\u0440\u0430\u043a\u0446\u0438\u044f\u201c \u043d\u0430\u043c \u043f\u043e\u0441\u0442\u0430\u0432\u0438\u043b\u0430 \u043c\u0430\u0442\u2026 \u0434\u0430\u0436\u0435 \u0435\u0441\u043b\u0438 \u043d\u0435 \u0447\u0435\u0440\u0435\u0437 \u0442\u0440\u0438 \u0434\u043d\u044f, '
                '\u0442\u043e \u0432\u0441\u0451 \u0440\u0430\u0432\u043d\u043e\u2026 \u043d\u0430\u043c \u0443\u0436\u0435 \u043f\u043e\u0441\u0442\u0430\u0432\u0438\u043b\u0438 \u043c\u0430\u0442\u00bb.')
        chunks = structure_chunks(text)
        fixed = [{**chunk, 'mode': mode} for chunk, mode in zip(
            chunks, ['speech', 'narrator', 'speech', 'speech', 'speech', 'speech'])]
        wrong = {'parts': [
            {'first': 0, 'last': 0, 'speaker': 'irishka'},
            {'first': 1, 'last': 1, 'speaker': 'narrator'},
            {'first': 2, 'last': 3, 'speaker': 'irishka'},
            {'first': 4, 'last': 8, 'speaker': 'radugin'},
        ]}
        self.assertIn('one continuous speech turn uses multiple speakers',
                      structure_problem(wrong, source('p1', text), fixed))
        corrected = {'parts': [
            {'first': 0, 'last': 0, 'speaker': 'irishka'},
            {'first': 1, 'last': 1, 'speaker': 'narrator'},
            {'first': 2, 'last': 8, 'speaker': 'irishka'},
        ]}
        self.assertEqual('', structure_problem(corrected, source('p1', text), fixed))

    def turn_problem(self, text: str, modes: list[str], speakers: list[str]) -> str:
        chunks = structure_chunks(text)
        self.assertEqual(len(chunks), len(modes))
        fixed = [{**chunk, 'mode': mode} for chunk, mode in zip(chunks, modes)]
        parts = []
        for chunk, speaker in zip(chunks, speakers):
            first, last = unit_range(text, chunk['start'], chunk['end'])
            parts.append({'first': first, 'last': last, 'speaker': speaker})
        return structure_problem({'parts': parts}, source('p1', text), fixed)

    def test_narration_between_turns_allows_a_different_speaker(self):
        self.assertEqual('', self.turn_problem(
            '— Я готов, — сказал Иван. Маша посмотрела на него. — Ты идёшь?',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'masha']))
        self.assertEqual('', self.turn_problem(
            '— Кто там? — спросил он. — Я, — ответил Петя.',
            ['speech', 'narrator', 'speech', 'narrator'],
            ['ivan', 'narrator', 'petya', 'narrator']))
        self.assertEqual('', self.turn_problem(
            '— Я готов. — Маша спросила: — Ты идёшь?',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'masha']))

    def test_same_speaker_resumes_after_narration(self):
        self.assertEqual('', self.turn_problem(
            '— Да, — сказал он. Помолчал. — Пойдём.',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'ivan']))
        self.assertEqual('', self.turn_problem(
            '— Да, — сказал он, — пойдём.',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'ivan']))
        self.assertEqual('', self.turn_problem(
            '— Стой! — крикнул он, — стой!',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'ivan']))

    def test_author_action_between_remarks_cannot_take_the_speaker(self):
        # Reality Benders 2, c0031-p00040: the frozen lane answer gave the
        # narrator's action sentence to the diplomat along with his remark.
        text = ('— Нашу фракцию более чем устроит такая помощь! — Дипломат уважительно поклонился '
                'грозному морфу, после чего повернулся в мою сторону. — Комар, на день, а может даже '
                'на два, я бы хотел воспользоваться услугами миелонки Айни — и с нашими «научниками» '
                'её свести, и ребят подкачать. Тебя же прошу уже со следующей сменой направиться на '
                '«Античный Пляж». Там кентавриха Филира снова спрашивала тебя. ')
        chunks = structure_chunks(text)
        modes = ['speech', 'narrator'] + ['speech'] * (len(chunks) - 2)
        fixed = [{**chunk, 'mode': mode} for chunk, mode in zip(chunks, modes)]

        def answer(*spans):
            parts = []
            for first, last, speaker in spans:
                low, high = unit_range(text, chunks[first]['start'], chunks[last]['end'])
                parts.append({'first': low, 'last': high, 'speaker': speaker})
            return {'parts': parts}

        frozen = answer((0, 1, 'ivan'), (2, len(chunks) - 1, 'ivan'))
        self.assertIn('as narrator', structure_problem(frozen, source('p1', text), fixed))
        corrected = answer((0, 0, 'ivan'), (1, 1, 'narrator'), (2, len(chunks) - 1, 'ivan'))
        self.assertEqual('', structure_problem(corrected, source('p1', text), fixed))

    def test_comma_attribution_insertion_keeps_one_performer(self):
        self.assertIn('one continuous speech turn uses multiple speakers', self.turn_problem(
            '— Да, — сказал он, — пойдём.',
            ['speech', 'narrator', 'speech'], ['ivan', 'narrator', 'masha']))

    def test_batch_contract_is_checked_and_keeps_owned_offsets(self):
        target = source('p1', '— Привет, — сказал он.')
        plain = source('p2', 'Обычное повествование.')
        request, schema = speech_structure_request(
            {'segments': [target, plain], 'before': [], 'after': []})
        self.assertEqual(SPEECH_STRUCTURE_SCHEMA, 2)
        self.assertEqual([item['id'] for item in request['dash_targets']], ['p1'])
        self.assertEqual(schema['required'], ['p1'])
        fixed = checked_speech_structure({'p1': ['speech', 'narrator']}, request)
        self.assertEqual([chunk['mode'] for chunk in fixed['p1']],
                         ['speech', 'narrator'])
        self.assertEqual(''.join(chunk['text'] for chunk in fixed['p1']),
                         target['text'])

    def test_local_staged_analysis_emits_the_same_structure_shape(self):
        target = source('p1', '— Привет, — сказал он.')

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator']}
            if 'units' in request:
                return {'contexts': [{'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
                                      'confidence': 'high', 'evidence': []}
                                     for _ in request['units']]}
            return {'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': ''}

        _, fixed = staged_proposal_with_details(
            [target], [], [], [], infer, include_context=False)
        self.assertEqual([set(chunk) for chunk in fixed['p1']], [
            {'index', 'start', 'end', 'text', 'mode'},
            {'index', 'start', 'end', 'text', 'mode'},
        ])
        self.assertEqual([chunk['mode'] for chunk in fixed['p1']],
                         ['speech', 'narrator'])

    def test_local_staged_analysis_resolves_adjacent_turns_independently(self):
        target = source('p1', '— Кто там? — Я.')
        speaker_calls = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'speech']}
            speaker_calls.append(request['speech'])
            return {'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': ''}

        answer, _ = staged_proposal_with_details(
            [target], [], [], [], infer, include_context=False)
        self.assertEqual(speaker_calls, ['— Кто там? ', '— Я.'])
        self.assertEqual([(part['start'], part['end']) for part in
                          answer['passages'][0]['parts']],
                         [(0, target['text'].index('—', 1)),
                          (target['text'].index('—', 1), len(target['text']))])

    def test_full_analysis_cannot_merge_adjacent_speech_turns(self):
        text = '— Кто там? — Я.'
        boundary = text.index('—', 1)
        fixed = {'p1': [
            {'index': 0, 'start': 0, 'end': boundary,
             'text': text[:boundary], 'mode': 'speech'},
            {'index': 1, 'start': boundary, 'end': len(text),
             'text': text[boundary:], 'mode': 'speech'},
        ]}
        sources = {'p1': source('p1', text)}
        self.assertEqual(unit_range(text, 0, boundary), (0, 0))
        self.assertEqual(unit_range(text, boundary, len(text)), (1, 1))
        merged = {'passages': [{'id': 'p1', 'parts': [
            {'first': 0, 'last': 1, 'speaker': 'one'},
        ]}]}
        with self.assertRaisesRegex(AppError, 'speech turn starting at .* is separate'):
            structure_matches_answer(merged, fixed, sources)
        split = {'passages': [{'id': 'p1', 'parts': [
            {'first': 0, 'last': 0, 'speaker': 'one'},
            {'first': 1, 'last': 1, 'speaker': 'two'},
        ]}]}
        structure_matches_answer(split, fixed, sources)


class DeterministicSpeakerTierTests(unittest.TestCase):
    def setUp(self):
        self.cast = [
            {'id': 'ivan', 'name': 'Иван', 'aliases': ['Ваня'],
             'description': ''},
            {'id': 'petr', 'name': 'Пётр', 'aliases': [],
             'description': ''},
            {'id': 'uline', 'name': 'Улине', 'aliases': [],
             'description': ''},
        ]

    def test_only_named_same_paragraph_attribution_is_authoritative(self):
        self.assertEqual(named_attribution_speaker('— сказал Иван.', self.cast),
                         'ivan')
        self.assertEqual(named_attribution_speaker('— ответил Ваня.', self.cast),
                         'ivan')
        self.assertEqual(named_attribution_speaker('— тихо сказал Иван.', self.cast),
                         'ivan')
        self.assertEqual(named_attribution_speaker('— сказал очень тихо Иван.', self.cast),
                         'ivan')
        self.assertIsNone(named_attribution_speaker('— сказал он.', self.cast))
        self.assertIsNone(named_attribution_speaker('Иван сказал:', self.cast))
        self.assertIsNone(named_attribution_speaker('— Иван подошёл.', self.cast))
        self.assertIsNone(named_attribution_speaker(
            '— сказал Иван Улине.', self.cast))

    def test_pronoun_aliases_never_become_names_or_attributions(self):
        from rimsky.analysis import alias_labels
        polluted = [{**self.cast[0], 'aliases': ['он', 'я', 'Ваня']}]
        self.assertEqual(['Иван'], alias_labels(['он', 'я', 'и', 'это', 'Иван']))
        for attribution in ('— сказал он.', '— ответил он.', '— спросил он.'):
            self.assertIsNone(named_attribution_speaker(attribution, polluted))

    def test_reviewed_function_word_name_is_not_treated_as_a_pronoun(self):
        unusual = [{'id': 'she', 'name': 'Она', 'aliases': [], 'description': ''}]
        self.assertEqual('she', named_attribution_speaker('— сказала Она.', unusual))
        self.assertIsNone(named_attribution_speaker('— сказала она.', unusual))

    def test_speaker_evidence_schema_bounds_the_quote(self):
        schema = speaker_schema(['ivan'], ['p1'])
        self.assertEqual(schema['properties']['evidence_quote']['maxLength'], 200)

    def test_named_attribution_rejects_addressee_and_later_clause_names(self):
        rejected = [
            '— шепнул я Улине.',
            '— сказал он, глядя на Улине.',
            '— ответил я Улине.',
            '— сказал я, и Иван кивнул.',
            '— удивился я, на что Улине с усмешкой ответила:',
            '— поправил я своего друга, на что Иван ответил…',
        ]
        for attribution in rejected:
            with self.subTest(attribution=attribution):
                self.assertIsNone(named_attribution_speaker(attribution, self.cast))

    def test_rejected_attribution_names_remain_model_hints(self):
        self.assertEqual(attribution_name_hints(
            '— сказал он, глядя на Улине.', self.cast), ['uline'])
        self.assertEqual(attribution_name_hints(
            '— сказал я, и Иван кивнул.', self.cast), ['ivan'])

    def test_named_attribution_requires_only_adverbs_around_first_verb(self):
        self.assertIsNone(named_attribution_speaker(
            '— улыбнувшись сказал Иван.', self.cast))
        self.assertIsNone(named_attribution_speaker(
            '— очень тихо спокойно сказал Иван.', self.cast))

    def test_comma_insertion_resumes_but_finished_attribution_does_not(self):
        def case(middle: str, ending: str):
            text = '— Начало, ' + middle + ending
            first_end = len('— Начало, ')
            middle_end = first_end + len(middle)
            return (resumes_after_insertion(
                {'start': 0, 'end': first_end, 'mode': 'speech'},
                {'start': first_end, 'end': middle_end, 'mode': 'narrator'},
                {'start': middle_end, 'end': len(text), 'mode': 'speech'}, text))
        self.assertTrue(case('— сказал он, ', '— да.'))
        self.assertFalse(case('— сказал он. ', '— Нет.'))

    def test_finished_remark_does_not_resume_after_colon_insertion(self):
        def case(remark: str, middle: str, ending: str):
            text = remark + middle + ending
            return resumes_after_insertion(
                {'start': 0, 'end': len(remark), 'mode': 'speech'},
                {'start': len(remark), 'end': len(remark) + len(middle), 'mode': 'narrator'},
                {'start': len(remark) + len(middle), 'end': len(text), 'mode': 'speech'}, text)
        self.assertFalse(case('— Я готов. ', '— Маша спросила: ', '— Ты идёшь?'))
        self.assertFalse(case('— Стой! ', '— крикнул он, ', '— стой!'))
        self.assertTrue(case('— Я… ', '— замялся он, ', '— не знаю.'))
        self.assertTrue(case('— Да, ', '— сказал он и добавил: ', '— пойдём.'))

    def test_staged_analysis_lets_a_new_speaker_answer_after_a_finished_remark(self):
        target = source('p1', '— Я готов. — Она спросила: — Ты идёшь?')
        speakers = {'— Я готов. ': 'ivan', '— Ты идёшь?': 'petr'}

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator', 'speech']}
            return {'speaker': speakers[request['speech']], 'name': '',
                    'evidence_id': 'p1', 'evidence_quote': request['speech'].strip('— ')}

        def tie_infer(system, request, schema, limit):
            raise AssertionError(f'unexpected escalation: {request["escalation_reasons"]}')

        resolutions = []
        answer, _ = staged_proposal_with_details(
            [target], self.cast, [], [], infer, include_context=False,
            tie_breaker_infer=tie_infer, resolution_log=resolutions)
        speech = [part for part in answer['passages'][0]['parts']
                  if part['speaker'] != 'narrator']
        self.assertEqual([part['speaker'] for part in speech], ['ivan', 'petr'])
        self.assertFalse(any('resumed_speech_disagreement' in item.get('triggers', [])
                             for item in resolutions))

    def test_staged_analysis_keeps_one_performer_after_exclamation_attribution(self):
        target = source('p1', '— Стой! — крикнул Иван, — стой!')

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator', 'speech']}
            return {'speaker': 'ivan', 'name': '', 'evidence_id': 'p1',
                    'evidence_quote': 'крикнул Иван'}

        def tie_infer(system, request, schema, limit):
            raise AssertionError(f'unexpected escalation: {request["escalation_reasons"]}')

        resolutions = []
        answer, _ = staged_proposal_with_details(
            [target], self.cast, [], [], infer, include_context=False,
            tie_breaker_infer=tie_infer, resolution_log=resolutions)
        speech = [part for part in answer['passages'][0]['parts']
                  if part['speaker'] != 'narrator']
        self.assertEqual([part['speaker'] for part in speech], ['ivan', 'ivan'])
        self.assertEqual([item['tier'] for item in resolutions][0], 'explicit_attribution')

    def test_staged_analysis_uses_named_attribution_before_model(self):
        target = source('p1', '— Привет, — сказал Иван, — пока.')
        speaker_calls = []
        tie_calls = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator', 'speech']}
            speaker_calls.append(request)
            return {'speaker': 'petr', 'name': '', 'evidence_id': 'p1',
                    'evidence_quote': 'Привет'}

        def tie_infer(system, request, schema, limit):
            tie_calls.append(request)
            return {'speaker': 'ivan', 'name': '', 'evidence_id': 'p1',
                    'evidence_quote': 'сказал Иван'}

        resolutions = []
        answer, _ = staged_proposal_with_details(
            [target], self.cast, [], [], infer, include_context=False,
            tie_breaker_infer=tie_infer, resolution_log=resolutions)
        speech = [part for part in answer['passages'][0]['parts']
                  if part['speaker'] != 'narrator']
        self.assertEqual([part['speaker'] for part in speech], ['ivan', 'ivan'])
        self.assertEqual(len(speaker_calls), 1)
        self.assertEqual(tie_calls[0]['escalation_reasons'],
                         ['resumed_speech_disagreement'])
        self.assertEqual([item['tier'] for item in resolutions],
                         ['explicit_attribution', 'tie_breaker'])

    def test_negated_or_hearsay_attribution_is_never_deterministic(self):
        for attribution in ('— сказал не Иван.', '— не сказал Иван.', '— вовсе не сказал Иван.', '— якобы сказал Иван.', '— будто бы сказал Иван.'):
            with self.subTest(attribution=attribution):
                self.assertIsNone(named_attribution_speaker(attribution, self.cast))
                self.assertEqual(['ivan'], attribution_name_hints(attribution, self.cast))
        self.assertEqual('ivan', named_attribution_speaker('— тихо сказал Иван.', self.cast))

    def test_staged_analysis_sends_negated_attribution_to_the_model_path(self):
        target = source('p1', '— Привет, — сказал не Иван.')
        speaker_calls = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator']}
            speaker_calls.append(request)
            return {'speaker': 'unknown', 'name': '', 'evidence_id': 'p1', 'evidence_quote': 'сказал не Иван'}

        resolutions = []
        answer, _ = staged_proposal_with_details([target], self.cast, [], [], infer, include_context=False, resolution_log=resolutions)
        self.assertEqual(1, len(speaker_calls))
        self.assertEqual(['ivan'], speaker_calls[0]['attribution_name_hints'])
        self.assertNotIn('explicit_attribution', [item['tier'] for item in resolutions])
        self.assertNotIn('ivan', [part['speaker'] for part in answer['passages'][0]['parts']])

    def test_rejected_name_hint_disagreement_escalates(self):
        target = source('p1', '— Привет, — шепнул я Улине.')
        speaker_calls = []
        tie_calls = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator']}
            speaker_calls.append(request)
            return {'speaker': 'ivan', 'name': '', 'evidence_id': 'p1',
                    'evidence_quote': 'шепнул я'}

        def tie_infer(system, request, schema, limit):
            tie_calls.append(request)
            return {'speaker': 'ivan', 'name': '', 'evidence_id': 'p1',
                    'evidence_quote': 'шепнул я'}

        resolutions = []
        staged_proposal_with_details(
            [target], self.cast, [], [], infer, include_context=False,
            tie_breaker_infer=tie_infer, resolution_log=resolutions)
        self.assertEqual(speaker_calls[0]['attribution_name_hints'], ['uline'])
        self.assertEqual(tie_calls[0]['escalation_reasons'],
                         ['named_attribution_disagreement'])
        self.assertEqual(resolutions[0]['tier'], 'tie_breaker')
        self.assertEqual(resolutions[0]['attribution_name_hints'], ['uline'])

    def test_metrics_keep_unattributed_exchange_trigger_separate(self):
        metrics = resolution_metrics([
            {'tier': 'local', 'triggers': [], 'status': 'resolved',
             'speaker': 'a', 'primary_speaker': 'a'},
            {'tier': 'tie_breaker', 'triggers': ['unattributed_exchange'],
             'status': 'resolved', 'speaker': 'b', 'primary_speaker': 'a'},
            {'tier': 'local', 'triggers': ['primary_unknown'], 'status': 'review',
             'speaker': 'unknown', 'primary_speaker': 'unknown'},
        ])
        self.assertEqual(metrics, {
            'speech_spans': 3, 'deterministic': 0, 'escalated': 2,
            'tie_breaker_used': 1, 'review': 1,
            'unattributed_exchange_escalations': 1,
            'unattributed_exchange_changed_answer': 1,
        })

    def test_default_hold_includes_only_flagged_segments_and_direct_neighbours(self):
        segments = [
            {'id': str(index), 'chapter_id': 'c1', 'source_id': 'p', 'start': index * 10,
             'end': (index + 1) * 10}
            for index in range(5)]
        records = [{'source_id': 'p', 'start': 21, 'end': 25,
                    'status': 'review'}]
        flagged, held = speaker_review_holds(segments, records)
        self.assertEqual(flagged, {'2'})
        self.assertEqual(held, {'1', '2', '3'})

    def test_default_hold_does_not_cross_chapter_boundaries(self):
        segments = [
            {'id': 'previous', 'chapter_id': 'c1', 'source_id': 'p1',
             'start': 0, 'end': 10},
            {'id': 'flagged', 'chapter_id': 'c2', 'source_id': 'p2',
             'start': 0, 'end': 10},
            {'id': 'next', 'chapter_id': 'c2', 'source_id': 'p3',
             'start': 0, 'end': 10},
        ]
        records = [{'source_id': 'p2', 'start': 1, 'end': 5,
                    'status': 'review'}]
        flagged, held = speaker_review_holds(segments, records)
        self.assertEqual(flagged, {'flagged'})
        self.assertEqual(held, {'flagged', 'next'})


if __name__ == '__main__':
    unittest.main()
