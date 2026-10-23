from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import no_structure, FB2, NEUTRAL_CONTEXT, SyntheticEngine, narration, pack_fixture
import worker
from rimsky.analysis import (DEGRADED_HOLD, DEGRADED_WARNING, PROMPT_MARGIN, analysis_schema, analysis_target, batches, boundary_units, clip,
                             context_target, merge_character, merge_description, output_estimate, output_limit, registry, relevant_characters,
                             review_analysis, target_tokens, validate_analysis)
from rimsky.casting import CASTING_SCHEMA, KINDS
from rimsky.jobs import Engine, Jobs, base_segments, canonical_name, name_tokens, placeholder_segments
from rimsky.preparation import default_chapters
from rimsky.store import Store
from rimsky.util import AnalysisCapacityError, AppError, EngineTransportError, digest

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
TIMEOUT = 'Local inference exceeded its 1800-second operation limit.'


def source(sid, text, start=0, ordinal=0):
    return {'id': f'{sid}-{start}-{start + len(text)}', 'source_id': sid, 'start': start, 'end': start + len(text), 'text': text,
            'ordinal': ordinal, 'chapter_id': 'c1'}


def part(first, last, speaker='narrator', delivery='neutral', *evidence):
    return {'first': first, 'last': last, 'speaker': speaker, 'delivery': delivery, 'evidence': list(evidence), 'context': dict(NEUTRAL_CONTEXT)}


def character(cid, name, *aliases, evidence=(), **fields):
    return {'id': cid, 'name': name, 'aliases': list(aliases), 'description': '', 'evidence': list(evidence), 'kind': 'person',
            'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}, **fields}


def stems(text):
    return canonical_name(name_tokens(text))


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class Units(unittest.TestCase):
    def test_dialogue_dash_sentence_and_quote_boundaries(self):
        cases = {'Маша открыла книгу. — Читай, — сказал Иван.': ['Маша открыла книгу. ', '— Читай, ', '— сказал Иван.'],
                 '— Да, — сказал Иван. — Пойдём.': ['— Да, ', '— сказал Иван. ', '— Пойдём.'],
                 'Он сказал: «Пойдём». Потом ушёл.': ['Он сказал: ', '«Пойдём». ', 'Потом ушёл.'],
                 '«Пойдём!» — сказал он.': ['«Пойдём!» ', '— сказал он.'],
                 'He said “Stop!” and left.': ['He said ', '“Stop!” ', 'and left.'],
                 'Она посмотрела на формулу: F = ma.': ['Она посмотрела на формулу: F = ma.'],
                 '— «Да» ': ['— «Да» '], '* * *': ['* * *']}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, [text[start:end] for start, end in boundary_units(text)])

    def test_boundary_candidates_are_never_joined_to_fit_a_limit(self):
        text = 'Да. ' * 60 + '— Нет, — сказал он.'
        units = [text[start:end] for start, end in boundary_units(text)]
        self.assertEqual(62, len(units))
        self.assertEqual(['— Нет, ', '— сказал он.'], units[-2:])
        self.assertEqual(text, ''.join(units))

    def test_units_tile_random_text_exactly(self):
        generator = random.Random(1004)
        alphabet = 'аб Да! — – , . … ? « » “ ” „ \ń x1'
        for trial in range(500):
            text = ''.join(generator.choice(alphabet.split(' ') + [' ']) for _ in range(generator.randint(1, 160)))
            units = boundary_units(text)
            with self.subTest(text=text):
                self.assertEqual(text, ''.join(text[start:end] for start, end in units))
                self.assertTrue(all(start < end for start, end in units))
                self.assertTrue(all(left[1] == right[0] for left, right in zip(units, units[1:])))
                if len(units) > 1:
                    self.assertTrue(all(any(char.isalnum() for char in text[start:end]) for start, end in units))

    def test_targets_send_numbered_units_and_context_sends_speaker_snippets(self):
        first = source('p', '— Да, — сказал Иван.')
        self.assertEqual({'id': first['id'], 'units': [{'n': 0, 'text': '— Да, '}, {'n': 1, 'text': '— сказал Иван.'}]}, analysis_target(first))
        accepted = {first['id']: [{'start': 0, 'end': 6, 'speaker': 'ivan'}, {'start': 6, 'end': 20, 'speaker': 'narrator'}]}
        self.assertEqual({'id': first['id'], 'text': first['text'], 'source_id': first['source_id'], 'accepted_speakers': [{'speaker': 'ivan', 'text': '— Да, '}]},
                         context_target(first, accepted))

    def test_batches_are_bounded_by_estimated_output_as_well_as_input(self):
        sources = [{**source(f's{index}', '— Да! — Нет! — Ага.', ordinal=index), 'scene_id': 'x'} for index in range(40)]
        grouped = list(batches(sources))
        self.assertEqual([12, 12, 12, 4], [len(batch) for batch in grouped])
        self.assertTrue(all(sum(map(target_tokens, batch)) <= 3072 for batch in grouped))
        self.assertEqual(320 + 12 * (48 + 3 * 64), output_estimate(grouped[0]))


class Review(unittest.TestCase):
    def test_parts_select_unit_ranges_and_store_exact_offsets(self):
        target = source('b', 'Маша открыла книгу. — Читай, — сказал Иван.', start=100)
        result = {'characters': [], 'passages': [{'id': target['id'], 'parts': [part(0, 0), part(1, 1, 'ivan', 'firm', target['id']), part(2, 2)]}]}
        parts, _ = validate_analysis(result, [target], [character('ivan', 'Иван')])
        self.assertEqual([('b-100-120', 'narrator'), ('b-120-129', 'ivan'), ('b-129-143', 'narrator')], [(p['id'], p['speaker']) for p in parts])
        self.assertEqual(target['text'], ''.join(p['text'] for p in parts))

    def test_part_ranges_must_tile_units(self):
        target = source('b', 'Маша открыла книгу. — Читай, — сказал Иван.')
        for parts in ([part(1, 2)], [part(0, 1), part(1, 2)], [part(0, 3)], [part(0, 1)], [], [part(1, 0)]):
            with self.subTest(parts=parts):
                _, _, invalid = review_analysis({'characters': [], 'passages': [{'id': target['id'], 'parts': parts}]}, [target], [])
                self.assertRegex(invalid[target['id']], f'^Passage {target["id"]}: .*(unit|parts)')

    def test_character_speech_and_expressive_delivery_cite_their_own_target(self):
        target, context = source('b', '— Да, — сказал Иван.'), source('a', 'Иван вошёл.')
        cast = [character('ivan', 'Иван')]
        for speaker, delivery, evidence, valid in (('ivan', 'neutral', [], False), ('ivan', 'neutral', [context['id']], False),
                                                   ('narrator', 'angry', [], False), ('unknown', 'sad', [context['id']], False),
                                                   ('ivan', 'neutral', [target['id']], True), ('narrator', 'angry', [target['id']], True),
                                                   ('unknown', 'neutral', [], True)):
            with self.subTest(speaker=speaker, delivery=delivery, evidence=evidence):
                result = {'characters': [], 'passages': [{'id': target['id'], 'parts': [part(0, 1, speaker, delivery, *evidence)]}]}
                _, _, invalid = review_analysis(result, [target], cast, [context])
                self.assertEqual(not valid, target['id'] in invalid)
                if not valid:
                    self.assertIn(f'cite its own passage ID {target["id"]}', invalid[target['id']])

    def test_valid_targets_survive_an_invalid_neighbour(self):
        first, second = source('a', 'Он кивнул.', ordinal=0), source('b', 'Она ушла.', ordinal=1)
        parts, _, invalid = review_analysis({'characters': [], 'passages': [narration(first)]}, [first, second], [])
        self.assertEqual([first['id']], [p['id'] for p in parts])
        self.assertEqual({second['id']: f'Passage {second["id"]} is missing from passages.'}, invalid)
        _, _, invalid = review_analysis({'characters': [], 'passages': [narration(first), narration(first)]}, [first, second], [])
        self.assertEqual([first['id'], second['id']], list(invalid))
        self.assertIn('more than once', invalid[first['id']])
        _, _, invalid = review_analysis('not json', [first, second], [])
        self.assertEqual({first['id'], second['id']}, set(invalid))

    def test_unusable_character_invalidates_only_targets_that_speak_with_it(self):
        first, second = source('a', '— Да, — сказал Пётр.', ordinal=0), source('b', 'Он кивнул.', ordinal=1)
        result = {'characters': [character('new_1', 'Пётр', evidence=[])],
                  'passages': [{'id': first['id'], 'parts': [part(0, 0, 'new_1', 'neutral'), part(1, 1)]}, narration(second)]}
        parts, cast, invalid = review_analysis(result, [first, second], [])
        self.assertEqual([], cast)
        self.assertEqual([second['id']], [p['id'] for p in parts])
        self.assertEqual({first['id']: f'Passage {first["id"]}: new character new_1 must cite at least one target passage ID in its evidence.'}, invalid)
        result['characters'][0]['id'] = 'Пётр'
        result['passages'][0]['parts'][0]['speaker'] = 'Пётр'
        self.assertEqual({first['id'], second['id']}, set(review_analysis(result, [first, second], [])[2]))
        self.assertIn("reply.characters[0].id has an unknown value 'Пётр'", review_analysis(result, [first, second], [])[2][first['id']])

    def test_title_cased_function_word_name_is_kept_only_for_review(self):
        target = source('a', '— Меня зовут Она.')
        first, last = 0, len(boundary_units(target['text'])) - 1
        record = character('new_1', 'Она', evidence=[target['id']])
        result = {'characters': [record], 'passages': [{'id': target['id'], 'parts': [
            part(first, last, 'new_1', 'neutral', target['id'])]}]}
        parts, cast, invalid = review_analysis(result, [target], [])
        self.assertEqual({}, invalid)
        self.assertEqual('Она', cast[0]['name'])
        self.assertTrue(cast[0]['name_review'])
        self.assertEqual(cast[0]['id'], parts[0]['speaker'])
        record['name'] = 'она'
        self.assertIn('needs a name', next(iter(review_analysis(result, [target], [])[2].values())))

    def test_unsupported_labels_and_unevidenced_traits_are_dropped_without_rejecting_the_batch(self):
        target = source('a', '— Да, — сказал робот Р-2.')
        record = {**character('new_1', 'Р-2', 'робот', 'пришелец', evidence=[target['id']]), 'kind': 'mechanical',
                  'gender': {'value': 'female', 'evidence': []}, 'description': ('слово ' * 50).strip()}
        parts, cast, invalid = review_analysis({'characters': [record], 'passages': [narration(target)]}, [target], [])
        self.assertEqual({}, invalid)
        [stored] = cast
        self.assertEqual((['робот'], 'mechanical', {}, {'mechanical': [target['id']]}, 'unknown', [target['id']]),
                         (stored['aliases'], stored['kind'], stored['kind_votes'], stored['kind_support'], stored['gender']['value'], stored['evidence']))

    def test_malformed_character_records_reject_the_reply_instead_of_being_coerced(self):
        target = source('a', '— Да, — сказал робот Р-2.')
        record = character('new_1', 'Р-2', evidence=[target['id']])
        cases = {'kind': {**record, 'kind': 'robot'}, 'value': {**record, 'gender': {'value': 'woman', 'evidence': []}},
                 'age_band': {**record, 'age_band': 'adult'}, 'misses kind': {key: value for key, value in record.items() if key != 'kind'},
                 'unknown fields': {**record, 'voice': 'low'}, "'elsewhere'": {**record, 'evidence': [target['id'], 'elsewhere']},
                 'aliases[0]': {**record, 'aliases': [7]}, 'string': {**record, 'name': 7}}
        for expected, malformed in cases.items():
            with self.subTest(expected=expected):
                parts, cast, invalid = review_analysis({'characters': [malformed], 'passages': [narration(target)]}, [target], [])
                self.assertEqual(([], []), (parts, cast))
                self.assertIn('does not match the response schema', invalid[target['id']])
                self.assertIn(expected, invalid[target['id']])

    def test_reply_over_a_schema_limit_rejects_every_target(self):
        target = source('a', '— Да, — сказал робот Р-2.')
        record = character('new_1', 'Р-2', evidence=[target['id']])
        for oversized, limit in (({**record, 'aliases': ['робот'] * 31}, 'aliases'), ({**record, 'description': 'а' * 301}, 'description'),
                                 ({**record, 'evidence': [target['id']] * 17}, 'evidence'), ({**record, 'name': 'Р' * 121}, 'name')):
            with self.subTest(limit=limit):
                parts, cast, invalid = review_analysis({'characters': [oversized], 'passages': [narration(target)]}, [target], [])
                self.assertEqual(([], []), (parts, cast))
                self.assertIn('does not match the response schema', invalid[target['id']])
                self.assertIn(limit, invalid[target['id']])
        many = [character(f'new_{index}', 'Р-2', evidence=[target['id']]) for index in range(1, 42)]
        self.assertIn('reply.characters must hold 0 to 40 items', review_analysis({'characters': many, 'passages': [narration(target)]}, [target], [])[2][target['id']])
        self.assertIn('reply.passages must hold 0 to 1 items', review_analysis({'characters': [], 'passages': [narration(target)] * 2}, [target], [])[2][target['id']])

    def test_temporary_ids_outside_new_1_to_new_40_are_unusable(self):
        target = source('a', '— Да, — сказал Пётр.')
        for cid in ('new_41', 'new_petr'):
            with self.subTest(cid=cid):
                result = {'characters': [character(cid, 'Пётр', evidence=[target['id']])],
                          'passages': [{'id': target['id'], 'parts': [part(0, 0, cid, 'neutral', target['id']), part(1, 1)]}]}
                self.assertIn(f"reply.characters[0].id has an unknown value '{cid}'", review_analysis(result, [target], [])[2][target['id']])

    def test_trait_without_valid_evidence_is_unknown(self):
        target = source('a', '— Да, — сказала Маша.')
        record = {**character('new_1', 'Маша', evidence=[target['id']]), 'gender': {'value': 'female', 'evidence': []}}
        self.assertEqual('unknown', validate_analysis({'characters': [record], 'passages': [narration(target)]}, [target], [])[1][0]['gender']['value'])
        for gender in ({'value': 'female', 'evidence': ['elsewhere']}, {'value': 'female'}):
            with self.subTest(gender=gender), self.assertRaisesRegex(AppError, 'does not match the response schema'):
                validate_analysis({'characters': [{**record, 'gender': gender}], 'passages': [narration(target)]}, [target], [])

    def test_known_id_gains_only_names_found_in_the_window_and_not_owned_by_others(self):
        target = source('a', 'Иван, он же Кузнец, кивнул.')
        cast = [character('ivan', 'Иван', 'Ваня'), character('petr', 'Пётр')]
        record = character('ivan', 'Пётр', 'Кузнец', 'Петя', evidence=[target['id']])
        stored = validate_analysis({'characters': [record], 'passages': [narration(target)]}, [target], cast)[1][0]
        self.assertEqual(('Иван', ['Ваня', 'Кузнец']), (stored['name'], stored['aliases']))
        context = source('b', 'Кузнеца звали Пётр.')
        owned = validate_analysis({'characters': [record], 'passages': [narration(target)]}, [target], cast, [context])[1][0]
        self.assertEqual(['Ваня', 'Кузнец'], owned['aliases'])

    def test_known_descriptor_is_renamed_by_a_proper_name_found_in_the_text(self):
        target = source('a', 'Стражника звали Иван.')
        cast = [character('guard', 'стражник')]
        stored = validate_analysis({'characters': [character('guard', 'Иван', evidence=[target['id']])], 'passages': [narration(target)]},
                                   [target], cast)[1][0]
        self.assertEqual(('Иван', ['стражник']), (stored['name'], stored['aliases']))

    def test_temporary_ids_with_one_normalized_name_stay_separate_records(self):
        target = source('a', '— Стой! — крикнул стражник. Стражник поднял руку.')
        result = {'characters': [character('new_1', 'Стражник', evidence=[target['id']]), character('new_2', 'стражника')],
                  'passages': [{'id': target['id'], 'parts': [part(0, 0, 'new_2', 'firm', target['id']), part(1, 2)]}]}
        parts, cast = validate_analysis(result, [target], [], names=stems)
        self.assertEqual(['Стражник', 'стражника'], [character['name'] for character in cast])
        self.assertEqual(cast[1]['id'], parts[0]['speaker'])

    def test_gendered_surnames_in_one_reply_stay_two_people(self):
        target = source('a', '— Иди, — сказал Петров. — Нет, — ответила Петрова.')
        units = len(boundary_units(target['text']))
        result = {'characters': [character('new_1', 'Петров', evidence=[target['id']], gender={'value': 'male', 'evidence': [target['id']]}),
                                 character('new_2', 'Петрова', evidence=[target['id']], gender={'value': 'female', 'evidence': [target['id']]})],
                  'passages': [{'id': target['id'], 'parts': [part(0, 0, 'new_1', 'neutral', target['id']), part(1, 1),
                                                              part(2, 2, 'new_2', 'neutral', target['id']), part(3, units - 1)]}]}
        parts, cast = validate_analysis(result, [target], [], names=stems)
        self.assertEqual([('Петров', 'male'), ('Петрова', 'female')], [(c['name'], c['gender']['value']) for c in cast])
        self.assertEqual([cast[0]['id'], 'narrator', cast[1]['id'], 'narrator'], [p['speaker'] for p in parts])

    def test_new_ids_use_first_target_evidence_and_batch_key_and_collisions_stay_separate(self):
        target, context = source('a', 'Стражник кивнул.'), source('b', 'Стражник у ворот.')
        record = character('new_1', 'Стражник', evidence=[context['id'], target['id']])
        def created(cast=(), key='k'):
            return validate_analysis({'characters': [record], 'passages': [narration(target)]}, [target], list(cast), [context], key)[1]
        expected = 'char_' + digest(['Стражник', target['id'], 'new_1', 'k'])[:16]
        self.assertEqual(expected, created()[0]['id'])
        self.assertNotEqual(expected, created(key='other')[0]['id'])
        existing = character(expected, 'Стражник', evidence=['old'])
        collided = created([existing])
        self.assertEqual(existing, collided[0])
        self.assertEqual(2, len(collided))
        self.assertNotEqual(expected, collided[1]['id'])


class Records(unittest.TestCase):
    def test_display_name_prefers_specific_proper_names(self):
        for old, new, name, aliases in (('стражник', 'Иван', 'Иван', ['стражник']), ('Иван', 'стражник', 'Иван', ['стражник']),
                                        ('Анна', 'Анна Петровна', 'Анна Петровна', ['Анна']), ('Анна Петровна', 'Анна', 'Анна Петровна', ['Анна']),
                                        ('Иванов', 'сержант Иванов', 'сержант Иванов', ['Иванов'])):
            with self.subTest(old=old, new=new):
                merged = merge_character(character('a', old), character('b', new))
                self.assertEqual(('a', name, aliases), (merged['id'], merged['name'], merged['aliases']))

    def test_aliases_are_bounded_at_thirty_everywhere(self):
        merged = merge_character(character('a', 'A', *[f'old {index}' for index in range(25)]), character('b', 'B', *[f'new {index}' for index in range(25)]))
        self.assertEqual(30, len(merged['aliases']))
        self.assertEqual(12, len(registry([merged])[0]['aliases']))

    def test_kind_votes_resolve_by_majority_with_person_on_ties(self):
        first = merge_character(character('a', 'A', kind='mechanical', kind_votes={'mechanical': 1}), character('a', 'A', kind='person', kind_votes={'person': 1}))
        self.assertEqual(('person', {'mechanical': 1, 'person': 1}), (first['kind'], first['kind_votes']))
        second = merge_character(first, character('a', 'A', kind='mechanical', kind_votes={'mechanical': 1}))
        self.assertEqual(('mechanical', {'mechanical': 2, 'person': 1}), (second['kind'], second['kind_votes']))
        self.assertEqual('person', merge_character(character('a', 'A'), character('a', 'A'))['kind'])
        self.assertEqual('collective', registry([character('a', 'A', kind='collective')])[0]['kind'])

    def test_description_is_cut_at_a_word_boundary_and_its_echo_is_contained(self):
        stored = ' '.join(['высокий худой человек в куртке'] * 12)
        echo = clip(stored, 300)
        self.assertTrue(len(echo) <= 300 and stored.startswith(echo) and stored[len(echo)] == ' ')
        self.assertEqual(echo, registry([character('a', 'A', description=stored)])[0]['description'])
        self.assertEqual(stored, merge_description(stored, stored[:299]))
        self.assertEqual(stored, merge_description(stored, echo))
        self.assertEqual(f'{stored[:200].strip()}; рыжий', merge_description(stored[:200], 'рыжий'))


class Projection(unittest.TestCase):
    def test_projection_keeps_forty_relevant_entries_in_priority_order(self):
        cast = [character(f'c{index:02d}', f'Имя{index:02d}') for index in range(60)]
        chosen = relevant_characters(cast, ['Имя50 вошёл.'], ['c55', 'narrator', 'c56'], {'c10': 100, 'c11': 50})
        expected = ['c50', 'c55', 'c56', 'c10', 'c11'] + [f'c{index:02d}' for index in range(10)] + [f'c{index:02d}' for index in range(12, 37)]
        self.assertEqual(expected, [item['id'] for item in chosen])

    def test_mentions_use_the_supplied_name_forms(self):
        cast = [character('a', 'Анна'), character('b', 'Кирилл', 'Комар')]
        self.assertEqual(['b', 'a'], [item['id'] for item in relevant_characters(cast, ['Кирилла не было.'], names=stems)])
        self.assertEqual(['a', 'b'], [item['id'] for item in relevant_characters(cast, ['Кирилла не было.'])])


class Schema(unittest.TestCase):
    def test_schema_bounds_arrays_strings_and_identifiers(self):
        first, second, context = source('a', '— Да, — сказал Иван.'), source('b', 'Он ушёл.'), source('c', 'Ночь.')
        schema = analysis_schema([first, second], [context])
        passages = schema['properties']['passages']
        passage = passages['items']['properties']
        item = passage['parts']['items']['properties']
        record = schema['properties']['characters']['items']['properties']
        self.assertEqual((2, 2, [first['id'], second['id']]), (passages['minItems'], passages['maxItems'], passage['id']['enum']))
        self.assertEqual((2, [0, 1], [0, 1]), (passage['parts']['maxItems'], item['first']['enum'], item['last']['enum']))
        self.assertEqual(([first['id'], second['id'], context['id']], 16), (item['evidence']['items']['enum'], item['evidence']['maxItems']))
        self.assertEqual((40, 30, 119, 300, list(KINDS)), (schema['properties']['characters']['maxItems'], record['aliases']['maxItems'],
                                                         record['aliases']['items']['maxLength'], record['description']['maxLength'], record['kind']['enum']))
        self.assertIn('kind', schema['properties']['characters']['items']['required'])
        json.dumps(schema)


class WorkerProtocol(unittest.TestCase):
    setUp = no_structure

    def analyse(self, tokens, content='{"characters": [], "passages": []}', finish='stop', retry=''):
        model = Mock()
        model.tokenize.return_value = [1] * tokens
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': content}, 'finish_reason': finish}]}
        target = source('a', '— Да, — сказал Иван.')
        payload = {'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': [target], 'cast': [], 'retry_error': retry,
                   'before': [context_target(source('z', 'Ночь.'), {})], 'after': []}
        with patch.object(worker, 'load_text', return_value=model):
            return worker.analyze(payload), model

    def test_max_tokens_follow_the_measured_prompt_and_stop_at_the_output_limit(self):
        limit = output_limit([source('a', '— Да, — сказал Иван.')])
        self.assertEqual(500, self.analyse(8192 - PROMPT_MARGIN - 500)[1].create_chat_completion.call_args.kwargs['max_tokens'])
        result, model = self.analyse(1000, retry='Passage a-0-20: cover every unit.')
        options = model.create_chat_completion.call_args.kwargs
        self.assertEqual((3072, 3072), (limit, options['max_tokens']))
        self.assertEqual(['a-0-20'], options['response_format']['schema']['properties']['passages']['items']['properties']['id']['enum'])
        self.assertEqual('Passage a-0-20: cover every unit.', json.loads(options['messages'][1]['content'])['previous_validation_error'])
        self.assertEqual({'reply': {'characters': [], 'passages': []}, 'known': [], 'speech_structure': {}, 'local_unverified': True}, result)

    def test_prompt_without_room_for_the_estimated_output_is_a_capacity_failure(self):
        estimate = output_estimate([source('a', '— Да, — сказал Иван.')])
        self.analyse(8192 - PROMPT_MARGIN - estimate)
        with self.assertRaises(AnalysisCapacityError):
            self.analyse(8192 - PROMPT_MARGIN - estimate + 1)

    def test_unparseable_model_output_is_returned_for_validation(self):
        self.assertEqual({'reply': '{"characters": [', 'known': [], 'speech_structure': {}, 'local_unverified': True}, self.analyse(10, '{"characters": [')[0])
        with self.assertRaises(AnalysisCapacityError):
            self.analyse(10, '{', 'length')

    def test_programming_errors_are_not_reclassified_as_model_validation(self):
        payload = {'pack': {'text_model': {'analysis_mode': 'staged'}},
                   'device': 'cpu', 'segments': [], 'cast': []}
        for error in (KeyError('missing'), TypeError('wrong type'), ValueError('bad value')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(worker, 'load_text', return_value=object()), \
                    patch.object(worker, 'staged_analysis', side_effect=error), \
                    self.assertRaises(type(error)):
                worker.analyze(payload)

    def test_error_replies_drop_the_class_name_only_for_application_errors(self):
        import contextlib
        import io
        for error, reply in ((AppError('Plain message.'), {'error': 'Plain message.'}), (ValueError('bad value'), {'error': 'ValueError: bad value'}),
                             (AnalysisCapacityError('Too large.'), {'error': 'Too large.', 'error_code': 'analysis_capacity'})):
            output = io.StringIO()
            with self.subTest(error=error), patch.object(worker, 'analyze', side_effect=error), \
                    patch('sys.stdin', io.StringIO(json.dumps({'id': 'r', 'method': 'analyze', 'data': {}}) + '\n')), contextlib.redirect_stdout(output):
                worker.main()
            self.assertEqual({'id': 'r', **reply}, json.loads(output.getvalue()))

    def test_workers_round_trip_utf8_under_a_legacy_console_encoding(self):
        text = 'Ёлка — «Нет», Џ ё'
        for module, method, patched, expected in (('worker', 'analyze', 'worker.analyze=lambda data: data["text"][::-1]', text[::-1]),
                                                  ('align_worker', 'align', 'align_worker.align=lambda data: [{"text": data["text"][::-1], "start": 0.0, "end": 1.0}]',
                                                   [{'text': text[::-1], 'start': 0.0, 'end': 1.0}])):
            script = f'import sys\nsys.path.insert(0, {str(RUNTIME)!r})\nimport {module}\n{patched}\n{module}.main()\n'
            request = json.dumps({'id': 'r1', 'method': method, 'data': {'text': text}}, ensure_ascii=False) + '\n'
            with self.subTest(module=module):
                completed = subprocess.run([sys.executable, '-c', script], input=request.encode('utf-8'), capture_output=True, timeout=60,
                                           env={**os.environ, 'PYTHONIOENCODING': 'cp1252', 'PYTHONUTF8': '0'})
                self.assertEqual(0, completed.returncode, completed.stderr.decode('utf-8', 'replace'))
                self.assertIn(text[::-1].encode('utf-8'), completed.stdout)
                self.assertEqual({'id': 'r1', 'result': expected}, json.loads(completed.stdout.decode('utf-8')))


class Transport(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def engine(self, body, record=None):
        original = subprocess.Popen
        def launch(command, **options):
            if record is not None:
                record.update(options)
            return original([sys.executable, '-u', '-c', 'import os, sys, json, time\n' + body], **options)
        with patch('rimsky.jobs.subprocess.Popen', side_effect=launch):
            engine = Engine(self.root)
        self.addCleanup(engine.close)
        return engine

    def test_engine_launches_workers_with_a_utf8_environment(self):
        options = {}
        body = 'for line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({"id":r["id"],"result":[os.environ.get(k) for k in ("PYTHONUTF8","PYTHONIOENCODING","RIMSKY_PROBE")]}),flush=True)'
        with patch.dict(os.environ, {'RIMSKY_PROBE': 'kept', 'PYTHONIOENCODING': 'cp1252'}):
            engine = self.engine(body, options)
        self.assertEqual(['1', 'utf-8', 'kept'], engine.call('probe', {}, threading.Event(), timeout=10))
        self.assertEqual(('1', 'utf-8'), (options['env']['PYTHONUTF8'], options['env']['PYTHONIOENCODING']))

    def test_engine_types_transport_failures(self):
        for body, timeout, code in (('sys.stdin.readline()', 10, 'exit'), ('sys.stdin.readline()\ntime.sleep(30)', .5, 'timeout'),
                                    ("sys.stdin.readline()\nprint(json.dumps({'id': 'other', 'result': 1}), flush=True)\ntime.sleep(30)", 10, 'protocol')):
            engine = self.engine(body)
            with self.subTest(code=code), self.assertRaises(EngineTransportError) as raised:
                engine.call('analyze', {}, threading.Event(), timeout=timeout)
            self.assertEqual(code, raised.exception.code)
            with self.assertRaises(EngineTransportError) as again:
                engine.call('analyze', {}, threading.Event(), timeout=timeout)
            self.assertEqual('exit', again.exception.code)

    def corrected(self, *replies):
        class Fake:
            def __init__(self):
                self.payloads = []
            def call(self, method, payload, stop):
                self.payloads.append(payload)
                reply = replies[len(self.payloads) - 1]
                if isinstance(reply, Exception):
                    raise reply
                return reply
        jobs = Jobs(None, self.root)
        jobs.engine = Fake()
        jobs.stop = threading.Event()
        return jobs

    def test_transport_errors_are_never_sent_to_the_model_and_exit_is_retried_once(self):
        exited = EngineTransportError('Local inference process exited before completing its request.', 'exit')
        jobs = self.corrected(exited, {'same': True}, {'same': False, 'evidence': []})
        def validate(reply):
            if 'evidence' not in reply:
                raise AppError('Invalid alias decision.')
            return reply['same']
        self.assertFalse(jobs._corrected('reconcile', {}, validate))
        self.assertEqual(['', '', 'Invalid alias decision.'], [payload['retry_error'] for payload in jobs.engine.payloads])
        for replies, calls in (((exited, exited), 2), ((EngineTransportError(TIMEOUT, 'timeout'),), 1), ((AppError('ValueError: worker failure'),), 1)):
            jobs = self.corrected(*replies)
            with self.subTest(replies=replies), self.assertRaises(AppError):
                jobs._corrected('reconcile', {}, validate)
            self.assertEqual(calls, len(jobs.engine.payloads))


class Flow(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.book = self.store.import_data(FB2, 'flow.fb2')
        self.pack = pack_fixture(self.root / 'pack')

    def generation(self, book=None):
        return self.store.create_generation((book or self.book)['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                                         'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False})

    def run_job(self, generation, factory):
        jobs = Jobs(self.store, self.root, engine_factory=factory)
        jobs.start(generation['id'])
        jobs.thread.join(20)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def test_invalid_target_is_reasked_alone_with_its_specific_error(self):
        class Reask(SyntheticEngine):
            def __init__(self, stubborn=False):
                super().__init__()
                self.calls, self.stubborn = [], stubborn
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'analyze':
                    ids = [s['id'] for s in payload['segments']]
                    self.calls.append((ids, payload['retry_error']))
                    if len(ids) > 1 or self.stubborn and ids[0].endswith('p00002-0-43'):
                        result['passages'][min(1, len(ids) - 1)]['parts'][0]['first'] = 1
                return result
        engine = Reask()
        done = self.run_job(self.generation(), lambda _: engine)
        self.assertNotEqual('failed', done['status'], done['error'])
        self.assertEqual([3, 1, 3, 1], [len(ids) for ids, _ in engine.calls])
        retried = [(ids, error) for ids, error in engine.calls if error]
        self.assertEqual(['c0001-p00002-0-43', 'c0002-p00002-0-29'], [ids[0] for ids, _ in retried])
        self.assertTrue(all(error.startswith(f'Passage {ids[0]}: parts must cover units') for ids, error in retried))
        self.assertEqual([], done['analysis']['degraded'])
        engine = Reask(stubborn=True)
        store = self.store
        generation = self.generation()
        done = self.run_job(generation, lambda _: engine)
        self.assertEqual([{'source': 'c0001-p00002-0-43', 'reason': engine.calls[1][1]}], done['analysis']['degraded'])
        degraded = [s for s in done['segments'] if s['source_id'] == 'c0001-p00002']
        self.assertEqual([('unknown', 'neutral', 0, 43)], [(s['speaker'], s['delivery'], s['start'], s['end']) for s in degraded])
        self.assertTrue(degraded[0]['warning'].startswith(DEGRADED_HOLD))
        self.assertEqual('degraded_analysis', done['analysis']['speaker_resolution'][0]['triggers'][0])
        self.assertTrue(store.generation(generation['id'])['analysis']['complete'])

    def test_job_payloads_round_trip_through_the_real_worker_analysis(self):
        no_structure(self)
        texts = {}
        class Bridge(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'analyze':
                    return super().call(method, payload, stop)
                self.analyzed += 1
                def complete(messages, response_format, temperature, max_tokens):
                    content = json.loads(messages[1]['content'])
                    texts.update({target['id']: ''.join(unit['text'] for unit in target['units']) for target in content['targets']})
                    ids = response_format['schema']['properties']['passages']['items']['properties']['id']['enum']
                    passages = [{'id': target['id'], 'parts': [part(0, len(target['units']) - 1)]} for target in content['targets']]
                    return {'choices': [{'message': {'content': json.dumps({'characters': [], 'passages': passages if ids == [p['id'] for p in passages] else []})},
                                         'finish_reason': 'stop'}]}
                model = Mock()
                model.tokenize.side_effect = lambda data: [1] * (len(data) // 4)
                model.create_chat_completion.side_effect = complete
                sent = json.loads(json.dumps(payload))
                sent['pack']['text_model']['analysis_mode'] = 'legacy'
                with patch.object(worker, 'load_text', return_value=model):
                    return worker.analyze(sent)
        engine = Bridge()
        generation = self.generation()
        done = self.run_job(generation, lambda _: engine)
        self.assertNotEqual('failed', done['status'], done['error'])
        self.assertEqual((2, []), (engine.analyzed, done['analysis']['degraded']))
        self.assertEqual({s['id']: s['text'] for s in generation['segments']}, texts)

    def test_progress_is_written_with_each_batch_commit(self):
        seen = []
        engine = SyntheticEngine()
        original = engine.call
        def call(method, payload, stop):
            if method == 'analyze':
                seen.append(self.store.generation(generation['id'])['analysis'].get('progress'))
            return original(method, payload, stop)
        engine.call = call
        generation = self.generation()
        done = self.run_job(generation, lambda _: engine)
        self.assertNotEqual('failed', done['status'], done['error'])
        self.assertEqual([None, {'stage': 'analysis', 'done': 3, 'total': 6}], seen)

    def test_timeouts_bisect_batches_retry_a_single_target_once_and_stop_on_a_second_timeout_in_a_row(self):
        slow = 'c0001-p00003-0-34'
        for stalls, status, engine_count in ((1, 'review', 6), (2, 'failed', 4)):
            engines, retries, left = [], [], [stalls]
            class Slow(SyntheticEngine):
                def __init__(self):
                    super().__init__()
                    self.process = Process()
                def call(self, method, payload, stop):
                    if method == 'analyze':
                        retries.append(payload['retry_error'])
                        single = [s['id'] for s in payload['segments']] == [slow]
                        if len(payload['segments']) > 1 or single and left[0]:
                            left[0] -= single
                            self.process.code = -15
                            raise EngineTransportError(TIMEOUT, 'timeout')
                    return super().call(method, payload, stop)
            def factory(_):
                engines.append(Slow())
                return engines[-1]
            with self.subTest(stalls=stalls):
                done = self.run_job(self.generation(), factory)
                self.assertEqual((status, '' if status == 'review' else TIMEOUT), (done['status'], done['error']))
                self.assertEqual(([], engine_count, {''}), (done['analysis'].get('degraded', []), len(engines), set(retries)))

    def test_stale_split_placeholders_are_rebuilt_before_analysis(self):
        text = ' '.join(f'Предложение номер {index} о погоде и о дороге домой.' for index in range(14))
        raw = FB2.replace('<p>Иван кивнул.</p>'.encode(), f'<p>{text}</p>'.encode())
        book = self.store.import_data(raw, 'long.fb2')
        generation = self.generation(book)
        long = [s for s in generation['segments'] if len(s['text']) > 100]
        self.assertEqual((True, 1), (len(long) > 1, len({s['source_id'] for s in long})))
        with self.store.lock, self.store.db:
            self.store.db.execute('DELETE FROM segments WHERE generation_id=? AND source_id=?', (generation['id'], long[0]['source_id']))
            self.store.db.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken) VALUES(?,?,?,?,?,?,?,?,?)',
                                  (generation['id'], f'{long[0]["source_id"]}-0-{len(text)}', long[0]['ordinal'], long[0]['chapter_id'],
                                   long[0]['source_id'], 0, len(text), text, text))
        done = self.run_job(generation, lambda _: SyntheticEngine())
        self.assertNotEqual('failed', done['status'], done['error'])
        self.assertEqual([s['id'] for s in long], [s['id'] for s in done['segments'] if s['source_id'] == long[0]['source_id']])

    def test_schema_restart_rebuilds_placeholders_and_drops_stale_speakers(self):
        generation = self.generation()
        ids = [s['id'] for s in generation['segments']]
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({'casting_schema': CASTING_SCHEMA - 1, 'completed_sources': ids}), generation['id']))
        self.store.execute("UPDATE segments SET speaker='char_stale' WHERE generation_id=?", (generation['id'],))
        engine = SyntheticEngine()
        done = self.run_job(generation, lambda _: engine)
        self.assertNotEqual('failed', done['status'], done['error'])
        self.assertEqual(2, engine.analyzed)
        self.assertNotIn('char_stale', {s['speaker'] for s in done['segments']})
        self.assertEqual(CASTING_SCHEMA, done['analysis']['casting_schema'])

    def test_placeholder_rebuild_refuses_generations_with_saved_audio(self):
        generation = self.generation()
        bases = base_segments(self.book, default_chapters(self.book))
        self.store.execute("UPDATE segments SET status='ready',audio_hash=? WHERE generation_id=? AND id=?", ('a' * 64, generation['id'], generation['segments'][0]['id']))
        before = self.store.generation(generation['id'])['segments']
        with self.assertRaises(AppError):
            placeholder_segments(self.store, generation['id'], bases, generation['config']['chapters'])
        self.assertEqual(before, self.store.generation(generation['id'])['segments'])


if __name__ == '__main__':
    unittest.main()
