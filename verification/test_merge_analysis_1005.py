from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).parent))
from rimsky.analysis import (DEGRADED_HOLD, analysis_schema, offered_reply, provider_schema, rejected_answer, review_analysis,
                             synthesis_units, validate_analysis)
from rimsky.boundaries import boundary_units, structure_chunks, unit_offsets, unit_range
from rimsky.jobs import Jobs, render_parts, speaker_review_holds
from rimsky.speech_structure import structure_problem
from rimsky.staged_analysis import staged_proposal_with_details, unit_answer
from rimsky.store import Store
from rimsky.util import AppError, GenerationCapError
from test_core import FB2, NEUTRAL_CONTEXT, SyntheticEngine, VariedInvalidEngine, narration, pack_fixture, unit_parts, write_wave


def source(text, ident='p1', ordinal=0):
    return {'id': ident, 'source_id': ident, 'start': 0, 'end': len(text), 'text': text, 'ordinal': ordinal, 'chapter_id': 'c', 'kind': 'paragraph'}


def context(mood='neutral', evidence=()):
    return {**NEUTRAL_CONTEXT, 'mood': mood, 'evidence': list(evidence)}


HERO = {'id': 'hero', 'name': 'Иван', 'aliases': [], 'description': '', 'evidence': ['p1']}


class SharedBoundaries(unittest.TestCase):
    def test_units_keep_every_structural_and_sentence_candidate(self):
        text = ' '.join(f'Предложение {index}.' for index in range(80)) + ' — Да, — сказал он. «Нет», — ответила она.'
        units = boundary_units(text)
        self.assertEqual(''.join(text[start:end] for start, end in units), text)
        self.assertGreater(len(units), 80)
        cuts = {start for start, _ in units}
        for chunk in structure_chunks(text):
            self.assertIn(chunk['start'], cuts)

    def test_master_and_feature_dash_rules_are_a_union(self):
        text = 'Захотел спать — спи прямо в игре. Ответ, - сказал он.'
        starts = {start for start, _ in boundary_units(text)}
        self.assertIn(text.index('—'), starts)
        self.assertIn(text.index('- сказал'), starts)
        self.assertNotIn(text.index('—') + 1, starts)

    def test_combining_marks_do_not_turn_an_in_word_dash_into_a_boundary(self):
        for text in ('Кто\u0301—то пришёл.', 'Те\u0301ст–пример.'):
            with self.subTest(text=text):
                self.assertEqual(((0, len(text)),), boundary_units(text))

    def test_unit_ranges_round_trip_exact_unicode_text(self):
        text = '— Ёжик 🙂, — сказала Ася. — Ну?'
        for start, end in boundary_units(text):
            first, last = unit_range(text, start, end)
            self.assertEqual((start, end), unit_offsets(text, first, last))
        with self.assertRaises(AppError):
            unit_range(text, 1, len(text))


class StrictMergedValidation(unittest.TestCase):
    def test_parts_need_context_and_unknown_fields_are_rejected(self):
        target = source('Просто текст.')
        missing = {'characters': [], 'passages': [{'id': 'p1', 'parts': [{'first': 0, 'last': 0, 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}]}]}
        with self.assertRaisesRegex(AppError, 'misses context'):
            validate_analysis(missing, [target], [])

    def test_context_quote_must_be_exact_text_of_a_supplied_passage(self):
        target = source('Он тихо вздохнул.')
        bad = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(target['text'], [
            {'start': 0, 'end': len(target['text']), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': [],
             'context': context('tired', [{'id': 'p1', 'quote': 'громко крикнул'}])}])}]}
        with self.assertRaisesRegex(AppError, 'Passage p1: units 0 to 0: Context evidence must be an exact quote'):
            validate_analysis(bad, [target], [])

    def test_unsent_character_ids_are_rejected_even_after_repair(self):
        text = '— Да, — сказал Иван.'
        target = source(text)
        cut = text.index('— сказал')
        reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        _, _, invalid = review_analysis(reply, [target], [HERO], offered=[])
        self.assertIn('p1', invalid)
        parts, _, invalid = review_analysis(reply, [target], [HERO], offered=['hero'])
        self.assertFalse(invalid)
        self.assertEqual(['hero', 'narrator'], [part['speaker'] for part in parts])

    def test_neutral_delivery_is_upgraded_only_with_own_target_context_evidence(self):
        text = '— Ура! — сказал Иван.'
        cut = text.index('— сказал')
        def reply(evidence_id):
            return {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
                {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1'],
                 'context': context('excited', [{'id': evidence_id, 'quote': 'Ура'}])},
                {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        before = source('Ура, кричали все.', 'p0')
        parts, _ = validate_analysis(reply('p1'), [source(text)], [HERO], [before])
        self.assertEqual('excited', parts[0]['delivery'])
        parts, _ = validate_analysis(reply('p0'), [source(text)], [HERO], [before])
        self.assertEqual('neutral', parts[0]['delivery'])

    def test_fixed_structure_contradictions_are_named_per_passage(self):
        text = '— Кто там? — Я.'
        target = source(text)
        fixed = [{**chunk, 'mode': 'speech'} for chunk in structure_chunks(text)]
        merged = {'id': 'p1', 'parts': unit_parts(text, [{'start': 0, 'end': len(text), 'speaker': 'unknown', 'delivery': 'neutral', 'evidence': []}])}
        self.assertIn('separate', structure_problem(merged, target, fixed))
        narrated = {'id': 'p1', 'parts': unit_parts(text, [{'start': 0, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}
        self.assertIn('fixed_speech_structure', structure_problem(narrated, target, fixed))
        _, _, invalid = review_analysis({'characters': [], 'passages': [narrated]}, [target], [], fixed={'p1': fixed})
        self.assertIn('fixed_speech_structure', invalid['p1'])

    def test_repair_echo_carries_only_the_failing_passage_and_its_records(self):
        reply = {'characters': [{'id': 'new_1'}, {'id': 'new_2'}],
                 'passages': [{'id': 'p1', 'parts': [{'speaker': 'new_1'}]}, {'id': 'p2', 'parts': [{'speaker': 'narrator'}]}]}
        self.assertEqual(rejected_answer(reply, {'p1'}), {'passages': [reply['passages'][0]], 'characters': [{'id': 'new_1'}]})
        self.assertIsNone(rejected_answer({'characters': [], 'passages': []}, {'p1'}))

    def test_provider_schema_keeps_enums_but_drops_count_limits(self):
        schema = provider_schema(analysis_schema([source('Текст.')], [], ['hero']))
        self.assertNotIn('maxItems', json.dumps(schema))
        self.assertIn('hero', json.dumps(schema))

    def test_offered_reply_accepts_the_unified_envelope(self):
        reply, known, extras = offered_reply({'reply': {}, 'known': ['a'], 'speech_structure': {'p1': []}, 'speaker_resolution': [{'x': 1}], 'staged': True}, {})
        self.assertEqual((known, extras['speech_structure'], extras['staged']), (['a'], {'p1': []}, True))


class SynthesisUnits(unittest.TestCase):
    def test_mixed_sentence_keeps_every_part_and_never_flattens(self):
        text = 'Маша открыла книгу. — Читай, — сказал Иван.'
        first, second = text.index('— Читай'), text.index('— сказал')
        target = source(text)
        reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': first, 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []},
            {'start': first, 'end': second, 'speaker': 'hero', 'delivery': 'firm', 'evidence': ['p1']},
            {'start': second, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        parts, _ = validate_analysis(reply, [target], [HERO])
        units = synthesis_units(parts, [target])
        self.assertEqual(text, ''.join(unit['text'] for unit in units))
        self.assertEqual(2, len(units))
        mixed = units[1]
        self.assertTrue(mixed['performance']['internal_transition'])
        self.assertEqual(['hero', 'narrator'], [part['speaker'] for part in mixed['performance']['analysis_parts']])
        self.assertEqual(['hero', 'narrator'], [part['speaker'] for part in render_parts(mixed)])
        self.assertIsNone(render_parts({**mixed, 'spoken': 'Читай, сказал он.'}))

    def test_unknown_in_a_sentence_is_the_displayed_speaker_so_it_is_held(self):
        text = '— Кто? — спросил кто-то.'
        cut = text.index('— спросил')
        target = source(text)
        reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'unknown', 'delivery': 'neutral', 'evidence': []},
            {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        parts, _ = validate_analysis(reply, [target], [])
        self.assertEqual('unknown', synthesis_units(parts, [target])[0]['speaker'])


class StagedEvidence(unittest.TestCase):
    def test_neighbor_only_identity_stays_a_held_proposal(self):
        before = {'id': 'p0', 'source_id': 'p0', 'text': 'Иван повернулся и сказал:', 'start': 0, 'ordinal': 0, 'chapter_id': 'c'}
        target = source('— Поехали.')
        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech']}
            if 'units' in request:
                return {'contexts': [NEUTRAL_CONTEXT for _ in request['units']]}
            if 'sentences' in request:
                return {'enumerations': [False for _ in request['sentences']]}
            return {'speaker': 'hero', 'name': '', 'evidence_id': 'p0', 'evidence_quote': 'Иван повернулся и сказал'}
        log = []
        answer, structure = staged_proposal_with_details([target], [HERO], [before], [], infer, resolution_log=log)
        self.assertEqual('unknown', answer['passages'][0]['parts'][0]['speaker'])
        self.assertEqual('review', log[0]['status'])
        self.assertEqual('hero', log[0]['speaker'])
        self.assertIn('neighbor_only_evidence', log[0]['triggers'])
        units = unit_answer(answer, [target])
        parts, _, invalid = review_analysis(units, [target], [HERO], [before], fixed=structure, kind_evidence=False)
        self.assertFalse(invalid)


class LocalAndRepair(unittest.TestCase):
    def test_single_pass_local_identities_stay_review_items(self):
        from rimsky.analysis import resolution_records
        text = '— Да, — сказал Иван.'
        target = source(text)
        cut = text.index('— сказал')
        reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        parts, _ = validate_analysis(reply, [target], [HERO])
        records = resolution_records([target], parts, local=True)
        self.assertEqual([('hero', 'local', ['local_fallback_review'], 'review')],
                         [(item['speaker'], item['tier'], item['triggers'], item['status']) for item in records])
        self.assertEqual([], resolution_records([target], parts))

    def test_staged_repair_carries_the_reason_and_omits_rejected_data_only_when_it_cannot_fit(self):
        import worker
        from rimsky.util import AnalysisCapacityError
        seen = []
        def infer(system, request, schema, limit):
            seen.append(request)
            if 'previous_rejected_answer' in request:
                raise AnalysisCapacityError('full')
            return {}
        ask = worker.repaired(infer, {'retry_error': 'Passage p1: bad', 'retry_response': {'passages': []}})
        ask('s', {'chunks': []}, {}, 10)
        self.assertEqual([('Passage p1: bad', True), ('Passage p1: bad', False)],
                         [(item['previous_validation_error'], 'previous_rejected_answer' in item) for item in seen])
        self.assertTrue(seen[1]['previous_rejected_answer_omitted'])
        self.assertIs(infer, worker.repaired(infer, {}))


class ProviderRecovery(unittest.TestCase):
    def test_one_failed_cli_process_is_retried_once_then_fails(self):
        from rimsky import cli_analysis
        calls = []
        def flaky(system, request, schema, **kwargs):
            calls.append(system)
            if len(calls) == 1:
                raise AppError('Claude character analysis failed: transient')
            return {'characters': [], 'passages': []}
        reply, fixed = cli_analysis.analyze({'cast': [], 'segments': [source('Просто текст.')], 'before': [], 'after': []}, flaky)
        self.assertEqual(({'characters': [], 'passages': []}, {}, 2), (reply, fixed, len(calls)))
        def broken(system, request, schema, **kwargs):
            calls.append(system)
            raise AppError('down')
        calls.clear()
        with self.assertRaisesRegex(AppError, 'down'):
            cli_analysis.analyze({'cast': [], 'segments': [source('Просто текст.')], 'before': [], 'after': []}, broken)
        self.assertEqual(2, len(calls))


class DescriptorScope(unittest.TestCase):
    def test_descriptor_identity_reused_in_another_scene_is_held(self):
        from rimsky.analysis import scoped
        guard = {'id': 'g', 'name': 'охранник', 'aliases': []}
        first = [{'speaker': 'g', 'chapter_id': 'c1', 'scene_id': 's1', 'source_id': 'p', 'start': 0, 'end': 5}]
        created, reused = scoped([], [guard], first)
        self.assertEqual(([['c1', 's1']], []), (created[0]['scopes'], reused))
        later = [{'speaker': 'g', 'chapter_id': 'c2', 'scene_id': 's9', 'source_id': 'q', 'start': 0, 'end': 5}]
        _, reused = scoped(created, created, later)
        self.assertEqual(later, reused)
        _, same = scoped(created, created, first)
        self.assertEqual([], same)
        named = [{**created[0], 'name': 'Иван'}]
        self.assertEqual([], scoped(named, named, later)[1])
        legacy = [{'id': 'g', 'name': 'охранник', 'aliases': []}]
        self.assertEqual([], scoped(legacy, legacy, later)[1])


class CriticPass(unittest.TestCase):
    def setUp(self):
        text = '— Да, — сказал Иван.'
        self.target = source(text)
        cut = text.index('— сказал')
        self.reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}

    def run_loop(self, critique):
        from rimsky.analysis import analyse_sources
        committed, order = [], []
        def call(payload):
            order.append('primary')
            return {'reply': self.reply, 'known': ['hero'], 'speech_structure': {}}
        def critic(request, schema):
            order.append('critic')
            return critique(request, schema)
        state = {'cast': [HERO], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources([self.target], [self.target], state, call, lambda done, units, failed, key, records=(), structure=None, reviews=(): committed.append((list(records), list(reviews))),
                        critique=critic)
        self.reviews = committed[0][1]
        return committed[0][0], order

    def test_critic_runs_after_primary_and_agreement_is_retained(self):
        from test_core import agreeing_critic
        records, order = self.run_loop(lambda request, schema: agreeing_critic({'request': request}))
        self.assertEqual(['primary', 'critic'], order)
        self.assertEqual([('hero', 'resolved', ['critic_agreement'])], [(item['speaker'], item['status'], item['triggers']) for item in records])

    def test_critic_dispute_is_held_with_its_proposal(self):
        def dispute(request, schema):
            return {'passages': [{'id': 'p1', 'reviews': [
                {'index': 0, 'role_supported': True, 'speaker_supported': False, 'alternative': 'unknown', 'evidence': ['p1'], 'reason': 'addressee'},
                {'index': 1, 'role_supported': True, 'speaker_supported': True, 'alternative': '', 'evidence': [], 'reason': ''}]}]}
        records, _ = self.run_loop(dispute)
        self.assertEqual([('hero', 'review', ['critic_disagreement'], 'unknown', 'addressee')],
                         [(item['speaker'], item['status'], item['triggers'], item['critic_speaker'], item['critic_reason']) for item in records])

    def test_unusable_critic_holds_speech_but_never_narration(self):
        calls = []
        def broken(request, schema):
            calls.append(request.get('previous_validation_error', ''))
            return {'passages': []}
        records, order = self.run_loop(broken)
        self.assertEqual(['primary', 'critic', 'critic'], order)
        self.assertTrue(calls[1])
        self.assertEqual({'critic_unavailable'}, {item['triggers'][0] for item in records})
        self.assertEqual([('hero', 'critic_unavailable')],
                         [(item['speaker'], item['triggers'][0]) for item in records])

    def test_invalid_critic_is_contained_but_provider_failure_stops_for_retry(self):
        from rimsky.util import AnalysisInvalidError, ProviderTransportError
        error = AnalysisInvalidError('Critic returned invalid JSON.')
        records, _ = self.run_loop(lambda request, schema: (_ for _ in ()).throw(error))
        self.assertEqual([('hero', 'critic_unavailable')],
                         [(item['speaker'], item['triggers'][0]) for item in records])
        with self.assertRaises(ProviderTransportError):
            self.run_loop(lambda request, schema: (_ for _ in ()).throw(
                ProviderTransportError('Critic CLI failed.')))

    def test_critic_receives_only_characters_relevant_to_the_run(self):
        from rimsky.analysis import critic_pass, validate_analysis
        cast = [HERO, *[{'id': f'other_{index}', 'name': f'Other {index}', 'aliases': [],
                         'description': 'Unrelated character.'} for index in range(40)]]
        parts, _ = validate_analysis(self.reply, [self.target], cast)
        seen = []
        def inspect(request, schema):
            seen.extend(character['id'] for character in request['known_characters'])
            from test_core import agreeing_critic
            return agreeing_critic({'request': request})
        critic_pass([self.target], parts, cast, lambda sources: [(sources, ([], []))], {}, inspect)
        self.assertEqual(['hero'], seen)

    def test_critic_keeps_an_accepted_context_speaker_for_pronoun_continuity(self):
        from rimsky.analysis import critic_pass, validate_analysis
        guide = {'id': 'guide', 'name': 'Guide', 'aliases': [], 'description': 'A guide.'}
        cast = [HERO, guide]
        parts, _ = validate_analysis(self.reply, [self.target], cast)
        before = source('Он вошёл.', 'before', -1)
        seen = []
        def inspect(request, schema):
            seen.extend(character['id'] for character in request['known_characters'])
            from test_core import agreeing_critic
            return agreeing_critic({'request': request})
        accepted = {'before': [{'start': 0, 'end': len(before['text']), 'speaker': 'guide'}]}
        critic_pass([self.target], parts, cast, lambda sources: [(sources, ([before], []))], {}, inspect, accepted)
        self.assertEqual(['hero', 'guide'], seen)

    def test_critic_cannot_agree_on_a_character_without_own_target_evidence(self):
        def weak(request, schema):
            return {'passages': [{'id': 'p1', 'reviews': [
                {'index': index, 'role_supported': True, 'speaker_supported': True, 'alternative': '', 'evidence': [], 'reason': ''} for index in range(2)]}]}
        records, _ = self.run_loop(weak)
        self.assertEqual([('hero', 'review')], [(item['speaker'], item['status']) for item in records])

    def test_missing_critic_holds_instead_of_accepting(self):
        from rimsky.analysis import critic_pass
        parts, _ = validate_analysis(self.reply, [self.target], [HERO])
        records, reviews = critic_pass([self.target], parts, [HERO], lambda sources: [(sources, ([], []))], {}, None)
        self.assertEqual(({'critic_unavailable'}, []), ({item['triggers'][0] for item in records}, reviews))


class CriticConsistency(CriticPass):
    def review(self, **first):
        base = {'index': 0, 'role_supported': True, 'speaker_supported': True, 'alternative': '', 'evidence': ['p1'], 'reason': ''}
        second = {'index': 1, 'role_supported': True, 'speaker_supported': True, 'alternative': '', 'evidence': [], 'reason': ''}
        return {'passages': [{'id': 'p1', 'reviews': [{**base, **first}, second]}]}

    def test_contradictory_agreements_and_disputes_are_repaired_then_held(self):
        for contradiction in ({'alternative': 'unknown'}, {'alternative': 'narrator'}, {'alternative': 'new'},
                              {'speaker_supported': False, 'alternative': 'hero'}, {'role_supported': False, 'alternative': 'hero'}):
            with self.subTest(**contradiction):
                errors = []
                def critique(request, schema, contradiction=contradiction):
                    errors.append(request.get('previous_validation_error', ''))
                    return self.review(**contradiction)
                records, order = self.run_loop(critique)
                self.assertEqual(['primary', 'critic', 'critic'], order)
                self.assertIn('contradictory', errors[1])
                self.assertEqual({'critic_unavailable'}, {item['triggers'][0] for item in records})

    def test_consistent_answers_are_accepted_and_every_part_is_retained(self):
        for answer, status in (({}, 'resolved'), ({'alternative': 'hero'}, 'resolved'), ({'speaker_supported': False, 'alternative': 'unknown'}, 'review'),
                               ({'speaker_supported': False, 'alternative': ''}, 'review'), ({'role_supported': False, 'alternative': 'narrator'}, 'review')):
            with self.subTest(**answer):
                records, _ = self.run_loop(lambda request, schema, answer=answer: self.review(**answer))
                self.assertEqual([status], [item['status'] for item in records])
                self.assertEqual(['hero', 'narrator'], [item['speaker'] for item in self.reviews])
                self.assertEqual({'role_supported', 'speaker_supported', 'alternative', 'evidence', 'reason', 'supported'} <= set(self.reviews[1]), True)


class CriticContext(unittest.TestCase):
    def test_critic_keeps_preceding_and_following_passages_in_order(self):
        from rimsky.analysis import analyse_sources
        from test_core import agreeing_critic
        sources = [source(text, f'p{index}', index) for index, text in enumerate(['Иван вошёл.', '— Привет, — сказал он.', 'Маша ответила.'])]
        seen = []
        def call(payload):
            return {'reply': {'characters': [], 'passages': [narration(item) for item in payload['segments']]}, 'known': [], 'speech_structure': {}}
        def critique(request, schema):
            seen.append(request)
            return agreeing_critic({'request': request})
        state = {'cast': [], 'accepted': {}, 'volume': {}, 'inputs': []}
        def first_only(payload):
            return call({**payload, 'segments': payload['segments']})
        analyse_sources([sources[1]], sources, state, call, lambda *_: None, critique=critique)
        self.assertEqual((['p0'], ['p2']), ([item['id'] for item in seen[0]['context_before']], [item['id'] for item in seen[0]['context_after']]))

    def test_critic_gets_unverified_summaries_and_raw_earlier_anchors_only(self):
        from rimsky.critic import critic_characters, critic_request, critic_schema
        earlier = source('— Я Иван, — представился он.', 'p0', 0)
        target = source('— Привет.', 'p5', 5)
        prior = [{**HERO, 'description': 'First-person protagonist; established in earlier chapters.', 'evidence': ['p0', 'p5']}]
        fabricated = [{**prior[0], 'description': 'Fabricated in this pass.'}, {'id': 'char_new', 'name': 'Гость', 'aliases': [], 'description': 'Also new.', 'evidence': ['p5']}]
        characters = critic_characters(prior, fabricated, {'p5'}, {'p0': earlier, 'p5': target})
        self.assertEqual('First-person protagonist; established in earlier chapters.', characters[0]['unverified_summary'])
        self.assertEqual([('p0', earlier['text'])], [(anchor['id'], anchor['text']) for anchor in characters[0]['anchors']])
        self.assertEqual({'id': 'char_new', 'name': 'Гость', 'aliases': [], 'unverified_summary': '', 'anchors': [], 'new_in_this_pass': True}, characters[1])
        self.assertNotIn('Fabricated in this pass.', json.dumps(characters, ensure_ascii=False))
        parts, _ = validate_analysis({'characters': [], 'passages': [narration(target)]}, [target], prior)
        request = critic_request({'segments': [target], 'cast': fabricated, 'characters': characters, 'before': [], 'after': []}, parts, {},
                                 {'p0': [{'start': 0, 'end': 10, 'speaker': 'hero'}]})
        enum = critic_schema(request, ['hero', 'char_new'])['properties']['passages']['items']['properties']['reviews']['items']['properties']['evidence']['items']['enum']
        self.assertEqual(['p5', 'p0'], enum)
        self.assertNotIn('description', request['known_characters'][0])

    def test_same_pass_primary_description_never_reaches_the_critic(self):
        from rimsky.analysis import analyse_sources
        from test_core import agreeing_critic
        earlier, later = source('— Я Иван, — представился он.', 'p0', 0), source('— Я вернусь, — сказал Иван.', 'p9', 9)
        target = source('— Да, — сказал Иван.', 'p1', 1)
        cut = target['text'].index('— сказал')
        prior = {**HERO, 'description': 'Earlier model summary.', 'evidence': ['p0', 'p9', 'p1']}
        claim = 'First-person protagonist; established in earlier chapters.'
        seen = []
        def call(payload):
            return {'reply': {'characters': [{**{key: prior[key] for key in ('id', 'name', 'aliases')}, 'description': claim, 'evidence': ['p1'],
                                              'kind': 'person', 'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}],
                              'passages': [{'id': 'p1', 'parts': unit_parts(target['text'], [
                                  {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
                                  {'start': cut, 'end': len(target['text']), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]},
                    'known': ['hero'], 'speech_structure': {}}
        def critique(request, schema):
            seen.append(request)
            return agreeing_critic({'request': request})
        state = {'cast': [prior], 'accepted': {}, 'volume': {}, 'inputs': [], 'sources': {item['id']: item for item in (earlier, target, later)}}
        analyse_sources([target], [target], state, call, lambda *_: None, critique=critique)
        self.assertIn(claim, state['cast'][0]['description'])
        self.assertNotIn(claim, json.dumps(seen, ensure_ascii=False))
        self.assertEqual('Earlier model summary.', seen[0]['known_characters'][0]['unverified_summary'])
        self.assertEqual(['p0'], [anchor['id'] for anchor in seen[0]['known_characters'][0]['anchors']])
        self.assertIn('unverified hints, never evidence', __import__('rimsky.critic', fromlist=['CRITIC_SYSTEM']).CRITIC_SYSTEM)
        self.assertNotIn('established in earlier passages', __import__('rimsky.critic', fromlist=['CRITIC_SYSTEM']).CRITIC_SYSTEM)

    def test_long_anchor_keeps_its_whole_sentence_and_range(self):
        from rimsky.critic import critic_characters
        from rimsky.preparation import speech_sources
        sentence = ('— Я сделаю всё, что ты скажешь, и даже больше, ' + 'потому что иначе нельзя, ' * 22 + '— сказал Иван, но на самом деле говорил Коста.')
        self.assertGreater(len(sentence), 600)
        book = {'chapters': [{'id': 'c1', 'blocks': [{'id': 'b1', 'kind': 'paragraph', 'text': sentence}, {'id': 'b2', 'kind': 'paragraph', 'text': '— Да.'}]}]}
        earlier, target = list(speech_sources(book, ['c1']))
        self.assertEqual(sentence, earlier['text'])
        prior = [{**HERO, 'evidence': [earlier['id']]}]
        anchor = critic_characters(prior, prior, {target['id']}, {earlier['id']: earlier, target['id']: target})[0]['anchors'][0]
        self.assertEqual(sentence, anchor['text'])
        self.assertTrue(anchor['text'].endswith('но на самом деле говорил Коста.'))
        self.assertEqual(('b1', earlier['start'], earlier['start'] + len(sentence), 'c1', 'c1-scene0'),
                         (anchor['source_id'], anchor['start'], anchor['end'], anchor['chapter_id'], anchor['scene_id']))

    def test_an_anchor_cannot_stand_in_for_the_target_citation(self):
        from rimsky.critic import critic_records
        target = source('— Да, — сказал Иван.', 'p1', 1)
        cut = target['text'].index('— сказал')
        parts, _ = validate_analysis({'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(target['text'], [
            {'start': 0, 'end': cut, 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': cut, 'end': len(target['text']), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}, [target], [HERO])
        review = {'role_supported': True, 'speaker_supported': True, 'alternative': '', 'evidence': ['p0'], 'reason': 'anchor only'}
        records, _ = critic_records(parts, {'p1': [{**review, 'index': 0}, {**review, 'index': 1}]}, {'p1': target})
        self.assertEqual([('hero', 'review', 'critic_disagreement')], [(item['speaker'], item['status'], item['triggers'][0]) for item in records])

    def test_capacity_subdivision_gives_each_target_its_own_neighbours(self):
        from rimsky.analysis import analyse_sources
        from rimsky.util import AnalysisCapacityError
        from test_core import agreeing_critic
        sources = [source(text, f'p{index}', index) for index, text in enumerate(['Один.', 'Два.', 'Три.', 'Четыре.'])]
        seen = []
        def call(payload):
            return {'reply': {'characters': [], 'passages': [narration(item) for item in payload['segments']]}, 'known': [], 'speech_structure': {}}
        def critique(request, schema):
            if len(request['targets']) > 1:
                raise AnalysisCapacityError('full')
            seen.append(request)
            return agreeing_critic({'request': request})
        state = {'cast': [], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources(sources[1:3], sources, state, call, lambda *_: None, critique=critique)
        self.assertEqual([('p1', ['p0'], ['p2', 'p3']), ('p2', ['p0', 'p1'], ['p3'])],
                         [(item['targets'][0]['id'], [entry['id'] for entry in item['context_before']], [entry['id'] for entry in item['context_after']]) for item in seen])


class BlindAgreement(unittest.TestCase):
    def setUp(self):
        self.text = '— Да, — сказал Иван. — Нет, — ответила Маша.'
        self.target = source(self.text)
        self.cuts = [self.text.index('— сказал'), self.text.index('— Нет'), self.text.index('— ответила')]
        self.cast = [HERO, {'id': 'masha', 'name': 'Маша', 'aliases': [], 'description': '', 'evidence': ['p1']}]

    def reply(self, first, second, new=None, evidence=('p1',)):
        characters = [] if not new else [{'id': 'new_1', 'name': new, 'aliases': [], 'description': '', 'evidence': ['p1'], 'kind': 'person',
                                          'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}]
        c = self.cuts
        return {'reply': {'characters': characters, 'passages': [{'id': 'p1', 'parts': unit_parts(self.text, [
            {'start': 0, 'end': c[0], 'speaker': first, 'delivery': 'neutral', 'evidence': list(evidence)},
            {'start': c[0], 'end': c[1], 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []},
            {'start': c[1], 'end': c[2], 'speaker': second, 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': c[2], 'end': len(self.text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]},
            'known': ['hero', 'masha'], 'speech_structure': {}}

    def gate(self, primary, second, cast=None, repair=True, inputs=None):
        from rimsky.analysis import analyse_sources
        from test_core import agreeing_critic
        committed, calls = [], {'primary': [], 'second': []}
        def first(payload):
            calls['primary'].append(payload)
            return primary(payload) if callable(primary) else primary
        def other(payload):
            calls['second'].append(payload)
            return second(payload) if callable(second) else second
        state = {'cast': list(self.cast if cast is None else cast), 'accepted': {}, 'volume': {}, 'inputs': []}
        targets = inputs or [self.target]
        self.calls = calls
        analyse_sources(targets, targets, state, first,
                        lambda done, units, failed, key, records=(), structure=None, reviews=(), audit=(): committed.append((list(records), list(audit))),
                        critique=lambda request, schema: agreeing_critic({'request': request}), second=other, second_repair=repair)
        self.audit = [entry for _, audit in committed for entry in audit]
        return [(item['speaker'], item['status'], item['triggers'][0]) for records, _ in committed for item in records if item['tier'] == 'agreement']

    def test_agreement_accepts_and_disagreement_holds_without_voting(self):
        self.assertEqual([('hero', 'resolved', 'provider_agreement'), ('masha', 'resolved', 'provider_agreement')],
                         self.gate(self.reply('hero', 'masha'), self.reply('hero', 'masha')))
        self.assertEqual([('masha', 'review', 'provider_disagreement'), ('hero', 'resolved', 'provider_agreement')],
                         self.gate(self.reply('hero', 'masha'), self.reply('hero', 'hero')))

    def test_same_new_name_from_both_providers_is_not_proof(self):
        held = self.gate(self.reply('hero', 'new_1', 'Маша'), self.reply('hero', 'new_1', 'Маша'), [HERO])
        self.assertEqual([('review', 'provider_new_identity'), ('resolved', 'provider_agreement')], [(status, trigger) for _, status, trigger in held])
        self.assertTrue(held[0][0].startswith('char_'))

    def test_structural_disagreement_holds_the_target(self):
        second = {'reply': {'characters': [], 'passages': [narration(self.target)]}, 'known': ['hero', 'masha'],
                  'speech_structure': {'p1': [{'start': 0, 'end': len(self.text), 'text': self.text, 'mode': 'narrator'}]}}
        self.assertEqual([('hero', 'review', 'provider_structure_disagreement')], self.gate(self.reply('hero', 'masha'), second))

    def test_second_provider_is_blind_to_the_primary_repair_and_answers_each_target_once(self):
        answers = iter([self.reply('hero', 'masha', evidence=()), self.reply('hero', 'masha')])
        held = self.gate(lambda payload: next(answers), self.reply('hero', 'masha'))
        self.assertEqual(2, len(self.calls['primary']))
        self.assertTrue(self.calls['primary'][1]['retry_error'])
        self.assertEqual(1, len(self.calls['second']))
        self.assertEqual(('', False), (self.calls['second'][0]['retry_error'], 'retry_response' in self.calls['second'][0]))
        self.assertEqual({'provider_agreement'}, {trigger for _, _, trigger in held})
        self.assertEqual([('original', False), ('repair', True)], [(item['kind'], item['valid']) for item in self.audit[0]['primary']])
        self.assertTrue(self.audit[0]['primary'][1]['repair_of'].startswith('Passage p1:'))

    def test_invalid_second_answer_gets_one_own_repair_and_both_outcomes_are_kept(self):
        answers = iter([self.reply('hero', 'masha', evidence=()), self.reply('hero', 'masha')])
        def second(payload):
            answer = next(answers)
            return {**answer, 'speech_structure': payload.get('fixed_structure', {})}
        held = self.gate(self.reply('hero', 'masha'), second)
        self.assertEqual((1, 2), (len(self.calls['primary']), len(self.calls['second'])))
        repair = self.calls['second'][1]
        self.assertTrue(repair['retry_error'].startswith('Passage p1:'))
        self.assertEqual(self.reply('hero', 'masha', evidence=())['reply']['passages'], repair['retry_response']['passages'])
        self.assertEqual({}, repair['fixed_structure'])
        self.assertEqual({'provider_agreement'}, {trigger for _, _, trigger in held})
        attempts = self.audit[0]['secondary']
        self.assertEqual([('original', False), ('repair', True)], [(item['kind'], item['valid']) for item in attempts])
        self.assertEqual(['p1'], attempts[1]['passage']['parts'][0]['evidence'])
        self.assertEqual([], attempts[0]['passage']['parts'][0]['evidence'])
        self.assertEqual({'provider_invalid'}, {item['triggers'][0] for item in self.audit[0]['first_records'] if item['status'] == 'review'})
        self.assertEqual(['hero', 'masha'], self.audit[0]['primary'][0]['offered'])

    def test_valid_second_structure_is_kept_for_its_repair(self):
        structure = {'p1': [{'start': 0, 'end': len(self.text), 'text': self.text, 'mode': 'speech'}]}
        answers = iter([{**self.reply('hero', 'masha', evidence=()), 'speech_structure': structure}, self.reply('hero', 'masha')])
        self.gate(self.reply('hero', 'masha'), lambda payload: {**next(answers), 'speech_structure': payload.get('fixed_structure', structure)})
        self.assertEqual(structure, self.calls['second'][1]['fixed_structure'])

    def test_second_answer_still_invalid_after_repair_holds_and_no_repair_when_disabled(self):
        bad = self.reply('hero', 'masha', evidence=())
        self.assertEqual([('hero', 'review', 'provider_invalid')], self.gate(self.reply('hero', 'masha'), bad))
        self.assertEqual(2, len(self.calls['second']))
        self.assertEqual([('hero', 'review', 'provider_invalid')], self.gate(self.reply('hero', 'masha'), bad, repair=False))
        self.assertEqual(1, len(self.calls['second']))

    def test_missing_second_provider_is_an_explicit_hold(self):
        from rimsky.util import ProviderUnavailableError
        def missing(payload):
            raise ProviderUnavailableError('Claude Code CLI is not installed or is not on PATH.')
        self.assertEqual([('hero', 'review', 'provider_unavailable')], self.gate(self.reply('hero', 'masha'), missing))
        self.assertEqual('provider_unavailable', self.audit[0]['secondary'][0]['category'])

    def test_second_transport_failures_stop_the_run_and_never_re_ask_the_primary(self):
        from rimsky.util import EngineTransportError, ProviderTransportError
        for error in (EngineTransportError('Local inference process exited.', 'exit'), EngineTransportError('Invalid response.', 'protocol'),
                      ProviderTransportError('Claude character analysis failed: 500'), AppError('Unsupported Claude effort for character analysis.')):
            with self.subTest(error=str(error)):
                def broken(payload, error=error):
                    raise error
                with self.assertRaises(type(error)):
                    self.gate(self.reply('hero', 'masha'), broken)
                self.assertEqual(1, len(self.calls['primary']))

    def test_second_timeout_is_retried_once_then_stops(self):
        from rimsky.util import EngineTransportError
        answers = iter([EngineTransportError('Timed out.', 'timeout'), self.reply('hero', 'masha')])
        def flaky(payload):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.assertEqual({'provider_agreement'}, {trigger for _, _, trigger in self.gate(self.reply('hero', 'masha'), flaky)})
        self.assertEqual((1, 2), (len(self.calls['primary']), len(self.calls['second'])))
        def stuck(payload):
            raise EngineTransportError('Timed out.', 'timeout')
        with self.assertRaises(EngineTransportError):
            self.gate(self.reply('hero', 'masha'), stuck)
        self.assertEqual((1, 2), (len(self.calls['primary']), len(self.calls['second'])))

    def test_second_capacity_splits_only_the_second_requests_and_holds_an_indivisible_target(self):
        from rimsky.util import AnalysisCapacityError
        texts = ['— Да, — сказал Иван.', '— Нет, — ответила Маша.']
        inputs = [source(text, f'p{index}', index) for index, text in enumerate(texts)]
        def answer(payload):
            passages = []
            for item in payload['segments']:
                cut = item['text'].index(' — ') + 1
                speaker = 'hero' if 'Иван' in item['text'] else 'masha'
                passages.append({'id': item['id'], 'parts': unit_parts(item['text'], [
                    {'start': 0, 'end': cut, 'speaker': speaker, 'delivery': 'neutral', 'evidence': [item['id']]},
                    {'start': cut, 'end': len(item['text']), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])})
            return {'reply': {'characters': [], 'passages': passages}, 'known': ['hero', 'masha'], 'speech_structure': {}}
        def second(payload):
            if len(payload['segments']) > 1 or 'Маша' in payload['segments'][0]['text']:
                raise AnalysisCapacityError('Too large.')
            return answer(payload)
        held = self.gate(answer, second, inputs=inputs)
        self.assertEqual(1, len(self.calls['primary']))
        self.assertEqual([2, 1, 1], [len(payload['segments']) for payload in self.calls['second']])
        self.assertEqual(sorted([('hero', 'resolved', 'provider_agreement'), ('masha', 'review', 'provider_capacity')]), sorted(held))

    def test_agreement_never_clears_other_holds(self):
        from rimsky.analysis import analyse_sources
        committed = []
        state = {'cast': list(self.cast), 'accepted': {}, 'volume': {}, 'inputs': []}
        def disputing(request, schema):
            return {'passages': [{'id': target['id'], 'reviews': [{'index': part['index'], 'role_supported': True, 'speaker_supported': part['speaker'] != 'masha',
                                                                  'alternative': '' if part['speaker'] != 'masha' else 'unknown', 'evidence': [target['id']], 'reason': ''}
                                                                 for part in target['proposal']]} for target in request['targets']]}
        analyse_sources([self.target], [self.target], state, lambda payload: self.reply('hero', 'masha'),
                        lambda done, units, failed, key, records=(), structure=None, reviews=(), audit=(): committed.append((units, list(records))),
                        critique=disputing, second=lambda payload: self.reply('hero', 'masha'))
        units, records = committed[0]
        held = speaker_review_holds(units, records)[1]
        self.assertTrue(held)
        self.assertIn('critic_disagreement', {item['triggers'][0] for item in records if item['status'] == 'review'})


def attributed(item, speaker, valid=True):
    cut = item['text'].index(' — ') + 1
    return {'id': item['id'], 'parts': unit_parts(item['text'], [
        {'start': 0, 'end': cut, 'speaker': speaker, 'delivery': 'neutral', 'evidence': [item['id']] if valid else []},
        {'start': cut, 'end': len(item['text']), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}


def created(name, evidence):
    return {'id': 'new_1', 'name': name, 'aliases': [], 'description': '', 'evidence': [evidence], 'kind': 'person',
            'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}


class FrozenSecondSnapshots(unittest.TestCase):
    def run_pair(self, targets, inputs, primary, second, cast=()):
        from rimsky.analysis import analyse_sources
        from test_core import agreeing_critic
        self.calls, self.committed = {'primary': [], 'second': []}, []
        def first(payload):
            self.calls['primary'].append(payload)
            return primary(payload)
        def other(payload):
            self.calls['second'].append(payload)
            return second(payload)
        self.state = {'cast': list(cast), 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources(targets, inputs, self.state, first,
                        lambda done, units, failed, key, records=(), structure=None, reviews=(), audit=(): self.committed.append((done, list(records), list(audit))),
                        critique=lambda request, schema: agreeing_critic({'request': request}), second=other)
        return [(record['target_id'], record['status'], record['triggers'][0]) for _, records, _ in self.committed for record in records if record['tier'] == 'agreement']

    def test_cached_new_identity_is_never_promoted_by_a_sibling_registration(self):
        targets = [source('— Да, — сказал Иван.', 'p0', 0), source('— Нет, — сказал Иван.', 'p1', 1)]
        def primary(payload):
            original = len(payload['segments']) > 1
            speaker = 'new_1' if original else payload['cast'][0]['id']
            return {'reply': {'characters': [created('Иван', 'p1')] if original else [],
                              'passages': [attributed(item, speaker, not original or item['id'] != 'p0') for item in payload['segments']]},
                    'known': [character['id'] for character in payload['cast']], 'speech_structure': {}}
        def second(payload):
            return {'reply': {'characters': [created('Иван', 'p1')], 'passages': [attributed(item, 'new_1') for item in payload['segments']]},
                    'known': [], 'speech_structure': {}}
        held = self.run_pair(targets, targets, primary, second)
        self.assertEqual(['char_'], [entry['id'][:5] for entry in self.calls['primary'][1]['cast']])
        self.assertEqual(1, len(self.calls['second']))
        self.assertIn(('p0', 'review', 'provider_new_identity'), held)
        self.assertIn(('p1', 'review', 'provider_new_identity'), held)
        self.assertNotIn('resolved', {status for _, status, _ in held})

    def test_own_repair_keeps_the_original_request_sources_registry_and_order(self):
        targets = [source('— Да, — сказал Иван.', 'p0', 0), source('— Нет, — сказал Иван.', 'p1', 1)]
        inputs = targets + [source('За дверью стоял Коста.', 'p2', 2), source('Иван никого не звал.', 'p3', 3)]
        cast = [HERO, *({'id': f'extra{index}', 'name': f'Персонаж{index}', 'aliases': [], 'description': '', 'evidence': ['p2']} for index in range(45))]
        def reply(payload, invalid=False):
            return {'reply': {'characters': [], 'passages': [attributed(item, 'hero', not invalid or item['id'] != 'p0') for item in payload['segments']]},
                    'known': [character['id'] for character in payload['cast']], 'speech_structure': payload.get('fixed_structure', {})}
        held = self.run_pair(targets, inputs, reply, lambda payload: reply(payload, len(self.calls['second']) == 1), cast)
        original, repair = self.calls['second']
        self.assertEqual((['p2', 'p3'], ['p1', 'p2', 'p3'], []), ([item['id'] for item in original['after']], [item['id'] for item in repair['after']],
                                                               [item['id'] for item in repair['before']]))
        self.assertEqual(['p0'], [item['id'] for item in repair['segments']])
        self.assertEqual(original['cast'], repair['cast'])
        self.assertLess(len(original['cast']), len(cast))
        self.assertEqual({key: original[key] for key in original if key not in ('segments', 'before', 'after', 'required', 'retry_error')},
                         {key: repair[key] for key in repair if key not in ('segments', 'before', 'after', 'retry_error', 'retry_response', 'fixed_structure')})
        self.assertEqual({('p0', 'resolved', 'provider_agreement'), ('p1', 'resolved', 'provider_agreement')}, set(held))
        audit = {entry['target_id']: entry for _, _, entries in self.committed for entry in entries}
        self.assertEqual({'segments': ['p0'], 'before': [], 'after': ['p1', 'p2', 'p3'], 'offered': [item['id'] for item in original['cast']]},
                         audit['p0']['secondary'][1]['request'])

    def test_repair_that_changes_its_registry_is_held(self):
        targets = [source('— Да, — сказал Иван.', 'p0', 0)]
        answers = iter([{'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero', False)]}, 'known': ['hero'], 'speech_structure': {}},
                        {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero')]}, 'known': ['hero', 'ghost'], 'speech_structure': {}}])
        held = self.run_pair(targets, targets, lambda payload: {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero')]}, 'known': ['hero'],
                                                                'speech_structure': {}}, lambda payload: next(answers), [HERO])
        self.assertEqual([('p0', 'review', 'provider_invalid')], held)

    def test_failed_primary_target_keeps_its_complete_audit(self):
        targets = [source('— Да, — сказал Иван.', 'p0', 0), source('— Нет, — сказал Иван.', 'p1', 1)]
        def primary(payload):
            return {'reply': {'characters': [], 'passages': [attributed(item, 'hero', item['id'] != 'p0') for item in payload['segments']]},
                    'known': ['hero'], 'speech_structure': {}}
        def second(payload):
            return {'reply': {'characters': [], 'passages': [attributed(item, 'hero') for item in payload['segments']]}, 'known': ['hero'], 'speech_structure': {}}
        self.run_pair(targets, targets, primary, second, [HERO])
        trail = self.state['agreement_trail']
        self.assertEqual({'p0', 'p1'}, set(trail))
        self.assertEqual(('degraded', [('original', False), ('repair', False)], [('original', True)]),
                         (trail['p0']['terminal'], [(item['kind'], item['valid']) for item in trail['p0']['primary']],
                          [(item['kind'], item['valid']) for item in trail['p0']['secondary']]))
        self.assertTrue(trail['p0']['primary'][1]['error'].startswith('Passage p0:'))
        self.assertEqual([], trail['p0']['primary'][0]['passage']['parts'][0]['evidence'])
        degraded = next(entries for done, _, entries in self.committed if done[0]['id'] == 'p0')
        self.assertEqual(['p0'], [entry['target_id'] for entry in degraded])

    def test_transport_failure_during_own_repair_stops_with_partial_audit(self):
        from rimsky.util import ProviderTransportError
        targets = [source('— Да, — сказал Иван.', 'p0', 0)]
        def second(payload):
            if payload['retry_error']:
                raise ProviderTransportError('Claude character analysis failed: 500')
            return {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero', False)]}, 'known': ['hero'], 'speech_structure': {}}
        with self.assertRaises(ProviderTransportError):
            self.run_pair(targets, targets, lambda payload: {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero')]}, 'known': ['hero'],
                                                             'speech_structure': {}}, second, [HERO])
        self.assertEqual(1, len(self.calls['primary']))
        self.assertEqual([], self.committed)
        trail = json.loads(json.dumps(self.state['agreement_trail'], ensure_ascii=False))['p0']
        self.assertEqual([('original', True)], [(item['kind'], item['valid']) for item in trail['primary']])
        self.assertEqual([('original', False, None), ('repair', False, 'ProviderTransportError')],
                         [(item['kind'], item['valid'], item.get('category')) for item in trail['secondary']])
        self.assertEqual({'segments': ['p0'], 'before': [], 'after': [], 'offered': ['hero']}, trail['secondary'][1]['request'])
        self.assertNotIn('terminal', trail)

    def test_transport_failures_of_the_first_calls_and_the_critic_keep_attempt_provenance(self):
        from rimsky.analysis import analyse_sources
        from rimsky.util import EngineTransportError, ProviderTransportError
        targets = [source('— Да, — сказал Иван.', 'p0', 0), source('— Нет, — сказал Иван.', 'p1', 1)]
        def answer(payload):
            return {'reply': {'characters': [], 'passages': [attributed(item, 'hero') for item in payload['segments']]}, 'known': ['hero'], 'speech_structure': {}}
        def broken(payload):
            raise ProviderTransportError('Codex failed.')
        with self.assertRaises(ProviderTransportError):
            self.run_pair(targets, targets, broken, answer, [HERO])
        self.assertEqual({'p0', 'p1'}, set(self.state['agreement_trail']))
        self.assertEqual(('ProviderTransportError', ['p0', 'p1']), (self.state['agreement_trail']['p0']['primary'][0]['category'],
                                                                   self.state['agreement_trail']['p0']['primary'][0]['targets']))
        self.assertEqual([], self.calls['second'])
        def second(payload):
            raise EngineTransportError('Local inference process exited.', 'exit')
        with self.assertRaises(EngineTransportError):
            self.run_pair(targets, targets, answer, second, [HERO])
        self.assertEqual(['transport_exit', 'transport_exit'], [self.state['agreement_trail'][tid]['secondary'][0]['category'] for tid in ('p0', 'p1')])
        self.assertEqual([True, True], [self.state['agreement_trail'][tid]['primary'][0]['valid'] for tid in ('p0', 'p1')])
        state = {'cast': [HERO], 'accepted': {}, 'volume': {}, 'inputs': []}
        def critic(request, schema):
            raise EngineTransportError('Critic process exited.', 'exit')
        with self.assertRaises(EngineTransportError):
            analyse_sources(targets, targets, state, answer, lambda *args, **kwargs: None, critique=critic, second=answer)
        self.assertEqual([('original', True)] * 2, [(state['agreement_trail'][tid]['secondary'][0]['kind'], state['agreement_trail'][tid]['secondary'][0]['valid'])
                                                    for tid in ('p0', 'p1')])

    def test_capacity_during_own_repair_holds_with_its_request(self):
        from rimsky.util import AnalysisCapacityError
        targets = [source('— Да, — сказал Иван.', 'p0', 0)]
        def second(payload):
            if payload['retry_error']:
                raise AnalysisCapacityError('Too large.')
            return {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero', False)]}, 'known': ['hero'], 'speech_structure': {}}
        held = self.run_pair(targets, targets, lambda payload: {'reply': {'characters': [], 'passages': [attributed(targets[0], 'hero')]}, 'known': ['hero'],
                                                                'speech_structure': {}}, second, [HERO])
        self.assertEqual([('p0', 'review', 'provider_capacity')], held)
        self.assertEqual('provider_capacity', self.state['agreement_trail']['p0']['secondary'][1]['category'])


class NewIdentityAlignment(unittest.TestCase):
    def aligned(self, text, primary_name, second_name=None, cast=(), common=frozenset(), before=False):
        from rimsky.agreement import agreement_records, outcome
        from rimsky.analysis import hold_record
        from rimsky.jobs import canonical_name, name_tokens
        target = source(text, 'p0')
        if before:
            cut = text.index('— ')
            passage = {'id': 'p0', 'parts': unit_parts(text, [
                {'start': 0, 'end': cut, 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []},
                {'start': cut, 'end': len(text), 'speaker': 'new_1', 'delivery': 'neutral', 'evidence': ['p0']}])}
        else:
            passage = attributed(target, 'new_1')
        def parts(name):
            reply = {'characters': [created(name, 'p0')], 'passages': [passage]}
            parts, updated, invalid = review_analysis(reply, [target], list(cast), [], 'k', lambda value: canonical_name(name_tokens(value)), common)
            self.assertEqual({}, invalid)
            return parts, updated
        first, updated = parts(primary_name)
        second, second_updated = parts(second_name or primary_name)
        registered = {character['id'] for character in cast}
        answers = {'p0': {**outcome(second, {}, 'p0', None, second_updated, registered)}}
        records = agreement_records([target], first, {}, answers, {'p0': registered}, hold_record,
                                    primary_characters=outcome(first, {}, '', None, updated, registered)['characters'], common=common, registry=list(cast))
        held = [record for record in records if record['status'] == 'review']
        self.assertEqual(['provider_new_identity'], [record['triggers'][0] for record in held])
        return held[0]['alignment']

    def test_single_nominative_attribution_name_aligns(self):
        result = self.aligned('— Да, — сказал Гоша.', 'Гоша')
        self.assertEqual((True, 'Гоша', 15, 19), (result['aligned'], result['anchor']['text'], result['anchor']['start'], result['anchor']['end']))
        self.assertTrue(self.aligned('— Да, — тихо сказал Гоша.', 'Гоша')['aligned'])

    def test_listeners_silent_actors_and_lead_ins_never_align(self):
        for text, before, reason in (('— Стой! — Гоша услышал незнакомый голос.', False, 'the remark is not a deterministic named speech attribution'),
                                     ('— Да, — Гоша молчал.', False, 'the remark is not a deterministic named speech attribution'),
                                     ('— Стой! — услышал Гоша.', False, 'the remark is not a deterministic named speech attribution'),
                                     ('Гоша услышал крик: — Стой!', True, 'no adjacent dash attribution remark'),
                                     ('— Да, — сказал не Гоша.', False, 'the remark is not a deterministic named speech attribution'),
                                     ('— Да, — не сказал Гоша.', False, 'the remark is not a deterministic named speech attribution'),
                                     ('— Да, — вовсе не сказал Гоша.', False, 'the remark is not a deterministic named speech attribution')):
            with self.subTest(text=text):
                result = self.aligned(text, 'Гоша', before=before, common=frozenset({'незнакомый'}))
                self.assertEqual((False, reason), (result['aligned'], result['reason']))

    def test_addressee_descriptor_and_ambiguous_remarks_never_align(self):
        for text, name, reason in (('— Да, — сказал он Гоше.', 'Гоша', 'a new character does not carry the remark name in that exact form'),
                                   ('— Люди Земли, — сказал посланник.', 'Посланник', 'no proper name in the remark'),
                                   ('— Да, — сказал Гоша Мите.', 'Гоша', 'a new character does not carry the remark name in that exact form'),
                                   ('— Да, — сказал Гоша, глядя на Митю.', 'Гоша', 'more than one proper name in the remark'),
                                   ('— Да. — Гоша ушёл.', 'Гоша', 'no adjacent dash attribution remark')):
            with self.subTest(text=text):
                result = self.aligned(text, name, common=frozenset({'посланник'}))
                self.assertEqual((False, reason), (result['aligned'], result['reason']))

    def test_different_names_or_an_existing_person_never_align(self):
        self.assertFalse(self.aligned('— Да, — сказал Гоша Петров.', 'Гоша Петров', 'Гоша')['aligned'])
        existing = {'id': 'gosha', 'name': 'Гоша', 'aliases': [], 'description': '', 'evidence': ['p9']}
        result = self.aligned('— Да, — сказал Гоша.', 'Гоша', cast=[existing])
        self.assertEqual((False, 'a registered character already has this name'), (result['aligned'], result['reason']))


class HeldContextTrust(unittest.TestCase):
    """Held proposals never become accepted-speaker hints or speaker-driven registry priorities."""
    def setUp(self):
        self.ivan = {'id': 'ivan', 'name': 'Иван', 'aliases': [], 'description': '', 'evidence': ['p9']}
        self.envoy = {'id': 'envoy', 'name': 'Гуманоид', 'aliases': [], 'description': '', 'evidence': ['p9']}
        self.first, self.second = source('— Чего? — спросил гость.', 'p0', 0), source('— Пять? — Отличный результат.', 'p1', 1)

    def reply(self, payload, speaker='ivan'):
        return {'reply': {'characters': [], 'passages': [attributed(item, speaker) for item in payload['segments']]}, 'known': ['ivan', 'envoy'], 'speech_structure': {}}

    def run_two(self, first_call, critique=None, second=None, extras=None):
        from rimsky.analysis import analyse_sources
        from test_core import agreeing_critic
        critique = critique or (lambda request, schema: agreeing_critic({'request': request}))
        state = {'cast': [self.ivan, self.envoy], 'accepted': {}, 'volume': {}, 'inputs': []}
        seen, critic_seen, holds = [], [], {}
        def commit(done, units, failed, key, records=(), structure=None, reviews=(), audit=()):
            holds[done[0]['id']] = sorted({trigger for record in records if record['status'] == 'review' for trigger in record['triggers']})
        def observe(request, schema):
            critic_seen.append(request)
            return critique(request, schema)
        inputs = [self.first, self.second]
        options = {'second': second} if second else {}
        analyse_sources([self.first], inputs, state, first_call, commit, critique=observe, **options)
        def capture(payload):
            seen.append(payload)
            return self.reply(payload)
        analyse_sources([self.second], inputs, state, capture, commit, critique=observe, **({'second': capture} if second else {}))
        return state, seen, critic_seen, holds

    def test_provider_disagreement_hold_never_reaches_next_primary_second_or_critic(self):
        state, seen, critic_seen, holds = self.run_two(self.reply, second=lambda payload: self.reply(payload, 'envoy'))
        self.assertEqual(['provider_disagreement'], holds['p0'])
        self.assertEqual(2, len(seen))
        for payload in seen:
            self.assertEqual([], payload['before'][0].get('accepted_speakers', []))
        self.assertEqual([], critic_seen[-1]['context_before'][0].get('accepted_speakers', []))
        self.assertEqual(['p1'], [entry['source_id'] for entry in state['trusted'] if entry['speaker'] == 'ivan'])
        self.assertEqual(8, state['volume']['ivan'])
        self.assertEqual(['p0', 'p1'], [entry['source_id'] for entry in state['inputs'] if entry['speaker'] == 'ivan'])

    def test_critic_local_and_degraded_holds_are_not_trusted(self):
        def disputing(request, schema):
            return {'passages': [{'id': target['id'], 'reviews': [{'index': part['index'], 'role_supported': True, 'speaker_supported': part['speaker'] == 'narrator',
                                                                  'alternative': '' if part['speaker'] == 'narrator' else 'unknown', 'evidence': [target['id']], 'reason': ''}
                                                                 for part in target['proposal']]} for target in request['targets']]}
        state, seen, _, holds = self.run_two(self.reply, critique=disputing)
        self.assertIn('critic_disagreement', holds['p0'])
        self.assertEqual([], seen[0]['before'][0].get('accepted_speakers', []))
        state, seen, _, holds = self.run_two(lambda payload: {**self.reply(payload), 'local_unverified': True})
        self.assertIn('local_fallback_review', holds['p0'])
        self.assertEqual([], seen[0]['before'][0].get('accepted_speakers', []))
        from rimsky.util import AnalysisCapacityError
        def too_large(payload):
            raise AnalysisCapacityError('Too large.')
        state, seen, _, holds = self.run_two(too_large)
        self.assertIn('degraded_analysis', holds['p0'])
        self.assertEqual([], seen[0]['before'][0].get('accepted_speakers', []))

    def test_trusted_continuity_is_kept(self):
        state, seen, critic_seen, holds = self.run_two(self.reply)
        self.assertEqual([], holds['p0'])
        self.assertEqual([{'speaker': 'ivan', 'text': '— Чего? '}], seen[0]['before'][0]['accepted_speakers'])
        self.assertEqual(['p0', 'p1'], [entry['source_id'] for entry in state['trusted'] if entry['speaker'] == 'ivan'])

    def test_current_batch_is_not_accepted_before_its_critic(self):
        from rimsky.analysis import analyse_sources
        from rimsky.util import AnalysisCapacityError
        from test_core import agreeing_critic
        seen = []
        def critique(request, schema):
            if len(request['targets']) > 1:
                raise AnalysisCapacityError('Too large.')
            seen.append(request)
            return agreeing_critic({'request': request})
        state = {'cast': [self.ivan, self.envoy], 'accepted': {}, 'volume': {}, 'inputs': []}
        analyse_sources([self.first, self.second], [self.first, self.second], state, self.reply, lambda *args, **kwargs: None, critique=critique)
        p1 = next(request for request in seen if request['targets'][0]['id'] == 'p1')
        self.assertEqual(['p0'], [item['id'] for item in p1['context_before']])
        self.assertEqual([], p1['context_before'][0].get('accepted_speakers', []))
        self.assertEqual({'p0', 'p1'}, set(state['accepted']))

    def test_a_verified_prefix_never_promotes_a_held_suffix(self):
        from rimsky.analysis import accepted_speakers
        from rimsky.store import trusted_inputs
        entry = {'source_id': 'b', 'start': 0, 'characters': 614, 'speaker': 'x', 'chapter_id': 'c'}
        hold = [{'source_id': 'b', 'start': 0, 'end': 614, 'speaker': 'x', 'tier': 'critic', 'triggers': ['critic_disagreement'], 'status': 'review'}]
        segments = [{'id': 's1', 'source_id': 'b', 'start': 0, 'end': 418, 'speaker': 'x'}, {'id': 's2', 'source_id': 'b', 'start': 418, 'end': 614, 'speaker': 'x'}]
        prefix = trusted_inputs([entry], hold, segments, {'s1': {'speaker': 'x'}})
        self.assertEqual([(0, 418)], [(item['start'], item['start'] + item['characters']) for item in prefix])
        self.assertEqual([(0, 614)], [(item['start'], item['start'] + item['characters'])
                                      for item in trusted_inputs([entry], hold, segments, {'s1': {'speaker': 'x'}, 's2': {'speaker': 'x'}})])
        self.assertEqual([], trusted_inputs([entry], hold, segments, {'s2': {'speaker': 'y'}, 's1': {'speaker': 'y'}}))
        source_block = {'id': 'b-0-614', 'source_id': 'b', 'start': 0, 'end': 614, 'text': 'x' * 614}
        self.assertEqual({'b-0-614': [{'start': 0, 'end': 418, 'speaker': 'x'}]}, accepted_speakers([source_block], prefix))

    def test_a_held_middle_range_splits_the_input_and_mixed_parts_need_their_own_verdict(self):
        from rimsky.store import trusted_inputs
        entry = {'source_id': 'b', 'start': 0, 'characters': 100, 'speaker': 'x', 'chapter_id': 'c'}
        middle = [{'source_id': 'b', 'start': 40, 'end': 60, 'speaker': 'x', 'tier': 'descriptor', 'triggers': ['descriptor_identity'], 'status': 'review'}]
        self.assertEqual([(0, 40), (60, 100)], [(item['start'], item['start'] + item['characters']) for item in trusted_inputs([entry], middle)])
        mixed = {'id': 'm', 'source_id': 'b', 'start': 0, 'end': 100, 'speaker': 'x',
                 'performance': {'internal_transition': True, 'analysis_parts': [{'start': 0, 'end': 50, 'speaker': 'x'}, {'start': 50, 'end': 100, 'speaker': 'x'}]}}
        whole = [{**middle[0], 'start': 0, 'end': 100}]
        self.assertEqual([(0, 50)], [(item['start'], item['start'] + item['characters']) for item in trusted_inputs([entry], whole, [mixed], {'m#0': {'speaker': 'x'}})])
        self.assertEqual([(0, 100)], [(item['start'], item['start'] + item['characters'])
                                      for item in trusted_inputs([entry], whole, [mixed], {'m#0': {'speaker': 'x'}, 'm#1': {'speaker': 'x'}})])

    def test_real_synthesis_units_partial_full_and_gapped_verdict_coverage(self):
        from rimsky.analysis import synthesis_units
        from rimsky.casting import casting_inputs
        from rimsky.store import speaker_review_holds, trusted_inputs
        text = '— ' + 'Иван говорил спокойно. ' * 60 + 'Он замолчал.'
        original = source(text, 'p0', 0)
        cut = text.index('Он замолчал.')
        parts = [{**original, 'start': left, 'end': right, 'text': text[left:right], 'speaker': speaker, 'delivery': 'neutral', 'context': dict(NEUTRAL_CONTEXT), 'warning': ''}
                 for left, right, speaker in ((0, cut, 'ivan'), (cut, len(text), 'narrator'))]
        units = synthesis_units(parts, [original])
        speech = [unit for unit in units if unit['start'] < cut]
        self.assertGreaterEqual(len(speech), 3)
        entry = casting_inputs(parts)[0]
        record = {'source_id': 'p0', 'start': 0, 'end': cut, 'speaker': 'ivan', 'tier': 'critic', 'triggers': ['critic_disagreement'], 'status': 'review'}
        ranges = lambda verdict_units: [(item['start'], item['start'] + item['characters']) for item in
                                        trusted_inputs([entry], [record], units, {unit['id']: {'speaker': 'ivan'} for unit in verdict_units})]
        self.assertEqual([], ranges([]))
        self.assertEqual([(speech[0]['start'], speech[0]['end'])], ranges(speech[:1]))
        self.assertIn(speech[1]['id'], speaker_review_holds(units, [record], {speech[0]['id']: {'speaker': 'ivan'}})[0])
        self.assertEqual([(0, cut)], ranges(speech))
        gapped = ranges([speech[0], *speech[2:]])
        self.assertEqual([(speech[0]['start'], speech[0]['end']), (speech[2]['start'], cut)], gapped)
        self.assertNotIn(speech[1]['start'], [left for left, _ in gapped])

    def test_trust_view_filters_exact_ranges_and_follows_human_verdicts(self):
        from rimsky.store import trusted_inputs
        inputs = [{'source_id': 'b', 'start': 0, 'characters': 8, 'speaker': 'ivan', 'chapter_id': 'c'},
                  {'source_id': 'b', 'start': 8, 'characters': 14, 'speaker': 'narrator', 'chapter_id': 'c'},
                  {'source_id': 'b', 'start': 22, 'characters': 6, 'speaker': 'envoy', 'chapter_id': 'c'}]
        held = [{'source_id': 'b', 'start': 22, 'end': 28, 'speaker': 'envoy', 'tier': 'critic', 'triggers': ['critic_disagreement'], 'status': 'review'},
                {'source_id': 'b', 'start': 0, 'end': 8, 'speaker': 'ivan', 'tier': 'presentation', 'triggers': ['presentation_review'], 'status': 'review'}]
        self.assertEqual(['ivan', 'narrator'], [entry['speaker'] for entry in trusted_inputs(inputs, held)])
        segment = {'id': 's1', 'source_id': 'b', 'start': 0, 'end': 28, 'speaker': 'envoy',
                   'performance': {'internal_transition': True, 'analysis_parts': [{'start': 0, 'end': 8, 'speaker': 'ivan'}, {'start': 8, 'end': 22, 'speaker': 'narrator'},
                                                                                  {'start': 22, 'end': 28, 'speaker': 'envoy'}]}}
        self.assertEqual(3, len(trusted_inputs(inputs, held, [segment], {'s1#2': {'speaker': 'envoy'}})))
        self.assertEqual(2, len(trusted_inputs(inputs, held, [segment], {'s1#2': {'speaker': 'ivan'}})))
        self.assertEqual(2, len(trusted_inputs(inputs, held, [segment], {'s1#0': {'speaker': 'ivan'}})))
        locked = [{**held[0], 'triggers': ['critic_disagreement', 'backend_limit']}]
        self.assertEqual(3, len(trusted_inputs(inputs, locked)))


class CliFailureTypes(unittest.TestCase):
    def test_analysis_environment_strips_credentials_and_path_search_ignores_relative_entries(self):
        from rimsky.cli_process import analysis_environment, trusted_executable
        clean = analysis_environment({'PATH': 'safe', 'HOME': 'home', 'OPENAI_API_KEY': 'secret',
                                      'ANTHROPIC_API_KEY': 'secret', 'RANDOM_TOKEN': 'secret',
                                      'HTTPS_PROXY': 'http://proxy', 'SSL_CERT_FILE': 'certificate.pem'})
        self.assertEqual({'PATH': 'safe', 'HOME': 'home', 'HTTPS_PROXY': 'http://proxy',
                          'SSL_CERT_FILE': 'certificate.pem'}, clean)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = root / 'safe'
            safe.mkdir()
            executable = safe / ('codex.exe' if os.name == 'nt' else 'codex')
            executable.write_bytes(b'MZ')
            with patch.dict(os.environ, {'PATH': os.pathsep.join(['.', str(safe)]), 'PATHEXT': '.EXE'}, clear=False):
                self.assertEqual(str(executable.resolve()), trusted_executable(('codex.exe', 'codex'), label='Codex CLI'))

    def test_cli_stderr_decoding_replaces_invalid_bytes(self):
        from rimsky.cli_process import run_cli
        result = run_cli([sys.executable, '-c', "import os; os.write(2, b'\\xff')"], timeout=10)
        self.assertEqual((0, '\ufffd'), (result.returncode, result.stderr))

    def test_closing_worker_terminates_its_complete_process_tree(self):
        from rimsky.jobs import Engine
        engine = Engine.__new__(Engine)
        engine.close_lock = __import__('threading').Lock()
        engine.reader = __import__('threading').current_thread()
        class Stream:
            def close(self): pass
        class Process:
            stdin = Stream()
            def poll(self): return None
            def wait(self, timeout=None): return 0
            def kill(self): raise AssertionError('tree termination should finish the worker')
        engine.process = Process()
        with patch('rimsky.jobs.terminate_process_tree') as terminate:
            engine.close()
        terminate.assert_called_once_with(engine.process)

    def test_codex_disables_shell_plugins_browsers_and_mcp(self):
        from rimsky import codex_analysis
        from rimsky.util import ProviderTransportError
        failed = subprocess.CompletedProcess([], 2, '', 'stop')
        with patch('rimsky.codex_analysis.codex_executable', return_value='/bin/codex'), \
                patch('rimsky.codex_analysis.run_cli', return_value=failed) as invoked, \
                self.assertRaises(ProviderTransportError):
            codex_analysis.structured_call('system', {}, {})
        command = invoked.call_args.args[0]
        disabled = {command[index + 1] for index, item in enumerate(command[:-1]) if item == '--disable'}
        self.assertTrue({'shell_tool', 'apps', 'plugins', 'browser_use', 'computer_use',
                         'enable_mcp_apps', 'mcp_2026_07_28'} <= disabled)
        self.assertIn('--ignore-user-config', command)

    def test_claude_windows_shim_prefers_adjacent_native_binary(self):
        from rimsky import claude_analysis
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shim = root / 'claude.cmd'
            native = root / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe'
            shim.write_text('@echo off')
            native.parent.mkdir(parents=True)
            native.write_bytes(b'MZ')
            located = lambda name: str(shim) if name == 'claude.cmd' else None
            with patch('rimsky.claude_analysis.trusted_executable', return_value=str(shim)):
                self.assertEqual(str(native), claude_analysis.claude_executable())

    def test_cli_adapters_raise_typed_missing_and_transport_errors(self):
        import subprocess
        from rimsky import claude_analysis, codex_analysis
        from rimsky.util import ProviderTransportError, ProviderUnavailableError
        for module, resolver, runner in ((codex_analysis, 'rimsky.codex_analysis.codex_executable', 'rimsky.codex_analysis.run_cli'),
                                         (claude_analysis, 'rimsky.claude_analysis.claude_executable', 'rimsky.claude_analysis.run_cli')):
            with self.subTest(module=module.__name__):
                with patch(resolver, side_effect=ProviderUnavailableError('missing')), self.assertRaises(ProviderUnavailableError):
                    module.structured_call('system', {}, {})
                with patch(resolver, return_value='/bin/cli'), patch(runner, side_effect=subprocess.TimeoutExpired('cli', 1)), \
                        self.assertRaises(ProviderTransportError):
                    module.structured_call('system', {}, {})
                failed = subprocess.CompletedProcess([], 2, '', 'boom')
                with patch(resolver, return_value='/bin/cli'), patch(runner, return_value=failed) as invoked, self.assertRaises(ProviderTransportError):
                    module.structured_call('system', {}, {})
                self.assertEqual(2, invoked.call_count)

    def test_claude_transport_retry_keeps_the_same_bounded_request(self):
        import subprocess
        from rimsky import claude_analysis
        failed = subprocess.CompletedProcess([], 2, '', 'temporary TLS failure')
        passed = subprocess.CompletedProcess(
            [], 0, json.dumps({'structured_output': {'ok': True}}), '')
        with patch('rimsky.claude_analysis.claude_executable', return_value='/bin/claude'), \
                patch('rimsky.claude_analysis.run_cli', side_effect=[failed, passed]) as invoked:
            self.assertEqual({'ok': True}, claude_analysis.structured_call(
                'system', {'book': 'data'}, {'type': 'object'}))
        self.assertEqual(2, invoked.call_count)
        first, second = invoked.call_args_list
        self.assertEqual(first.args, second.args)
        self.assertEqual(first.kwargs['input_text'], second.kwargs['input_text'])

    def test_claude_large_schema_uses_stdin_instead_of_command_line(self):
        import subprocess
        from rimsky import claude_analysis
        passed = subprocess.CompletedProcess(
            [], 0, json.dumps({'result': json.dumps({'ok': True})}), '')
        schema = {'type': 'object', 'description': 'x' * 30_000}
        metadata = {}
        with patch('rimsky.claude_analysis.claude_executable', return_value='/bin/claude'), \
                patch('rimsky.claude_analysis.run_cli', return_value=passed) as invoked:
            self.assertEqual({'ok': True}, claude_analysis.structured_call(
                'system', {'book': 'data'}, schema, metadata=metadata))
        analysis_call = next(call for call in invoked.call_args_list
                             if 'input_text' in call.kwargs)
        command = analysis_call.args[0]
        self.assertNotIn('--json-schema', command)
        self.assertIn('The required output JSON Schema follows.',
                      analysis_call.kwargs['input_text'])
        self.assertEqual('stdin', metadata['schema_transport'])

    def test_worker_codes_and_engine_decoding_keep_the_type(self):
        import contextlib
        import io
        import threading
        import worker
        from rimsky.jobs import Engine
        from rimsky.util import ProviderTransportError, ProviderUnavailableError
        for error, code in ((ProviderUnavailableError('No CLI.'), 'provider_unavailable'), (ProviderTransportError('CLI failed.'), 'provider_transport')):
            output = io.StringIO()
            with self.subTest(code=code), patch.object(worker, 'analyze', side_effect=error), \
                    patch('sys.stdin', io.StringIO(json.dumps({'id': 'r', 'method': 'analyze', 'data': {}}) + '\n')), contextlib.redirect_stdout(output):
                worker.main()
            self.assertEqual({'id': 'r', 'error': str(error), 'error_code': code}, json.loads(output.getvalue()))
        with tempfile.TemporaryDirectory() as temporary:
            import sys as _sys
            script = Path(temporary) / 'rimsky-infer'
            script.write_text('#!' + _sys.executable + '\nimport sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({"id":r["id"],"error":"No CLI.","error_code":"provider_unavailable"}),flush=True)\n')
            script.chmod(0o755)
            engine = Engine(Path(temporary))
            try:
                with self.assertRaises(ProviderUnavailableError):
                    engine.call('analyze', {}, threading.Event(), timeout=5)
                self.assertIsNone(engine.process.poll())
            finally:
                engine.close()

    def test_second_repair_reuses_its_own_fixed_structure(self):
        from rimsky import cli_analysis
        calls = []
        def structured(system, request, schema, **options):
            calls.append(system)
            return {'characters': [], 'passages': []}
        segment = source('— Да, — сказал Иван.')
        payload = {'segments': [segment], 'cast': [], 'before': [], 'after': [],
                   'fixed_structure': {'p1': [{'start': 0, 'end': len(segment['text']), 'text': segment['text'], 'mode': 'speech'}]}}
        envelope = cli_analysis.envelope(payload, structured)
        self.assertEqual(payload['fixed_structure'], envelope['speech_structure'])
        self.assertEqual(1, len(calls))


class LocalStructure(unittest.TestCase):
    def test_single_pass_local_analysis_runs_the_structure_pass_and_rejects_narrated_speech(self):
        import json as _json
        import worker
        from unittest.mock import Mock
        text = '— Да, — сказал Иван.'
        target = source(text)
        seen = []
        def complete(messages, response_format, temperature, max_tokens):
            request = _json.loads(messages[1]['content'])
            seen.append(request)
            if 'dash_targets' in request:
                return {'choices': [{'message': {'content': _json.dumps({'p1': ['speech', 'narrator']})}, 'finish_reason': 'stop'}]}
            reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [{'start': 0, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
            return {'choices': [{'message': {'content': _json.dumps(reply)}, 'finish_reason': 'stop'}]}
        model = Mock()
        model.tokenize.side_effect = lambda data: [1] * (len(data) // 4)
        model.create_chat_completion.side_effect = complete
        with patch.object(worker, 'load_text', return_value=model):
            result = worker.analyze({'pack': {'text_model': {'context': 8192, 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'segments': [target], 'cast': []})
        self.assertIn('dash_targets', seen[0])
        self.assertEqual([[0, 0, 'speech'], [1, 1, 'narrator']], [[chunk['first'], chunk['last'], chunk['mode']] for chunk in seen[1]['fixed_speech_structure'][0]['chunks']])
        _, _, invalid = review_analysis(result['reply'], [target], [], fixed=result['speech_structure'])
        self.assertIn('fixed_speech_structure', invalid['p1'])


class Performance(unittest.TestCase):
    def test_pace_reaches_the_tts_instruction_and_the_render_key(self):
        from rimsky.jobs import render_controls, render_key, segment_pauses
        pack = {'_fingerprint': 'f', 'speech': {'variant': 'custom', 'instruction_control': True}}
        heading = {'source_id': 'p', 'kind': 'heading', 'spoken': 'Глава первая', 'performance': {'schema': 2, 'pace': 'deliberate', 'ending_pace': None,
                                                                                                  'pause_before_seconds': 0.85, 'pause_after_seconds': 1.15}}
        part = {'text': 'Глава первая', 'delivery': 'neutral', 'speaker': 'narrator', 'performance': heading['performance']}
        self.assertEqual(({'pace': 'deliberate'}, False), render_controls(pack, part, heading))
        self.assertEqual(({}, True), render_controls({**pack, 'speech': {'variant': 'clone'}}, part, heading))
        self.assertEqual([0.85, 1.15], segment_pauses(heading))
        self.assertEqual([], segment_pauses({**heading, 'spoken': '* * *'}))
        voice = {'speaker': 'Ryan'}
        config = {'device': 'cpu'}
        self.assertNotEqual(render_key(pack, config, 'Глава', voice, 'neutral', 'Russian', 0), render_key(pack, config, 'Глава', voice, 'neutral', 'Russian', 0, {'pace': 'deliberate'}))
        self.assertEqual(render_key(pack, config, 'Глава', voice, 'neutral', 'Russian', 0), render_key(pack, config, 'Глава', voice, 'neutral', 'Russian', 0, {}))

    def test_worker_builds_the_instruction_from_delivery_and_pace(self):
        import worker
        self.assertEqual('Use a slower, deliberate pace with clear phrasing.', worker.PACES['pace']['deliberate'])
        instruction = ' '.join([worker.INSTRUCTIONS['neutral'], *(worker.PACES[key][value] for key, value in {'pace': 'brisk', 'ending_pace': 'deliberate'}.items())])
        self.assertIn('brisk', instruction)
        self.assertIn('trailing off', instruction)

    def test_pauses_pad_the_concatenated_audio(self):
        from rimsky.jobs import concatenate
        with tempfile.TemporaryDirectory() as temp:
            piece = Path(temp) / 'a.wav'
            write_wave(piece, .1)
            result = concatenate([piece], Path(temp) / 'b.wav', [0.5, 0.25])
            self.assertAlmostEqual(0.85, result['duration'], places=3)


class Timing(unittest.TestCase):
    def mixed(self):
        text = '— Я думал… — сказал Иван, — но нет.'
        target = source(text)
        cuts = [text.index('— сказал'), text.index('— но')]
        reply = {'characters': [], 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
            {'start': 0, 'end': cuts[0], 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']},
            {'start': cuts[0], 'end': cuts[1], 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []},
            {'start': cuts[1], 'end': len(text), 'speaker': 'hero', 'delivery': 'neutral', 'evidence': ['p1']}])}]}
        parts, _ = validate_analysis(reply, [target], [HERO])
        [unit] = synthesis_units(parts, [target])
        return unit

    def test_interior_ellipsis_pause_is_placed_between_parts(self):
        from rimsky.jobs import segment_timeline
        unit = self.mixed()
        self.assertEqual([0.0, 0.8, 0.0, 0.0], segment_timeline(unit, render_parts(unit)))

    def test_concatenation_writes_exact_interior_and_outer_silence(self):
        from rimsky.jobs import concatenate
        with tempfile.TemporaryDirectory() as temp:
            pieces = []
            for index in range(3):
                piece = Path(temp) / f'{index}.wav'
                write_wave(piece, .1)
                pieces.append(piece)
            concatenate(pieces, Path(temp) / 'all.wav', [0.25, 0.8, 0.0, 0.5])
            with wave.open(str(Path(temp) / 'all.wav'), 'rb') as audio:
                self.assertEqual(round(24000 * (0.3 + 0.25 + 0.8 + 0.5)), audio.getnframes())
                frames = audio.readframes(audio.getnframes())
            start = round(24000 * (0.25 + 0.1)) * 2
            self.assertEqual(bytes(round(24000 * 0.8) * 2), frames[start:start + round(24000 * 0.8) * 2])

    def test_timeline_changes_the_composite_key(self):
        from rimsky.util import digest
        self.assertNotEqual(digest({'schema': 3, 'parts': ['a', 'b']}), digest({'schema': 3, 'parts': ['a', 'b'], 'pauses': [0, 0.8, 0]}))


class PaceCapability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def generation(self, instruction):
        from rimsky.packs import load_pack
        from rimsky.util import write_json
        pack = pack_fixture(self.root / 'pack')
        manifest = json.loads((self.root / 'pack' / 'pack.json').read_text('utf-8'))
        manifest['speech']['instruction_control'] = instruction
        write_json(self.root / 'pack' / 'pack.json', manifest)
        pack = load_pack(str(self.root / 'pack'), verify=True)
        return self.store.create_generation(self.book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'], 'pack_name': pack['name'],
                                                               'device': 'cpu', 'align_words': False})

    def run_job(self, generation):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: SyntheticEngine())
        jobs.start(generation['id'])
        jobs.thread.join(timeout=10)
        return self.store.generation(generation['id'])

    def test_unsupported_pace_renders_with_a_visible_best_effort_notice(self):
        done = self.run_job(self.generation(False))
        self.assertEqual('ready', done['status'], done['error'])
        warned = [segment for segment in done['segments'] if 'default pace' in segment['warning']]
        self.assertTrue(warned and all(segment['status'] == 'ready' and segment['audio_hash'] for segment in warned))
        self.assertNotIn('performance_capability', done['analysis']['speaker_review_holds']['reasons'])
        self.assertTrue(all(segment['status'] == 'ready' for segment in done['segments']))

    def test_preview_policy_does_not_turn_a_capability_notice_into_a_hold(self):
        self.store.update_settings({'unresolved_policy': 'render_with_warning'})
        generation = self.generation(False)
        self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps({**generation['config'], 'unresolved_policy': 'render_with_warning'}), generation['id']))
        done = self.run_job(self.store.generation(generation['id']))
        self.assertEqual('ready', done['status'], done['error'])
        capability = [segment for segment in done['segments'] if 'default pace' in segment['warning']]
        self.assertTrue(capability)
        self.assertTrue(all(segment['status'] == 'ready' and segment['audio_hash'] for segment in capability))

    def test_a_legacy_capability_hold_is_removed_on_resume(self):
        done = self.run_job(self.generation(False))
        target = next(segment for segment in done['segments'] if 'default pace' in segment['warning'])
        analysis = done['analysis']
        analysis['speaker_resolution'].append({'target_id': target['id'], 'source_id': target['source_id'], 'start': target['start'], 'end': target['end'],
                                               'speaker': target['speaker'], 'primary_speaker': target['speaker'], 'tier': 'performance',
                                               'triggers': ['performance_capability'], 'status': 'review'})
        self.store.execute('UPDATE generations SET analysis=?,status=? WHERE id=?', (json.dumps(analysis), 'paused', done['id']))
        self.store.execute("UPDATE segments SET status='review',audio_hash=NULL,duration=0,warning=? WHERE generation_id=? AND id=?",
                           ('Held: legacy capability record.', done['id'], target['id']))
        again = self.run_job(self.store.generation(done['id']))
        resumed = {segment['id']: segment for segment in again['segments']}[target['id']]
        self.assertEqual('ready', resumed['status'])
        self.assertTrue(resumed['audio_hash'])
        self.assertNotIn('performance_capability', again['analysis']['speaker_review_holds']['reasons'])

    def test_supported_pace_renders_with_the_instruction(self):
        done = self.run_job(self.generation(True))
        self.assertEqual('ready', done['status'], done['error'])
        self.assertNotIn('performance_capability', (done['analysis'].get('speaker_review_holds') or {}).get('reasons', {}))


class MergedDescriptors(unittest.TestCase):
    def test_merging_into_a_descriptor_identity_keeps_other_scenes_held(self):
        from rimsky.jobs import merged_descriptor_holds
        cast = [{'id': 'g', 'name': 'охранник', 'aliases': [], 'scopes': [['c1', 's1']]}]
        inputs = [{'speaker': 'x', 'chapter_id': 'c2', 'scene_id': 's2', 'source_id': 'q', 'start': 0, 'characters': 5},
                  {'speaker': 'x', 'chapter_id': 'c1', 'scene_id': 's1', 'source_id': 'p', 'start': 0, 'characters': 5}]
        records = merged_descriptor_holds(inputs, {'x': 'g'}, cast, frozenset())
        self.assertEqual([('q', 'descriptor_identity')], [(item['source_id'], item['triggers'][0]) for item in records])
        named = [{**cast[0], 'name': 'Иван'}]
        self.assertEqual([], merged_descriptor_holds(inputs, {'x': 'g'}, named, frozenset()))

    def test_two_unnamed_people_in_one_scene_stay_distinct(self):
        text = '— Стой! — крикнул усатый мужчина. — Лежать! — добавил усатый офицер.'
        target = source(text)
        cuts = [text.index('— крикнул'), text.index('— Лежать'), text.index('— добавил')]
        reply = {'characters': [{'id': 'new_1', 'name': 'усатый мужчина', 'aliases': [], 'description': '', 'evidence': ['p1'], 'kind': 'person',
                                 'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}},
                                {'id': 'new_2', 'name': 'усатый офицер', 'aliases': [], 'description': '', 'evidence': ['p1'], 'kind': 'person',
                                 'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}],
                 'passages': [{'id': 'p1', 'parts': unit_parts(text, [
                     {'start': 0, 'end': cuts[0], 'speaker': 'new_1', 'delivery': 'neutral', 'evidence': ['p1']},
                     {'start': cuts[0], 'end': cuts[1], 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []},
                     {'start': cuts[1], 'end': cuts[2], 'speaker': 'new_2', 'delivery': 'neutral', 'evidence': ['p1']},
                     {'start': cuts[2], 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}])}]}
        parts, cast = validate_analysis(reply, [target], [])
        self.assertEqual(2, len({part['speaker'] for part in parts} - {'narrator'}))
        self.assertEqual(['усатый мужчина', 'усатый офицер'], sorted(character['name'] for character in cast))


class HeldGenerations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def generation(self, **settings):
        self.store.update_settings({'cast_review': False, **settings})
        pack = pack_fixture(self.root / 'pack')
        return self.store.create_generation(self.book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'], 'pack_name': pack['name'],
                                                               'device': 'cpu', 'align_words': False,
                                                               'unresolved_policy': self.store.settings()['unresolved_policy']})

    def run_job(self, generation, engine, approve=False):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        jobs.start(generation['id'], approve)
        jobs.thread.join(timeout=10)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def test_resume_rebuilds_hints_from_the_trusted_view_only(self):
        from unittest.mock import patch as patched
        done = self.run_job(self.generation(), SyntheticEngine())
        self.assertIn(done['status'], ('ready', 'held'), done['error'])
        target = done['analysis']['casting_inputs'][0]
        hold = {'target_id': 'x', 'source_id': target['source_id'], 'start': target['start'], 'end': target['start'] + target['characters'],
                'speaker': target['speaker'], 'primary_speaker': target['speaker'], 'tier': 'critic', 'triggers': ['critic_disagreement'], 'status': 'review'}
        captured = []
        def capture(targets, inputs, state, *args, **kwargs):
            captured.append({key: state[key] for key in ('accepted', 'volume', 'trusted', 'inputs')})
        for verdicts, expected in (({}, False), (None, True)):
            analysis = {**done['analysis'], 'complete': False, 'speaker_resolution': [*done['analysis'].get('speaker_resolution', []), hold]}
            if verdicts is None:
                segment = next(item for item in done['segments'] if item['source_id'] == target['source_id'] and item['start'] <= target['start'] < item['end'])
                parts = (segment.get('performance') or {}).get('analysis_parts') or []
                index = next((i for i, part in enumerate(parts) if part['start'] <= target['start'] < part['end']), None)
                key = f'{segment["id"]}#{index}' if (segment.get('performance') or {}).get('internal_transition') and len(parts) > 1 else segment['id']
                analysis['speaker_verdicts'] = {key: {'speaker': target['speaker'], 'tier': 'human_confirmation'}}
            self.store.execute("UPDATE generations SET status='paused',analysis=? WHERE id=?", (json.dumps(analysis), done['id']))
            captured.clear()
            with patched('rimsky.jobs.analyse_sources', side_effect=capture):
                self.run_job(self.store.generation(done['id']), SyntheticEngine())
            state = captured[0]
            self.assertEqual(len(done['analysis']['casting_inputs']), len(state['inputs']))
            self.assertEqual(expected, target in state['trusted'])
            self.assertEqual(len(state['trusted']), len(state['inputs']) - (0 if expected else 1))

    def test_successful_resume_retries_passages_degraded_before_the_breaker(self):
        done = self.run_job(self.generation(), SyntheticEngine())
        original_inputs = done['analysis']['casting_inputs']
        source_id = done['analysis']['completed_sources'][0]
        analysis = {**done['analysis'], 'complete': False,
                    'degraded': [{'source': source_id, 'reason': 'systemic invalid reply'}],
                    'validation_guard': {'key': 'analysis_invalid:same', 'consecutive': 4,
                                         'recent': ['analysis_invalid:same'] * 4}}
        self.store.execute("UPDATE generations SET status='paused',analysis=? WHERE id=?",
                           (json.dumps(analysis), done['id']))
        engine = SyntheticEngine()
        recovered = self.run_job(self.store.generation(done['id']), engine)
        self.assertGreater(engine.analyzed, 0)
        self.assertNotIn(source_id, {item.get('source') for item in recovered['analysis'].get('degraded', [])})
        self.assertEqual(0, recovered['analysis']['validation_guard']['consecutive'])
        self.assertEqual(len(original_inputs), len(recovered['analysis']['casting_inputs']))
        keys = [(item['source_id'], item['start'], item['characters'], item['speaker'])
                for item in recovered['analysis']['casting_inputs']]
        self.assertEqual(len(keys), len(set(keys)))

    def test_resume_trusts_only_the_verified_segment_of_a_held_input_spanning_segments(self):
        from unittest.mock import patch as patched
        done = self.run_job(self.generation(), SyntheticEngine())
        entry = max(done['analysis']['casting_inputs'], key=lambda item: item['characters'])
        segment = next(item for item in done['segments'] if item['source_id'] == entry['source_id'] and item['start'] == entry['start'])
        middle = entry['start'] + entry['characters'] // 2
        self.store.execute('UPDATE segments SET end=?,text=?,spoken=? WHERE generation_id=? AND id=?',
                           (middle, segment['text'][:middle - segment['start']], segment['text'][:middle - segment['start']], done['id'], segment['id']))
        self.store.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,warning,performance) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (done['id'], segment['id'] + '-tail', segment['ordinal'] + 1, segment['chapter_id'], segment['source_id'], middle, segment['end'],
                            segment['text'][middle - segment['start']:], segment['text'][middle - segment['start']:], segment['speaker'], segment['delivery'],
                            segment['status'], segment['warning'], '{}'))
        hold = {'target_id': 'x', 'source_id': entry['source_id'], 'start': entry['start'], 'end': entry['start'] + entry['characters'], 'speaker': entry['speaker'],
                'primary_speaker': entry['speaker'], 'tier': 'critic', 'triggers': ['critic_disagreement'], 'status': 'review'}
        captured = []
        def capture(targets, inputs, state, *args, **kwargs):
            captured.append([(item['start'], item['start'] + item['characters']) for item in state['trusted'] if item['source_id'] == entry['source_id']])
            self.assertEqual(len(done['analysis']['casting_inputs']), len(state['inputs']))
        end = entry['start'] + entry['characters']
        for verdicts, expected in (({}, []), ({segment['id']: {'speaker': entry['speaker']}}, [(entry['start'], middle)]),
                                   ({segment['id']: {'speaker': entry['speaker']}, segment['id'] + '-tail': {'speaker': entry['speaker']}}, [(entry['start'], end)]),
                                   ({segment['id'] + '-tail': {'speaker': entry['speaker']}}, [(middle, end)])):
            analysis = {**done['analysis'], 'complete': False, 'speaker_resolution': [*done['analysis'].get('speaker_resolution', []), hold], 'speaker_verdicts': verdicts}
            self.store.execute("UPDATE generations SET status='paused',analysis=? WHERE id=?", (json.dumps(analysis), done['id']))
            captured.clear()
            with patched('rimsky.jobs.analyse_sources', side_effect=capture):
                self.run_job(self.store.generation(done['id']), SyntheticEngine())
            self.assertEqual(expected, captured[0])

    def test_unknown_speech_is_held_not_narrated_and_generation_is_held(self):
        class Unknown(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    self.analyzed += 1
                    passages = []
                    for segment in payload['segments']:
                        text = segment['text']
                        if text.startswith('—'):
                            cut = text.index('—', 1)
                            parts = [{'start': 0, 'end': cut, 'speaker': 'unknown', 'delivery': 'neutral', 'evidence': []},
                                     {'start': cut, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}]
                        else:
                            parts = [{'start': 0, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'evidence': []}]
                        passages.append({'id': segment['id'], 'parts': unit_parts(text, parts)})
                    return {'characters': [], 'passages': passages}
                return super().call(method, payload, stop)
        engine = Unknown()
        generation = self.run_job(self.generation(), engine)
        self.assertEqual('held', generation['status'], generation['error'])
        held = [segment for segment in generation['segments'] if segment['status'] == 'review']
        self.assertTrue(any(segment['speaker'] == 'unknown' for segment in held))
        self.assertTrue(all(segment['audio_hash'] is None for segment in held))
        self.assertTrue(all(segment['status'] == 'ready' for segment in generation['segments'] if segment not in held))
        self.assertEqual(generation['analysis']['speaker_review_holds']['policy'], 'hold')
        again = self.run_job(generation, SyntheticEngine())
        self.assertEqual('held', again['status'])

    def test_degraded_passages_are_held_with_their_reason(self):
        generation = self.run_job(self.generation(), VariedInvalidEngine())
        self.assertEqual('held', generation['status'])
        self.assertTrue(all(segment['status'] == 'review' and DEGRADED_HOLD in segment['warning'] for segment in generation['segments']))
        self.assertTrue(all(item['reason'] for item in generation['analysis']['speaker_resolution']))

    def test_preview_policy_renders_but_still_ends_held(self):
        generation = self.run_job(self.generation(unresolved_policy='render_with_warning'), VariedInvalidEngine())
        self.assertEqual('held', generation['status'])
        self.assertTrue(all(segment['status'] == 'ready' and 'Unreviewed preview' in segment['warning'] for segment in generation['segments']))

    def test_mixed_sentence_renders_each_part_in_its_own_voice_and_concatenates(self):
        from test_core import CastEngine
        engine = CastEngine()
        generation = self.run_job(self.generation(), engine)
        self.assertEqual('ready', generation['status'], generation['error'])
        mixed = [segment for segment in generation['segments'] if segment['performance'].get('internal_transition')]
        self.assertTrue(mixed)
        for segment in mixed:
            with wave.open(str(self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'), 'rb') as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), .1 * len(segment['performance']['analysis_parts']), places=3)
        self.assertEqual(3, len(engine.render_voices))

    def test_generation_cap_holds_the_passage_without_clipping_or_rerendering(self):
        class Capped(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render' and 'Иван кивнул' in payload['text']:
                    self.capped = getattr(self, 'capped', 0) + 1
                    raise GenerationCapError('cap')
                return super().call(method, payload, stop)
        engine = Capped()
        generation = self.run_job(self.generation(), engine)
        self.assertEqual('held', generation['status'], generation['error'])
        capped = [segment for segment in generation['segments'] if 'Иван кивнул' in segment['text']]
        self.assertEqual(['review'], [segment['status'] for segment in capped])
        self.assertEqual(capped[0]['text'], capped[0]['spoken'])
        self.assertEqual(2, engine.capped)
        again = Capped()
        self.assertEqual('held', self.run_job(generation, again)['status'])
        self.assertEqual(0, getattr(again, 'capped', 0))

    def test_incomplete_old_checkpoint_is_preserved_before_restart(self):
        generation = self.generation()
        old = {'complete': False, 'casting_schema': 3, 'completed_sources': ['x'], 'speaker_resolution': [{'status': 'review'}]}
        self.store.execute('UPDATE generations SET analysis=?,cast=? WHERE id=?', (json.dumps(old), json.dumps([{'id': 'old'}]), generation['id']))
        ready = self.run_job(generation, SyntheticEngine())
        self.assertEqual('ready', ready['status'], ready['error'])
        saved = ready['analysis']['superseded_checkpoint']
        preserved = json.loads((self.store.root / saved['file']).read_text('utf-8'))
        self.assertEqual(old, preserved['analysis'])
        self.assertEqual([{'id': 'old'}], preserved['cast'])

    def test_repair_of_a_stale_audio_hash_renders_under_the_current_key(self):
        generation = self.run_job(self.generation(), SyntheticEngine())
        self.assertEqual('ready', generation['status'], generation['error'])
        target = generation['segments'][0]
        self.store.execute("UPDATE segments SET audio_hash=? WHERE generation_id=? AND id=?", ('0' * 64, generation['id'], target['id']))
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: SyntheticEngine())
        jobs.repair(generation['id'])
        jobs.thread.join(timeout=10)
        repaired = {segment['id']: segment for segment in self.store.generation(generation['id'])['segments']}[target['id']]
        self.assertEqual('ready', repaired['status'])
        self.assertTrue((self.store.root / 'audio' / f'{repaired["audio_hash"]}.wav').is_file())
        self.assertTrue((self.store.root / 'audio' / f'{repaired["audio_hash"]}.json').is_file())

    def test_unknown_speech_from_an_older_analysis_without_records_is_held(self):
        generation = self.run_job(self.generation(), SyntheticEngine(), )
        gid = generation['id']
        target = generation['segments'][1]
        self.store.execute("UPDATE segments SET speaker='unknown',status='pending',audio_hash=NULL,performance='{}' WHERE generation_id=? AND id=?", (gid, target['id']))
        analysis = {key: value for key, value in generation['analysis'].items() if key not in ('speaker_resolution', 'speaker_review_holds')}
        self.store.execute("UPDATE generations SET status='paused',analysis=? WHERE id=?", (json.dumps(analysis), gid))
        again = self.run_job(self.store.generation(gid), SyntheticEngine())
        self.assertEqual('held', again['status'])
        held = {segment['id']: segment for segment in again['segments']}[target['id']]
        self.assertEqual(('review', None), (held['status'], held['audio_hash']))
        self.assertTrue(any('unresolved_speaker' in item['triggers'] for item in again['analysis']['speaker_resolution']))

    def test_repair_never_resets_held_passages(self):
        generation = self.run_job(self.generation(), VariedInvalidEngine())
        self.assertEqual('held', generation['status'])
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: SyntheticEngine())
        jobs.repair(generation['id'])
        if jobs.thread:
            jobs.thread.join(timeout=10)
        after = self.store.generation(generation['id'])
        self.assertEqual({'review'}, {segment['status'] for segment in after['segments']})

    def test_repair_rebuilds_a_composite_from_its_retained_parts_without_tts(self):
        from test_core import CastEngine
        engine = CastEngine()
        generation = self.run_job(self.generation(), engine)
        mixed = next(segment for segment in generation['segments'] if segment['performance'].get('internal_transition'))
        self.assertEqual(len(mixed['performance']['analysis_parts']), len(mixed['performance']['render_parts']))
        self.store.collect_audio()
        self.assertTrue(all((self.store.root / 'audio' / f'{key}.wav').is_file() for key in mixed['performance']['render_parts']))
        wav = self.store.root / 'audio' / f'{mixed["audio_hash"]}.wav'
        original = wav.read_bytes()
        wav.write_bytes(b'damaged')
        rendered = engine.rendered
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        jobs.repair(generation['id'])
        jobs.thread.join(timeout=10)
        self.assertEqual(rendered, engine.rendered)
        self.assertEqual(original, wav.read_bytes())

    def test_hold_neighbours_stay_in_their_chapter(self):
        segments = [{'id': 'a', 'source_id': 's1', 'start': 0, 'end': 5, 'chapter_id': 'c1'},
                    {'id': 'b', 'source_id': 's2', 'start': 0, 'end': 5, 'chapter_id': 'c1'},
                    {'id': 'c', 'source_id': 's3', 'start': 0, 'end': 5, 'chapter_id': 'c2'}]
        flagged, held = speaker_review_holds(segments, [{'status': 'review', 'source_id': 's2', 'start': 1, 'end': 2}])
        self.assertEqual(({'b'}, {'a', 'b'}), (flagged, held))

    def test_critic_capacity_holds_only_the_affected_speech(self):
        segments = [{'id': 'n1', 'source_id': 's1', 'start': 0, 'end': 5, 'chapter_id': 'c1', 'speaker': 'narrator'},
                    {'id': 'speech', 'source_id': 's2', 'start': 0, 'end': 5, 'chapter_id': 'c1', 'speaker': 'hero'},
                    {'id': 'n2', 'source_id': 's3', 'start': 0, 'end': 5, 'chapter_id': 'c1', 'speaker': 'narrator'}]
        record = {'status': 'review', 'source_id': 's2', 'start': 0, 'end': 5,
                  'speaker': 'hero', 'triggers': ['critic_unavailable']}
        self.assertEqual(({'speech'}, {'speech'}), speaker_review_holds(segments, [record]))


if __name__ == '__main__':
    unittest.main()
