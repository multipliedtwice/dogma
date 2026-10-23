from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.analysis import SYSTEM_PROMPT, review_analysis
from rimsky.boundaries import boundary_units
from rimsky.critic import CRITIC_SYSTEM
from rimsky.speaker_resolution import (TIE_BREAKER_SYSTEM, TRANSLATION_SPEAKER_RULE,
                                       translation_leadin_candidates, translation_leadin_speaker)
from rimsky.staged_analysis import SPEAKER_SYSTEM, staged_proposal_with_details


CAST = [
    {'id': 'ayni', 'name': 'Айни Ури-Миайуу', 'aliases': ['Айни'], 'description': ''},
    {'id': 'unknown_mielon', 'name': 'Неизвестный миелонец', 'aliases': [],
     'description': 'Говорит через Айни.'},
]
CASES = [
    ('А между тем Айни перевела следующие фразы неизвестного миелонца:',
     '— Думаю, ты понимаешь, почему мы здесь. Ты нанёс серьёзное оскорбление прайду Небесного Бойца.'),
    ('А между тем неизвестный миелонец что-то спросил, и Айни с готовностью перевела:',
     '— Ты сам-то кто, Комар? Видим, что герд, но из какого ты рода-племени? Кто стоит за твоей спиной?'),
]
CONTEXT = {'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
           'confidence': 'low', 'evidence': []}


def source(ident, text, ordinal):
    return {'id': ident, 'source_id': ident, 'text': text, 'start': 0,
            'end': len(text), 'ordinal': ordinal, 'chapter_id': 'c', 'scene_id': 'scene'}


def answer(target, speaker):
    return {'characters': [], 'passages': [{'id': target['id'], 'parts': [{
        'first': 0, 'last': len(boundary_units(target['text'])) - 1,
        'speaker': speaker, 'delivery': 'neutral', 'context': dict(CONTEXT),
        'evidence': [] if speaker == 'unknown' else [target['id']]}]}]}


class TranslationAttributionTests(unittest.TestCase):
    def test_both_explicit_leadins_identify_the_translator_not_the_source(self):
        for lead, _ in CASES:
            with self.subTest(lead=lead):
                self.assertEqual(translation_leadin_speaker(lead, CAST), 'ayni')

    def test_nonassertive_silent_automatic_and_ambiguous_mentions_do_not_fix_identity(self):
        for lead in (
            'Айни не перевела слова неизвестного миелонца:',
            'Айни якобы перевела слова неизвестного миелонца:',
            'Айни перевела бы слова неизвестного миелонца:',
            'Айни могла перевести слова неизвестного миелонца:',
            'Айни попросила неизвестного миелонца перевести слова:',
            'Айни перевела взгляд на неизвестного миелонца:',
            'Айни молча прочитала перевод:',
            'Система автоматически перевела слова неизвестного миелонца:',
            'Неизвестный миелонец спросил Айни:',
            'Айни перевела слова. Неизвестный миелонец спросил:',
            'Она перевела слова неизвестного миелонца:',
        ):
            with self.subTest(lead=lead):
                self.assertIsNone(translation_leadin_speaker(lead, CAST))
        self.assertIsNone(translation_leadin_speaker(CASES[0][0], CAST[1:]))
        duplicate = [*CAST, {'id': 'other_ayni', 'name': 'Айни', 'aliases': []}]
        self.assertIsNone(translation_leadin_speaker(CASES[0][0], duplicate))
        system = [{'id': 'system', 'name': 'Система', 'aliases': []}]
        self.assertIsNone(translation_leadin_speaker(
            'Система автоматически перевела слова неизвестного миелонца:', system))

    def test_all_identity_prompts_include_the_translation_rule(self):
        for prompt in (SYSTEM_PROMPT, SPEAKER_SYSTEM, TIE_BREAKER_SYSTEM, CRITIC_SYSTEM):
            self.assertIn(TRANSLATION_SPEAKER_RULE, prompt)

    def test_primary_validator_rejects_originator_accepts_translator_or_unknown(self):
        for lead, speech in CASES:
            prior, target = source('prior', lead, 0), source('target', speech, 1)
            for speaker in ('unknown_mielon', 'ayni', 'unknown'):
                with self.subTest(speech=speech, speaker=speaker):
                    parts, _, invalid = review_analysis(
                        answer(target, speaker), [target], CAST, [prior], kind_evidence=False)
                    if speaker == 'unknown_mielon':
                        self.assertIn('explicit translation lead-in', invalid[target['id']])
                        self.assertEqual(parts, [])
                    else:
                        self.assertEqual(invalid, {})
                        self.assertTrue(parts)
                        self.assertTrue(all(part['speaker'] == speaker for part in parts))

    def test_validator_does_not_carry_translation_across_gap_scene_or_direct_attribution(self):
        lead, speech = CASES[0]
        prior, target = source('prior', lead, 0), source('target', speech, 1)
        for changed in ({**target, 'ordinal': 2}, {**target, 'scene_id': 'elsewhere'},
                        source('target', '— Я здесь. — сказал Неизвестный миелонец.', 1)):
            with self.subTest(target=changed):
                _, _, invalid = review_analysis(answer(changed, 'unknown_mielon'), [changed],
                                                CAST, [prior], kind_evidence=False)
                self.assertEqual(invalid, {})

    def test_staged_wrong_originator_is_repaired_to_translator(self):
        for lead, speech in CASES:
            prior, target = source('prior', lead, 0), source('target', speech, 1)
            requests, records = [], []

            def infer(system, request, schema, limit):
                if 'chunks' in request:
                    return {'modes': ['speech'] * len(request['chunks'])}
                requests.append(request)
                self.assertEqual(request['translation_performer_hint'], 'ayni')
                if len(requests) == 2:
                    self.assertIn('current performer', request['repair']['validation_error'])
                return {'speaker': 'unknown_mielon' if len(requests) == 1 else 'ayni',
                        'name': '', 'evidence_id': target['id'],
                        'evidence_quote': speech[2:20]}

            proposal, _ = staged_proposal_with_details(
                [target], CAST, [prior], [], infer, include_context=False, resolution_log=records)
            self.assertEqual(len(requests), 2)
            self.assertEqual([p['speaker'] for p in proposal['passages'][0]['parts']], ['ayni'])
            self.assertEqual(records[0]['status'], 'resolved')

    def test_staged_repeated_wrong_answer_stays_unknown_including_tie_breaker(self):
        prior, target = source('prior', CASES[0][0], 0), source('target', CASES[0][1], 1)
        records, calls = [], []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech'] * len(request['chunks'])}
            calls.append(system)
            return {'speaker': 'unknown_mielon', 'name': '',
                    'evidence_id': target['id'], 'evidence_quote': target['text'][2:20]}

        proposal, _ = staged_proposal_with_details(
            [target], CAST, [prior], [], infer, include_context=False,
            tie_breaker_infer=infer, resolution_log=records)
        self.assertEqual(len(calls), 4)
        self.assertIn(TIE_BREAKER_SYSTEM, calls)
        self.assertEqual(proposal['passages'][0]['parts'][0]['speaker'], 'unknown')
        self.assertEqual(records[0]['status'], 'review')

    def test_staged_translation_hint_does_not_cross_a_scene_or_chapter(self):
        prior = source('prior', CASES[0][0], 0)
        for target in ({**source('target', CASES[0][1], 1), 'scene_id': 'other'},
                       {**source('target', CASES[0][1], 1), 'chapter_id': 'other'},
                       source('target', CASES[0][1], 2)):
            with self.subTest(target=target):
                def infer(system, request, schema, limit):
                    if 'chunks' in request:
                        return {'modes': ['speech'] * len(request['chunks'])}
                    self.assertEqual(request['translation_performer_hint'], '')
                    return {'speaker': 'unknown_mielon', 'name': '',
                            'evidence_id': target['id'], 'evidence_quote': target['text'][2:20]}
                proposal, _ = staged_proposal_with_details(
                    [target], CAST, [prior], [], infer, include_context=False)
                self.assertEqual(proposal['passages'][0]['parts'][0]['speaker'], 'unknown_mielon')

    def test_duplicate_translator_entries_still_reject_the_originator_and_need_review(self):
        # The frozen Book 2 registry contains both the full name and short name.
        cast = [*CAST, {'id': 'ayni_short', 'name': 'Айни', 'aliases': [], 'description': ''}]
        prior, target = source('prior', CASES[0][0], 0), source('target', CASES[0][1], 1)
        self.assertEqual(translation_leadin_candidates(prior['text'], cast), {'ayni', 'ayni_short'})
        _, _, invalid = review_analysis(answer(target, 'unknown_mielon'), [target], cast,
                                        [prior], kind_evidence=False)
        self.assertIn('explicit translation lead-in', invalid[target['id']])
        records = []

        def infer(system, request, schema, limit):
            if 'chunks' in request:
                return {'modes': ['speech'] * len(request['chunks'])}
            self.assertEqual(request['translation_performer_hint'], '')
            self.assertEqual(request['translation_performer_candidates'], ['ayni', 'ayni_short'])
            return {'speaker': 'ayni', 'name': '', 'evidence_id': target['id'],
                    'evidence_quote': target['text'][2:20]}

        staged_proposal_with_details([target], cast, [prior], [], infer,
                                     include_context=False, tie_breaker_infer=infer,
                                     resolution_log=records)
        self.assertEqual(records[0]['status'], 'review')
        self.assertIn('ambiguous_translation_performer', records[0]['triggers'])


if __name__ == '__main__':
    unittest.main()
