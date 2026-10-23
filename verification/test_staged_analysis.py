from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))
from rimsky.analysis import review_analysis, source_target, synthesis_units
from rimsky.staged_analysis import (checked_infer, checked_modes, checked_speaker,
                                    dash_chunks,
                                    leading_addressee, leadin_speaker,
                                    staged_proposal, staged_proposal_with_details,
                                    unit_answer)
from rimsky.util import AppError
import worker


_original_staged_proposal = staged_proposal

def staged_proposal(*args, **kwargs):
    kwargs.setdefault('include_context', False)
    return _original_staged_proposal(*args, **kwargs)


UNVERIFIED = {'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
              'confidence': 'low', 'evidence': []}


def validate_analysis(proposal, targets, cast, context=(), fixed=None):
    answer = proposal if 'first' in proposal['passages'][0]['parts'][0] else unit_answer(
        {**proposal, 'passages': [{**passage, 'parts': [{'context': dict(UNVERIFIED), **part} for part in passage['parts']]}
                                  for passage in proposal['passages']]}, targets)
    parts, updated, invalid = review_analysis(answer, targets, cast, context, fixed=fixed, kind_evidence=False)
    if invalid:
        raise AppError(next(iter(invalid.values())))
    return parts, updated


class StagedAnalysisTests(unittest.TestCase):
    def source(self, start, text):
        return {'id': f'p-{start}-{start + len(text)}', 'source_id': 'p',
                'start': start, 'end': start + len(text), 'text': text,
                'ordinal': start, 'chapter_id': 'c'}

    def test_ascii_hyphen_dialogue_tag_is_a_candidate_boundary(self):
        text = ('— И ещё Модификатор удачи +2, - добавил я, и начальник '
                'удовлетворённо кивнул — теперь сумма у него сошлась.')
        chunks = dash_chunks(text)
        self.assertEqual([chunk['text'] for chunk in chunks], [
            '— И ещё Модификатор удачи +2, ',
            '- добавил я, и начальник удовлетворённо кивнул ',
            '— теперь сумма у него сошлась.',
        ])
        self.assertEqual(''.join(chunk['text'] for chunk in chunks), text)
        self.assertEqual([chunk['start'] for chunk in chunks], [0, 30, 77])

    def test_ascii_hyphen_self_introduction_tag_is_a_candidate_boundary(self):
        text = ('— Комар, человек, фракция Н3, - представился я, но такой ответ '
                'систему не устроил, и столбик с панелью снова ушёл под землю.')
        chunks = dash_chunks(text)
        self.assertEqual([chunk['text'] for chunk in chunks], [
            '— Комар, человек, фракция Н3, ',
            '- представился я, но такой ответ систему не устроил, и столбик '
            'с панелью снова ушёл под землю.',
        ])
        self.assertEqual([chunk['start'] for chunk in chunks], [0, 30])

    def test_split_source_carries_speech_and_reuses_identity(self):
        first = self.source(0, '— Привет? — спросил Иван. — Как дела? ')
        second = self.source(first['end'], 'Снова привет.')
        calls = []

        def infer(system, request, schema, limit):
            calls.append(request)
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator', 'speech']}
            if not any(c['id'] == 'new_1' for c in request['characters']):
                return {'speaker': 'new', 'name': 'Иван',
                        'evidence_id': first['id'], 'evidence_quote': 'спросил Иван'}
            return {'speaker': 'new_1', 'name': '',
                    'evidence_id': request['target_id'],
                    'evidence_quote': request['speech'].strip()[-6:]}

        proposal = staged_proposal([first, second], [], [], [], infer)
        parts, cast = validate_analysis(proposal, [first, second], [])
        self.assertEqual(''.join(p['text'] for p in parts),
                         first['text'] + second['text'])
        self.assertEqual([p['speaker'] for p in parts],
                         [cast[0]['id'], 'narrator', cast[0]['id'], cast[0]['id']])
        self.assertEqual(parts[-1]['text'], second['text'])
        self.assertEqual(sum('chunks' in call for call in calls), 1)

    def test_split_source_recovers_prior_mode_after_batch_boundary(self):
        first = self.source(0, '— Привет? — спросил Иван. — Как дела? ')
        second = self.source(first['end'], 'Снова привет.')

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                self.assertEqual([x['text'] for x in request['chunks']],
                                 ['— Привет? ', '— спросил Иван. ', '— Как дела? '])
                return {'modes': ['speech', 'narrator', 'speech']}
            return {'speaker': 'unknown', 'name': '',
                    'evidence_id': '', 'evidence_quote': ''}

        proposal = staged_proposal([second], [], [source_target(first)], [], infer)
        parts, _ = validate_analysis(proposal, [second], [], [first])
        self.assertEqual(parts[0]['speaker'], 'unknown')
        self.assertEqual(parts[0]['text'], second['text'])

    def test_invalid_stage_response_is_repaired_with_error_context(self):
        requests = []

        def infer(system, request, schema, limit):
            requests.append(request)
            return {'modes': []} if len(requests) == 1 else {'modes': ['speech']}

        result = checked_infer(infer, 'mode', {'chunks': ['text']}, {}, 10,
                               lambda value: checked_modes(value, [{}]))
        self.assertEqual(result, ['speech'])
        self.assertEqual(requests[1]['repair']['rejected_response'], {'modes': []})
        self.assertIn('cover every chunk', requests[1]['repair']['validation_error'])

    def test_speaker_evidence_and_addressee_are_checked(self):
        self.assertEqual(leading_addressee('— Дмитрий, приготовься!'), 'Дмитрий')
        self.assertEqual(leading_addressee(
            '— Иван Лозовский, заместитель руководителя, ',
            '— представился он и попросил выключить свет.'), '')
        self.assertEqual(leading_addressee(
            '— Комар, человек, фракция Н3, ',
            '- представился я, но такой ответ систему не устроил.'), '')
        registry = [{'id': 'char_d', 'name': 'Дмитрий', 'aliases': ['Желтов']}]
        evidence = {'p': '— Дмитрий, приготовься! — приказал Кирилл.'}
        with self.assertRaisesRegex(AppError, 'person addressed'):
            checked_speaker({'speaker': 'char_d', 'name': '', 'evidence_id': 'p',
                             'evidence_quote': 'приказал Кирилл'}, registry,
                            evidence, 'Дмитрий')
        with self.assertRaisesRegex(AppError, 'contiguous quote'):
            checked_speaker({'speaker': 'new', 'name': 'Кирилл', 'evidence_id': 'p',
                             'evidence_quote': 'invented'}, registry,
                            evidence, 'Дмитрий')
        self.assertEqual(
            checked_speaker({'speaker': 'new', 'name': 'Кирилл',
                             'evidence_id': 'p',
                             'evidence_quote': '**приказал Кирилл**...'},
                            registry, evidence, 'Дмитрий'),
            ('new', 'Кирилл', 'p'))

    def test_leadin_requires_explicit_subject_and_does_not_confuse_prior_speech(self):
        cast = [
            {'id': 'kirill', 'name': 'Кирилл', 'aliases': [],
             'description': 'First-person protagonist.'},
            {'id': 'svetlana', 'name': 'Светлана', 'aliases': [],
             'description': 'Instructor.'},
        ]
        def cue(text):
            return leadin_speaker([{'id': 'before', 'text': text}], cast)
        self.assertEqual(cue('Я немного подумал и ответил:'), ('kirill', 'before'))
        self.assertEqual(cue('Светлана подвела итог:'), ('svetlana', 'before'))
        self.assertEqual(cue('Разведчик протянул планшет:'), ('unmapped', 'before'))
        self.assertIsNone(cue('Он ответил:'))
        self.assertIsNone(cue('Она спокойно сказала:'))
        self.assertIsNone(cue('Я возразил, но командир лишь гаркнул в ответ:'))
        self.assertIsNone(cue('Я показал Светлане, и она подвела итог:'))
        self.assertIsNone(cue('Светлана ждала у двери:'))

    def test_en_dash_quotes_and_interrupted_words_are_structural(self):
        text = 'Он сказал: «Да». Потом добавил – спокойно.'
        chunks = dash_chunks(text)
        self.assertEqual(text, ''.join(item['text'] for item in chunks))
        self.assertEqual(['Он сказал: ', '«Да». ', 'Потом добавил ', '– спокойно.'],
                         [item['text'] for item in chunks])
        interrupted = '— Я не хоте— — начал он.'
        pieces = [item['text'] for item in dash_chunks(interrupted)]
        self.assertEqual(['— Я не хоте— ', '— начал он.'], pieces)

    def test_previous_paragraph_leadin_does_not_restrict_the_cast(self):
        source = self.source(0, '— Держи маршрут!')
        before = [{'id': 'before', 'text': 'Разведчик протянул водителю планшет:'}]
        cast = [{'id': 'ivan', 'name': 'Иван', 'aliases': [],
                 'description': 'Diplomat.'}]
        speaker_requests = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech']}
            speaker_requests.append(request)
            if len(speaker_requests) == 1:
                return {'speaker': 'ivan', 'name': '', 'evidence_id': 'before',
                        'evidence_quote': 'Разведчик протянул'}
            return {'speaker': 'new', 'name': 'Разведчик Первого Легиона',
                    'evidence_id': 'before', 'evidence_quote': 'Разведчик протянул'}

        resolutions = []
        proposal, _ = staged_proposal_with_details(
            [source], cast, before, [], infer, dialogue_style='russian_dash',
            include_context=False, resolution_log=resolutions)
        parts, updated = validate_analysis(proposal, [source], cast, before)
        self.assertEqual(speaker_requests[0]['narrative_leadin'], 'unmapped')
        self.assertEqual([entry['id'] for entry in speaker_requests[0]['characters']], ['ivan'])
        self.assertEqual(parts[0]['speaker'], 'unknown')
        self.assertEqual([(item['speaker'], item['status'], item['triggers'], item['proposed_evidence_id'])
                          for item in resolutions],
                         [('ivan', 'review', ['neighbor_only_evidence'], 'before')])
        self.assertEqual(updated, cast)
        self.assertEqual(len(speaker_requests), 1)

    def test_same_paragraph_attribution_overrides_prior_paragraph_leadin(self):
        source = self.source(0, '— Привет. — сказал Иван. — Привет и тебе.')
        before = [{'id': 'before', 'text': 'Светлана сказала:'}]
        cast = [{'id': 'svetlana', 'name': 'Светлана', 'aliases': [],
                 'description': 'Instructor.'},
                {'id': 'ivan', 'name': 'Иван', 'aliases': [],
                 'description': 'Diplomat.'}]
        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator', 'speech']}
            return {'speaker': 'ivan', 'name': '', 'evidence_id': source['id'],
                    'evidence_quote': 'сказал Иван'}
        proposal = staged_proposal([source], cast, before, [], infer)
        parts, _ = validate_analysis(proposal, [source], cast, before)
        self.assertEqual([part['speaker'] for part in parts], ['ivan', 'narrator', 'ivan'])
        self.assertEqual(''.join(part['text'] for part in parts), source['text'])

    def test_cast_survives_separate_analysis_batches(self):
        first = self.source(0, '— Привет! — сказал Иван.')
        second = {'id': 'q-0-15', 'source_id': 'q', 'start': 0, 'end': 15,
                  'text': '— Снова привет.', 'ordinal': 1, 'chapter_id': 'c'}

        def first_infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator']}
            return {'speaker': 'new', 'name': 'Иван',
                    'evidence_id': first['id'], 'evidence_quote': 'сказал Иван'}

        proposal = staged_proposal([first], [], [], [], first_infer)
        _, cast = validate_analysis(proposal, [first], [])

        def second_infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech']}
            self.assertEqual(request['characters'][0]['id'], cast[0]['id'])
            return {'speaker': cast[0]['id'], 'name': '',
                    'evidence_id': second['id'], 'evidence_quote': 'Снова привет'}

        proposal = staged_proposal([second], cast, [], [], second_infer)
        parts, updated = validate_analysis(proposal, [second], cast)
        self.assertEqual(parts[0]['speaker'], cast[0]['id'])
        self.assertEqual(len(updated), 1)

    def test_dash_style_keeps_plain_prose_as_narration(self):
        source = self.source(0, 'Я взглянул на карту и увидел лагерь.')

        def infer(system, request, schema, limit):
            self.fail('Plain prose should not require a model call in dash style.')

        proposal = staged_proposal([source], [], [], [], infer,
                                   dialogue_style='russian_dash')
        parts, cast = validate_analysis(proposal, [source], [])
        self.assertEqual(parts[0]['speaker'], 'narrator')
        self.assertEqual(cast, [])

    def test_dash_style_classifies_internal_speech_to_narrator_transition(self):
        source = self.source(0, ('Если окажется неинтересно, проверим камень, — '
                                 'я убрал фильтр и указал на астероиды.'))
        cast = [{'id': 'komar', 'name': 'Комар', 'aliases': [],
                 'description': 'First-person protagonist.'}]
        calls = []

        def infer(system, request, schema, limit):
            calls.append((system, request))
            if 'chunks' in request:
                self.assertIn('every em dash', system)
                self.assertEqual(len(request['chunks']), 2)
                return {'modes': ['speech', 'narrator']}
            return {'speaker': 'komar', 'name': '',
                    'evidence_id': source['id'],
                    'evidence_quote': 'Если окажется неинтересно'}

        proposal = staged_proposal([source], cast, [], [], infer,
                                   dialogue_style='russian_dash')
        raw = proposal['passages'][0]['parts']
        self.assertEqual([part['speaker'] for part in raw], ['komar', 'narrator'])
        self.assertEqual(''.join(source['text'][p['start']:p['end']] for p in raw),
                         source['text'])
        self.assertTrue(any('chunks' in request for _, request in calls))

    def test_explanatory_dash_is_classified_even_when_both_sides_are_narration(self):
        source = self.source(0, 'Я понял одно — пора уходить.')

        def infer(system, request, schema, limit):
            self.assertEqual(len(request['chunks']), 2)
            return {'modes': ['narrator', 'narrator']}

        proposal = staged_proposal([source], [], [], [], infer,
                                   dialogue_style='russian_dash')
        self.assertEqual(proposal['passages'][0]['parts'][0]['speaker'], 'narrator')

    def test_enumeration_stage_marks_list_without_leadin(self):
        source = self.source(0, 'Случались разные варианты. И сигнал из космоса. И находка корабля. Война.')
        calls = []

        def infer(system, request, schema, limit):
            calls.append(request)
            if 'sentences' in request:
                return {'enumerations': [True, True, True, True]}
            self.fail('Dash-style narration needs only the enumeration stage.')

        proposal = staged_proposal([source], [], [], [], infer,
                                   dialogue_style='russian_dash')
        parts, _ = validate_analysis(proposal, [source], [])
        self.assertEqual([part['delivery'] for part in parts],
                         ['neutral', 'enumeration', 'enumeration', 'enumeration'])
        self.assertEqual(''.join(part['text'] for part in parts), source['text'])
        units = synthesis_units(parts, [source])
        self.assertEqual([unit['delivery'] for unit in units], ['neutral', 'enumeration'])
        self.assertEqual(''.join(unit['text'] for unit in units), source['text'])
        self.assertIn('enumeration', units[1]['performance']['cues'])
        self.assertEqual(len(calls), 1)

    def test_enumeration_can_start_before_repeated_and(self):
        source = self.source(0, 'Падение метеора. И крушение корабля. И война.')

        def infer(system, request, schema, limit):
            return {'enumerations': [True, True, True]}

        proposal = staged_proposal([source], [], [], [], infer,
                                   dialogue_style='russian_dash')
        parts, _ = validate_analysis(proposal, [source], [])
        self.assertEqual([part['delivery'] for part in parts],
                         ['enumeration'] * 3)
        self.assertEqual([unit['delivery'] for unit in synthesis_units(parts, [source])],
                         ['enumeration'])

    def test_explicit_joyful_speech_tag_sets_excited_only_on_quote(self):
        source = self.source(0, '— Как я его! Вы видели?! — восторженно прокричал я.')
        cast = [{'id': 'kirill', 'name': 'Кирилл', 'aliases': [],
                 'description': 'First-person protagonist.'}]

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech', 'narrator']}
            return {'speaker': 'kirill', 'name': '', 'evidence_id': source['id'],
                    'evidence_quote': 'Как я его'}

        proposal = staged_proposal([source], cast, [], [], infer)
        parts, _ = validate_analysis(proposal, [source], cast)
        self.assertEqual([(part['speaker'], part['delivery']) for part in parts],
                         [('kirill', 'excited'), ('narrator', 'neutral')])

    def test_reviewed_cast_description_reaches_speaker_stage(self):
        source = self.source(0, '— В бой!')
        seed = [{'id': 'rb1_tamara', 'name': 'герд Тамара',
                 'aliases': ['Тамара'],
                 'description': 'Commander of the Second Legion.',
                 'evidence': ['earlier-paragraph']}]

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech']}
            self.assertEqual(request['characters'][0]['description'],
                             'Commander of the Second Legion.')
            return {'speaker': 'rb1_tamara', 'name': '',
                    'evidence_id': source['id'], 'evidence_quote': 'В бой'}

        proposal = staged_proposal([source], seed, [], [], infer)
        parts, cast = validate_analysis(proposal, [source], seed)
        self.assertEqual(parts[0]['speaker'], 'rb1_tamara')
        self.assertEqual(len(cast), 1)

    def test_worker_staged_pack_uses_existing_analysis_contract(self):
        source = self.source(0, '— Привет! — сказал Иван.')

        class FakeModel:
            def tokenize(self, content):
                return list(content)

            def create_chat_completion(self, **kwargs):
                request = kwargs['messages'][1]['content']
                if '"units"' in request:
                    reply = json.dumps({'contexts': [{'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
                        'confidence': 'high', 'evidence': []} for unit in json.loads(request)['units']]})
                elif '"chunks"' in request:
                    reply = '{"modes":["speech","narrator"]}'
                else:
                    reply = ('{"speaker":"new","name":"Иван",'
                             '"evidence_id":"p-0-24",'
                             '"evidence_quote":"сказал Иван"}')
                return {'choices': [{'finish_reason': 'stop',
                                     'message': {'content': reply}}]}

        payload = {'pack': {'text_model': {'analysis_mode': 'staged',
                                          'context': 8192}},
                   'device': 'cpu', 'segments': [source], 'cast': [],
                   'before': [], 'after': []}
        with patch.object(worker, 'load_text', return_value=FakeModel()):
            result = worker.analyze(payload)
        self.assertTrue(result['staged'])
        self.assertEqual(result['known'], [])
        parts, cast = validate_analysis(result['reply'], [source], [], fixed=result['speech_structure'])
        self.assertEqual(len(cast), 1)
        self.assertEqual([part['speaker'] for part in parts], [cast[0]['id'], 'narrator'])
        self.assertEqual([(item['tier'], item['status']) for item in result['speaker_resolution']],
                         [('local', 'review')])
        self.assertIn('local_fallback_review', result['speaker_resolution'][0]['triggers'])
        self.assertEqual([chunk['mode'] for chunk in
                          result['speech_structure'][source['id']]],
                         ['speech', 'narrator'])

    def test_worker_pack_without_mode_defaults_to_staged_analysis(self):
        source = self.source(0, '— Привет! — сказал Иван.')

        class FakeModel:
            def tokenize(self, content):
                return list(content)

            def create_chat_completion(self, **kwargs):
                request = kwargs['messages'][1]['content']
                if '"units"' in request:
                    units = json.loads(request)['units']
                    reply = json.dumps({'contexts': [{'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
                        'confidence': 'high', 'evidence': []} for _ in units]})
                elif '"chunks"' in request:
                    reply = '{"modes":["speech","narrator"]}'
                else:
                    reply = json.dumps({'speaker': 'new', 'name': 'Иван',
                        'evidence_id': 'p-0-24', 'evidence_quote': 'сказал Иван'},
                        ensure_ascii=False)
                return {'choices': [{'finish_reason': 'stop',
                                     'message': {'content': reply}}]}

        payload = {'pack': {'text_model': {'context': 8192}},
                   'device': 'cpu', 'segments': [source], 'cast': [],
                   'before': [], 'after': []}
        with patch.object(worker, 'load_text', return_value=FakeModel()):
            result = worker.analyze(payload)
        self.assertTrue(result['staged'])
        parts, cast = validate_analysis(result['reply'], [source], [], fixed=result['speech_structure'])
        self.assertEqual(len(cast), 1)
        self.assertEqual(parts[0]['speaker'], cast[0]['id'])
        self.assertIn(source['id'], result['speech_structure'])


if __name__ == '__main__':
    unittest.main()
