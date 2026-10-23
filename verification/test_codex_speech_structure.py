from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.analysis import offered_reply, review_analysis
from rimsky.codex_analysis import analyze_with_details
from rimsky.speech_structure import (checked_speech_structure,
                                     speech_structure_request,
                                     structure_matches_answer)
from rimsky.util import AppError


def source(ident, text):
    return {'id': ident, 'source_id': ident, 'start': 0, 'end': len(text),
            'text': text, 'ordinal': 0, 'chapter_id': 'c', 'kind': 'paragraph'}


class CodexSpeechStructureTests(unittest.TestCase):
    def test_focused_pass_receives_only_em_dash_targets(self):
        dash = source('dash', 'Не помню, — признался он.')
        plain = source('plain', 'Обычное повествование.')
        request, schema = speech_structure_request(
            {'segments': [dash, plain], 'before': [], 'after': []})
        self.assertEqual([item['id'] for item in request['dash_targets']], ['dash'])
        self.assertEqual(schema['required'], ['dash'])
        fixed = checked_speech_structure({'dash': ['speech', 'narrator']}, request)
        self.assertEqual([item['mode'] for item in fixed['dash']],
                         ['speech', 'narrator'])

    def test_main_pass_must_preserve_focused_roles(self):
        text = 'Не помню, — признался он.'
        cut = text.index('—')
        fixed = {'dash': [
            {'start': 0, 'end': cut, 'text': text[:cut], 'mode': 'speech'},
            {'start': cut, 'end': len(text), 'text': text[cut:], 'mode': 'narrator'},
        ]}
        sources = {'dash': source('dash', text)}
        good = {'passages': [{'id': 'dash', 'parts': [
            {'first': 0, 'last': 0, 'speaker': 'person'},
            {'first': 1, 'last': 1, 'speaker': 'narrator'},
        ]}]}
        structure_matches_answer(good, fixed, sources)
        bad = {'passages': [{'id': 'dash', 'parts': [
            {'first': 0, 'last': 1, 'speaker': 'narrator'},
        ]}]}
        with self.assertRaisesRegex(AppError, 'fixed_speech_structure marks the units of .* as speech'):
            structure_matches_answer(bad, fixed, sources)
        with self.assertRaisesRegex(AppError, 'omitted fixed speech-structure target'):
            structure_matches_answer({'passages': []}, fixed, sources)

    def test_codex_analysis_runs_structure_before_voice_and_mood(self):
        target = source('dash', 'Не помню, — признался он.')
        split = target['text'].index('—')
        calls = []

        def fake_call(system, request, schema, **kwargs):
            calls.append(request)
            if 'dash_targets' in request:
                return {'dash': ['speech', 'narrator']}
            self.assertEqual(request['fixed_speech_structure'], [{'id': 'dash', 'chunks': [
                {'first': 0, 'last': 0, 'mode': 'speech'},
                {'first': 1, 'last': 1, 'mode': 'narrator'}]}])
            self.assertEqual([unit['text'] for unit in request['targets'][0]['units']],
                             [target['text'][:split], target['text'][split:]])
            neutral = {'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
                       'confidence': 'high', 'evidence': []}
            return {'characters': [], 'passages': [{'id': 'dash', 'parts': [
                {'first': 0, 'last': 0, 'speaker': 'unknown',
                 'delivery': 'neutral', 'evidence': [], 'context': neutral},
                {'first': 1, 'last': 1, 'speaker': 'narrator',
                 'delivery': 'neutral', 'evidence': [], 'context': neutral},
            ]}]}

        with patch('rimsky.codex_analysis.structured_call', side_effect=fake_call):
            answer, fixed = analyze_with_details(
                {'cast': [], 'segments': [target], 'before': [], 'after': []})
        self.assertEqual(len(calls), 2)
        self.assertEqual([item['mode'] for item in fixed['dash']],
                         ['speech', 'narrator'])
        self.assertEqual([part['speaker'] for part in answer['passages'][0]['parts']],
                         ['unknown', 'narrator'])
        parts, _, invalid = review_analysis(answer, [target], [], fixed=fixed)
        self.assertEqual(invalid, {})
        self.assertEqual([(part['text'], part['speaker']) for part in parts],
                         [(target['text'][:split], 'unknown'), (target['text'][split:], 'narrator')])

    def provider_envelope(self, mode, provider, other):
        import worker
        target = source('dash', 'Не помню, — признался он.')
        answer = {'characters': [], 'passages': [{'id': 'dash', 'parts': []}]}

        def fake_call(system, request, schema, **kwargs):
            return {'dash': ['speech', 'narrator']} if 'dash_targets' in request else answer

        payload = {'analysis_mode': mode, 'pack': {'text_model': {}},
                   'segments': [target], 'cast': [], 'before': [], 'after': []}
        with patch(f'rimsky.{provider}.structured_call', side_effect=fake_call), \
                patch(f'rimsky.{other}.structured_call', side_effect=AssertionError('wrong provider')):
            result = worker.analyze(payload)
        self.assertEqual(set(result), {'reply', 'known', 'speech_structure'})
        self.assertEqual(result['reply'], answer)
        self.assertEqual(result['known'], [])
        self.assertEqual([chunk['mode'] for chunk in result['speech_structure']['dash']],
                         ['speech', 'narrator'])
        reply, known, extras = offered_reply(result, payload)
        self.assertEqual((reply, known), (answer, []))
        self.assertEqual(extras['speech_structure'], result['speech_structure'])
        self.assertFalse(extras['staged'])

    def test_worker_returns_structure_envelope_for_desktop_job(self):
        self.provider_envelope('codex_cli', 'codex_analysis', 'claude_analysis')

    def test_worker_accepts_claude_as_full_analyzer(self):
        self.provider_envelope('claude_cli', 'claude_analysis', 'codex_analysis')

    def test_auto_prefers_codex_then_claude_then_local(self):
        import worker
        with patch('worker.trusted_executable', return_value='cli'):
            self.assertEqual(worker.resolved_analysis_mode('auto'), 'codex_cli')
        with patch('worker.trusted_executable', side_effect=lambda names, **options:
                   (_ for _ in ()).throw(worker.AppError('missing')) if 'codex' in names else 'claude'):
            self.assertEqual(worker.resolved_analysis_mode('auto'), 'claude_cli')
        with patch('worker.trusted_executable', side_effect=worker.AppError('missing')):
            self.assertEqual(worker.resolved_analysis_mode('auto'), 'local')

    def test_local_model_speakers_remain_review_suggestions(self):
        import worker
        records = [
            {'tier': 'explicit_attribution', 'status': 'resolved', 'triggers': []},
            {'tier': 'local', 'status': 'resolved', 'triggers': []},
            {'tier': 'tie_breaker', 'status': 'resolved', 'triggers': []},
        ]
        worker.hold_unverified_local_resolutions(records)
        self.assertEqual(records[0]['status'], 'resolved')
        self.assertEqual(records[1]['status'], 'review')
        self.assertIn('local_fallback_review', records[1]['triggers'])
        self.assertEqual(records[2]['status'], 'resolved')


if __name__ == '__main__':
    unittest.main()
