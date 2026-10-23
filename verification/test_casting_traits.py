from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from test_core import FB2, AliasEngine, narration, pack_fixture, unit_parts
from rimsky.analysis import accepted_speakers, context_target, registry, validate_analysis
from rimsky.casting import (ALIAS_COMPARISON_LIMIT, TRAIT_EVIDENCE_LIMIT, age_fit, cast_summary, cast_voices, casting_conflicts, casting_pool, casting_warnings,
                            colliding_pairs, collision_weight, compatible, conflict_graph, dialogue_contexts, gender_fit, gender_targets,
                            merge_trait, optimize_casting, performance_key, speech_volume)
from rimsky.jobs import Jobs, alias_candidates, alias_decision, merge_aliases
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AnalysisCapacityError, AppError, digest


def line(speaker, source, ordinal, start=0):
    return {'speaker': speaker, 'source_id': source, 'ordinal': ordinal, 'chapter_id': 'c', 'start': start, 'characters': 10}


def assignment(cast):
    return {character['id']: character['voice_id'] for character in cast}


def weighted_collisions(state, keys, neighbours):
    return collision_weight(colliding_pairs(state, keys, neighbours, neighbours), neighbours)


def incompatibilities(state, characters, pool):
    return sum(not compatible(characters[cid], pool[vid]) for cid, vid in state.items())


def trait(value, *evidence):
    return {'value': value, 'evidence': list(evidence)}


def supported(value='unknown', conflict=False, **support):
    return {'value': value, 'support': {key: list(ids) for key, ids in support.items()}} | ({'conflict': True} if conflict else {})


def person(cid, description='', gender='unknown', age='unknown', evidence=()):
    return {'id': cid, 'name': cid, 'aliases': [], 'description': description, 'evidence': list(evidence),
            'gender': trait(gender, *(['p'] if gender != 'unknown' else [])),
            'age_band': trait(age, *(['p'] if age != 'unknown' else []))}


UNAVAILABLE = '1 character(s) have no compatible voice profile; another profile is used.'
DISPLACED = '1 character(s) use another profile: a compatible profile was not used to avoid a local performance collision.'
NO_METADATA = 'No character voice profile declares {}; these character traits cannot guide casting.'
DISPUTED = '1 character(s) have conflicting gender or age evidence and are cast as unknown.'
UNMERGED = '1 possible alias pair(s) exceeded the model context or output limit and remain separate characters.'


def narrator():
    return {'id': 'n', 'role': 'narrator', 'speaker': 'N'}


def cast_one(character, *voices):
    return cast_voices([character], [narrator(), *voices], [line(character['id'], 'p1', 0)])[1]


class TagMatching(unittest.TestCase):
    def pick(self, description, first, second):
        voices = ({'id': 'a', 'speaker': 'A', 'tags': [first]}, {'id': 'b', 'speaker': 'B', 'tags': [second]})
        return cast_one(person('x', description), *voices)['voice_id']

    def test_male_tag_does_not_match_female_description(self):
        self.assertEqual('b', self.pick('young female student', 'male', 'female'))

    def test_tag_does_not_match_inside_another_word(self):
        self.assertEqual('b', self.pick('bold captain', 'old', 'bold'))

    def test_multi_word_tags_match_whole_phrases_in_order(self):
        self.assertEqual('b', self.pick('Старик, Низкий голос.', 'голос низкий', 'низкий голос'))

    def test_negated_tags_do_not_match(self):
        for description in ('not old', "isn't very old", 'without accent', 'не старый', 'без акцента', 'никогда не грубый'):
            with self.subTest(description=description):
                self.assertEqual('a', self.pick(description, 'calm', description.split()[-1]))

    def test_neither_nor_negate_their_terms(self):
        self.assertEqual('a', self.pick('neither old nor young', 'calm', 'old'))
        self.assertEqual('a', self.pick('neither old nor young', 'calm', 'young'))

    def test_negation_ends_at_clause_boundary(self):
        self.assertEqual('b', self.pick('not tall, old', 'calm', 'old'))

    def test_case_width_and_yo_are_normalized(self):
        self.assertEqual('b', self.pick('теплый голос', 'calm', 'Тёплый'))
        self.assertEqual('b', self.pick('ＯＬＤ sailor', 'calm', 'old'))

    def test_listener_family_guidance_affects_the_production_cast(self):
        voices=({'id':'a','speaker':'A','family':'plain'}, {'id':'b','speaker':'B','family':'captain'})
        guidance={
            'plain':{'descriptors':{},'casting_description':'plain neutral voice','character_fit':'student','avoid_for':'captain commander'},
            'captain':{'descriptors':{},'casting_description':'firm command voice','character_fit':'captain commander','avoid_for':'student'},
        }
        cast=cast_voices([person('x','veteran captain commander')],[narrator(),*voices],[line('x','p1',0)],family_guidance=guidance)
        self.assertEqual('b',cast[1]['voice_id'])

    def test_listener_only_guidance_reserves_a_family_instead_of_avoiding_it(self):
        voices=({'id':'a','speaker':'A','family':'generic'},
                {'id':'b','speaker':'B','family':'hero'})
        guidance={
            'generic':{'descriptors':{},'character_fit':'supporting character'},
            'hero':{'descriptors':{},'character_fit':'main protagonist',
                    'avoid_for':'main protagonist only'},
        }
        cast=cast_voices([person('x','main protagonist and player character')],
                         [narrator(),*voices],[line('x','p1',0)],family_guidance=guidance)
        self.assertEqual('b',cast[1]['voice_id'])

    def test_listener_young_descriptor_means_young_adult_not_child(self):
        voices=({'id':'a','speaker':'A','family':'child'},
                {'id':'b','speaker':'B','family':'young'})
        guidance={
            'child':{'descriptors':{'perceived_age':'child'}},
            'young':{'descriptors':{'perceived_age':'young'}},
        }
        cast=cast_voices([person('x','young adult hero',age='adult')],
                         [narrator(),*voices],[line('x','p1',0)],family_guidance=guidance)
        self.assertEqual('b',cast[1]['voice_id'])

    def test_listener_gender_override_layers_over_pack_metadata(self):
        voices=({'id':'a','speaker':'A','family':'one','gender':'male'},
                {'id':'b','speaker':'B','family':'two','gender':'male'})
        guidance={'two':{'descriptors':{'performed_gender':'feminine'}}}
        cast=cast_voices([person('x',gender='female')],[narrator(),*voices],[line('x','p1',0)],family_guidance=guidance)
        self.assertEqual(('b',True),(cast[1]['voice_id'],cast[1]['voice_compatible']))

    def test_listener_neutral_gender_clears_incompatible_pack_metadata(self):
        voices=({'id':'a','speaker':'A','family':'male','gender':'male'},
                {'id':'b','speaker':'B','family':'neutral','gender':'male'})
        guidance={'neutral':{'descriptors':{'performed_gender':'neutral'},'character_fit':'captain'}}
        cast=cast_voices([person('x','captain',gender='female')],[narrator(),*voices],[line('x','p1',0)],family_guidance=guidance)
        self.assertEqual(('b',True),(cast[1]['voice_id'],cast[1]['voice_compatible']))


class TraitValidation(unittest.TestCase):
    def target(self):
        text = '— Да, — сказала Маша.'
        return {'id': 'p-0', 'text': text, 'start': 0, 'end': len(text), 'source_id': 'p', 'ordinal': 0, 'chapter_id': 'c1'}

    def analyse(self, cast, record):
        target = self.target()
        return validate_analysis({'characters': [record], 'passages': [narration(target)]}, [target], cast)[1]

    def record(self, cid='new_1', gender=None, age=None, description=''):
        return {'id': cid, 'name': 'Маша', 'aliases': [], 'description': description, 'evidence': ['p-0'], 'kind': 'person',
                'gender': gender or trait('unknown'), 'age_band': age or trait('unknown')}

    def rejected(self, cast, record):
        with self.assertRaisesRegex(AppError, 'does not match the response schema'):
            self.analyse(cast, record)

    def test_known_trait_without_evidence_becomes_unknown_and_out_of_window_evidence_rejects_the_reply(self):
        self.assertEqual(supported(), self.analyse([], self.record(gender=trait('female')))[0]['gender'])
        self.rejected([], self.record(gender=trait('female', 'elsewhere')))

    def test_invalid_or_missing_traits_reject_the_reply(self):
        self.rejected([], self.record(gender=trait('woman', 'p-0')))
        record = self.record()
        del record['age_band']
        self.rejected([], record)

    def test_new_character_stores_evidenced_traits(self):
        stored = self.analyse([], self.record(gender=trait('female', 'p-0'), age=trait('unknown', 'p-0')))[0]
        self.assertEqual(supported('female', female=['p-0']), stored['gender'])
        self.assertEqual(supported(), stored['age_band'])

    def test_legacy_registry_entry_acquires_traits(self):
        legacy = {'id': 'masha', 'name': 'Маша', 'aliases': [], 'description': '', 'evidence': ['old']}
        stored = self.analyse([legacy], self.record('masha', gender=trait('female', 'p-0')))[0]
        self.assertEqual('female', stored['gender']['value'])
        self.assertEqual('unknown', stored['age_band']['value'])

    def test_unknown_update_keeps_supported_trait(self):
        known = person('masha', gender='female')
        stored = self.analyse([known], self.record('masha'))[0]
        self.assertEqual(supported('female', female=['p']), stored['gender'])

    def test_tied_evidence_is_disputed_until_later_evidence_resolves_it(self):
        known = person('masha', gender='female')
        disputed = self.analyse([known], self.record('masha', gender=trait('male', 'p-0')))[0]
        self.assertEqual(supported('unknown', True, female=['p'], male=['p-0']), disputed['gender'])
        resolved = self.analyse([disputed], self.record('masha', gender=trait('female', 'p-0')))[0]
        self.assertEqual(supported('female', female=['p', 'p-0'], male=['p-0']), resolved['gender'])

    def test_stale_evidence_rejects_the_reply_and_repeated_current_evidence_adds_once(self):
        known = person('masha', gender='female')
        for record in (self.record('masha', gender=trait('female', 'p')), self.record('masha', gender=trait('female', 'p', 'p-0')),
                       self.record('masha', age=trait('unknown', 'p'))):
            with self.subTest(record=record):
                self.rejected([known], record)
        once = self.analyse([known], self.record('masha', gender=trait('female', 'p-0')))[0]
        self.assertEqual(once['gender'], self.analyse([once], self.record('masha', gender=trait('female', 'p-0')))[0]['gender'])

    def test_changed_trait_without_current_evidence_is_ignored(self):
        known = person('masha', gender='female')
        disputed = {**known, 'gender': {'value': 'unknown', 'evidence': ['p'], 'conflict': True}}
        self.assertEqual(merge_trait(known['gender'], None), self.analyse([known], self.record('masha', gender=trait('male')))[0]['gender'])
        for cast, gender in (([known], trait('male', 'p')), ([disputed], trait('female', 'p')), ([person('masha')], trait('female', 'p'))):
            with self.subTest(cast=cast, gender=gender):
                self.rejected(cast, self.record('masha', gender=gender))

    def test_stable_id_collision_keeps_a_separate_character(self):
        stable = 'char_' + digest(['Маша', 'p-0', 'new_1', ''])[:16]
        known = {**person(stable, 'low voice', gender='female', evidence=['old']), 'name': 'Маша', 'aliases': ['Маруся']}
        record = {**self.record(gender=trait('female', 'p-0'), age=trait('child', 'p-0'), description='tired'), 'aliases': ['Машенька']}
        stored = self.analyse([known], record)
        self.assertEqual([known], stored[:1])
        self.assertEqual(2, len(stored))
        self.assertNotEqual(stable, stored[1]['id'])
        self.assertEqual(('Маша', [], 'tired', ['p-0'], supported('child', child=['p-0'])),
                         (stored[1]['name'], stored[1]['aliases'], stored[1]['description'], stored[1]['evidence'], stored[1]['age_band']))

    def test_existing_character_without_current_identity_evidence_is_not_updated(self):
        known = {**person('masha', 'low voice', gender='female'), 'evidence': ['old']}
        stale = {**self.record('masha', gender=trait('male'), description='changed'), 'evidence': []}
        target = self.target()
        result = {'characters': [stale], 'passages': [{'id': 'p-0', 'parts': unit_parts(target['text'], [
            {'start': 0, 'end': len(target['text']), 'speaker': 'masha', 'delivery': 'neutral', 'evidence': ['p-0']}])}]}
        parts, cast = validate_analysis(result, [target], [known])
        self.assertEqual([known], cast)
        self.assertEqual('masha', parts[0]['speaker'])
        mixed = self.analyse([known], {**self.record('masha'), 'evidence': ['p-0']})[0]
        self.assertEqual(['old', 'p-0'], mixed['evidence'])
        self.rejected([known], {**self.record('masha'), 'evidence': ['old', 'p-0']})

    def test_new_character_needs_current_target_evidence(self):
        self.assertEqual([], self.analyse([], {**self.record(), 'evidence': []}))
        self.assertEqual(['p-0'], self.analyse([], self.record())[0]['evidence'])
        self.rejected([], {**self.record(), 'evidence': ['old', 'p-0']})


    def test_batch_update_keeps_earlier_description(self):
        known = {**person('masha', 'low rough voice'), 'evidence': []}
        self.assertEqual('low rough voice; tired', self.analyse([known], self.record('masha', description='tired'))[0]['description'])
        self.assertEqual('low rough voice', self.analyse([known], self.record('masha'))[0]['description'])
        self.assertEqual('woman; man', self.analyse([{**known, 'description': 'woman'}], self.record('masha', description='man'))[0]['description'])
        self.assertEqual('Low voice.', self.analyse([{**known, 'description': 'Low voice.'}], self.record('masha', description='low voice'))[0]['description'])
        self.assertEqual('old, low rough voice', self.analyse([known], self.record('masha', description='old, low rough voice'))[0]['description'])
        long = {**known, 'description': 'a' * 900}
        self.assertEqual('a' * 900, self.analyse([long], self.record('masha', description='b' * 200))[0]['description'])


class TraitEvidence(unittest.TestCase):
    def test_value_with_most_unique_passages_wins(self):
        merged = merge_trait(supported('female', female=['a', 'b']), trait('male', 'c'))
        self.assertEqual(supported('female', female=['a', 'b'], male=['c']), merged)
        repeated = merge_trait(merge_trait(trait('male', 'c'), trait('male', 'c')), supported('female', female=['a']))
        self.assertEqual(supported('unknown', True, male=['c'], female=['a']), repeated)

    def test_cap_is_per_value_and_keeps_competing_citations(self):
        many = [f'f{index}' for index in range(TRAIT_EVIDENCE_LIMIT + 20)]
        merged = merge_trait(supported('female', female=many[:TRAIT_EVIDENCE_LIMIT]), {'value': 'female', 'evidence': many})
        merged = merge_trait(merged, trait('male', 'm1', 'm2'))
        self.assertEqual(('female', TRAIT_EVIDENCE_LIMIT, ['m1', 'm2']), (merged['value'], len(merged['support']['female']), merged['support']['male']))

    def test_votes_count_only_retained_citations_at_saturation(self):
        female = {'value': 'female', 'evidence': [f'f{index}' for index in range(TRAIT_EVIDENCE_LIMIT + 1)]}
        male = {'value': 'male', 'evidence': [f'm{index}' for index in range(TRAIT_EVIDENCE_LIMIT)]}
        merged = merge_trait(merge_trait(None, female), male)
        self.assertEqual(('unknown', True, TRAIT_EVIDENCE_LIMIT), (merged['value'], merged['conflict'], len(merged['support']['female'])))

    def test_legacy_traits_migrate_on_merge(self):
        self.assertEqual(supported('elderly', elderly=['p']), merge_trait(trait('elderly', 'p'), None))
        self.assertEqual(supported(), merge_trait({'value': 'unknown', 'evidence': ['p', 'q'], 'conflict': True}, None))
        self.assertEqual(supported(), merge_trait(None, None))


class AnalysisContext(unittest.TestCase):
    def test_registry_sends_only_identity_and_resolved_traits(self):
        character = {**person('masha', 'd' * 400, gender='female', evidence=['p1']), 'name': 'Маша', 'aliases': ['Маруся'],
                     'age_band': supported('unknown', True, child=['p'], adult=['q'])}
        self.assertEqual([{'id': 'masha', 'name': 'Маша', 'aliases': ['Маруся'], 'description': 'd' * 300, 'kind': 'person',
                           'gender': 'female', 'age_band': 'unknown'}], registry([character]))

    def test_context_carries_accepted_speaker_ranges(self):
        sources = [{'id': 'b1-0-20', 'source_id': 'b1', 'start': 0, 'end': 20, 'text': 'x' * 20},
                   {'id': 'b1-20-30', 'source_id': 'b1', 'start': 20, 'end': 30, 'text': 'y' * 10}]
        inputs = [{'source_id': 'b1', 'start': 0, 'characters': 5, 'speaker': 'narrator'},
                  {'source_id': 'b1', 'start': 5, 'characters': 15, 'speaker': 'masha'},
                  {'source_id': 'b2', 'start': 0, 'characters': 4, 'speaker': 'ivan'}]
        accepted = accepted_speakers(sources, inputs)
        self.assertEqual({'b1-0-20': [{'start': 0, 'end': 5, 'speaker': 'narrator'}, {'start': 5, 'end': 20, 'speaker': 'masha'}]}, accepted)
        self.assertEqual({'id': 'b1-0-20', 'text': 'x' * 20, 'source_id': 'b1', 'accepted_speakers': [{'speaker': 'masha', 'text': 'x' * 15}]},
                         context_target(sources[0], accepted))
        self.assertEqual({'id': 'b1-20-30', 'text': 'y' * 10, 'source_id': 'b1'}, context_target(sources[1], accepted))

class Reconciliation(unittest.TestCase):
    def characters(self, **specs):
        return [{**person(cid, gender=gender), 'name': name, 'evidence': [f'p{cid}']} for cid, (name, gender) in specs.items()]

    def jobs(self, decide):
        class Engine:
            def __init__(self):
                self.payloads = []
            def call(self, method, payload, stop):
                self.payloads.append(payload)
                reply = decide(payload)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        jobs = Jobs(None, Path('.'))
        jobs.engine = Engine()
        jobs.stop = threading.Event()
        return jobs

    def same(self, payload):
        return {'same': True, 'evidence': [next(iter(passages)) for passages in payload['evidence'].values()]}

    def test_candidates_need_a_whole_name_inside_another_name(self):
        characters = [{'id': cid, 'name': name, 'aliases': aliases} for cid, name, aliases in (
            ('a', 'Анна', []), ('b', 'Анна Петровна', []), ('c', 'Mr Smith', []), ('d', 'Mr Jones', []),
            ('e', 'Алёна', []), ('f', 'Sister', ['алена']), ('g', 'Annabel', []))]
        self.assertEqual([('a', 'b'), ('e', 'f')], list(alias_candidates(characters)))

    def test_candidates_are_generated_lazily_in_registry_order(self):
        characters = [{'id': f'c{index:04d}', 'name': f'Имя{index}', 'aliases': ['солдат']} for index in range(3000)]
        pairs = alias_candidates(characters)
        self.assertIs(pairs, iter(pairs))
        self.assertEqual([('c0000', 'c0001'), ('c0000', 'c0002'), ('c0000', 'c0003')], [next(pairs) for _ in range(3)])

    def test_short_name_hub_does_not_join_distinct_full_names(self):
        characters = self.characters(a=('Ла-Фин', 'unknown'), b=('Минн-О Ла-Фин', 'female'), c=('Тимор-Анху Ла-Фин', 'unknown'))
        def decide(payload):
            ids = [c['id'] for c in payload['characters']]
            return {'same': False, 'evidence': []} if ids == ['b', 'c'] else self.same(payload)
        jobs = self.jobs(decide)
        cast, merges, unmerged, skipped, _ = jobs._reconcile({}, characters, {f'p{cid}': 'text' for cid in 'abc'})
        self.assertEqual(({'b': 'a'}, 0, 0), (merges, unmerged, skipped))
        self.assertEqual([['a', 'b'], ['a', 'c'], ['b', 'c']], [[c['id'] for c in p['characters']] for p in jobs.engine.payloads])
        self.assertEqual(['a', 'c'], [c['id'] for c in cast])
        jobs = self.jobs(self.same)
        self.assertEqual({'b': 'a', 'c': 'a'}, jobs._reconcile({}, characters, {f'p{cid}': 'text' for cid in 'abc'})[1])

    def test_pair_judged_different_blocks_later_merges_between_groups(self):
        characters = self.characters(a=('Анна', 'unknown'), b=('Анна Петровна', 'unknown'), c=('Анна', 'unknown'))
        def decide(payload):
            ids = [c['id'] for c in payload['characters']]
            return {'same': False, 'evidence': []} if ids == ['a', 'b'] else self.same(payload)
        jobs = self.jobs(decide)
        cast, merges, unmerged, skipped, _ = jobs._reconcile({}, characters, {f'p{cid}': 'text' for cid in 'abc'})
        self.assertEqual({'c': 'a'}, merges)
        self.assertEqual([['a', 'b'], ['a', 'c']], [[c['id'] for c in p['characters']] for p in jobs.engine.payloads])

    def test_alias_decision_requires_evidence_from_both_characters(self):
        evidence = {'a': {'pa': 'text'}, 'b': {'pb': 'text'}}
        self.assertTrue(alias_decision({'same': True, 'evidence': ['pa', 'pb']}, evidence))
        self.assertFalse(alias_decision({'same': False, 'evidence': []}, evidence))
        for proposal in ({'same': True, 'evidence': ['pa']}, {'same': True, 'evidence': ['pa', 'pc']}, {'same': True, 'evidence': []},
                         {'same': 'yes', 'evidence': ['pa', 'pb']}, {'merges': []}):
            with self.subTest(proposal=proposal), self.assertRaises(AppError):
                alias_decision(proposal, evidence)

    def test_chained_merges_never_join_opposite_known_genders_through_an_unknown_bridge(self):
        characters = self.characters(a=('Анна', 'female'), b=('Анна Петровна', 'unknown'), c=('Петровна', 'male'))
        jobs = self.jobs(self.same)
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, {f'p{cid}': 'text' for cid in 'abc'})
        self.assertEqual(({'b': 'a'}, 0, 0, []), (merges, unmerged, skipped, state['unmerged_pairs']))
        self.assertEqual(['a', 'c'], [c['id'] for c in cast])
        self.assertEqual(supported('female', female=['p']), cast[0]['gender'])
        self.assertEqual([], casting_warnings(cast, [], [line('a', 'p1', 0)], [narrator(), {'id': 'v', 'gender': 'male', 'age_bands': ['adult']}]))
        self.assertEqual([{'id': 'a', 'name': 'Анна', 'aliases': [], 'description': '', 'kind': 'person', 'gender': 'female', 'age_band': 'unknown'},
                          {'id': 'b', 'name': 'Анна Петровна', 'aliases': [], 'description': '', 'kind': 'person', 'gender': 'unknown', 'age_band': 'unknown'}],
                         jobs.engine.payloads[0]['characters'])
        self.assertEqual({'a': {'pa': 'text'}, 'b': {'pb': 'text'}}, jobs.engine.payloads[0]['evidence'])

    def test_context_or_output_exhaustion_leaves_the_pair_separate_with_a_warning(self):
        characters = self.characters(a=('Анна', 'unknown'), b=('Анна Петровна', 'unknown'))
        for message in ('Alias comparison exceeds this pack context.', 'Character reconciliation reached its output limit.'):
            with self.subTest(message=message):
                jobs = self.jobs(lambda payload: AnalysisCapacityError(message))
                cast, merges, unmerged, skipped, _ = jobs._reconcile({}, characters, {'pa': 'text', 'pb': 'text'})
                self.assertEqual((['a', 'b'], {}, 1, 0, 1), ([c['id'] for c in cast], merges, unmerged, skipped, len(jobs.engine.payloads)))
                self.assertIn(UNMERGED, casting_warnings(cast, [], [line('a', 'p1', 0)], [narrator()], unmerged))

    def test_failed_pair_joined_through_another_character_is_not_reported_separate(self):
        characters = self.characters(a=('Анна', 'unknown'), b=('Анна', 'unknown'), c=('Анна', 'unknown'))
        jobs = self.jobs(lambda payload: AnalysisCapacityError('full') if [c['id'] for c in payload['characters']] == ['a', 'b'] else self.same(payload))
        cast, merges, unmerged, skipped, _ = jobs._reconcile({}, characters, {f'p{cid}': 'text' for cid in 'abc'})
        self.assertEqual(3, len(jobs.engine.payloads))
        self.assertEqual((['a'], {'b': 'a', 'c': 'a'}, 0, 0), ([c['id'] for c in cast], merges, unmerged, skipped))

    def test_comparisons_stop_at_the_hard_limit(self):
        characters = [{**person(f'c{index:02d}'), 'name': 'Анна', 'evidence': [f'p{index}']} for index in range(30)]
        jobs = self.jobs(lambda payload: {'same': False, 'evidence': []})
        cast, merges, unmerged, skipped, _ = jobs._reconcile({}, characters, {f'p{index}': 'text' for index in range(30)})
        pairs = list(alias_candidates(characters))
        self.assertEqual((435, 256, ALIAS_COMPARISON_LIMIT), (len(pairs), len(jobs.engine.payloads), 256))
        self.assertEqual((30, {}, 0, 435 - 256), (len(cast), merges, unmerged, skipped))
        self.assertEqual([list(pair) for pair in pairs[:256]], [[c['id'] for c in p['characters']] for p in jobs.engine.payloads])
        self.assertIn('179 possible alias pair(s) were not compared because the 256-comparison limit was reached; they remain separate characters.',
                      casting_warnings(cast, [], [], [narrator()], unmerged, skipped))

    def test_pairs_without_supplied_evidence_or_names_are_not_compared(self):
        characters = self.characters(a=('Анна', 'unknown'), b=('Анна Петровна', 'unknown'), c=('Борис', 'unknown'))
        jobs = self.jobs(self.same)
        self.assertEqual({}, jobs._reconcile({}, characters, {'pa': 'text', 'pc': 'text'})[1])
        self.assertEqual([], jobs.engine.payloads)

    def test_merge_keeps_source_description(self):
        characters = [{**person('a', 'low rough male'), 'evidence': ['pa']}, {**person('b'), 'evidence': ['pb']}]
        cast = merge_aliases(characters, {'a': 'b'})
        self.assertEqual(['b'], [c['id'] for c in cast])
        self.assertEqual('low rough male', cast[0]['description'])


class TraitCasting(unittest.TestCase):
    def test_reviewed_series_character_binding_uses_the_named_voice(self):
        character = {**person('imran', 'young male', gender='male'), 'name': 'Имран', 'aliases': ['Имран Младший']}
        voices = [
            {'id': 'generic', 'speaker': 'Generic', 'gender': 'male', 'tags': ['young male']},
            {'id': 'imran', 'speaker': 'Imran', 'gender': 'male', 'character_names': ['Имран']},
        ]
        self.assertEqual('imran', cast_one(character, *voices)['voice_id'])

    def test_reviewed_series_alias_binding_is_visible(self):
        character = {**person('hero', gender='male'), 'name': 'Комар'}
        voices = [
            {'id': 'generic', 'speaker': 'Generic', 'gender': 'male'},
            {'id': 'kirill', 'speaker': 'Kirill', 'gender': 'male',
             'character_names': ['Кирилл Комаров', 'Кирилл', 'Комаров', 'Комар']},
        ]
        cast = cast_voices([character], [narrator(), *voices], [line('hero', 'p', 0)])
        self.assertEqual('kirill', cast[1]['voice_id'])
        self.assertEqual({'status': 'applied', 'voice_ids': ['kirill'], 'matched_names': ['комар']},
                         cast[1]['series_voice_binding'])
        notices = {notice['code']: notice for notice in cast_summary(cast, [narrator(), *voices], [line('hero', 'p', 0)])['notices']}
        self.assertEqual(['Комар'], notices['series_voice_binding_applied']['params']['names'])

    def test_tracked_reality_benders_alias_registry_keeps_callsign(self):
        registry = json.loads((Path(__file__).resolve().parents[1] / 'training' / 'series_character_aliases' /
                               'reality-benders.json').read_text(encoding='utf-8'))
        kirill = next(character for character in registry['characters'] if character['voice_id'] == 'rb1_kirill')
        self.assertEqual('Кирилл Комаров', kirill['name'])
        self.assertIn('Комар', kirill['aliases'])

    def test_ambiguous_series_alias_uses_normal_casting_and_is_visible(self):
        character = {**person('hero', gender='male'), 'name': 'Комар'}
        voices = [
            {'id': 'a', 'speaker': 'A', 'gender': 'male', 'character_names': ['Комар']},
            {'id': 'b', 'speaker': 'B', 'gender': 'male', 'character_names': ['Комар']},
        ]
        cast = cast_voices([character], [narrator(), *voices], [line('hero', 'p', 0)])
        self.assertEqual('ambiguous', cast[1]['series_voice_binding']['status'])
        notices = {notice['code']: notice for notice in cast_summary(cast, [narrator(), *voices], [line('hero', 'p', 0)])['notices']}
        self.assertEqual(['Комар'], notices['series_voice_binding_ambiguous']['params']['names'])

    def test_reviewed_series_binding_outranks_generic_traits(self):
        character = {**person('imran', gender='male'), 'name': 'Имран'}
        voices = [
            {'id': 'female', 'speaker': 'F', 'gender': 'female', 'character_names': ['Имран']},
            {'id': 'male', 'speaker': 'M', 'gender': 'male'},
        ]
        self.assertEqual('female', cast_one(character, *voices)['voice_id'])

    def test_compatible_gender_is_preferred(self):
        chosen = cast_one(person('x', gender='female'), {'id': 'a', 'speaker': 'A', 'gender': 'male'}, {'id': 'b', 'speaker': 'B', 'gender': 'female'})
        self.assertEqual(('b', True), (chosen['voice_id'], chosen['voice_compatible']))

    def test_legacy_pack_without_traits_keeps_tag_and_id_order(self):
        chosen = cast_one(person('x', 'low', gender='female'), {'id': 'a', 'speaker': 'A'}, {'id': 'b', 'speaker': 'B', 'tags': ['low']})
        self.assertEqual(('b', True), (chosen['voice_id'], chosen['voice_compatible']))

    def test_unknown_gender_is_compatible_with_any_profile(self):
        chosen = cast_one(person('x'), {'id': 'a', 'speaker': 'A', 'gender': 'male'}, {'id': 'b', 'speaker': 'B', 'gender': 'female'})
        self.assertEqual(('a', True), (chosen['voice_id'], chosen['voice_compatible']))

    def test_disputed_gender_is_cast_as_unknown(self):
        character = {**person('x'), 'gender': {'value': 'unknown', 'evidence': ['p'], 'conflict': True}}
        chosen = cast_one(character, {'id': 'a', 'speaker': 'A', 'gender': 'male'}, {'id': 'b', 'speaker': 'B', 'gender': 'female'})
        self.assertEqual(('a', True), (chosen['voice_id'], chosen['voice_compatible']))

    def test_missing_compatible_profile_uses_warned_fallback(self):
        cast = cast_voices([person('x', gender='female')], [narrator(), {'id': 'a', 'speaker': 'A', 'gender': 'male'}], [line('x', 'p1', 0)])
        self.assertEqual(('a', False), (cast[1]['voice_id'], cast[1]['voice_compatible']))
        voices = [narrator(), {'id': 'a', 'speaker': 'A', 'gender': 'male'}]
        self.assertEqual([UNAVAILABLE, NO_METADATA.format('age bands')], casting_warnings(cast, [], [line('x', 'p1', 0)], voices))
        self.assertEqual([NO_METADATA.format('age bands')], casting_warnings(cast, [], [line('narrator', 'p1', 0)], voices))

    def test_known_gender_compatibility_outranks_local_collisions(self):
        voices = [narrator(), {'id': 'f', 'speaker': 'F', 'gender': 'female'}, {'id': 'm', 'speaker': 'M', 'gender': 'male'}]
        parts = [line('x', 'p1', 0), line('y', 'p1', 0, 5)]
        cast = cast_voices([person('x', gender='female'), person('y', gender='female')], voices, parts)
        self.assertEqual({'x': ('f', True), 'y': ('f', True)}, {c['id']: (c['voice_id'], c['voice_compatible']) for c in cast[1:]})
        conflicts = casting_conflicts(cast, voices, parts)
        self.assertEqual([[['x', 'y']]], [conflict['shared_speakers'] for conflict in conflicts])
        self.assertNotIn(DISPLACED, casting_warnings(cast, conflicts, parts, voices))

    def test_compatible_profile_is_used_when_it_avoids_collisions(self):
        voices = [narrator(), {'id': 'a', 'speaker': 'A', 'gender': 'male'}, {'id': 'f1', 'speaker': 'F1', 'gender': 'female'},
                  {'id': 'f2', 'speaker': 'F2', 'gender': 'female'}]
        parts = [line('x', 'p1', 0), line('y', 'p1', 0, 5)]
        cast = cast_voices([person('x', gender='female'), person('y', gender='female')], voices, parts)
        self.assertEqual({'f1', 'f2'}, {c['voice_id'] for c in cast[1:]})
        self.assertTrue(all(c['voice_compatible'] for c in cast[1:]))

    def test_age_is_preferred_after_collisions(self):
        voices = [narrator(), {'id': 'a', 'speaker': 'A', 'gender': 'female', 'age_bands': ['adult']},
                  {'id': 'b', 'speaker': 'B', 'gender': 'female', 'age_bands': ['elderly']}]
        self.assertEqual('b', cast_one(person('x', gender='female', age='elderly'), *voices[1:])['voice_id'])
        parts = [line('x', 'p1', 0), line('y', 'p1', 0, 5)]
        cast = cast_voices([person('x', gender='female', age='elderly'), person('y', gender='female', age='elderly')], voices, parts)
        self.assertEqual({'a', 'b'}, {c['voice_id'] for c in cast[1:]})

    def test_exact_trait_beats_missing_metadata_and_missing_beats_mismatch(self):
        for name, value, mismatch, exact in (('gender', 'female', {'gender': 'male'}, {'gender': 'female'}),
                                             ('age', 'elderly', {'age_bands': ['child']}, {'age_bands': ['elderly']})):
            character = person('x', **{name: value})
            voices = [{'id': 'a', 'speaker': 'A', **mismatch}, {'id': 'b', 'speaker': 'B'}, {'id': 'c', 'speaker': 'C', **exact}]
            with self.subTest(name=name):
                self.assertEqual('c', cast_one(character, *voices)['voice_id'])
                self.assertEqual('b', cast_one(character, *voices[:2])['voice_id'])

    def test_unknown_traits_are_neutral_across_profiles(self):
        voices = [{'id': 'a', 'speaker': 'A', 'gender': 'male', 'age_bands': ['child']}, {'id': 'b', 'speaker': 'B'}]
        self.assertEqual('a', cast_one(person('x'), *voices)['voice_id'])

    def test_character_avoids_narrator_performance_when_an_alternative_exists(self):
        shared = {'id': 'r', 'speaker': 'n', 'tags': ['low']}
        self.assertEqual('z', cast_one(person('x', 'low'), shared, {'id': 'z', 'speaker': 'Z'})['voice_id'])
        self.assertEqual('r', cast_one(person('x', 'low'), shared)['voice_id'])

    def test_equal_fits_spread_by_assigned_speech_volume(self):
        parts = [{**line(cid, f'p{index}', index), 'chapter_id': f'c{index}', 'characters': size}
                 for index, (cid, size) in enumerate((('x', 30), ('y', 20), ('z', 10), ('w', 5)))]
        voices = [narrator(), {'id': 'a', 'speaker': 'A'}, {'id': 'b', 'speaker': 'B'}, {'id': 'c', 'speaker': 'C'}]
        cast = cast_voices([person(cid) for cid in 'wxyz'], voices, parts)
        self.assertEqual({'x': 'a', 'y': 'b', 'z': 'c', 'w': 'c'}, assignment(cast[1:]))
        voices = [narrator(), {'id': 'f', 'speaker': 'F', 'gender': 'female'}, {'id': 'm', 'speaker': 'M', 'gender': 'male'}]
        cast = cast_voices([person('x', gender='female'), person('y', gender='female')], voices, parts[:2])
        self.assertEqual({'x': 'f', 'y': 'f'}, assignment(cast[1:]))

    def test_missing_pack_metadata_is_warned(self):
        for pool, missing in (([{'id': 'a'}], 'gender or age bands'), ([{'id': 'a'}, {'id': 'b', 'gender': 'male'}], 'age bands'),
                              ([{'id': 'a', 'age_bands': ['adult']}], 'gender')):
            with self.subTest(missing=missing):
                self.assertEqual([NO_METADATA.format(missing)], casting_warnings([], [], [], [narrator(), *pool]))
        self.assertEqual([], casting_warnings([], [], [], [narrator(), {'id': 'a', 'gender': 'male'}, {'id': 'b', 'age_bands': ['child']}]))


class CastingRepair(unittest.TestCase):
    voices = [narrator(), {'id': 'f', 'speaker': 'F', 'gender': 'female'}, {'id': 'm', 'speaker': 'M', 'gender': 'male'},
              {'id': 'u', 'speaker': 'U'}]
    characters = [person('a'), person('b', gender='female'), person('c', gender='female')]
    parts = [line('a', 'p1', 0), line('b', 'p2', 1), line('c', 'p3', 2)]

    def state(self, characters, voices, parts):
        pool = {voice['id']: voice for voice in voices if voice.get('role') != 'narrator'}
        keys = {vid: performance_key(voice, 'custom') for vid, voice in pool.items()}
        return {c['id']: c for c in characters}, pool, keys, conflict_graph(dialogue_contexts(parts), characters)

    def test_three_character_conversation_has_no_collision_or_incompatibility(self):
        cast = cast_voices(self.characters, self.voices, self.parts)
        self.assertEqual([], casting_conflicts(cast, self.voices, self.parts))
        self.assertTrue(all(c['voice_compatible'] for c in cast[1:]))
        self.assertEqual([NO_METADATA.format('age bands')], casting_warnings(cast, [], self.parts, self.voices))

    def optimize(self, assigned, characters, voices, parts, stop=None):
        characters_by_id, pool, keys, neighbours = self.state(characters, voices, parts)
        return optimize_casting(assigned, {p['speaker'] for p in parts}, characters_by_id, pool, keys, neighbours, lambda cid, vid: 0, stop)

    def test_repair_resolves_greedy_incompatibility(self):
        characters, pool, keys, neighbours = self.state(self.characters, self.voices, self.parts)
        repaired = self.optimize({'a': 'f', 'b': 'u', 'c': 'm'}, self.characters, self.voices, self.parts)
        self.assertEqual(0, incompatibilities(repaired, characters, pool))

    def test_repair_compares_performance_identities(self):
        voices = [narrator(), {'id': 'f', 'speaker': 'F', 'gender': 'female'}, {'id': 'g', 'speaker': 'f'}, {'id': 'm', 'speaker': 'M', 'gender': 'male'}]
        self.assertEqual({'b': 'f', 'c': 'f'}, self.optimize({'b': 'f', 'c': 'm'}, self.characters[1:], voices, self.parts[1:]))

    def test_collision_reduction_outranks_gender_fit(self):
        characters = [person('c0', gender='female'), person('c1'), person('c2', gender='female'), person('c3', gender='female')]
        voices = [narrator(), {'id': 'v0', 'speaker': 'S0'}, {'id': 'v1', 'speaker': 'S1', 'gender': 'female'}]
        pool = {voice['id']: voice for voice in voices[1:]}
        keys = {vid: performance_key(voice, 'custom') for vid, voice in pool.items()}
        neighbours = {cid: Counter() for cid in ('c0', 'c1', 'c2', 'c3')}
        for a, b, weight in (('c0', 'c1', 2), ('c0', 'c2', 1), ('c1', 'c2', 1), ('c1', 'c3', 2), ('c2', 'c3', 1)):
            neighbours[a][b] = neighbours[b][a] = weight
        greedy = {'c0': 'v1', 'c1': 'v0', 'c2': 'v1', 'c3': 'v1'}
        optimized = optimize_casting(greedy, set(neighbours), {c['id']: c for c in characters}, pool, keys, neighbours, lambda cid, vid: 0)
        self.assertEqual((2, 1), (weighted_collisions(greedy, keys, neighbours), weighted_collisions(optimized, keys, neighbours)))
        self.assertEqual('v0', optimized['c2'])

    def test_cast_leaves_no_single_move_that_lowers_incompatibility_then_collisions(self):
        generator = random.Random(14)
        for trial in range(300):
            voices = [narrator()] + [{'id': f'v{i}', 'speaker': f'S{i}', **({'gender': generator.choice(['female', 'male'])} if generator.random() < .7 else {})}
                                     for i in range(generator.randint(2, 3))]
            characters = [person(f'c{i}', gender=generator.choice(['female', 'male', 'unknown'])) for i in range(generator.randint(4, 7))]
            parts = [{**line(generator.choice(characters)['id'], f'p{i}', i), 'chapter_id': f'c{i // 4}'} for i in range(generator.randint(4, 16))]
            assigned = assignment(cast_voices(characters, voices, parts)[1:])
            characters_by_id, pool, keys, neighbours = self.state(characters, voices, parts)
            def score(state):
                return incompatibilities(state, characters_by_id, pool), weighted_collisions(state, keys, neighbours)
            current = score(assigned)
            for cid in sorted(assigned):
                for vid in sorted(pool):
                    with self.subTest(trial=trial, cid=cid, vid=vid):
                        self.assertGreaterEqual(score({**assigned, cid: vid}), current)

    def test_casting_stops_when_preparation_is_paused(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(AppError, 'Preparation paused.'):
            cast_voices(self.characters, self.voices, self.parts, stop=stop)
        with self.assertRaisesRegex(AppError, 'Preparation paused.'):
            self.optimize({'a': 'f', 'b': 'u', 'c': 'm'}, self.characters, self.voices, self.parts, stop)

    def improve(self, assigned, characters, voices, parts):
        return self.optimize(assigned, characters, voices, parts)

    def test_improvement_moves_and_swaps_toward_better_age_fit(self):
        voices = [narrator(), {'id': 'adult', 'speaker': 'A', 'age_bands': ['adult']}, {'id': 'old', 'speaker': 'O', 'age_bands': ['elderly']}]
        parts = [line('x', 'p1', 0), line('y', 'p1', 0, 5)]
        self.assertEqual({'x': 'old'}, self.improve({'x': 'adult'}, [person('x', age='elderly')], voices, parts[:1]))
        swapped = self.improve({'x': 'adult', 'y': 'old'}, [person('x', age='elderly'), person('y', age='adult')], voices, parts)
        self.assertEqual({'x': 'old', 'y': 'adult'}, swapped)

    def test_improvement_never_adds_collisions_or_worse_gender_fit(self):
        voices = [narrator(), {'id': 'adult', 'speaker': 'A', 'gender': 'female', 'age_bands': ['adult']},
                  {'id': 'old', 'speaker': 'O', 'gender': 'female', 'age_bands': ['elderly']}, {'id': 'plain', 'speaker': 'P', 'age_bands': ['elderly']}]
        parts = [line('x', 'p1', 0), line('z', 'p1', 0, 5)]
        characters = [person('x', gender='female', age='elderly'), person('z', gender='female', age='elderly')]
        self.assertEqual({'x': 'adult', 'z': 'old'}, self.improve({'x': 'adult', 'z': 'old'}, characters, voices, parts))

    def test_cast_leaves_no_single_move_that_improves_compatibility_collisions_then_fit(self):
        generator = random.Random(11)
        for trial in range(400):
            voices = [narrator()] + [{'id': f'v{i}', 'speaker': f'S{generator.randrange(4)}', 'gender': generator.choice(['female', 'male']),
                                      'age_bands': [generator.choice(['child', 'teen', 'adult', 'elderly'])]} for i in range(generator.randint(2, 4))]
            characters = [person(f'c{i}', gender=generator.choice(['female', 'male', 'unknown']), age=generator.choice(['child', 'teen', 'adult', 'elderly']))
                          for i in range(generator.randint(2, 6))]
            parts = [line(generator.choice([c['id'] for c in characters] + ['narrator']), f'p{i}', i) for i in range(generator.randint(3, 12))]
            assigned = assignment(cast_voices(characters, voices, parts)[1:])
            characters_by_id, pool, keys, neighbours = self.state(characters, voices, parts)
            targets = gender_targets(characters, {c['id']: casting_pool(c, voices) for c in characters}, speech_volume(parts))
            def score(state):
                genders = [gender_fit(characters_by_id[c], pool[v], targets.get(c)) for c, v in state.items()]
                return (sum(fit == 2 for fit in genders), weighted_collisions(state, keys, neighbours), sum(genders),
                        sum(age_fit(characters_by_id[c], pool[v]) for c, v in state.items()))
            for cid in sorted({part['speaker'] for part in parts} & set(assigned)):
                for vid in sorted(pool):
                    with self.subTest(trial=trial, cid=cid, vid=vid):
                        self.assertGreaterEqual(score({**assigned, cid: vid}), score(assigned))

    def test_repair_never_increases_incompatibility_then_collisions_and_is_deterministic(self):
        generator = random.Random(7)
        for trial in range(200):
            voices = [narrator()] + [{'id': f'v{i}', 'speaker': f'S{generator.randrange(4)}',
                                      **({'gender': generator.choice(['female', 'male'])} if generator.random() < .7 else {})}
                                     for i in range(generator.randint(1, 5))]
            characters = [person(f'c{i}', gender=generator.choice(['female', 'male', 'unknown'])) for i in range(generator.randint(1, 7))]
            parts = [line(generator.choice(characters)['id'], f'p{i}', i, generator.randrange(2)) for i in range(generator.randint(1, 12))]
            parts = [{**part, 'chapter_id': generator.choice('cd')} for part in parts]
            characters_by_id, pool, keys, neighbours = self.state(characters, voices, parts)
            initial = {c['id']: generator.choice(sorted(pool)) for c in characters}
            def repair():
                return optimize_casting(dict(initial), set(initial), characters_by_id, pool, keys, neighbours, lambda cid, vid: 0)
            repaired = repair()
            with self.subTest(trial=trial):
                self.assertLessEqual((incompatibilities(repaired, characters_by_id, pool), weighted_collisions(repaired, keys, neighbours)),
                                     (incompatibilities(initial, characters_by_id, pool), weighted_collisions(initial, keys, neighbours)))
                self.assertEqual(repaired, repair())
                shuffled = generator.sample(characters, len(characters))
                self.assertEqual(assignment(cast_voices(characters, voices, parts)), assignment(cast_voices(shuffled, voices, parts)))


class VariantIdentity(unittest.TestCase):
    def clone(self, vid, speaker, audio, role='character'):
        return {'id': vid, 'role': role, 'speaker': speaker, 'references': {'neutral': {'audio': audio, 'text': 't'}}}

    def test_clone_identity_ignores_speaker_label(self):
        first = self.clone('a', 'x', 'voices/same.wav')
        second = self.clone('b', 'y', 'voices/same.wav')
        self.assertEqual(performance_key(first, 'clone'), performance_key(second, 'clone'))
        self.assertNotEqual(performance_key(first, 'custom'), performance_key(second, 'custom'))

    def test_clone_identity_uses_recording_checksum(self):
        files = {'voices/one.wav': 'sum', 'voices/two.wav': 'sum'}
        self.assertEqual(performance_key(self.clone('a', 'x', 'voices/one.wav'), 'clone', files),
                         performance_key(self.clone('b', 'y', 'voices/two.wav'), 'clone', files))

    def test_identical_clone_recordings_with_distinct_labels_conflict(self):
        voices = [self.clone('n', 'n', 'voices/n.wav', 'narrator'), self.clone('a', 'x', 'voices/same.wav'), self.clone('b', 'y', 'voices/same.wav')]
        parts = [line('alice', 'p1', 0), line('bob', 'p1', 0, start=10)]
        characters = [person('alice'), person('bob')]
        cast = cast_voices(characters, voices, parts, variant='clone')
        self.assertEqual(1, len(casting_conflicts(cast, voices, parts, variant='clone')))

    def test_distinct_clone_recordings_with_identical_labels_do_not_conflict(self):
        voices = [self.clone('n', 'n', 'voices/n.wav', 'narrator'), self.clone('a', 'x', 'voices/one.wav'), self.clone('b', 'x', 'voices/two.wav')]
        parts = [line('alice', 'p1', 0), line('bob', 'p1', 0, start=10)]
        cast = cast_voices([person('alice'), person('bob')], voices, parts, variant='clone')
        self.assertEqual({'a', 'b'}, {c['voice_id'] for c in cast[1:]})
        self.assertEqual([], casting_conflicts(cast, voices, parts, variant='clone'))


class PackTraits(unittest.TestCase):
    def load(self, **fields):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pack_fixture(root)
            manifest = json.loads((root / 'pack.json').read_text())
            manifest['voices'][1].update(fields)
            (root / 'pack.json').write_text(json.dumps(manifest))
            return load_pack(str(root), verify=True)

    def test_voice_traits_are_optional_and_validated(self):
        self.assertEqual('female', self.load(gender='female', age_bands=['adult', 'elderly'])['voices'][1]['gender'])
        self.assertNotIn('gender', self.load()['voices'][1])
        for fields in ({'gender': 'unknown'}, {'gender': 'other'}, {'age_bands': ['unknown']},
                       {'age_bands': ['adult', 'adult']}, {'age_bands': 'adult'}, {'age_bands': [['adult']]},
                       {'age_bands': [{'adult': 1}]}, {'gender': None}, {'gender': ['female']}):
            with self.subTest(fields=fields), self.assertRaises(AppError):
                self.load(**fields)

    def test_character_name_bindings_are_optional_and_validated(self):
        self.assertEqual(['Имран'], self.load(character_names=['Имран'])['voices'][1]['character_names'])
        self.assertNotIn('character_names', self.load()['voices'][1])
        for value in ('Имран', [''], [1], ['x' * 201], ['x'] * 101):
            with self.subTest(value=value), self.assertRaises(AppError):
                self.load(character_names=value)


class CorrectiveRetry(unittest.TestCase):
    def jobs(self, *replies):
        class Engine:
            def __init__(self):
                self.payloads = []
            def call(self, method, payload, stop):
                self.payloads.append(payload)
                reply = replies[len(self.payloads) - 1]
                if isinstance(reply, Exception):
                    raise reply
                return reply
        jobs = Jobs(None, Path('.'))
        jobs.engine = Engine()
        jobs.stop = threading.Event()
        return jobs

    def validate(self, proposal):
        return alias_decision(proposal, {'a': {'p': 'text'}, 'b': {'q': 'text'}})

    def test_invalid_alias_decision_is_retried_with_its_error(self):
        jobs = self.jobs({'same': True, 'evidence': ['p']}, {'same': True, 'evidence': ['p', 'q']})
        self.assertTrue(jobs._corrected('reconcile', {}, self.validate))
        self.assertEqual(['', 'Alias merge evidence must cite passages of both characters.'], [p['retry_error'] for p in jobs.engine.payloads])

    def test_capacity_failure_is_not_retried(self):
        jobs = self.jobs(AnalysisCapacityError('full'), {'same': False, 'evidence': []})
        with self.assertRaises(AnalysisCapacityError):
            jobs._corrected('reconcile', {}, self.validate)
        self.assertEqual(1, len(jobs.engine.payloads))


class WorkerReconciliation(unittest.TestCase):
    def reconcile(self, tokens, finish='stop', context=8192):
        import worker
        from unittest.mock import Mock, patch
        model = Mock()
        model.tokenize.side_effect = lambda data: [1] * tokens(data)
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"same": false, "evidence": []}'}, 'finish_reason': finish}]}
        payload = {'pack': {'text_model': {'context': context}}, 'device': 'cpu', 'retry_error': 'Invalid alias decision.',
                   'characters': [{'id': 'a', 'name': 'Анна'}, {'id': 'b', 'name': 'Анна Петровна'}],
                   'evidence': {'a': {f'pa{i}': 'T' * 1000 for i in range(3)}, 'b': {f'pb{i}': 'T' * 1000 for i in range(3)}}}
        with patch.object(worker, 'load_text', return_value=model):
            result = worker.reconcile(payload)
        return result, json.loads(model.create_chat_completion.call_args.kwargs['messages'][1]['content'])

    def test_retry_error_and_per_character_evidence_reach_the_model(self):
        result, content = self.reconcile(lambda data: 1)
        self.assertEqual({'same': False, 'evidence': []}, result)
        self.assertEqual('Invalid alias decision.', content['previous_validation_error'])
        self.assertEqual([['pa0', 'pa1', 'pa2'], ['pb0', 'pb1', 'pb2']], [list(c['evidence_passages']) for c in content['characters']])

    def test_evidence_is_trimmed_alternately_to_fit(self):
        _, content = self.reconcile(lambda data: len(data) // 1000, context=2004)
        self.assertEqual([['pa0', 'pa1'], ['pb0', 'pb1']], [list(c['evidence_passages']) for c in content['characters']])

    def test_irreducible_pair_and_output_limit_are_pair_capacity_failures(self):
        for tokens, finish, message in ((8000, 'stop', 'Alias comparison exceeds this pack context.'),
                                        (1, 'length', 'Character reconciliation reached its output limit.')):
            with self.subTest(finish=finish), self.assertRaises(AnalysisCapacityError) as raised:
                self.reconcile(lambda data: tokens, finish)
            self.assertEqual(message, str(raised.exception))


class ReconciliationRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.book = self.store.import_data(FB2, 'book.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_with(self, *replies):
        pack = pack_fixture(self.root / 'pack')
        generation = self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint']})
        engine = AliasEngine()
        engine.decide = lambda payload: replies[len(engine.reconciled) - 1]
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(20)
        return self.store.generation(generation['id']), [payload['retry_error'] for payload in engine.reconciled]

    def speakers(self, current):
        return len({s['speaker'] for s in current['segments']} - {'narrator'})

    def test_invalid_alias_decision_then_valid_retry_merges(self):
        current, calls = self.run_with({'same': True, 'evidence': []}, True)
        self.assertEqual(['', 'Alias merge needs valid source evidence.'], calls)
        self.assertTrue(current['analysis']['complete'])
        self.assertEqual((1, 2), (self.speakers(current), len(current['cast'])))

    def test_exhausted_retry_leaves_no_partial_merge(self):
        current, calls = self.run_with({'same': True, 'evidence': []}, {'same': True, 'evidence': []})
        self.assertEqual(2, len(calls))
        self.assertEqual('failed', current['status'])
        self.assertFalse(current['analysis']['complete'])
        self.assertEqual((2, 2), (self.speakers(current), len(current['cast'])))
        self.assertFalse(any('voice_id' in character for character in current['cast']))

    def test_oversized_alias_pair_completes_casting_unmerged(self):
        current, calls = self.run_with(AnalysisCapacityError('full'))
        self.assertEqual([''], calls)
        self.assertTrue(current['analysis']['complete'], current['error'])
        self.assertEqual((2, 3), (self.speakers(current), len(current['cast'])))
        self.assertIn(UNMERGED, current['analysis']['casting_warnings'])


if __name__ == '__main__':
    unittest.main()
