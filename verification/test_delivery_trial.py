"""Delivery trial: strict answer contract, bound checkpoints, honest scoring."""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parents[1] / 'runtime')]
import run_delivery_trial as trial  # noqa: E402

RULES = [{'id': 'r1/mood/calm', 'mood': 'calm', 'status': 'observed'},
         {'id': 'r1/mood/curious', 'mood': 'curious', 'status': 'observed'},
         {'id': 'r1/mood/surprised', 'mood': 'surprised', 'status': 'unobserved_in_development'}]
REQUEST = {'targets': [{'unit_key': 'u1', 'text': 'Он вошёл.', 'performer_route': 'r1'},
                       {'unit_key': 'u2', 'text': 'Что это такое?', 'performer_route': 'r1'}],
           'scene': [], 'rulebook': {'all_supported_mood_ids': ['calm', 'curious', 'surprised', 'neutral'],
                                     'character_routes': [{'route_id': 'r1', 'rules': RULES}]}}


def decision(unit, mood, rules, status='proposed'):
    return {'unit_key': unit, 'mood': mood, 'nearest_alternative': 'calm', 'rule_ids': rules, 'reason': 'x', 'status': status}


GOOD = {'decisions': [decision('u1', 'calm', ['r1/mood/calm']), decision('u2', 'curious', ['r1/mood/curious'])]}
SCHEMA = trial.answer_schema(['u1', 'u2'], ['calm', 'curious', 'neutral', 'surprised'])
SETTINGS = {'provider': 'codex', 'reasoning_effort': 'low', 'cli_version': 'codex 1.0'}


class AnswerContractTests(unittest.TestCase):
    def test_complete_well_cited_answer_is_valid(self):
        self.assertEqual([], trial.validate_answer(REQUEST, GOOD))

    def test_missing_duplicate_and_unexpected_decisions_are_rejected(self):
        omitted = {'decisions': GOOD['decisions'][:1]}
        self.assertIn('missing decision for u2', trial.validate_answer(REQUEST, omitted))
        twice = {'decisions': GOOD['decisions'] + [decision('u2', 'calm', ['r1/mood/calm'])]}
        self.assertIn('duplicate decision for u2', trial.validate_answer(REQUEST, twice))
        extra = {'decisions': GOOD['decisions'] + [decision('u9', 'calm', ['r1/mood/calm'])]}
        self.assertIn('unexpected decision for u9', trial.validate_answer(REQUEST, extra))

    def test_citations_must_belong_to_the_route_and_cover_the_chosen_mood(self):
        foreign = {'decisions': [decision('u1', 'calm', ['r2/mood/calm', 'r1/mood/calm']), GOOD['decisions'][1]]}
        self.assertTrue(any('outside its route' in e for e in trial.validate_answer(REQUEST, foreign)))
        uncited = {'decisions': [decision('u1', 'calm', ['r1/mood/curious']), GOOD['decisions'][1]]}
        self.assertTrue(any('does not cite the rule' in e for e in trial.validate_answer(REQUEST, uncited)))

    def test_the_full_response_schema_is_checked_locally(self):
        self.assertEqual([], trial.validate_answer(REQUEST, GOOD, SCHEMA))
        odd = {'decisions': [dict(GOOD['decisions'][0], status='accepted'), GOOD['decisions'][1]]}
        self.assertEqual([], trial.validate_answer(REQUEST, odd), 'contract checks alone miss the status')
        self.assertTrue(any('status' in e for e in trial.validate_answer(REQUEST, odd, SCHEMA)))
        extra = {'decisions': [dict(GOOD['decisions'][0], confidence=1), GOOD['decisions'][1]]}
        self.assertTrue(any('unexpected field confidence' in e for e in trial.validate_answer(REQUEST, extra, SCHEMA)))
        long = {'decisions': [dict(GOOD['decisions'][0], reason='x' * 401), GOOD['decisions'][1]]}
        self.assertTrue(any('longer than 400' in e for e in trial.validate_answer(REQUEST, long, SCHEMA)))

    def test_unseen_moods_must_be_flagged_for_adjudication(self):
        silent = {'decisions': [GOOD['decisions'][0], decision('u2', 'surprised', ['r1/mood/surprised'])]}
        self.assertTrue(any('not flagged' in e for e in trial.validate_answer(REQUEST, silent)))
        flagged = {'decisions': [GOOD['decisions'][0], decision('u2', 'surprised', ['r1/mood/surprised'], 'needs_adjudication')]}
        self.assertEqual([], trial.validate_answer(REQUEST, flagged))
        outside = {'decisions': [GOOD['decisions'][0], decision('u2', 'neutral', [], 'needs_adjudication')]}
        self.assertEqual([], trial.validate_answer(REQUEST, outside))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        out = self.out = Path(self.temp.name)
        payload = {'system': 's', 'request': REQUEST, 'schema': SCHEMA}
        requests = {arm: [{'file': f'{arm}/batch-01.json', 'sha256': trial.dump(out / 'requests' / arm / 'batch-01.json', payload)}]
                    for arm in trial.ARMS}
        key = trial.dump(out / 'offline-answer-key.json', {'units': {
            'u1': {'mood': 'calm', 'identity': 'narrator', 'batch': 1}, 'u2': {'mood': 'curious', 'identity': 'narrator', 'batch': 1}}})
        self.manifest = {'rulebook': {'version': 'v1'}, 'offline_answer_key_sha256': key, 'requests': requests}
        trial.dump(out / 'manifest.json', self.manifest)
        self.binding = {'rulebook_version': 'v1', 'offline_answer_key_sha256': key}

    def answer(self, arm, answer, **override):
        item = self.manifest['requests'][arm][0]
        trial.dump(self.out / 'answers' / item['file'], {'request_sha256': item['sha256'], 'binding': self.binding,
                                                         'settings': SETTINGS, 'answer': answer, **override})

    def test_omitting_the_hard_target_cannot_score_full_marks(self):
        self.answer('general', {'decisions': GOOD['decisions'][:1]})
        self.answer('specific', GOOD)
        report = trial.score(self.out)
        self.assertEqual(0, report['general']['exact']['hits'])
        self.assertEqual(2, report['general']['units_in_invalid_answers'])
        self.assertEqual(1.0, report['specific']['exact']['rate'])
        self.assertEqual({'only_general_right': 0, 'only_specific_right': 2},
                         {k: report['paired'][k] for k in ('only_general_right', 'only_specific_right')})
        for metric in ('exact', 'expressive_recall', 'flagged_for_adjudication'):
            self.assertIn('ci95', report['specific'][metric])
        self.assertIn('ci95', report['specific']['per_identity']['narrator'])

    def test_unanswered_batches_count_as_failures(self):
        self.answer('specific', GOOD)
        report = trial.score(self.out)
        self.assertEqual(2, report['general']['units_unanswered'])
        self.assertEqual(0.0, report['general']['exact']['rate'])

    def test_answers_and_key_are_bound_to_the_frozen_run(self):
        self.answer('general', GOOD, request_sha256='other')
        self.answer('specific', GOOD)
        with self.assertRaises(SystemExit):
            trial.score(self.out)
        self.answer('general', GOOD)
        (self.out / 'offline-answer-key.json').write_text('{"units": {}}', encoding='utf-8')
        with self.assertRaises(SystemExit):
            trial.score(self.out)

    def test_modified_request_bytes_stop_resume_and_scoring(self):
        self.answer('general', GOOD)
        self.answer('specific', GOOD)
        target = self.out / 'requests/specific/batch-01.json'
        target.write_bytes(target.read_bytes().replace('Он вошёл.'.encode(), 'Он ушёл.'.encode()))
        with self.assertRaises(SystemExit):
            trial.score(self.out)
        with patch.object(trial, 'provider_settings', lambda provider: SETTINGS), self.assertRaises(SystemExit):
            trial.run(self.out, 'codex', ['general'], None)

    def test_resume_requires_the_same_effort_and_cli_version(self):
        self.answer('general', GOOD)
        for changed in (dict(SETTINGS, reasoning_effort='high'), dict(SETTINGS, cli_version='codex 2.0')):
            with patch.object(trial, 'provider_settings', lambda provider, value=changed: value), self.assertRaises(SystemExit):
                trial.run(self.out, 'codex', ['general'], None)
        with patch.object(trial, 'provider_settings', lambda provider: SETTINGS):
            self.assertEqual('kept', trial.run(self.out, 'codex', ['general'], None)['results']['general/batch-01.json'])

    def test_run_repairs_once_and_refuses_unbound_checkpoints(self):
        replies = [{'decisions': GOOD['decisions'][:1]}, GOOD]
        fake = types.SimpleNamespace(structured_call=lambda system, request, schema, effort: replies.pop(0))
        with patch.dict(sys.modules, {'rimsky.codex_analysis': fake}), \
                patch.object(trial, 'provider_settings', lambda provider: SETTINGS):
            result = trial.run(self.out, 'codex', ['general'], None)
            self.assertEqual({'general/batch-01.json': 'valid'}, result['results'])
            saved = json.loads((self.out / 'answers/general/batch-01.json').read_text(encoding='utf-8'))
            self.assertEqual(2, len(saved['attempts']))
            self.assertEqual('kept', trial.run(self.out, 'codex', ['general'], None)['results']['general/batch-01.json'])
            saved['binding'] = {'rulebook_version': 'older'}
            trial.dump(self.out / 'answers/general/batch-01.json', saved)
            with self.assertRaises(SystemExit):
                trial.run(self.out, 'codex', ['general'], None)


class GateVariantTests(unittest.TestCase):
    def gated(self, unit, mood, rules, departure, status='proposed'):
        return {**decision(unit, mood, rules, status), 'departure': departure,
                'departure_evidence': 'e', 'audible_stance': 's'}

    def test_gate_fields_are_required_and_must_agree_with_the_mood(self):
        schema = trial.answer_schema(['u1', 'u2'], ['calm', 'curious', 'neutral', 'surprised'], gate=True)
        good = {'decisions': [self.gated('u1', 'calm', ['r1/mood/calm'], False),
                              self.gated('u2', 'curious', ['r1/mood/curious'], True)]}
        self.assertEqual([], trial.validate_answer(REQUEST, good, schema))
        self.assertTrue(trial.validate_answer(REQUEST, GOOD, schema), 'v1 answers lack the gate fields')
        contradiction = {'decisions': [self.gated('u1', 'calm', ['r1/mood/calm'], True), good['decisions'][1]]}
        self.assertTrue(any('contradicts' in e for e in trial.validate_answer(REQUEST, contradiction, schema)))
        flat = {'decisions': [good['decisions'][0], self.gated('u2', 'curious', ['r1/mood/curious'], False)]}
        self.assertTrue(any('contradicts' in e for e in trial.validate_answer(REQUEST, flat, schema)))

    def test_single_arm_scoring_reports_departures_and_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            schema = trial.answer_schema(['u1', 'u2'], ['calm', 'curious', 'neutral', 'surprised'], gate=True)
            request_hash = trial.dump(out / 'requests/specific/batch-01.json', {'system': 's', 'request': REQUEST, 'schema': schema})
            key = trial.dump(out / 'offline-answer-key.json', {'units': {
                'u1': {'mood': 'calm', 'identity': 'narrator', 'batch': 1}, 'u2': {'mood': 'surprised', 'identity': 'narrator', 'batch': 1}}})
            trial.dump(out / 'manifest.json', {'rulebook': {'version': 'v1'}, 'offline_answer_key_sha256': key,
                                               'requests': {'specific': [{'file': 'specific/batch-01.json', 'sha256': request_hash}]}})
            answer = {'decisions': [self.gated('u1', 'calm', ['r1/mood/calm'], False),
                                    self.gated('u2', 'curious', ['r1/mood/curious'], True)]}
            trial.dump(out / 'answers/specific/batch-01.json', {'request_sha256': request_hash, 'settings': SETTINGS,
                                                                'binding': {'rulebook_version': 'v1', 'offline_answer_key_sha256': key},
                                                                'answer': answer})
            groups = out / 'groups.json'
            groups.write_text(json.dumps({'groups': {'baseline': ['calm', 'neutral'], 'inquiring': ['curious'],
                                                     'shock': ['surprised']}}), encoding='utf-8')
            report = trial.score(out, groups)
            self.assertNotIn('paired', report)
            detection = report['specific']['departure_detection']
            self.assertEqual((1.0, 1.0, 0, 1), (detection['precision']['rate'], detection['recall']['rate'],
                                                detection['added_expression'], detection['right_departure_wrong_mood']))
            self.assertEqual(0.5, report['specific']['group_view']['exact_group']['rate'])


if __name__ == '__main__':
    unittest.main()
