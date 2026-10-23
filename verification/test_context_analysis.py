from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.analysis import analysis_schema, degraded_parts, passage_parts, synthesis_units, validate_analysis, without_source_ranges
from rimsky.boundaries import unit_range
from rimsky.context_analysis import checked_context, MOODS, channel_voice, mark_displayed_game_cards
from rimsky.presentation_voice import validate_presentation_profile
from rimsky.staged_analysis import staged_proposal, unit_answer
from rimsky.util import AnalysisInvalidError, AppError


def source(text, ident='p', ordinal=0, formatting=None):
    return {'id': ident, 'source_id': ident, 'start': 0, 'end': len(text),
            'text': text, 'ordinal': ordinal, 'chapter_id': 'c',
            **({'formatting': formatting} if formatting else {})}


def context(mood='neutral', channel='direct', ident='p', quote='', register=None):
    register = register or {'game_ui': 'game_system', 'radio': 'transmitted_speech',
                            'intercom': 'transmitted_speech',
                            'public_address': 'transmitted_speech',
                            'recorded': 'recorded_message',
                            'telepathic': 'telepathy',
                            'written_text': 'written_document'}.get(channel, 'prose')
    return {'mood': mood, 'channel': channel, 'content_register': register,
            'confidence': 'high',
            'evidence': [{'id': ident, 'quote': quote}] if quote else []}


def units(proposal, targets, context=()):
    parts, cast = validate_analysis(unit_answer(proposal, targets), targets, [], context)
    return parts, synthesis_units(parts, targets, context), cast


def whole(target, **fields):
    first, last = unit_range(target['text'], 0, len(target['text']))
    return {'characters': [], 'passages': [{'id': target['id'], 'parts': [{
        'first': first, 'last': last, 'speaker': 'narrator', 'delivery': 'neutral',
        'evidence': [], **fields}]}]}


class ContextAnalysisTests(unittest.TestCase):
    def test_retry_replaces_only_evidence_inside_the_same_paragraph_range(self):
        entries = [
            {'source_id': 'p', 'start': 2, 'characters': 3},
            {'source_id': 'p', 'start': 12, 'characters': 3},
            {'source_id': 'q', 'start': 12, 'characters': 3},
        ]
        retried = {'source_id': 'p', 'start': 10, 'end': 20, 'text': 'x' * 10}
        self.assertEqual([entries[0], entries[2]], without_source_ranges(entries, [retried]))

    def test_game_ui_allows_a_system_message_or_a_formatted_stat_card(self):
        sources = {'p': 'Сила: 14'}
        base = {'mood': 'neutral', 'channel': 'game_ui', 'confidence': 'high',
                'evidence': [{'id': 'p', 'quote': 'Сила: 14'}]}
        for register in ('game_system', 'item_or_stat_card'):
            with self.subTest(register=register):
                self.assertEqual(register, checked_context({**base, 'content_register': register}, sources)['content_register'])

    def test_context_evidence_accepts_only_safe_typographic_variants_and_keeps_source_text(self):
        value = context('reflective', quote='Он все понял...')
        checked = checked_context(value, {'p': 'Он всё понял… Потом ушёл.'})
        self.assertEqual([{'id': 'p', 'quote': 'Он всё понял…'}], checked['evidence'])
        with self.assertRaisesRegex(AnalysisInvalidError, 'exact quote'):
            checked_context(context('reflective', quote='Он всё забыл...'), {'p': 'Он всё понял…'})

    def test_whole_passage_non_prose_can_supply_its_own_missing_evidence(self):
        target = source('Сила: 14', formatting={'enclosing': [], 'inline': ['strong'], 'full': ['strong']})
        proposal = whole(target, context=context(channel='game_ui'))
        fine, _ = validate_analysis(proposal, [target], [])
        self.assertEqual([{'id': 'p', 'quote': target['text']}], fine[0]['context']['evidence'])

    def test_staged_whole_passage_stat_card_supplies_missing_evidence(self):
        target = source('Сила: 14', formatting={'enclosing': [], 'inline': ['strong'], 'full': ['strong']})
        def infer(*args):
            return {'contexts': [context(channel='game_ui')]}
        proposal = staged_proposal([target], [], [], [], infer, 'russian_dash')
        self.assertEqual([{'id': 'p', 'quote': target['text']}],
                         proposal['passages'][0]['parts'][0]['context']['evidence'])

    def test_plain_stat_rows_inherit_a_formatted_card_header_until_prose(self):
        header = source('Характеристики персонажа:', 'h', 0,
                        {'enclosing': [], 'inline': ['strong'], 'full': ['strong']})
        strength = source('Сила 13', 's', 1)
        health = source('Очков здоровья 817 из 998', 'hp', 2)
        prose = source('Я закрыл окно и пошёл дальше.', 'p', 3)
        later_number = source('У него осталось 12 монет', 'n', 4)
        mark_displayed_game_cards([header, strength, health, prose, later_number])
        self.assertTrue(strength['displayed_game_card'])
        self.assertTrue(health['displayed_game_card'])
        self.assertNotIn('displayed_game_card', prose)
        self.assertNotIn('displayed_game_card', later_number)
        proposal = whole(strength, context=context(channel='game_ui', register='item_or_stat_card'))
        fine, _ = validate_analysis(proposal, [strength], [])
        self.assertEqual([{'id': 's', 'quote': 'Сила 13'}], fine[0]['context']['evidence'])

    def test_whole_passage_evidence_fallback_never_approves_mood_or_other_channels(self):
        target = source('Остановись сейчас!')
        rejected = [
            context('angry'), context('whisper'), context(channel='telepathic'),
            context('angry', channel='game_ui'),
        ]
        for value in rejected:
            with self.subTest(mood=value['mood'], channel=value['channel']):
                proposal = whole(target, context=value)
                with self.assertRaisesRegex(AppError, 'context evidence'):
                    validate_analysis(proposal, [target], [])

                def infer(*args, answer=value):
                    return {'contexts': [answer]}
                with self.assertRaisesRegex(AnalysisInvalidError, 'context evidence'):
                    staged_proposal([target], [], [], [], infer, 'russian_dash')

    def test_plain_prose_claimed_as_game_ui_still_needs_evidence(self):
        target = source('Требования были весьма щадящими.')
        proposal = whole(target, context=context(channel='game_ui'))
        with self.assertRaisesRegex(AppError, 'context evidence'):
            validate_analysis(proposal, [target], [])

    def test_partial_non_prose_still_requires_model_evidence(self):
        target = source('Сила: 14. Ловкость: 12.')
        first, last = unit_range(target['text'], 0, len(target['text']))
        proposal = {'characters': [], 'passages': [{'id': 'p', 'parts': [
            {'first': first, 'last': first, 'speaker': 'narrator', 'delivery': 'neutral',
             'evidence': [], 'context': context(channel='game_ui')},
            {'first': first + 1, 'last': last, 'speaker': 'narrator', 'delivery': 'neutral',
             'evidence': [], 'context': context()},
        ]}]}
        parts, error = passage_parts(proposal['passages'][0], target, lambda value: value, {}, {'p': target['text']})
        self.assertFalse(parts)
        self.assertIn('context evidence', error)

    def test_production_schema_requires_context(self):
        part = analysis_schema([source('Текст.')])['properties']['passages']['items']['properties']['parts']['items']
        self.assertIn('context', part['required'])

    def test_plain_prose_gets_context_call_even_in_dash_mode(self):
        text = ('Требования к характеристикам были весьма щадящими, '
                'подходящими практически каждому человеку.')
        target = source(text)
        before = [{'id': 'before', 'text': 'Время работы на одном резервуаре: 2,5 часа.'}]
        after = [{'id': 'after', 'text': 'Я открыл окно экипировки и переложил скафандр туда.'}]
        calls = []

        def infer(system, request, schema, tokens):
            calls.append(request)
            self.assertIn('context_before', request)
            self.assertEqual(request['context_after'], after)
            self.assertEqual(request['units'][0]['speaker'], 'narrator')
            self.assertEqual(request['target']['text'], text)
            return {'contexts': [context()]}

        proposal = staged_proposal([target], [], before, after, infer, 'russian_dash')
        _, parts, _ = units(proposal, [target], before + after)
        self.assertEqual(len(calls), 1)
        self.assertEqual(parts[0]['performance']['presentation_channel'], 'direct')
        self.assertNotIn('mechanical_voice', parts[0]['performance']['cues'])

    def test_game_and_radio_channels_do_not_create_characters(self):
        for channel in ('game_ui', 'radio'):
            target = source('Внимание! Угроза прорыва!')
            proposal = whole(target, context=context('warning', channel, quote=target['text']))
            fine, cast = validate_analysis(proposal, [target], [])
            parts = synthesis_units(fine, [target])
            self.assertEqual(cast, [])
            self.assertEqual(parts[0]['speaker'], 'narrator')
            self.assertEqual(parts[0]['performance']['mood'], 'warning')
            self.assertEqual(parts[0]['performance']['presentation_channel'], channel)
            self.assertEqual(parts[0]['performance']['content_register'],
                             context(channel=channel)['content_register'])

    def test_changed_mood_keeps_sentence_boundaries(self):
        text = 'Я побежал по лестнице. Увиденное впечатляло!'
        target = source(text)
        def infer(system, request, schema, tokens):
            return {'contexts': [context('energetic', quote='Я побежал по лестнице.'),
                                 context('excited', quote='Увиденное впечатляло!')]}
        proposal = staged_proposal([target], [], [], [], infer, 'russian_dash')
        fine, parts, _ = units(proposal, [target])
        self.assertEqual([p['delivery'] for p in fine], ['neutral', 'excited'])
        self.assertEqual([p['performance']['mood'] for p in parts], ['energetic', 'excited'])
        self.assertEqual(''.join(p['text'] for p in parts), text)

    def test_invalid_context_is_repaired_with_original_input_and_error(self):
        target = source('И тут мне вдруг стало хорошо.')
        calls = []
        def infer(system, request, schema, tokens):
            calls.append(request)
            if len(calls) == 1:
                return {'contexts': [context('relieved', quote='invented quotation')]}
            self.assertIn('exact quote', request['repair']['validation_error'])
            self.assertEqual(request['target']['text'], target['text'])
            return {'contexts': [context('relieved', quote=target['text'])]}
        proposal = staged_proposal([target], [], [], [], infer, 'russian_dash')
        _, parts, _ = units(proposal, [target])
        self.assertEqual(len(calls), 2)
        self.assertEqual(parts[0]['performance']['mood'], 'relieved')

    def test_repeated_invalid_context_fails_instead_of_approving_default(self):
        with self.assertRaisesRegex(AnalysisInvalidError, 'cover every numbered unit'):
            staged_proposal([source('Полученные знания пригодились мне.')], [], [], [],
                            lambda *args: {'contexts': []}, 'russian_dash')

    def test_evidence_and_allowed_moods(self):
        for value in (context('warning'), context(channel='game_ui'),
                      context('unsure'), context('surprised', ident='missing', quote='hello')):
            with self.assertRaises(AppError):
                checked_context(value, {'p': 'hello'})
        js = (ROOT / 'scripts/book1_review_shared.js').read_text(encoding='utf-8')
        groups = js.split('const MOOD_GROUPS = {', 1)[1].split('};', 1)[0]
        self.assertEqual(set(MOODS), set(re.findall(r"'([a-z_]+)'", groups)))

    def test_legacy_proposals_remain_unverified_not_relabelled(self):
        target = source('Обычное повествование.')
        with self.assertRaisesRegex(AppError, 'context'):
            validate_analysis(whole(target), [target], [])
        unverified = {**context(), 'confidence': 'low'}
        for parts in (validate_analysis(whole(target, context=unverified), [target], [])[0],
                      degraded_parts(target)):
            plan = synthesis_units(parts, [target])[0]['performance']
            self.assertEqual(plan['context_confidence'], 'low')
            self.assertEqual(plan['basis'], 'text_intent_unverified_audio')

    def test_channel_voice_requires_context_not_words(self):
        voices = [{'id': 'mechanical_game_voice', 'tags': []}, {'id': 'narrator', 'tags': []}]
        intent = {'presentation_channel': 'direct', 'context_confidence': 'high',
                  'context_evidence': [{'id': 'p', 'quote': 'Требования к характеристикам'}]}
        self.assertEqual(channel_voice(intent, voices, 'narrator'), ('narrator', ''))
        intent['presentation_channel'] = 'game_ui'
        self.assertEqual(channel_voice(intent, voices, 'narrator'), ('mechanical_game_voice', ''))
        intent['context_confidence'] = 'low'
        self.assertEqual(channel_voice(intent, voices, 'narrator'), ('narrator', ''))
        intent['context_confidence'] = 'high'
        intent['presentation_channel'] = 'radio'
        self.assertEqual(channel_voice(intent, voices, 'male_1'), ('male_1', ''))
        intent['presentation_channel'] = 'game_ui'
        assigned, warning = channel_voice(intent, [{'id': 'narrator'}], 'narrator')
        self.assertEqual(assigned, 'narrator')
        self.assertIn('No single game readout voice', warning)

    def test_narrator_profile_maps_displayed_content_but_preserves_transmission_speaker(self):
        voices = [
            {'id': 'feminine_3', 'tags': ['feminine']},
            {'id': 'feminine_4_mechanical', 'tags': []},
            {'id': 'masculine_6_mechanical', 'tags': []},
            {'id': 'mechanical_stats', 'tags': []},
        ]
        profile = {'schema': 1, 'narrator_id': 'vlad-rimsky', 'channels': {
            'radio': {'mode': 'speaker'},
            'game_ui': {'mode': 'fixed', 'voice': 'mechanical_stats'},
            'telepathic': {'mode': 'mapped',
                           'by_assigned_tag': {'feminine': 'feminine_4_mechanical',
                                               'masculine': 'masculine_6_mechanical'},
                           'fallback': 'review'},
        }, 'registers': {
            'written_document': {'mode': 'mapped',
                                 'by_assigned_tag': {'feminine': 'feminine_4_mechanical'},
                                 'fallback': 'review'},
        }}
        intent = {'presentation_channel': 'telepathic', 'context_confidence': 'high',
                  'context_evidence': [{'id': 'p', 'quote': 'message'}]}
        self.assertEqual(channel_voice(intent, voices, 'feminine_3', profile),
                         ('feminine_4_mechanical', ''))
        intent['presentation_channel'] = 'radio'
        self.assertEqual(channel_voice(intent, voices, 'feminine_3', profile),
                         ('feminine_3', ''))
        intent['presentation_channel'] = 'telepathic'
        voice, warning = channel_voice(intent, voices, 'unknown_family', profile)
        self.assertEqual(voice, 'unknown_family')
        self.assertIn('review is required', warning)

        intent.update(presentation_channel='direct', content_register='written_document')
        self.assertEqual(channel_voice(intent, voices, 'feminine_3', profile),
                         ('feminine_4_mechanical', ''))
        intent['content_register'] = 'poem'
        voice, warning = channel_voice(intent, voices, 'feminine_3', profile)
        self.assertEqual(voice, 'feminine_3')
        self.assertIn('review is required', warning)

    def test_vlad_profile_warns_for_provisional_rules_and_mapping_branches(self):
        profile = json.loads((ROOT / 'training/narrator_presentation_profiles/vlad-rimsky.json').read_text(
            encoding='utf-8'))
        voices = json.loads((ROOT / 'training/voice_families.json').read_text(
            encoding='utf-8'))['families']
        validate_presentation_profile(profile, {voice['id'] for voice in voices})

        recorded = {'presentation_channel': 'recorded',
                    'content_register': 'recorded_message',
                    'context_confidence': 'high',
                    'context_evidence': [{'id': 'p', 'quote': 'recording'}]}
        voice, warning = channel_voice(recorded, voices, 'masculine_1', profile)
        self.assertEqual(voice, 'mechanical_envoy')
        self.assertIn('provisional', warning)
        self.assertIn('2 reviewed clips', warning)

        telepathy = {'presentation_channel': 'telepathic',
                     'content_register': 'telepathy',
                     'context_confidence': 'high',
                     'context_evidence': [{'id': 'p', 'quote': 'thought'}]}
        female_voice, female_warning = channel_voice(
            telepathy, voices, 'feminine_3', profile)
        self.assertEqual(female_voice, 'feminine_4_mechanical')
        self.assertEqual(female_warning, '')
        male_voice, male_warning = channel_voice(
            telepathy, voices, 'masculine_1', profile)
        self.assertEqual(male_voice, 'masculine_6_mechanical')
        self.assertIn('no reviewed male telepathy', male_warning)

        quoted = {**telepathy, 'presentation_channel': 'written_text',
                  'content_register': 'quoted_document'}
        quoted_voice, quoted_warning = channel_voice(
            quoted, voices, 'feminine_3', profile)
        self.assertEqual(quoted_voice, 'feminine_4_mechanical')
        self.assertIn('borrowed from written text', quoted_warning)

if __name__ == '__main__':
    unittest.main()
