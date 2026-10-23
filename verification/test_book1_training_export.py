"""A synthetic review tests the export contract; it never accepts real clips."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]
from rimsky_training.corpus import load_corpus  # noqa: E402
from rimsky_training.dataset import checked_rows  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
from finalize_book1_training_review import precision_clip, bucket_review_matches  # noqa: E402
from review_label_resolution import resolved_review_mood, resolved_review_speaker  # noqa: E402


class Book1TrainingExportTest(unittest.TestCase):
    def test_speaker_correction_is_independent_from_voice_family(self):
        self.assertEqual(resolved_review_speaker('Радугин', 'no', 'Иришка'), 'Иришка')
        self.assertEqual(resolved_review_speaker('Радугин', None, 'Иришка'), 'Иришка')
        self.assertEqual(resolved_review_speaker('Радугин', 'yes', None), 'Радугин')

    def test_pending_or_passively_heard_mood_is_not_a_training_label(self):
        self.assertIsNone(resolved_review_mood('mood_pending', None, None))
        self.assertIsNone(resolved_review_mood('mood_pending', 'yes', None))
        self.assertIsNone(resolved_review_mood('neutral', None, None))
        self.assertEqual(resolved_review_mood('neutral', 'yes', None), 'neutral')
        self.assertEqual(resolved_review_mood('neutral', 'no', 'uncertain'), 'hesitant')
    def test_group_verification_requires_current_labels_and_audio(self):
        decision = {'bucket_review': {'verdict': 'consistent', 'voice_family_id': 'masculine_2',
                                     'mood': 'warning', 'audio_sha256': 'test-audio'}}
        self.assertTrue(bucket_review_matches(decision, 'masculine_2', 'warning', 'test-audio'))
        self.assertFalse(bucket_review_matches(decision, 'masculine_1', 'warning', 'test-audio'))
        self.assertFalse(bucket_review_matches(decision, 'masculine_2', 'neutral', 'test-audio'))
        self.assertFalse(bucket_review_matches(decision, 'masculine_2', 'warning', 'new-cut'))
    def test_precision_rejects_stale_or_different_audio(self):
        folder = ROOT / 'training-work/character-rb1/fine-tune-all'
        candidate = next(row for row in (json.loads(line) for line in
                         (folder / 'candidates.jsonl').read_text(encoding='utf-8').splitlines())
                         if row['id'] == 'sentence_3bb90c3de2ba65e91f2b')
        repair = json.loads((folder / 'precision-v2/repairs.json').read_text(encoding='utf-8'))['clips'][candidate['id']]
        audit = next(row for row in (json.loads(line) for line in
                     (folder / 'precision-v2/clip-asr-audit.jsonl').read_text(encoding='utf-8').splitlines())
                     if row['id'] == candidate['id'])
        self.assertIsNotNone(precision_clip(candidate, repair, audit))
        changed = deepcopy(audit)
        changed['audio_sha256'] = '0' * 64
        self.assertIsNone(precision_clip(candidate, repair, changed))
        changed = deepcopy(audit)
        changed['heard'] = 'wrong words'
        self.assertIsNone(precision_clip(candidate, repair, changed))

    def test_synthetic_review_reaches_dataset_gate(self):
        self.check_precision_folder()

    def test_legacy_exports_are_closed(self):
        for args in ([], ['--expanded']):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/finalize_book1_training_review.py'),
                                     *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('older candidate exports are retired', result.stderr)

    def check_precision_folder(self):
        folder = ROOT / 'training-work/character-rb1/fine-tune-all'
        complete = (folder / 'precision-v2/status.json').is_file()
        summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
        cases = ('sentence_3bb90c3de2ba65e91f2b',  # re-cut and exact independent ASR
                 'expanded_e02a69eb4391e0c2afd6',  # old screen held it; new clip passes
                 'sentence_b591acb3c591fbb6d702',  # exact ASR, but human flagged edge
                 'sentence_850ba0a9a43f77d1ea59')  # no safe quiet boundary
        decisions = {cid: {'voice_verdict': 'yes', 'corrected_mood': 'neutral'} for cid in cases}
        decisions[cases[2]]['boundary_end'] = True
        decisions[cases[2]]['group_comment'] = 'Synthetic boundary report'
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            review = temp / 'review.json'
            review.write_text(json.dumps({'schema': 1, 'candidates_sha256': summary['candidates_sha256'],
                                          'reviewer': 'synthetic-test-only', 'decisions': decisions}), encoding='utf-8')
            output = temp / 'export'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/finalize_book1_training_review.py'),
                                     '--all', '--review', str(review), '--output', str(output)],
                                    check=True, capture_output=True, text=True)
            report = json.loads(result.stdout)
            rows = [json.loads(line) for line in (output / 'reviewed.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['id'] for row in rows], list(cases[:2]) if complete else [])
            self.assertEqual(report['boundary_holds'], 1)
            self.assertEqual(report['accepted'], 2 if complete else 0)
            self.assertEqual(report['precision_complete'], complete)
            corpus_root, corpus = load_corpus(ROOT / 'training-work/corpus-rb1-current')
            self.assertEqual(len(checked_rows(corpus_root, corpus, rows)), 2 if complete else 0)
            if complete:
                decisions[cases[0]]['bucket_review'] = {
                    'verdict': 'consistent', 'voice_family_id': rows[0]['voice_family_id'],
                    'mood': rows[0]['mood'], 'audio_sha256': rows[0]['candidate_audio_sha256']}
                review.write_text(json.dumps({'schema': 1, 'candidates_sha256': summary['candidates_sha256'],
                                              'reviewer': 'synthetic-test-only', 'decisions': decisions}), encoding='utf-8')
                verified_output = temp / 'verified-export'
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/finalize_book1_training_review.py'),
                                         '--all', '--require-bucket-review', '--review', str(review),
                                         '--output', str(verified_output)], check=True, capture_output=True, text=True)
                verified_rows = [json.loads(line) for line in (verified_output / 'reviewed.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual([row['id'] for row in verified_rows], [cases[0]])
                self.assertTrue(json.loads(result.stdout)['bucket_review_required'])


if __name__ == '__main__':
    unittest.main()
