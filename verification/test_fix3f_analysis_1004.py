from __future__ import annotations

import json
import unittest
from collections import Counter
from unittest.mock import Mock, patch

from test_core import no_structure, SyntheticEngine, narration
from test_fix3_analysis_1004 import Library, character, fb2, source
import worker
from rimsky.analysis import (PROMPT_MARGIN, SYSTEM_PROMPT, analyse_sources, batches, analysis_target, common_words, identity, merge_character, output_estimate,
                             output_limit, preferred_name)
from rimsky.jobs import merge_aliases
from rimsky.util import AnalysisCapacityError

CAST = [character(f'char_{index:02d}', f'Имя{index:02d}', description='очень длинное описание персонажа ' * 9) for index in range(40)]
REPLY = {'choices': [{'message': {'content': '{"characters": [], "passages": []}'}, 'finish_reason': 'stop'}]}


def measured(data):
    return [1] * (len(data) // 4)


def lines(count):
    return [source(f's{index:02d}', f'— Реплика номер {index}, — сказал Иван.', ordinal=index) for index in range(count)]


CONTEXT = 12288


def room(segments, count, listed=None):
    listed = count if listed is None else listed
    content = {'known_characters': CAST[:count], 'context_before': [], 'targets': [analysis_target(item) for item in segments], 'context_after': []}
    if listed > count:
        content['more_known_characters'] = [identity(item) for item in CAST[count:listed]]
    raw = json.dumps(content, ensure_ascii=False)
    return CONTEXT - len(measured((SYSTEM_PROMPT + raw).encode())) - PROMPT_MARGIN


def analyse(segments, count, required=None):
    model = Mock()
    model.tokenize.side_effect = measured
    model.create_chat_completion.return_value = REPLY
    payload = {'pack': {'text_model': {'context': CONTEXT, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': segments, 'cast': CAST[:count], 'before': [], 'after': [],
               **({} if required is None else {'required': required})}
    with patch.object(worker, 'load_text', return_value=model):
        worker.analyze(payload)
    options = model.create_chat_completion.call_args.kwargs
    content = json.loads(options['messages'][1]['content'])
    return len(content['known_characters']), len(content.get('more_known_characters', [])), options['max_tokens']


class Budget(unittest.TestCase):
    setUp = no_structure

    def test_every_request_gets_the_output_limit_and_more_optional_entries_never_change_the_budget(self):
        for segments, required in ((lines(20), 2), ([source('a', '— Да, — сказал Иван.')], None)):
            limit = output_limit(segments)
            def expected(count):
                detailed = max(size for size in range(count + 1) if room(segments, size, count) >= limit)
                return detailed, count - detailed, limit
            self.assertTrue(room(segments, 0, 40) >= limit > room(segments, 40))
            with self.subTest(targets=len(segments)):
                self.assertEqual([expected(count) for count in range(41)], [analyse(segments, count, required) for count in range(41)])

    def test_every_offered_character_stays_a_valid_speaker_when_descriptions_do_not_fit(self):
        model = Mock()
        model.tokenize.side_effect = measured
        model.create_chat_completion.return_value = REPLY
        payload = {'pack': {'text_model': {'context': CONTEXT, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': lines(20), 'cast': CAST, 'before': [], 'after': [], 'required': 2}
        with patch.object(worker, 'load_text', return_value=model):
            worker.analyze(payload)
        options = model.create_chat_completion.call_args.kwargs
        content = json.loads(options['messages'][1]['content'])
        speakers = options['response_format']['schema']['properties']['passages']['items']['properties']['parts']['items']['properties']['speaker']['enum']
        # Expanded identity-continuity instructions may leave room only for
        # compact entries. Every identity and alias must still be offered.
        self.assertTrue(0 <= len(content['known_characters']) < 40)
        self.assertEqual([item['id'] for item in CAST], [item['id'] for item in content['known_characters'] + content['more_known_characters']])
        self.assertEqual({'id', 'name', 'aliases'}, set(content['more_known_characters'][0]))
        self.assertEqual([item['id'] for item in CAST], speakers[2:42])

    def test_multi_target_batch_whose_limit_is_out_of_reach_is_a_capacity_failure_whatever_the_registry_size(self):
        batch = lines(32)
        self.assertTrue(room(batch, 0) < output_limit(batch) and room(batch, 5) >= output_estimate(batch))
        for count in range(41):
            with self.subTest(count=count), self.assertRaises(AnalysisCapacityError):
                analyse(batch, count, 0)
        batch = lines(20)
        required = next(count for count in range(41) if room(batch, count) < output_limit(batch))
        detailed, compact, limit = analyse(batch, required, required)
        self.assertEqual((required, output_limit(batch)), (detailed + compact, limit))
        self.assertLess(detailed, required)

    def test_loop_splits_a_batch_whose_limit_is_out_of_reach_and_sends_each_half_with_its_full_limit(self):
        targets, sent = lines(40), []
        def call(payload):
            model = Mock()
            model.tokenize.side_effect = measured
            def complete(messages, response_format, temperature, max_tokens):
                sent.append((len(payload['segments']), max_tokens, output_limit(payload['segments'])))
                return {'choices': [{'message': {'content': json.dumps({'characters': [], 'passages': [narration(item) for item in payload['segments']]})},
                                     'finish_reason': 'stop'}]}
            model.create_chat_completion.side_effect = complete
            with patch.object(worker, 'load_text', return_value=model):
                return worker.analyze({'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', **payload})
        shared = {'cast': CAST[:3], 'accepted': {}, 'volume': Counter(), 'inputs': []}
        analyse_sources(targets, targets, shared, call, lambda *_: None)
        self.assertEqual([17, 17, 6], [len(batch) for batch in batches(targets)])
        self.assertEqual([8, 9, 8, 9, 6], [size for size, _, _ in sent])
        self.assertTrue(all(budget == limit for _, budget, limit in sent), sent)


BOOK = ['Его вера была крепка, и вера отца тоже, а вера деда и вера матери были ещё крепче.',
        '— Иди сюда, — сказала Вера. Потом кивнула Вера, и засмеялась Вера.', '— Верочка, подожди! — сказал он.',
        'Will said he will come. I will wait, and you will see that they will stay, it will rain, we will sing and he will dance.',
        '"Go home," Will said. I told Will about it, and later Will agreed. "Will you?" she asked.', '— Willy, come here! — she said.',
        'Роман писал роман третий год, и роман почти готов.', '— Рома, иди обедать! — крикнула мать.']


class Names(unittest.TestCase):
    def test_name_capitalised_mid_sentence_three_times_is_kept_although_its_lowercase_word_is_more_frequent(self):
        common = common_words(BOOK)
        self.assertTrue({'вера', 'will', 'роман'}.isdisjoint(common) and {'come', 'said', 'отца'} <= common)
        for old, new in (('Вера', 'Верочка'), ('Will', 'Willy'), ('Роман', 'Рома')):
            with self.subTest(old=old):
                merged = merge_character(character('a', old), character('b', new), common)
                self.assertEqual((old, old, [new]), (preferred_name(old, new, common), merged['name'], merged['aliases']))
                self.assertEqual([(old, [new])], [(item['name'], item['aliases']) for item in merge_aliases([character('a', old), character('b', new)], {'b': 'a'}, common)])

    def test_two_mid_sentence_capitals_are_not_enough(self):
        common = common_words([text.replace('и засмеялась Вера', 'и засмеялась она').replace('later Will agreed', 'later he agreed') for text in BOOK])
        self.assertTrue({'вера', 'will'} <= common and 'роман' not in common)
        pairs = (('Вера', 'Павловна'), ('Will', 'Turner'), ('Роман', 'Рома'))
        self.assertEqual(('Павловна', 'Turner', 'Роман'), tuple(preferred_name(old, new, common) for old, new in pairs))
        self.assertEqual(('Вера', 'Will', 'Роман'), tuple(preferred_name(old, new, common_words(BOOK)) for old, new in pairs))

    def test_only_title_case_words_after_a_mid_sentence_gap_count_as_names(self):
        def vocabulary(gap):
            return common_words([' '.join(f'и старик{gap}Старик' for _ in range(3)) + ' и старик и старик'])
        for gap in ('. ', '! — ', '? ', '… ', ': ', '\n', '.\n— ', ' «', ' „', ' “', ' "', ' (', ' ['):
            with self.subTest(opening=gap):
                self.assertIn('старик', vocabulary(gap))
        for gap in (' ', ', ', ', — ', ' — ', '; ', '," ', '», — ', '“ '):
            with self.subTest(middle=gap):
                self.assertNotIn('старик', vocabulary(gap))
        common = common_words(['Старик кивнул.', 'Старик сел.', 'Старик ушёл.', 'Он закричал: «НЕТ, СТАРИК, НЕТ, СТАРИК, НЕТ, СТАРИК!»', 'и старик ' * 7])
        self.assertIn('старик', common)
        self.assertEqual('Иван Петрович', preferred_name('Старик', 'Иван Петрович', common))


class JobsNames(Library):
    def test_jobs_keep_a_name_the_book_writes_capitalised_mid_sentence_when_a_diminutive_arrives(self):
        class Diminutive(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'analyze':
                    return super().call(method, payload, stop)
                vera = next((item['id'] for item in payload['cast'] if item['name'] == 'Вера'), None)
                characters = [character(vera, 'Верочка', evidence=[item['id']]) if vera else character('new_1', 'Вера', evidence=[item['id']])
                              for item in payload['segments'] if ('Верочка' if vera else 'сказала Вера') in item['text']]
                return {'characters': characters, 'passages': [narration(item) for item in payload['segments']]}
        done = self.run_book(fb2(('Первая', ['Его вера была крепка, и вера отца, и вера деда, и вера матери, и вера сына.', '— Иди сюда, — сказала Вера.',
                                             'Потом кивнула Вера, и засмеялась Вера.']), ('Вторая', ['— Верочка, подожди! — сказал он.'])),
                             lambda _: Diminutive())
        self.assertEqual('review', done['status'], done['error'])
        self.assertEqual([('Вера', ['Верочка'])], [(item['name'], item['aliases']) for item in done['cast'] if item['id'] != 'narrator'])


if __name__ == '__main__':
    unittest.main()
