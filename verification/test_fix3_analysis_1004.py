from __future__ import annotations

import importlib.util
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import no_structure, NEUTRAL_CONTEXT, SyntheticEngine, narration, pack_fixture
import worker
from rimsky import analysis
from rimsky import jobs as jobs_module
from rimsky.analysis import (PROMPT_MARGIN, SYSTEM_PROMPT, TEMPORARY_IDS, analyse_sources, analysis_schema, boundary_units, clip, identity,
                             common_words, context_target, merge_description, output_estimate, output_limit, preferred_name, review_analysis)
from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import AnalysisCapacityError, AppError, EngineTransportError, digest

TIMEOUT = 'Local inference exceeded its 1800-second operation limit.'
SPEC = importlib.util.spec_from_file_location('evaluate_speakers_fix3', Path(__file__).resolve().parents[1] / 'scripts' / 'evaluate_speakers.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def source(sid, text, start=0, ordinal=0):
    return {'id': f'{sid}-{start}-{start + len(text)}', 'source_id': sid, 'start': start, 'end': start + len(text), 'text': text,
            'ordinal': ordinal, 'chapter_id': 'c1'}


def part(first, last, speaker='narrator', delivery='neutral', *evidence):
    return {'first': first, 'last': last, 'speaker': speaker, 'delivery': delivery, 'evidence': list(evidence), 'context': dict(NEUTRAL_CONTEXT)}


def character(cid, name, *aliases, evidence=(), **fields):
    return {'id': cid, 'name': name, 'aliases': list(aliases), 'description': '', 'evidence': list(evidence), 'kind': 'person',
            'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}, **fields}


def fb2(*chapters):
    sections = ''.join(f'<section><title><p>{title}</p></title>' + ''.join(f'<p>{text}</p>' for text in paragraphs) + '</section>'
                       for title, paragraphs in chapters)
    return ('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>'
            '<genre>prose</genre><author><first-name>A</first-name><last-name>B</last-name></author><book-title>T</book-title><lang>ru</lang>'
            '</title-info></description><body>' + sections + '</body></FictionBook>').encode()


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class Library(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.pack = pack_fixture(self.root / 'pack')

    def run_book(self, raw, factory):
        book = self.store.import_data(raw, 'book.fb2')
        generation = self.store.create_generation(book['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                               'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False})
        jobs = Jobs(self.store, self.root, engine_factory=factory)
        jobs.start(generation['id'])
        jobs.thread.join(20)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])


class Timeouts(Library):
    def test_a_second_timeout_in_a_row_stops_while_any_reply_resets_the_streak(self):
        replies = [EngineTransportError(TIMEOUT, 'timeout'), 'ok', EngineTransportError(TIMEOUT, 'timeout'), AnalysisCapacityError('Too large.'),
                   EngineTransportError(TIMEOUT, 'timeout'), EngineTransportError(TIMEOUT, 'timeout')]
        engines = []
        class Scripted:
            def __init__(self):
                self.process = Process()
            def call(self, method, payload, stop):
                reply = replies.pop(0)
                if isinstance(reply, Exception):
                    self.process.code = -15 if reply.code == 'timeout' else None
                    raise reply
                return reply
            def close(self):
                pass
        def factory(_):
            engines.append(Scripted())
            return engines[-1]
        jobs = Jobs(None, self.root, engine_factory=factory)
        jobs.engine = factory(None)
        outcomes = []
        for _ in range(6):
            try:
                outcomes.append(jobs._call('analyze', {}))
            except AppError as error:
                outcomes.append((type(error).__name__, str(error)))
        self.assertEqual([('EngineTransportError', TIMEOUT), 'ok', ('EngineTransportError', TIMEOUT), ('AnalysisCapacityError', 'Too large.'),
                          ('EngineTransportError', TIMEOUT), ('AppError', TIMEOUT)], outcomes)
        self.assertEqual(4, len(engines))

    def test_a_hanging_backend_fails_the_generation_after_two_requests(self):
        calls = []
        class Hung(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    calls.append(len(payload['segments']))
                    raise EngineTransportError(TIMEOUT, 'timeout')
                return super().call(method, payload, stop)
        paragraphs = [f'— Реплика номер {index}, — сказал Иван. — Ещё одна.' for index in range(24)]
        done = self.run_book(fb2(('Глава', paragraphs)), lambda _: Hung())
        self.assertEqual(('failed', TIMEOUT, [13, 6]), (done['status'], done['error'], calls))
        self.assertEqual([], done['analysis'].get('degraded', []))

    def test_alias_comparisons_that_time_out_twice_in_a_row_fail_instead_of_continuing(self):
        engines = []
        class Hanging:
            def __init__(self):
                self.process = Process()
            def call(self, method, payload, stop):
                self.process.code = -15
                raise EngineTransportError(TIMEOUT, 'timeout')
            def close(self):
                pass
        def factory(_):
            engines.append(Hanging())
            return engines[-1]
        jobs = Jobs(None, self.root, engine_factory=factory)
        jobs.engine = factory(None)
        characters = [{'id': cid, 'name': name, 'aliases': [], 'evidence': [pid]}
                      for cid, name, pid in (('a', 'Иван', 'p1'), ('b', 'Иван Петров', 'p2'), ('c', 'Иван Сидоров', 'p3'))]
        texts = {'p1': 'Иван пришёл.', 'p2': 'Иван Петров ушёл.', 'p3': 'Иван Сидоров спал.'}
        with self.assertRaises(AppError) as raised:
            jobs._reconcile({}, characters, texts)
        self.assertEqual((AppError, TIMEOUT, 2), (type(raised.exception), str(raised.exception), len(engines)))

    def test_shared_loop_retries_a_single_target_timeout_once_before_degrading(self):
        targets = [source(f's{index}', 'Он кивнул.', ordinal=index) for index in range(3)]
        state = {'cast': [], 'accepted': {}, 'volume': Counter(), 'inputs': []}
        calls, commits = [], []
        def call(payload):
            ids = [item['id'] for item in payload['segments']]
            calls.append(ids)
            if len(ids) > 1 or ids == [targets[2]['id']]:
                raise EngineTransportError(TIMEOUT, 'timeout')
            if ids == [targets[1]['id']]:
                raise AnalysisCapacityError('Too large.')
            return {'characters': [], 'passages': [narration(item) for item in payload['segments']]}
        analyse_sources(targets, targets, state, call, lambda done, parts, failed, key, *_: commits.append(([item['id'] for item in done], failed, key)))
        first, second, third = (item['id'] for item in targets)
        self.assertEqual([[first, second, third], [first], [second, third], [second], [third], [third]], calls)
        self.assertEqual([([first], [], digest([first])), ([second], [{'source': second, 'reason': 'Too large.'}], digest([second])),
                          ([third], [{'source': third, 'reason': TIMEOUT}], digest([third]))], commits)
        self.assertEqual(3, len(state['inputs']))

    def test_output_limit_grows_with_the_estimate_beyond_the_floor(self):
        small, large = [source('a', 'Да.')], [source(f's{index}', '— Да! — Нет! — Ага. — Ну. — Всё.', ordinal=index) for index in range(20)]
        self.assertEqual((3072, 2 * output_estimate(large)), (output_limit(small), output_limit(large)))
        self.assertGreater(output_limit(large), 3072)


class Units(unittest.TestCase):
    def test_ascii_and_single_quotes_and_unspaced_dashes_split_speech_from_attribution(self):
        cases = {'"Wait," said Tom, "you forgot this."': ['"Wait," ', 'said Tom, ', '"you forgot this."'],
                 '‘I don’t know,’ he said. ‘Maybe tomorrow.’': ['‘I don’t know,’ ', 'he said. ', '‘Maybe tomorrow.’'],
                 '"Stop!" he cried.': ['"Stop!" ', 'he cried.'],
                 'He said, "Come here."': ['He said, ', '"Come here."'],
                 '"Стой!" — крикнул он.': ['"Стой!" ', '— крикнул он.'],
                 '«Стой!»— крикнул он.': ['«Стой!»', '— крикнул он.'],
                 '— Иди сюда,— сказал он.': ['— Иди сюда,', '— сказал он.'],
                 'Он спросил:— Ты кто?': ['Он спросил:', '— Ты кто?'],
                 'Tom’s dog isn’t here.': ['Tom’s dog isn’t here.'],
                 'Маша открыла книгу. — Читай, — сказал Иван.': ['Маша открыла книгу. ', '— Читай, ', '— сказал Иван.']}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, [text[start:end] for start, end in boundary_units(text)])

    def test_new_quote_and_dash_rules_keep_units_tiling(self):
        generator = random.Random(31004)
        pieces = ['аб', 'Да!', '—', '–', '-', ',', '.', '…', '?', ':', '«', '»', '“', '”', '„', '"', '‘', '’', 'Tom’s', ',—', ':—', '\n', 'x1', ' ', ' ']
        for _ in range(800):
            text = ''.join(generator.choice(pieces) for _ in range(generator.randint(1, 120)))
            units = boundary_units(text)
            with self.subTest(text=text):
                self.assertEqual(text, ''.join(text[start:end] for start, end in units))
                self.assertTrue(all(start < end for start, end in units) and len(units) <= 50)
                if len(units) > 1:
                    self.assertTrue(all(any(char.isalnum() for char in text[start:end]) for start, end in units))


class Speakers(unittest.TestCase):
    setUp = no_structure

    def test_schema_limits_speakers_and_character_ids_to_known_and_temporary_ids(self):
        target = source('a', '— Да, — сказал Иван.')
        schema = analysis_schema([target], [], ['char_a', 'narrator', 'char_b', 'char_a'])
        speaker = schema['properties']['passages']['items']['properties']['parts']['items']['properties']['speaker']
        record = schema['properties']['characters']['items']['properties']['id']
        self.assertEqual(['narrator', 'unknown', 'char_a', 'char_b', *TEMPORARY_IDS], speaker['enum'])
        self.assertEqual(['char_a', 'char_b', *TEMPORARY_IDS], record['enum'])
        self.assertEqual((40, 'new_1', 'new_40'), (len(TEMPORARY_IDS), TEMPORARY_IDS[0], TEMPORARY_IDS[-1]))
        self.assertIn('new_1, new_2', SYSTEM_PROMPT)

    def test_worker_constrains_speakers_to_the_projection_and_context_speakers(self):
        model = Mock()
        model.tokenize.return_value = [1] * 100
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"characters": [], "passages": []}'}, 'finish_reason': 'stop'}]}
        accepted = {'z-0-11': [{'start': 0, 'end': 6, 'speaker': 'char_b'}, {'start': 6, 'end': 11, 'speaker': 'unknown'}]}
        payload = {'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': [source('a', '— Да, — сказал Иван.')],
                   'cast': [character('char_a', 'Иван')], 'before': [context_target(source('z', '— Да. Ночь.'), accepted)], 'after': []}
        with patch.object(worker, 'load_text', return_value=model):
            worker.analyze(payload)
        schema = model.create_chat_completion.call_args.kwargs['response_format']['schema']
        speaker = schema['properties']['passages']['items']['properties']['parts']['items']['properties']['speaker']['enum']
        self.assertEqual(['narrator', 'unknown', 'char_a', 'char_b', 'new_1'], speaker[:5])

    def test_trivial_speaker_slips_resolve_to_ids(self):
        target = source('a', '— Да, — сказал Иван.')
        ivan = character('char_aaaaaaaaaaaaaaaa', 'Иван')
        def resolved(speaker, cast=(ivan,), characters=()):
            result = {'characters': list(characters), 'passages': [{'id': target['id'], 'parts': [part(0, 1, speaker, 'neutral', target['id'])]}]}
            parts, _, invalid = review_analysis(result, [target], list(cast))
            return parts[0]['speaker'] if parts else invalid[target['id']]
        for slip, expected in (('Narrator', 'narrator'), ('UNKNOWN', 'unknown'), ('CHAR_AAAAAAAAAAAAAAAA', ivan['id']), ('Иван', ivan['id']),
                               ('иван', ivan['id'])):
            with self.subTest(slip=slip):
                self.assertEqual(expected, resolved(slip))
        created = resolved('NEW_1', characters=[character('new_1', 'Иван', evidence=[target['id']])])
        self.assertTrue(created.startswith('char_') and created != ivan['id'])
        ambiguous = resolved('Иван', (ivan, character('char_bbbbbbbbbbbbbbbb', 'Иван')))
        self.assertEqual(f"Passage {target['id']}: speaker 'Иван' is not narrator, unknown, a known character ID or a new_ ID listed in characters.", ambiguous)


class Registry(unittest.TestCase):
    setUp = no_structure

    def analyse(self, cast, tokenize):
        model = Mock()
        model.tokenize.side_effect = tokenize
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"characters": [], "passages": []}'}, 'finish_reason': 'stop'}]}
        payload = {'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': [source('a', '— Да, — сказал Иван.')], 'cast': cast,
                   'before': [], 'after': []}
        with patch.object(worker, 'load_text', return_value=model):
            worker.analyze(payload)
        return model

    def test_descriptions_are_cut_to_the_largest_relevance_prefix_that_fits_and_the_rest_stay_listed(self):
        cast = [character(f'char_{index:02d}', f'Имя{index:02d}', description='очень длинное описание персонажа ' * 9) for index in range(40)]
        measure = lambda data: [1] * (len(data) // 4)
        model = self.analyse(cast, measure)
        options = model.create_chat_completion.call_args.kwargs
        content = json.loads(options['messages'][1]['content'])
        kept = len(content['known_characters'])
        limit = output_limit([source('a', '— Да, — сказал Иван.')])
        def room(count):
            raw = json.dumps({**content, 'known_characters': cast[:count], 'more_known_characters': [identity(item) for item in cast[count:]]},
                             ensure_ascii=False)
            return 8192 - len(measure((SYSTEM_PROMPT + raw).encode())) - PROMPT_MARGIN
        self.assertTrue(0 < kept < 40)
        self.assertEqual(cast[:kept], content['known_characters'])
        self.assertTrue(room(kept) >= limit > room(kept + 1))
        self.assertEqual(3072, options['max_tokens'])
        speaker = options['response_format']['schema']['properties']['passages']['items']['properties']['parts']['items']['properties']['speaker']['enum']
        self.assertEqual([identity(item) for item in cast[kept:]], content['more_known_characters'])
        self.assertEqual([item['id'] for item in cast], speaker[2:42])
        self.assertEqual('new_1', speaker[42])

    def test_capacity_failure_only_when_even_an_empty_registry_does_not_fit(self):
        model = Mock()
        model.tokenize.return_value = [1] * 8000
        payload = {'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': [source('a', 'Да.')],
                   'cast': [character(f'char_{index:02d}', 'Имя') for index in range(40)]}
        with patch.object(worker, 'load_text', return_value=model), self.assertRaises(AnalysisCapacityError):
            worker.analyze(payload)
        self.assertLessEqual(model.tokenize.call_count, 8)
        model.create_chat_completion.assert_not_called()


class Ghosts(Library):
    def test_partially_valid_reply_keeps_only_record_updates_supported_by_valid_targets(self):
        first, second = source('a', 'Маша кивнула.', ordinal=0), source('b', '— Стой, — сказал Пётр.', ordinal=1)
        cast = [character('ivan', 'Иван'), character('masha', 'Маша')]
        result = {'characters': [character('new_1', 'Пётр', evidence=[second['id']], description='Строгий'),
                                 character('ivan', 'Иван', evidence=[second['id']], gender={'value': 'male', 'evidence': [second['id']]}),
                                 character('masha', 'Маша', evidence=[first['id']], description='Рыжая')],
                  'passages': [narration(first), {'id': second['id'], 'parts': [part(1, 1)]}]}
        parts, updated, invalid = review_analysis(result, [first, second], cast)
        self.assertEqual(([first['id']], [second['id']]), ([item['id'] for item in parts], list(invalid)))
        self.assertEqual(['ivan', 'masha'], [item['id'] for item in updated])
        self.assertEqual(('unknown', 'Рыжая'), (updated[0]['gender']['value'], updated[1]['description']))

    def test_valid_target_speaking_through_a_new_record_keeps_it_and_a_record_with_neither_is_dropped(self):
        first, second = source('a', '— Да, — сказал Пётр.', ordinal=0), source('b', 'Пётр ушёл.', ordinal=1)
        result = {'characters': [character('new_1', 'Пётр', evidence=[second['id']])],
                  'passages': [{'id': first['id'], 'parts': [part(0, 0, 'new_1', 'neutral', first['id']), part(1, 1)]},
                               {'id': second['id'], 'parts': [part(1, 0)]}]}
        parts, updated, invalid = review_analysis(result, [first, second], [])
        [petr] = updated
        self.assertEqual(('Пётр', [first['id']], [petr['id'], 'narrator']), (petr['name'], petr['evidence'], [item['speaker'] for item in parts]))
        self.assertEqual([second['id']], list(invalid))
        self.assertIn('cover units', invalid[second['id']])
        result['passages'][0]['parts'][0]['evidence'] = []
        parts, updated, invalid = review_analysis(result, [first, second], [])
        self.assertEqual(([], []), (parts, updated))
        self.assertIn(f'must cite its own passage ID {first["id"]}', invalid[first['id']])

    def test_degraded_passage_leaves_no_character_behind(self):
        class Broken(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'analyze':
                    return super().call(method, payload, stop)
                passages, characters = [], []
                for item in payload['segments']:
                    if 'Пётр' not in item['text']:
                        passages.append(narration(item))
                        continue
                    characters.append(character('new_1', 'Пётр', evidence=[item['id']], gender={'value': 'male', 'evidence': [item['id']]}))
                    passages.append({'id': item['id'], 'parts': [part(0, 0, 'new_1', 'firm', item['id']),
                                                                 part(2, len(boundary_units(item['text'])) - 1, 'new_1', 'neutral', item['id'])]})
                return {'characters': characters, 'passages': passages}
        done = self.run_book(fb2(('Глава', ['Маша вошла в комнату.', '— Стой, — сказал Пётр. — Не ходи туда.', 'Она остановилась.'])),
                             lambda _: Broken())
        self.assertEqual('review', done['status'], done['error'])
        self.assertEqual(['narrator'], [item['id'] for item in done['cast']])
        self.assertEqual(['c0001-p00003-0-38'], [item['source'] for item in done['analysis']['degraded']])
        self.assertEqual({'narrator', 'unknown'}, {item['speaker'] for item in done['analysis']['casting_inputs']})


class Names(Library):
    def test_descriptor_written_mostly_in_lowercase_yields_to_a_revealed_name(self):
        vocabulary = common_words(['Вошёл незнакомец, а за ним старик. Незнакомец сел, и незнакомец молчал.', 'Из рации донёсся голос.', 'the stranger came',
                                   'Вера кивнула. — Вера, иди! — сказала мама. Его вера крепка.'])
        self.assertTrue({'незнакомец', 'старик', 'голос', 'the'} <= vocabulary and 'вера' not in vocabulary)
        for old, new, expected in (('Незнакомец', 'Кирилл Комаров', 'Кирилл Комаров'), ('Голос из рации', 'Артёмов', 'Артёмов'),
                                   ('Старик', 'Иван Петрович', 'Иван Петрович'), ('The stranger', 'John Smith', 'John Smith'),
                                   ('Кирилл Комаров', 'Незнакомец', 'Кирилл Комаров'), ('Иван', 'Ваня', 'Иван'), ('Анна', 'Анна Петровна', 'Анна Петровна'),
                                   ('Вера', 'Верочка', 'Вера')):
            with self.subTest(old=old, new=new):
                self.assertEqual(expected, preferred_name(old, new, vocabulary))
        self.assertEqual('Незнакомец', preferred_name('Незнакомец', 'Кирилл Комаров'))

    def test_jobs_rename_a_descriptor_with_the_book_vocabulary(self):
        class Reveal(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'analyze':
                    return super().call(method, payload, stop)
                passages, characters = [], []
                stranger = next((item['id'] for item in payload['cast'] if item['name'] == 'Незнакомец'), None)
                for item in payload['segments']:
                    if 'сказал незнакомец' in item['text']:
                        characters.append(character('new_1', 'Незнакомец', evidence=[item['id']]))
                        passages.append({'id': item['id'], 'parts': [part(0, 0, 'new_1', 'neutral', item['id']), part(1, 1)]})
                        continue
                    if 'Кирилл Комаров' in item['text'] and stranger:
                        characters.append(character(stranger, 'Кирилл Комаров', evidence=[item['id']]))
                    passages.append(narration(item))
                return {'characters': characters, 'passages': passages}
        done = self.run_book(fb2(('Первая', ['— Привет, — сказал незнакомец.']), ('Вторая', ['Незнакомца звали Кирилл Комаров.'])),
                             lambda _: Reveal())
        self.assertEqual('review', done['status'], done['error'])
        [person] = [item for item in done['cast'] if item['id'] != 'narrator']
        self.assertEqual(('Кирилл Комаров', ['Незнакомец']), (person['name'], person['aliases']))


class Descriptions(unittest.TestCase):
    def test_echoed_registry_description_adds_only_the_new_detail(self):
        stored = ('Высокий худой человек в длинном сером плаще, с аккуратно подстриженной седой бородой и внимательными серыми глазами, '
                  'который всегда носит с собой потёртый кожаный портфель и старинные карманные часы на цепочке; говорит медленно и негромко, '
                  'часто делает паузы, словно подбирая слова, и никогда не повышает голос.')
        self.assertGreater(len(stored), 300)
        merged = stored
        for detail in ('Хромает на левую ногу.', 'Курит трубку.', 'Боится высоты.'):
            merged = merge_description(merged, f'{clip(merged, 300)} {detail}')
        self.assertEqual(f'{stored}; Хромает на левую ногу.; Курит трубку.; Боится высоты.', merged)
        self.assertEqual(stored, merge_description(stored, clip(stored, 300)))

    def test_short_descriptions_keep_the_existing_merge_rules(self):
        self.assertEqual('Высокий. Хромает.', merge_description('Высокий.', 'Высокий. Хромает.'))
        self.assertEqual('Старый друг Ивана; Старый друг Маши', merge_description('Старый друг Ивана', 'Старый друг Маши'))


class SharedLoop(Library):
    def test_jobs_and_the_evaluation_harness_run_the_same_analysis_loop(self):
        self.assertIs(analysis.analyse_sources, jobs_module.analyse_sources)
        self.assertIs(analysis.analyse_sources, harness.analyse_sources)
        self.assertFalse(hasattr(harness, 'review_analysis') or hasattr(harness, 'batches'))
        with patch.object(jobs_module, 'analyse_sources', wraps=analyse_sources) as shared:
            done = self.run_book(fb2(('Первая', ['Он кивнул.']), ('Вторая', ['Она ушла.'])), lambda _: SyntheticEngine())
        self.assertEqual('review', done['status'], done['error'])
        self.assertEqual(['c0001', 'c0002'], [call.args[0][0]['chapter_id'] for call in shared.call_args_list])
        case = {'id': 'c1-p1', 'chapter_id': 'c1', 'source_id': 'c1-p1', 'text': 'Он кивнул.', 'before': [], 'after': []}
        engine = SyntheticEngine()
        jobs = Jobs(None, self.root, engine_factory=lambda _: engine)
        jobs.engine = engine
        with patch.object(harness, 'analyse_sources', wraps=analyse_sources) as shared:
            state = harness.predict([case], jobs, {'pack': self.pack, 'device': 'cpu'}, reconcile=False)
        self.assertEqual((1, ['c1-p1'], 1), (shared.call_count, state['done'], engine.analyzed))


if __name__ == '__main__':
    unittest.main()
