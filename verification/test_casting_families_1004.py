from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky.casting import (ALIAS_COMPARISON_LIMIT, CASTING_SCHEMA, NOTICES, cast_summary, cast_voices, casting_conflicts, casting_warnings,
                            character_kind, contains, gender_targets, normal, optimize_casting, performance_key, conflict_graph,
                            dialogue_contexts, resolve_kind, voice_details, voice_identities, words)
from rimsky.packs import load_pack
from rimsky.util import AppError
from test_core import pack_fixture

ROOT = Path(__file__).resolve().parents[1]
CODES = {'shared_performance_contexts', 'no_compatible_profile', 'displaced_for_collision', 'no_collision_gain', 'disputed_traits',
         'pack_missing_metadata', 'alias_unmerged_capacity', 'alias_skipped_limit', 'unknown_gender_speakers', 'unresolved_speech',
         'degraded_passages', 'no_mechanical_voice', 'narrator_performance_shared', 'genderless_profile_for_gendered', 'unknown_gender_unbalanced',
         'series_voice_binding_applied', 'series_voice_binding_ambiguous'}
NARRATOR = {'id': 'n', 'name': 'Narrator', 'role': 'narrator', 'speaker': 'Ryan', 'family': 'narrator_baseline', 'group': 'Narrator'}


def trait(value):
    return {'value': value, 'evidence': ['p'] if value != 'unknown' else []}


def person(cid, gender='unknown', **fields):
    return {'id': cid, 'name': cid.title(), 'aliases': [], 'description': '', 'evidence': [], 'gender': trait(gender),
            'age_band': trait('unknown'), **fields}


def say(speaker, source, ordinal, chapter='c1', characters=100):
    return {'speaker': speaker, 'source_id': source, 'ordinal': ordinal, 'chapter_id': chapter, 'start': 0,
            'characters': characters, 'kind': 'paragraph', 'scene_id': ''}


def alone(*speakers, characters=100):
    return [say(cid, f'p{index}', index, f'c{index}', characters) for index, cid in enumerate(speakers)]


def voice(vid, gender=None, **fields):
    return {'id': vid, 'name': vid.title(), 'speaker': vid.upper(), **({'gender': gender} if gender else {}), **fields}


def assignment(cast):
    return {character['id']: character['voice_id'] for character in cast if character['id'] != 'narrator'}


def notices(summary):
    return {notice['code']: notice for notice in summary['notices']}


class VoiceRoles(unittest.TestCase):
    def test_narrator_variants_and_mechanical_profiles_are_never_cast_to_people(self):
        pool = [NARRATOR, voice('variant', 'male', role='narrator_variant', tags=['low']), voice('mech', 'male', role='mechanical', tags=['low']),
                voice('a', 'male'), voice('b', 'female')]
        parts = [say('x', 'p1', 0), say('y', 'p2', 1)]
        cast = cast_voices([person('x', 'male', description='low'), person('y', 'male', description='low')], pool, parts)
        self.assertEqual({'a'}, set(assignment(cast).values()))

    def test_mechanical_characters_use_mechanical_profiles_and_people_never_do(self):
        pool = [NARRATOR, voice('mech', role='mechanical'), voice('a', 'male')]
        cast = cast_voices([person('robot', kind='mechanical'), person('crowd', kind='collective'), person('ivan', 'male')], pool,
                           alone('robot', 'crowd', 'ivan'))
        self.assertEqual({'robot': 'mech', 'crowd': 'a', 'ivan': 'a'}, assignment(cast))
        self.assertEqual({'robot': ('mechanical', 'mechanical'), 'crowd': ('character', 'collective'), 'ivan': ('character', 'person')},
                         {c['id']: (c['role'], c['kind']) for c in cast[1:]})
        self.assertNotIn('no_mechanical_voice', notices(cast_summary(cast, pool, alone('robot', 'crowd', 'ivan'))))

    def test_mechanical_character_without_mechanical_profile_uses_character_profile_with_notice(self):
        pool = [NARRATOR, voice('a', 'male')]
        parts = alone('robot')
        cast = cast_voices([person('robot', kind_votes={'mechanical': 2, 'person': 1})], pool, parts)
        self.assertEqual({'robot': 'a'}, assignment(cast))
        notice = notices(cast_summary(cast, pool, parts))['no_mechanical_voice']
        self.assertEqual((1, ['Robot'], ['robot']), (notice['count'], notice['params']['names'], notice['params']['ids']))

    def test_kind_resolution(self):
        self.assertEqual('mechanical', resolve_kind({'mechanical': 2, 'person': 1}))
        self.assertEqual('person', resolve_kind({'mechanical': 1, 'collective': 1}))
        self.assertEqual('mechanical', resolve_kind({'robot': 5, 'mechanical': 1, 'collective': True}))
        for votes in ({}, None, [], {'mechanical': 0}, {'mechanical': -1}):
            with self.subTest(votes=votes):
                self.assertEqual('person', resolve_kind(votes))
        self.assertEqual('collective', character_kind({'kind': 'collective', 'kind_votes': {'mechanical': 3}}))
        self.assertEqual('mechanical', character_kind({'kind': 'alien', 'kind_votes': {'mechanical': 1}}))
        self.assertEqual('person', character_kind({}))

    def test_cast_entries_carry_voice_role_family_group_and_kind(self):
        pool = [NARRATOR, voice('a', 'male', family='masculine_1', group='Masculine voices'), voice('b', 'female')]
        cast = cast_voices([person('ivan', 'male'), person('masha', 'female')], pool, alone('ivan', 'masha'))
        fields = ('voice_id', 'voice_name', 'role', 'family', 'group')
        self.assertEqual(('n', 'Narrator', 'narrator', 'narrator_baseline', 'Narrator'), tuple(cast[0][key] for key in fields))
        self.assertEqual({'ivan': ('a', 'A', 'character', 'masculine_1', 'Masculine voices', 'person'),
                          'masha': ('b', 'B', 'character', 'b', '', 'person')},
                         {c['id']: (*(c[key] for key in fields), c['kind']) for c in cast[1:]})
        self.assertEqual({'id': 'b', 'name': 'B', 'role': 'character', 'family': 'b', 'group': '', 'gender': 'female', 'age_bands': [],
                          'castable': True},
                         voice_details(pool[2]))

    def test_voice_reuse_counts_speaking_characters_only(self):
        pool = [NARRATOR, voice('a')]
        cast = cast_voices([person('speaker'), person('silent')], pool, alone('speaker'))
        self.assertEqual({'speaker': False, 'silent': True}, {c['id']: c['voice_reused'] for c in cast[1:]})
        cast = cast_voices([person('first'), person('second')], pool, alone('first', 'second'))
        self.assertEqual({'first': True, 'second': True}, {c['id']: c['voice_reused'] for c in cast[1:]})


class VoiceFamilies(unittest.TestCase):
    def test_identity_joins_families_speakers_and_references(self):
        pool = [NARRATOR, voice('variant', role='narrator_variant', family='narrator_baseline'), {'id': 'a', 'speaker': 'Aiden'},
                {'id': 'b', 'speaker': 'aiden', 'family': 'other'}, {'id': 'c', 'speaker': 'Eric', 'family': 'other'}, {'id': 'd', 'speaker': 'Serena'}]
        identities = voice_identities(pool, 'custom')
        self.assertEqual(identities['n'], identities['variant'])
        self.assertEqual({identities['a']}, {identities['b'], identities['c']})
        self.assertEqual(3, len({identities['n'], identities['a'], identities['d']}))
        clones = [{'id': 'x', 'references': {'neutral': {'audio': 'one.wav'}}}, {'id': 'y', 'references': {'neutral': {'audio': 'two.wav'}}},
                  {'id': 'z', 'family': 'y', 'references': {'neutral': {'audio': 'three.wav'}}}]
        identities = voice_identities(clones, 'clone', {'one.wav': 's', 'two.wav': 's', 'three.wav': 't'})
        self.assertEqual(1, len(set(identities.values())))

    def test_one_family_is_one_performance_in_a_dialogue(self):
        pool = [NARRATOR, voice('a', 'male', family='masculine_1'), voice('a2', 'male', family='masculine_1'), voice('b', 'male')]
        parts = [say('x', 'p1', 0), say('y', 'p2', 1)]
        cast = cast_voices([person('x', 'male'), person('y', 'male')], pool, parts)
        self.assertIn('b', assignment(cast).values())
        self.assertEqual([], casting_conflicts(cast, pool, parts))
        shared = [cast[0], {**cast[1], 'voice_id': 'a'}, {**cast[2], 'voice_id': 'a2'}]
        self.assertEqual([[['x', 'y']]], [conflict['shared_speakers'] for conflict in casting_conflicts(shared, pool, parts)])


class NarratorIdentity(unittest.TestCase):
    pool = [NARRATOR, voice('a', 'male'), voice('e', 'male'), voice('r', 'male', family='narrator_baseline')]

    def test_narrator_family_is_avoided_when_another_profile_fits(self):
        parts = alone('p', 'q', 's', 't', 'u', 'w')
        cast = cast_voices([person(cid, 'male') for cid in 'pqstuw'], self.pool, parts)
        self.assertNotIn('r', assignment(cast).values())
        self.assertNotIn('narrator_performance_shared', notices(cast_summary(cast, self.pool, parts)))

    def test_narrator_family_is_used_when_collisions_force_it_with_a_notice(self):
        parts = [say('kirill', 'p1', 0, characters=500), say('ivan', 'p2', 1, characters=400), say('kisly', 'p3', 2, characters=200)]
        cast = cast_voices([person(cid, 'male') for cid in ('kirill', 'ivan', 'kisly')], self.pool, parts)
        self.assertEqual('r', assignment(cast)['kisly'])
        summary = cast_summary(cast, self.pool, parts)
        self.assertEqual([], summary['conflicts'])
        self.assertEqual((1, ['Kisly']), (notices(summary)['narrator_performance_shared']['count'], notices(summary)['narrator_performance_shared']['params']['names']))

    def test_gender_compatibility_outranks_narrator_avoidance(self):
        pool = [NARRATOR, voice('r', 'male', family='narrator_baseline'), voice('f', 'female')]
        cast = cast_voices([person('ivan', 'male')], pool, alone('ivan'))
        self.assertEqual({'ivan': 'r'}, assignment(cast))
        self.assertIn('narrator_performance_shared', notices(cast_summary(cast, pool, alone('ivan'))))


class UnknownGender(unittest.TestCase):
    pool = [NARRATOR, *(voice(f'masculine_{i}', 'male') for i in range(1, 7)), *(voice(f'feminine_{i}', 'female') for i in range(1, 4))]

    def test_unknown_speakers_follow_the_known_gender_speech_balance(self):
        characters = ([person(f'm{i:02d}', 'male') for i in range(12)] + [person(f'f{i}', 'female') for i in range(3)]
                      + [person(f'u{i}') for i in range(4)])
        parts = alone(*(c['id'] for c in characters))
        cast = cast_voices(characters, self.pool, parts)
        unknown = [vid for cid, vid in assignment(cast).items() if cid.startswith('u')]
        self.assertEqual((3, 1), (sum(vid.startswith('masculine') for vid in unknown), sum(vid.startswith('feminine') for vid in unknown)))
        notice = notices(cast_summary(cast, self.pool, parts))['unknown_gender_speakers']
        self.assertEqual((4, ['U0', 'U1', 'U2', 'U3']), (notice['count'], notice['params']['names']))

    def test_targets_keep_the_running_balance(self):
        characters = [person('m', 'male'), person('f', 'female'), *(person(f'u{i}') for i in range(5))]
        counts = {'m': 400, 'f': 100, **{f'u{i}': 100 for i in range(5)}}
        targets = gender_targets(characters, {c['id']: self.pool for c in characters}, counts)
        self.assertEqual(['male', 'male', 'female', 'male', 'male'], [targets[f'u{i}'] for i in range(5)])
        self.assertEqual({}, gender_targets([person('u')], {'u': self.pool}, {'u': 10}))

    def test_without_known_gender_speech_unknown_speakers_spread_by_volume(self):
        pool = [NARRATOR, voice('a', 'male'), voice('b', 'female')]
        cast = cast_voices([person('x'), person('y')], pool, [say('x', 'p1', 0, 'c1', 30), say('y', 'p2', 1, 'c2', 20)])
        self.assertEqual({'x': 'a', 'y': 'b'}, assignment(cast))

    def test_unknown_speakers_may_use_genderless_profiles(self):
        pool = [NARRATOR, voice('m', 'male'), voice('g')]
        cast = cast_voices([person('k', 'male'), person('u')], pool, [say('k', 'p1', 0, 'c1', 1000), say('u', 'p2', 1, 'c2', 100)])
        self.assertEqual({'k': 'm', 'u': 'g'}, assignment(cast))

    def test_names_are_capped_and_omitted_without_gendered_profiles(self):
        characters = [person(f'u{i:02d}') for i in range(25)]
        parts = alone(*(c['id'] for c in characters))
        cast = cast_voices([person('k', 'male'), *characters], self.pool, [say('k', 'k', 99, 'ck', 5000), *parts])
        summary = cast_summary(cast, self.pool, parts)
        notice = notices(summary)['unknown_gender_speakers']
        self.assertEqual((25, 20), (notice['count'], len(notice['params']['names'])))
        self.assertTrue(summary['warnings'][summary['notices'].index(notice)].endswith(': U00, U01, U02, U03, U04, U05, U06, U07, U08, U09, U10, U11, '
                                                                                         'U12, U13, U14, U15, U16, U17, U18, U19 and 5 more.'))
        legacy = [NARRATOR, voice('a'), voice('b')]
        self.assertNotIn('unknown_gender_speakers', notices(cast_summary(cast_voices(characters, legacy, parts), legacy, parts)))


class CollisionFit(unittest.TestCase):
    def test_equal_weight_move_that_improves_fit_is_accepted_when_pairs_change(self):
        pool = [NARRATOR, voice('a', 'male'), voice('b', 'female')]
        parts = [say('ivan', 'p1', 1, characters=600), say('petr', 'p2', 2, characters=300), say('anna', 'p3', 3)]
        cast = cast_voices([person('ivan', 'male'), person('petr', 'male'), person('anna', 'female')], pool, parts)
        self.assertEqual({'ivan': 'a', 'petr': 'a', 'anna': 'b'}, assignment(cast))
        self.assertTrue(all(c['voice_compatible'] for c in cast[1:]))
        self.assertEqual([[['ivan', 'petr']]], [conflict['shared_speakers'] for conflict in cast_summary(cast, pool, parts)['conflicts']])
        characters = {c['id']: c for c in cast[1:]}
        voices = {v['id']: v for v in pool[1:]}
        keys = {vid: performance_key(v, 'custom') for vid, v in voices.items()}
        neighbours = conflict_graph(dialogue_contexts(parts), cast[1:])
        moved = optimize_casting({'ivan': 'a', 'petr': 'b', 'anna': 'b'}, set(characters), characters, voices, keys, neighbours, lambda cid, vid: 0)
        self.assertEqual({'ivan': 'a', 'petr': 'a', 'anna': 'b'}, moved)

    def test_crowded_scene_reuses_a_compatible_voice_for_the_lightest_conflict(self):
        pool = [NARRATOR, *(voice(f'masculine_{i}', 'male') for i in range(1, 7)), *(voice(f'feminine_{i}', 'female') for i in range(1, 4))]
        men = ['lecturer', 'questioner', 'r1', 'r2', 'r3', 'r4', 'r5']
        parts, ordinal = [], 0
        for run, speakers in enumerate((men + ['masha'], ['lecturer', 'r1', 'r2', 'r3', 'r4', 'r5', 'masha'])):
            for index, cid in enumerate(speakers + ['narrator', 'narrator']):
                ordinal += 1
                parts.append(say(cid, f'k{run}-p{index}', ordinal, 'c8', 400 if cid == 'questioner' else 60))
        parts += [say('questioner', f'q{index}', 100 + index, f'c{10 + index}', 200) for index in range(10)]
        cast = cast_voices([person(cid, 'male') for cid in men] + [person('masha', 'female')], pool, parts)
        self.assertTrue(all(c['voice_compatible'] for c in cast[1:]))
        self.assertTrue(all(vid.startswith('masculine') for cid, vid in assignment(cast).items() if cid != 'masha'))
        self.assertEqual([[['questioner', 'r1']]], [item['shared_speakers'] for item in cast_summary(cast, pool, parts)['conflicts']])

    def test_incompatible_profiles_are_classified_by_collision_benefit(self):
        pool = [NARRATOR, voice('f', 'female'), voice('m', 'male')]
        cast = [{'id': 'narrator', 'voice_id': 'n'}, {**person('x', 'female'), 'voice_id': 'f'}, {**person('y', 'female'), 'voice_id': 'm'}]
        together = [say('x', 'p1', 0), say('y', 'p2', 1)]
        apart = [say('x', 'p1', 0, 'c1'), say('y', 'p2', 1, 'c2')]
        self.assertIn('displaced_for_collision', notices(cast_summary(cast, pool, together)))
        self.assertIn('no_collision_gain', notices(cast_summary(cast, pool, apart)))
        self.assertNotIn('displaced_for_collision', notices(cast_summary(cast, pool, apart)))
        self.assertIn('no_compatible_profile', notices(cast_summary([cast[0], cast[2]], [NARRATOR, voice('m', 'male')], apart)))
        self.assertEqual([], cast_summary(cast, [NARRATOR, voice('m', 'male')], together)['conflicts'])

    def test_genderless_profile_for_a_known_gender_is_noticed(self):
        pool = [NARRATOR, voice('g'), voice('f', 'female')]
        cast = cast_voices([person('ivan', 'male')], pool, alone('ivan'))
        self.assertEqual({'ivan': 'g'}, assignment(cast))
        notice = notices(cast_summary(cast, pool, alone('ivan')))['genderless_profile_for_gendered']
        self.assertEqual((1, ['ivan']), (notice['count'], notice['params']['ids']))
        legacy = [NARRATOR, voice('g'), voice('h')]
        self.assertNotIn('genderless_profile_for_gendered', notices(cast_summary(cast_voices([person('ivan', 'male')], legacy, alone('ivan')), legacy, alone('ivan'))))


class AliasHints(unittest.TestCase):
    pool = [NARRATOR, voice('f1', 'female'), voice('f2', 'female'), voice('f3', 'female')]
    characters = [person('anna', 'female'), person('anna_petrovna', 'female'), person('olga', 'female')]

    def test_unmerged_alias_pair_prefers_one_performance_when_it_never_meets(self):
        parts = [say('anna', 'p1', 0, 'c1', 300), say('anna_petrovna', 'p2', 1, 'c2', 200), say('olga', 'p3', 2, 'c3', 100)]
        self.assertNotEqual(*(assignment(cast_voices(self.characters, self.pool, parts))[cid] for cid in ('anna', 'anna_petrovna')))
        hinted = assignment(cast_voices(self.characters, self.pool, parts, same_hints=[['anna', 'anna_petrovna']]))
        self.assertEqual(hinted['anna'], hinted['anna_petrovna'])

    def test_hint_never_joins_speakers_of_one_dialogue(self):
        parts = [say('anna', 'p1', 0), say('anna_petrovna', 'p2', 1), say('olga', 'p3', 5, 'c3')]
        hinted = assignment(cast_voices(self.characters, self.pool, parts, same_hints=[('anna', 'anna_petrovna'), ('olga', 'ghost')]))
        self.assertNotEqual(hinted['anna'], hinted['anna_petrovna'])


class SummaryNotices(unittest.TestCase):
    pool = [NARRATOR, voice('a', 'male'), voice('b', 'male')]

    def test_every_spec_code_has_english_text(self):
        self.assertEqual(CODES, set(NOTICES))
        self.assertEqual(6, CASTING_SCHEMA)

    def test_summary_returns_coded_notices_and_derived_warnings(self):
        parts = [say('ivan', 'p1', 0), say('unknown', 'p2', 1, characters=40), say('unknown', 'p3', 5, 'c2', characters=60)]
        cast = cast_voices([person('ivan', 'male')], self.pool, parts)
        summary = cast_summary(cast, self.pool, parts, unmerged=1, skipped=3, degraded=2)
        found = notices(summary)
        self.assertEqual({'pack_missing_metadata', 'alias_unmerged_capacity', 'alias_skipped_limit', 'unresolved_speech', 'degraded_passages'}, set(found))
        self.assertEqual((2, {'characters': 100}), (found['unresolved_speech']['count'], found['unresolved_speech']['params']))
        self.assertEqual((2, 3, {'limit': ALIAS_COMPARISON_LIMIT}, {'fields': ['age_bands']}),
                         (found['degraded_passages']['count'], found['alias_skipped_limit']['count'], found['alias_skipped_limit']['params'],
                          found['pack_missing_metadata']['params']))
        self.assertEqual(['No character voice profile declares age bands; these character traits cannot guide casting.',
                          '1 possible alias pair(s) exceeded the model context or output limit and remain separate characters.',
                          '3 possible alias pair(s) were not compared because the 256-comparison limit was reached; they remain separate characters.',
                          '2 speech part(s) have no resolved speaker and are held for review.',
                          '2 passage(s) could not be analysed for speakers and are held for review.'], summary['warnings'])
        self.assertEqual(summary['warnings'][:3], casting_warnings(cast, summary['conflicts'], parts, self.pool, 1, 3))

    def test_rendered_chapters_scope_conflicts_and_notices(self):
        parts = [say('ivan', 'p1', 0, 'c1'), say('petr', 'p2', 1, 'c2'), say('oleg', 'p3', 2, 'c2'), say('igor', 'p4', 3, 'c2'),
                 say('unknown', 'p5', 4, 'c2')]
        cast = cast_voices([person(cid, 'male') for cid in ('ivan', 'petr', 'oleg', 'igor')], self.pool, parts)
        whole = cast_summary(cast, self.pool, parts)
        self.assertEqual((1, ['c2']), (len(whole['conflicts']), notices(whole)['shared_performance_contexts']['params']['chapters']))
        scoped = cast_summary(cast, self.pool, parts, rendered_chapters=['c1'])
        self.assertEqual([], scoped['conflicts'])
        self.assertEqual({'pack_missing_metadata'}, set(notices(scoped)))


class NameNormalization(unittest.TestCase):
    def test_stress_marks_are_removed_but_letters_survive(self):
        self.assertEqual(('иван', 'петров'), words('Ива́н Петро́в'))
        self.assertEqual(('йод', 'елка', 'еж'), words('Йод, Ёлка, ёж'))
        self.assertEqual(('йод',), words('йод'))
        self.assertEqual(('josé',), words('José'))
        self.assertEqual('i', normal('İ'))
        self.assertTrue(contains(words('Иван Петров'), words('Ива́н')))


class PackVoiceSchema(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'pack'
        pack_fixture(self.root)
        self.manifest = json.loads((self.root / 'pack.json').read_text('utf-8'))

    def tearDown(self):
        self.temporary.cleanup()

    def load(self, voices=None, **text_model):
        manifest = {**self.manifest, 'voices': voices or self.manifest['voices'], 'text_model': {**self.manifest['text_model'], **text_model}}
        (self.root / 'pack.json').write_text(json.dumps(manifest), 'utf-8')
        return load_pack(str(self.root), verify=True)

    def rejected(self, message, voices=None, **text_model):
        with self.assertRaises(AppError) as raised:
            self.load(voices, **text_model)
        self.assertEqual(message, str(raised.exception))

    def test_roles_families_and_groups_are_validated(self):
        narrator, first, second = self.manifest['voices']
        loaded = self.load([{**narrator, 'family': 'narrator_baseline', 'group': 'Narrator'},
                            {'id': 'v', 'name': 'V', 'speaker': 'Dylan', 'role': 'narrator_variant', 'family': 'narrator_baseline'},
                            {'id': 'm', 'name': 'M', 'speaker': 'Eric', 'role': 'mechanical', 'family': 'mechanical.1-x', 'group': 'Mechanical voices'},
                            {**first, 'role': 'character'}, second])
        self.assertEqual(['narrator', 'narrator_variant', 'mechanical', 'character', 'character'],
                         [voice_details(item)['role'] for item in loaded['voices']])
        self.rejected('Unknown voice role.', [narrator, {**first, 'role': 'special'}, second])
        for family in ('bad family', '', 'x' * 65, 5, 'кириллица'):
            with self.subTest(family=family):
                self.rejected('Voice families must be stable, whitespace-free identifiers.', [narrator, {**first, 'family': family}, second])
        for group in ('', ' ', 'x' * 65, 5):
            with self.subTest(group=group):
                self.rejected('Expected nonempty text of at most 64 characters.', [narrator, {**first, 'group': group}, second])
        self.rejected('A pack must contain exactly one narrator profile.', [narrator, {**first, 'role': 'narrator'}, second])
        self.rejected('A voice pack needs at least one character performance.',
                      [narrator, {**first, 'role': 'narrator_variant'}, {**second, 'role': 'mechanical'}])

    def test_text_model_context_must_be_at_least_8192(self):
        for context in (4096, 8191, 0):
            with self.subTest(context=context):
                self.rejected('Text model context must be at least 8192 tokens.', context=context)
        for context in (131073, '8192', 8192.0):
            with self.subTest(context=context):
                self.rejected('Invalid model context or generation limit.', context=context)
        self.assertEqual(8192, self.load(context=8192)['text_model']['context'])
        del self.manifest['text_model']['context']
        self.assertNotIn('context', self.load()['text_model'])

    def test_example_pack_voices_are_valid(self):
        example = json.loads((ROOT / 'packs' / 'pack.example.json').read_text('utf-8'))
        clone = json.loads((ROOT / 'packs' / 'clone-voice.example.json').read_text('utf-8'))
        loaded = self.load(example['voices'], context=example['text_model']['context'])
        self.assertEqual({'narrator', 'narrator_variant', 'mechanical', 'character'}, {voice_details(item)['role'] for item in loaded['voices']})
        self.assertTrue(any(item.get('gender') for item in loaded['voices'] if voice_details(item)['role'] == 'character'))
        fields = {key: clone[key] for key in ('role', 'family', 'group')}
        self.assertEqual(fields, {key: voice_details(self.load([self.manifest['voices'][0], {**self.manifest['voices'][1], **fields}])['voices'][1])[key]
                                  for key in fields})


if __name__ == '__main__':
    unittest.main()
