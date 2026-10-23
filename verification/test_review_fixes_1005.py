from __future__ import annotations

from pathlib import Path
import sys
import json
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_analysis_protocol_1004 import character, narration, part, source, stems
from test_casting_traits import line, narrator, person
from test_core import no_structure, SyntheticEngine
from test_render_1004 import Base, tone
from rimsky.analysis import SYSTEM_PROMPT, UNRESOLVED_HOLD, analyse_sources, identity, merge_character, review_analysis
from rimsky.casting import cast_summary, cast_voices, conflict_graph, dialogue_contexts
from rimsky.jobs import IMPLAUSIBLE, WORKER_FAILED, alias_candidates
from rimsky.util import EngineTransportError, signal_group
import worker


def ivan(**fields):
    return {'id': 'char_ivan', 'name': 'Иван', 'aliases': [], 'description': '', 'evidence': ['old'], 'kind': 'person', **fields}


class Labels(unittest.TestCase):
    def reply(self, record, targets, context=()):
        return review_analysis({'characters': [record], 'passages': [narration(target) for target in targets]}, targets, [ivan()], list(context), names=stems)

    def test_known_character_gains_a_label_only_from_its_cited_passages(self):
        target, context = source('a', '— Да, — сказал Иван.'), source('b', 'Пётр вошёл.', ordinal=1)
        record = character('char_ivan', 'Иван', 'Пётр', evidence=[target['id']])
        self.assertEqual([], self.reply(record, [target], [context])[1][0]['aliases'])
        stored = self.reply({**record, 'evidence': [target['id'], context['id']]}, [target], [context])[1][0]
        self.assertEqual((['Пётр'], {'Иван': [target['id']], 'Пётр': [context['id']]}), (stored['aliases'], stored['labels']))

    def test_new_character_name_must_occur_in_a_cited_passage(self):
        target = source('a', '— Стой, — сказал охранник.')
        result = {'characters': [character('new_1', 'Джон Смит', 'Анна', evidence=[target['id']])],
                  'passages': [{'id': target['id'], 'parts': [part(0, 0, 'new_1', 'neutral', target['id']), part(1, 1)]}]}
        self.assertIn('needs a name or alias that occurs in a passage it cites', review_analysis(result, [target], [], names=stems)[2][target['id']])
        result['characters'][0]['aliases'] = ['Охранник', 'Анна']
        parts, cast, invalid = review_analysis(result, [target], [], names=stems)
        self.assertEqual(({}, 'Охранник', []), (invalid, cast[0]['name'], cast[0]['aliases']))

    def test_new_character_alias_owned_by_another_character_is_dropped(self):
        target = source('a', '— Иван, стой, — сказал охранник.')
        result = {'characters': [character('new_1', 'охранник', 'Иван', evidence=[target['id']])], 'passages': [narration(target)]}
        new = review_analysis(result, [target], [ivan()], names=stems)[1][1]
        self.assertEqual(('охранник', []), (new['name'], new['aliases']))


class Evidence(unittest.TestCase):
    setUp = no_structure

    def test_repeated_trait_counts_only_passages_that_name_or_voice_the_character(self):
        named, unrelated = source('a', 'Иван кивнул.'), source('b', 'Она ушла.', ordinal=1)
        known = ivan(gender={'value': 'male', 'support': {'male': ['old']}})
        def gender(*cited):
            record = character('char_ivan', 'Иван', evidence=list(cited), gender={'value': 'male', 'evidence': list(cited)})
            return review_analysis({'characters': [record], 'passages': [narration(named), narration(unrelated)]}, [named, unrelated], [known],
                                   names=stems)[1][0]['gender']['support']
        self.assertEqual({'male': ['old', named['id']]}, gender(named['id'], unrelated['id']))
        self.assertEqual({'male': ['old']}, gender(unrelated['id']))

    def test_voiced_passage_supports_traits_and_kind(self):
        target = source('a', '— Готов, — раздалось из динамика.')
        record = {**character('char_ivan', 'Иван', evidence=[target['id']], gender={'value': 'male', 'evidence': [target['id']]}), 'kind': 'mechanical'}
        reply = {'characters': [record], 'passages': [{'id': target['id'], 'parts': [part(0, 0, 'char_ivan', 'neutral', target['id']), part(1, 1)]}]}
        stored = review_analysis(reply, [target], [ivan(kind_votes={})], names=stems)[1][0]
        self.assertEqual(({'male': [target['id']]}, {'mechanical': [target['id']]}), (stored['gender']['support'], stored['kind_support']))
        self.assertEqual('mechanical', stored['kind'])

    def test_registry_sent_to_the_model_carries_no_traits_or_kind(self):
        target = source('a', 'Иван кивнул.')
        payloads = []
        def call(payload):
            payloads.append(payload)
            return {'characters': [], 'passages': [narration(target)]}
        state = {'cast': [ivan(gender={'value': 'male', 'support': {'male': ['old']}})], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources([target], [target], state, call, lambda *arguments: None, names=stems)
        self.assertEqual([{'id', 'name', 'aliases', 'description'}], [set(entry) for entry in payloads[0]['cast']])

    def test_aliases_that_match_the_request_come_first_and_stay_in_compact_identities(self):
        target = source('a', 'Иваныч и Ванюша кивнули.')
        payloads = []
        def call(payload):
            payloads.append(payload)
            return {'characters': [], 'passages': [narration(target)]}
        known = {**ivan(), 'aliases': ['Ваня', 'Иван Петрович', 'Петрович', 'Иваныч', 'Ванюша']}
        state = {'cast': [known], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources([target], [target], state, call, lambda *arguments: None)
        [entry] = payloads[0]['cast']
        self.assertEqual((['Иваныч', 'Ванюша', 'Ваня', 'Иван Петрович', 'Петрович'], {'char_ivan': 2}), (entry['aliases'], payloads[0]['matched_aliases']))
        self.assertEqual(['Иваныч', 'Ванюша', 'Ваня'], identity(entry, 2)['aliases'])
        self.assertEqual(['Иваныч', 'Ванюша', 'Ваня', 'Иван Петрович'], identity(entry, 4)['aliases'])

    def test_matched_aliases_beyond_the_detailed_cap_survive_serialization(self):
        aliases = [f'Кличка{index:02d}' for index in range(20)]
        target = source('a', ' '.join(aliases[:15]) + '.')
        payloads = []
        def call(payload):
            payloads.append(payload)
            return {'characters': [], 'passages': [narration(target)]}
        state = {'cast': [{**ivan(), 'aliases': [*aliases[15:], *aliases[:15]]}], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources([target], [target], state, call, lambda *arguments: None)
        [entry] = payloads[0]['cast']
        self.assertEqual((aliases[:15], 15), (entry['aliases'], payloads[0]['matched_aliases']['char_ivan']))
        self.assertEqual(aliases[:15], identity(entry, 15)['aliases'])

    def test_reply_using_an_id_the_worker_pruned_is_rejected(self):
        targets = [source(f's{index}', f'— Реплика {index}, — сказал он.', ordinal=index) for index in range(2)]
        cast = [{**ivan(), 'id': f'char_{index:02d}', 'name': f'Имя{index:02d}', 'description': 'очень длинное описание ' * 12} for index in range(40)]
        sent = []
        def complete(messages, response_format, temperature, max_tokens):
            content = json.loads(messages[1]['content'])
            listed = [item['id'] for item in content['known_characters'] + content.get('more_known_characters', [])]
            sent.append(listed)
            speaker = 'char_39'
            passages = [{'id': target['id'], 'parts': [part(0, 0, speaker, 'neutral', target['id']), part(1, 1)]} for target in content['targets']]
            return {'choices': [{'message': {'content': json.dumps({'characters': [], 'passages': passages})}, 'finish_reason': 'stop'}]}
        def call(payload):
            model = Mock()
            model.tokenize.side_effect = lambda data: [1] * len(data)
            model.create_chat_completion.side_effect = complete
            with patch.object(worker, 'load_text', return_value=model):
                return worker.analyze({'pack': {'text_model': {'context': len(SYSTEM_PROMPT) + 5030, 'analysis_mode': 'legacy'}}, 'device': 'cpu', **payload})
        state = {'cast': cast, 'accepted': {}, 'volume': {f'char_{index:02d}': 40 - index for index in range(40)}, 'inputs': []}
        committed = []
        analyse_sources(targets, targets, state, call, lambda done, parts, failed, key, *_: committed.append((parts, failed)))
        self.assertNotIn('char_39', sent[0])
        speakers = {part['speaker'] for parts, _ in committed for part in parts}
        self.assertNotIn('char_39', speakers)
        reasons = [item['reason'] for _, failed in committed for item in failed]
        self.assertTrue(reasons and all("'char_39'" in reason for reason in reasons), reasons)

    def test_retention_cap_keeps_a_supporting_passage_for_every_label_and_trait(self):
        old = {**ivan(), 'evidence': [f'e{index}' for index in range(60)], 'labels': {'Иван': ['e55']},
               'gender': {'value': 'male', 'support': {'male': ['e57']}}, 'kind_support': {'person': ['e58']}}
        merged = merge_character(old, {'name': 'Иван', 'aliases': ['Ваня'], 'labels': {'Ваня': ['e59']}, 'evidence': ['e59']})
        self.assertEqual(50, len(merged['evidence']))
        self.assertTrue({'e55', 'e57', 'e58', 'e59'} <= set(merged['evidence']))
        self.assertEqual({'Иван': ['e55'], 'Ваня': ['e59']}, merged['labels'])


class PrefixCandidates(unittest.TestCase):
    def pairs(self, *names):
        characters = [{'id': f'c{index}', 'name': name, 'aliases': []} for index, name in enumerate(names)]
        labels = {character['id']: character['name'] for character in characters}
        return [(labels[one], labels[two]) for one, two in alias_candidates(characters)]

    def test_patronymics_never_pair_with_the_father_s_name(self):
        for name, patronymic in (('Иван', 'Мария Ивановна'), ('Пётр', 'Анна Петровна'), ('Сергей', 'Ольга Сергеевна'),
                                 ('Николай', 'Ирина Николаевна'), ('Илья', 'Анна Ильинична')):
            with self.subTest(name=name):
                self.assertEqual([], self.pairs(name, patronymic))

    def test_prefix_only_pairs_come_after_every_other_candidate(self):
        found = self.pairs('Иван', 'Иванов', 'Саша', 'Александр', 'Fran', 'Frank', 'Анна', 'Анна Петровна', 'Комар', 'Кирилл Комаров')
        loose = [pair for pair in found if pair in {('Иван', 'Иванов'), ('Fran', 'Frank'), ('Комар', 'Кирилл Комаров')}]
        self.assertEqual(3, len(loose))
        self.assertEqual({('Саша', 'Александр'), ('Анна', 'Анна Петровна')}, set(found[:len(found) - len(loose)]))
        self.assertEqual(len(found), len(set(found)))


class DialogueWindow(unittest.TestCase):
    def parts(self, *speakers):
        return [line(speaker, f'p{index}', index) for index, speaker in enumerate(speakers)]

    def test_speakers_far_apart_in_one_run_do_not_conflict(self):
        parts = self.parts('x', 'z', 'w', 'z', 'w', 'z', 'w', 'y')
        [context] = dialogue_contexts(parts)
        self.assertEqual(['w', 'x', 'y', 'z'], context['speakers'])
        neighbours = conflict_graph([context], [person(cid) for cid in 'xyzw'])
        self.assertEqual((0, 1, 1), (neighbours['x']['y'], neighbours['x']['z'], neighbours['w']['y']))
        self.assertEqual(1, conflict_graph(dialogue_contexts(self.parts('x', 'z', 'w', 'z', 'w', 'y')), [person(cid) for cid in 'xyzw'])['x']['y'])

    def test_one_narrator_paragraph_inside_a_run_counts_toward_the_window(self):
        parts = self.parts('x', 'z', 'w', 'z', 'narrator', 'y')
        self.assertEqual(1, conflict_graph(dialogue_contexts(parts), [person(cid) for cid in 'xyzw'])['x']['y'])
        parts = self.parts('x', 'z', 'w', 'z', 'w', 'narrator', 'y')
        self.assertEqual(0, conflict_graph(dialogue_contexts(parts), [person(cid) for cid in 'xyzw'])['x']['y'])


class UnknownGenderNotice(unittest.TestCase):
    voices = [narrator(), {'id': 'f', 'speaker': 'F', 'gender': 'female'}, {'id': 'm', 'speaker': 'M', 'gender': 'male'}]

    def codes(self, characters, parts, cast=None):
        cast = cast or cast_voices(characters, self.voices, parts)
        return {notice['code']: notice['params'].get('names') for notice in cast_summary(cast, self.voices, parts)['notices']}

    def test_without_known_gender_speech_the_notice_does_not_claim_a_balance(self):
        parts = [line('u', 'p1', 0), line('v', 'p5', 5)]
        codes = self.codes([person('u'), person('v')], parts)
        self.assertEqual(['u', 'v'], codes['unknown_gender_unbalanced'])
        self.assertNotIn('unknown_gender_speakers', codes)

    def test_with_known_gender_speech_the_balance_notice_remains(self):
        parts = [line('u', 'p1', 0), line('k', 'p5', 5)]
        codes = self.codes([person('u'), person('k', gender='female')], parts)
        self.assertEqual(['u'], codes['unknown_gender_speakers'])
        self.assertNotIn('unknown_gender_unbalanced', codes)

    def test_cast_saved_before_gender_targets_keeps_the_earlier_notice(self):
        parts = [line('u', 'p1', 0)]
        cast = [{key: value for key, value in entry.items() if key != 'gender_target'} for entry in cast_voices([person('u')], self.voices, parts)]
        self.assertIn('unknown_gender_speakers', self.codes([], parts, cast))


class ProcessGroups(unittest.TestCase):
    def test_zombie_only_group_counts_as_gone(self):
        for error in (ProcessLookupError(), PermissionError(1, 'Operation not permitted')):
            with self.subTest(error=type(error).__name__), patch('os.killpg', side_effect=error, create=True):
                self.assertFalse(signal_group(1234, 0))
        with patch('os.killpg', create=True) as killpg:
            self.assertTrue(signal_group(1234, 15))
        killpg.assert_called_once_with(1234, 15)

    def test_platform_without_process_groups_reports_no_group(self):
        with patch('os.killpg', None, create=True):
            self.assertFalse(signal_group(1234, 15))


class WorkerFailures(Base):
    def test_worker_stalled_during_an_implausible_retake_is_replaced_before_the_next_passage(self):
        generation = self.generation()
        target = generation['segments'][1]['spoken']
        engines, dead_calls = [], []
        class Stalling(SyntheticEngine):
            dead = False
            def call(self, method, payload, stop):
                if method == 'render' and self.dead:
                    dead_calls.append(payload['text'])
                    raise EngineTransportError('Local inference process exited.', 'exit')
                if method == 'render' and payload['text'] == target:
                    if len(engines) == 1 and not getattr(self, 'long', False):
                        self.long = True
                        return tone(Path(payload['output']), 120)
                    self.dead = True
                    raise EngineTransportError('Local inference timed out.', 'timeout')
                return super().call(method, payload, stop)
        def factory(_):
            engines.append(Stalling())
            return engines[-1]
        _, done = self.run_job(generation, factory)
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(([], 2), (dead_calls, len(engines)))
        retaken = next(segment for segment in done['segments'] if segment['spoken'] == target)
        self.assertIn(IMPLAUSIBLE, retaken['warning'])

    def test_worker_that_stops_twice_is_not_reported_as_a_text_problem(self):
        generation = self.generation()
        target = generation['segments'][1]['spoken']
        class Exiting(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render' and payload['text'] == target:
                    raise EngineTransportError('Local inference process exited.', 'exit')
                return super().call(method, payload, stop)
        _, failed = self.run_job(generation, lambda _: Exiting())
        self.assertEqual('failed', failed['status'])
        self.assertTrue(failed['error'].startswith(WORKER_FAILED), failed['error'])
        self.assertEqual([], [segment['id'] for segment in failed['segments'] if segment['status'] == 'failed'])


if __name__ == '__main__':
    unittest.main()
