from __future__ import annotations

import json
import unittest
from collections import Counter
from unittest.mock import Mock, patch

from test_core import no_structure, SyntheticEngine, narration
from test_fix3_analysis_1004 import Library, Process, character, fb2, part, source
import worker
from rimsky.analysis import (ANALYSIS_CONTRACT, DEGRADED_HOLD, DEGRADED_WARNING, PROMPT_MARGIN, SYSTEM_PROMPT, analyse_sources, analysis_target, clip, common_words, identity,
                             merge_description, output_estimate, output_limit, preferred_name, registry, review_analysis)
from rimsky.casting import CASTING_SCHEMA
from rimsky.jobs import DIMINUTIVES, Jobs
from rimsky.util import AnalysisCapacityError, AppError, EngineTransportError

TIMEOUT = 'Local inference exceeded its 1800-second operation limit.'
STORED = ('Высокий худой человек в длинном сером плаще, с аккуратно подстриженной седой бородой и внимательными серыми глазами, '
          'который всегда носит с собой потёртый кожаный портфель и старинные карманные часы на цепочке; говорит медленно и негромко, '
          'часто делает паузы, словно подбирая слова, и никогда не повышает голос.')


def spoken(item, speaker, own=True):
    return {'id': item['id'], 'parts': [part(0, 0, speaker, 'neutral', *([item['id']] if own else [])), part(1, 1)]}


def lines(count):
    return [source(f's{index:02d}', f'— Реплика номер {index}, — сказал Иван.', ordinal=index) for index in range(count)]


def state(**extra):
    return {'cast': [], 'accepted': {}, 'volume': Counter(), 'inputs': [], **extra}


class Cascade(Library):
    def test_record_cited_only_by_an_invalid_target_is_kept_by_the_valid_targets_that_speak_through_it(self):
        targets = lines(4)
        result = {'characters': [character('new_1', 'Иван', evidence=[targets[0]['id']])],
                  'passages': [{'id': targets[0]['id'], 'parts': [part(0, 0, 'new_1', 'neutral', targets[0]['id'])]},
                               *(spoken(item, 'new_1') for item in targets[1:])]}
        parts, updated, invalid = review_analysis(result, targets, [])
        [ivan] = updated
        self.assertEqual([targets[0]['id']], list(invalid))
        self.assertIn('cover every unit', invalid[targets[0]['id']])
        self.assertEqual([item['id'] for item in targets[1:]], ivan['evidence'])
        self.assertEqual([ivan['id'], 'narrator'] * 3, [item['speaker'] for item in parts])

    def test_one_bad_introducing_line_costs_one_reask_instead_of_one_per_line(self):
        targets, calls, failed = lines(12), [], []
        def call(payload):
            batch = payload['segments']
            calls.append(len(batch))
            known = next((item['id'] for item in payload['cast'] if item['name'] == 'Иван'), None)
            return {'characters': [] if known else [character('new_1', 'Иван', evidence=[batch[0]['id']])],
                    'passages': [spoken(item, known or 'new_1', own=len(batch) == 1 or item is not batch[0]) for item in batch]}
        shared = state()
        analyse_sources(targets, targets, shared, call, lambda done, parts, degraded, key, *_: failed.extend(degraded))
        self.assertEqual(([12, 1], [], ['Иван']), (calls, failed, [item['name'] for item in shared['cast']]))
        self.assertEqual(12, sum(entry['speaker'] == shared['cast'][0]['id'] for entry in shared['inputs']))

    def test_jobs_analyse_a_dialogue_chapter_with_one_bad_introduction_in_two_requests(self):
        calls = []
        class Dialogue(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'analyze':
                    return super().call(method, payload, stop)
                segments = payload['segments']
                calls.append(len(segments))
                known = next((item['id'] for item in payload['cast'] if item['name'] == 'Иван'), None)
                dialogue = [item for item in segments if 'сказал Иван' in item['text']]
                return {'characters': [] if known or not dialogue else [character('new_1', 'Иван', evidence=[dialogue[0]['id']])],
                        'passages': [spoken(item, known or 'new_1', own=len(segments) == 1 or item is not dialogue[0]) if item in dialogue else narration(item)
                                     for item in segments]}
        done = self.run_book(fb2(('Глава', [f'— Реплика номер {index}, — сказал Иван.' for index in range(1, 13)])), lambda _: Dialogue())
        self.assertEqual(('review', [13, 1], []), (done['status'], calls, done['analysis']['degraded']))
        self.assertEqual(['Иван'], [item['name'] for item in done['cast'] if item['id'] != 'narrator'])


class Registry(unittest.TestCase):
    setUp = no_structure

    CAST = [character(f'char_{index:02d}', f'Имя{index:02d}', description='очень длинное описание персонажа ' * 9) for index in range(40)]

    def analyse(self, segments, required=None, context=12288):
        model = Mock()
        model.tokenize.side_effect = lambda data: [1] * (len(data) // 4)
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"characters": [], "passages": []}'}, 'finish_reason': 'stop'}]}
        payload = {'pack': {'text_model': {'context': context, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': segments, 'cast': self.CAST, 'before': [], 'after': [],
                   **({} if required is None else {'required': required})}
        with patch.object(worker, 'load_text', return_value=model):
            worker.analyze(payload)
        options = model.create_chat_completion.call_args.kwargs
        content = json.loads(options['messages'][1]['content'])
        return len(content['known_characters']), len(content.get('more_known_characters', [])), options['max_tokens']

    def room(self, segments, count, context=12288, listed=None):
        listed = count if listed is None else listed
        content = {'known_characters': self.CAST[:count], 'context_before': [], 'targets': [analysis_target(item) for item in segments], 'context_after': []}
        if listed > count:
            content['more_known_characters'] = [identity(item) for item in self.CAST[count:listed]]
        raw = json.dumps(content, ensure_ascii=False)
        return context - len((SYSTEM_PROMPT + raw).encode()) // 4 - PROMPT_MARGIN

    def test_multi_target_batch_keeps_the_required_tiers_and_room_for_the_full_output_limit(self):
        batch = lines(20)
        limit = output_limit(batch)
        self.assertTrue(self.room(batch, 40) < output_estimate(batch) and limit > 3072)
        detailed, compact, budget = self.analyse(batch, required=3)
        listed = detailed + compact
        self.assertTrue(3 <= listed and self.room(batch, 0, listed=listed) >= limit and (listed == 40 or self.room(batch, 0, listed=listed + 1) < limit))
        self.assertTrue(self.room(batch, detailed, listed=listed) >= limit > self.room(batch, detailed + 1, listed=listed))
        self.assertEqual(limit, budget)

    def test_multi_target_batch_whose_required_tiers_leave_less_than_the_output_limit_is_a_capacity_failure(self):
        batch = lines(20)
        limit = output_limit(batch)
        context = 12288 + limit - self.room(batch, 0, listed=3)
        self.assertTrue(self.room(batch, 0, context, 3) >= limit > self.room(batch, 0, context, 4) > output_estimate(batch))
        with self.assertRaises(AnalysisCapacityError):
            self.analyse(batch, required=4, context=context)
        self.assertEqual((0, 3, limit), self.analyse(batch, required=3, context=context))

    def test_single_target_is_cut_fully_when_even_an_empty_registry_leaves_less_than_the_output_limit(self):
        target = [source('a', '— Да, — сказал Иван.')]
        context = 12288 - self.room(target, 0) + 2000
        self.assertEqual((0, 0, 2000), self.analyse(target, context=context))
        self.assertTrue(output_estimate(target) < 2000 < output_limit(target))

    def test_loop_sends_the_size_of_the_mentioned_and_context_speaker_tiers_only_for_multi_target_batches(self):
        cast = [character(f'c{index}', f'Имя{index:02d}') for index in range(6)]
        context, first, second = source('x', 'Ночь.', ordinal=0), source('y', 'Имя01 вошёл.', ordinal=1), source('z', 'Имя02 сел.', ordinal=2)
        shared = state(cast=cast, accepted={context['id']: [{'start': 0, 'end': 5, 'speaker': 'c3'}]},
                       trusted=[{'speaker': 'c5', 'chapter_id': 'c1'}, {'speaker': 'c4', 'chapter_id': 'c1'}])
        payloads = []
        def call(payload):
            payloads.append(payload)
            return {'characters': [], 'passages': [narration(item) for item in payload['segments']]}
        analyse_sources([first, second], [context, first, second], shared, call, lambda *_: None)
        self.assertEqual((3, ['c1', 'c2', 'c3', 'c4', 'c5', 'c0']), (payloads[0]['required'], [item['id'] for item in payloads[0]['cast']]))
        analyse_sources([second], [context, first, second], state(cast=cast), call, lambda *_: None)
        self.assertNotIn('required', payloads[1])


class Descriptions(unittest.TestCase):
    def test_grammar_legal_echo_of_a_long_description_adds_only_each_new_detail(self):
        merged, details = STORED, ('Хромает на левую ногу.', 'Курит трубку.', 'Боится высоты.', 'Носит очки.')
        for detail in details:
            echo = f'{clip(registry([{"id": "x", "name": "X", "description": merged}])[0]["description"], 300 - len(detail) - 1)} {detail}'
            self.assertLessEqual(len(echo), 300)
            merged = merge_description(merged, echo)
        self.assertEqual('; '.join([STORED, *details]), merged)

    def test_short_description_echo_cut_by_the_length_bound_merges_only_its_tail(self):
        stored, detail = clip(STORED, 280), 'Носит круглые очки в тонкой оправе и всегда держит руки за спиной.'
        self.assertEqual(f'{stored}; {detail}', merge_description(stored, f'{clip(stored, 300 - len(detail) - 1)} {detail}'))

    def test_fewer_than_eight_shared_leading_words_are_not_an_echo(self):
        old = 'Один два три четыре пять шесть семь восемь девять десять.'
        self.assertEqual(f'{old}; Один два три четыре пять шесть семь иначе.', merge_description(old, 'Один два три четыре пять шесть семь иначе.'))
        self.assertEqual(f'{old}; иначе.', merge_description(old, 'Один два три четыре пять шесть семь восемь иначе.'))
        self.assertEqual('Старый друг Ивана; Старый друг Маши', merge_description('Старый друг Ивана', 'Старый друг Маши'))
        self.assertEqual(old, merge_description(old, 'Один два три четыре пять шесть семь восемь дев'))


class Names(unittest.TestCase):
    def test_given_names_of_the_name_table_are_never_book_vocabulary(self):
        book = ['Роман писал роман третий год. Этот роман о море, и роман почти готов.', '— Рома, иди обедать! — крикнула мать.',
                'Слава пришла поздно: слава любит терпеливых, а слава отца была громкой.', 'Незнакомец сел, и незнакомец молчал, а потом незнакомец ушёл.']
        common = common_words(book)
        self.assertTrue({'роман', 'слава'}.isdisjoint(common) and 'незнакомец' in common)
        self.assertTrue({'роман', 'рома', 'слава', 'вячеслав'} <= {*DIMINUTIVES, *(name for names in DIMINUTIVES.values() for name in names)})
        for old, new, expected in (('Роман', 'Рома', 'Роман'), ('Слава', 'Славик', 'Слава'), ('Незнакомец', 'Кирилл Комаров', 'Кирилл Комаров')):
            with self.subTest(old=old, new=new):
                self.assertEqual(expected, preferred_name(old, new, common))


class Timeouts(Library):
    def create(self, paragraphs):
        book = self.store.import_data(fb2(('Глава', paragraphs)), 'book.fb2')
        return self.store.create_generation(book['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                         'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False})['id']

    def resume(self, gid, slow=lambda payload: True):
        calls = []
        class Engine(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    calls.append(len(payload['segments']))
                    if slow(payload):
                        raise EngineTransportError(TIMEOUT, 'timeout')
                return super().call(method, payload, stop)
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: Engine())
        jobs.start(gid)
        jobs.thread.join(20)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(gid), calls

    @staticmethod
    def slowest(payload):
        return any('медленный' in item['text'] for item in payload['segments'])

    def test_passage_that_always_times_out_is_degraded_on_the_next_run(self):
        gid = self.create(['Он кивнул.', 'Она ушла.', 'Это медленный абзац.', 'Дверь закрылась.', 'Стало тихо.'])
        done, calls = self.resume(gid, self.slowest)
        sources = [item['id'] for item in done['segments']]
        self.assertEqual(('failed', TIMEOUT, [6, 3, 3, 1]), (done['status'], done['error'], calls))
        self.assertEqual(dict(zip(sources, [1, 1, 1, 3, 2, 2])), done['analysis']['timeouts'])
        done, calls = self.resume(gid, self.slowest)
        self.assertEqual(('review', '', [1, 1, 1]), (done['status'], done['error'], calls))
        self.assertEqual([{'source': sources[3], 'reason': TIMEOUT}], done['analysis']['degraded'])
        self.assertEqual(4, done['analysis']['timeouts'][sources[3]])
        slow = [item for item in done['segments'] if item['source_id'] == sources[3].rsplit('-', 2)[0]]
        self.assertEqual([('unknown', DEGRADED_HOLD, 'pending')], [(item['speaker'], item['warning'], item['status']) for item in slow])

    def test_stop_on_two_nested_batches_before_any_commit_is_resolved_on_the_next_run(self):
        gid = self.create(['Это медленный абзац.', *(f'Абзац номер {index}.' for index in range(7))])
        done, calls = self.resume(gid, self.slowest)
        sources = [item['id'] for item in done['segments']]
        self.assertEqual(('failed', [9, 4]), (done['status'], calls))
        self.assertEqual({'casting_schema': CASTING_SCHEMA, 'analysis_contract': ANALYSIS_CONTRACT, 'timeouts': dict(zip(sources, [2, 2, 2, 2, 1, 1, 1, 1, 1]))}, done['analysis'])
        done, calls = self.resume(gid, self.slowest)
        self.assertEqual(('review', [1, 1, 1, 1, 5]), (done['status'], calls))
        self.assertEqual([sources[1]], [item['source'] for item in done['analysis']['degraded']])

    def test_hanging_backend_still_stops_after_two_timeouts_in_a_row_on_a_later_run(self):
        gid = self.create(['Это медленный абзац.', *(f'Абзац номер {index}.' for index in range(7))])
        self.assertEqual([9, 4], self.resume(gid)[1])
        done, calls = self.resume(gid)
        self.assertEqual(('failed', TIMEOUT, [1, 1]), (done['status'], done['error'], calls))
        self.assertEqual(1, len(done['analysis']['degraded']))

    def test_loop_isolates_slow_targets_gives_them_one_attempt_and_lets_a_repeated_timeout_stop(self):
        targets = [source(f's{index}', 'Он кивнул.', ordinal=index) for index in range(4)]
        slow = targets[1]['id']
        for error, outcome in ((EngineTransportError(TIMEOUT, 'timeout'), None), (AppError(TIMEOUT, 'timeout'), TIMEOUT)):
            calls, commits = [], []
            def call(payload):
                ids = [item['id'] for item in payload['segments']]
                calls.append(ids)
                if slow in ids:
                    raise error
                return {'characters': [], 'passages': [narration(item) for item in payload['segments']]}
            with self.subTest(error=type(error).__name__):
                try:
                    analyse_sources(targets, targets, state(slow=frozenset({slow})), call, lambda done, parts, failed, key, *_: commits.append(failed))
                except AppError as raised:
                    self.assertEqual(outcome, str(raised))
                else:
                    self.assertIsNone(outcome)
                    self.assertEqual([[], [{'source': slow, 'reason': TIMEOUT}], []], commits)
                    self.assertEqual([[targets[0]['id']], [slow], [targets[2]['id'], targets[3]['id']]], calls)

    def test_second_timeout_in_a_row_is_marked_as_a_timeout(self):
        class Hanging:
            def __init__(self):
                self.process = Process()
            def call(self, method, payload, stop):
                self.process.code = -15
                raise EngineTransportError(TIMEOUT, 'timeout')
            def close(self):
                pass
        jobs = Jobs(None, self.root, engine_factory=lambda _: Hanging())
        jobs.engine = Hanging()
        with self.assertRaises(EngineTransportError):
            jobs._call('analyze', {})
        with self.assertRaises(AppError) as raised:
            jobs._call('analyze', {})
        self.assertEqual((AppError, 'timeout', TIMEOUT), (type(raised.exception), raised.exception.code, str(raised.exception)))


if __name__ == '__main__':
    unittest.main()
