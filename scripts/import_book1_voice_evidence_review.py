"""Validate an exported Book 1 source-audio review without accepting training clips."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / 'training-work/character-rb1/voice-evidence-atlas'
VERDICTS = {'distinct', 'no_distinct_change_heard', 'uncertain'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_review(review_file: Path, output: Path) -> dict:
    manifest_path = ATLAS / 'manifest.json'
    manifest_hash = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    submitted = json.loads(review_file.read_text(encoding='utf-8'))
    if (submitted.get('schema') != 1 or submitted.get('book') != manifest['book'] or
            submitted.get('kind') != 'voice_evidence_review' or
            submitted.get('manifest_sha256') != manifest_hash):
        raise ValueError('The review does not match this exact atlas manifest.')
    declared_reviewer = submitted.get('reviewer', '').strip()
    reviewer = declared_reviewer or 'anonymous'
    decisions = submitted.get('decisions')
    if not isinstance(decisions, dict):
        raise ValueError('The review needs a decisions object.')
    indexed = {case['id']: case for case in manifest['cases']}
    if set(decisions) - set(indexed):
        raise ValueError('The review contains unknown case IDs.')
    checked = []
    for case_id, choice in decisions.items():
        if not isinstance(choice, dict):
            raise ValueError(f'Invalid decision for {case_id}.')
        verdict = choice.get('verdict', '')
        note = choice.get('note', '').strip()
        if not verdict:
            continue
        if verdict not in VERDICTS or len(note) > 2000:
            raise ValueError(f'Invalid verdict or note for {case_id}.')
        if verdict == 'distinct' and len(note) < 10:
            raise ValueError(f'A distinct performance needs an audible-feature note: {case_id}.')
        case = indexed[case_id]
        source = (ATLAS / case['source_audio']).resolve()
        if not source.is_relative_to(ATLAS.resolve()) or sha256(source) != case['source_audio_sha256']:
            raise ValueError(f'The source excerpt changed: {case_id}.')
        checked.append({'case_id': case_id, 'role_id': case['role_id'],
                        'family': case['family'], 'source_id': case['source_id'],
                        'recording_id': case['recording_id'],
                        'audio_start': case['audio_start'], 'audio_end': case['audio_end'],
                        'source_audio_sha256': case['source_audio_sha256'],
                        'verdict': verdict, 'observed_features': note,
                        'reviewer': reviewer, 'status': 'audio_review_recorded'})
    by_role = defaultdict(list)
    for row in checked:
        if row['family'] == 'character_role':
            by_role[row['role_id']].append(row)
    repeated = [role for role, rows in by_role.items()
                if len({row['source_id'] for row in rows if row['verdict'] == 'distinct'}) >= 2 and
                len({row['recording_id'] for row in rows if row['verdict'] == 'distinct'}) >= 2]
    result = {'schema': 1, 'book': manifest['book'], 'kind': 'voice_evidence_review_import',
              'manifest_sha256': manifest_hash, 'submitted_review_sha256': sha256(review_file),
              'reviewer': reviewer, 'reviewer_declared': bool(declared_reviewer), 'reviewed_cases': checked,
              'summary': {'reviewed': len(checked),
                          'distinct': sum(row['verdict'] == 'distinct' for row in checked),
                          'no_distinct_change_heard': sum(row['verdict'] == 'no_distinct_change_heard' for row in checked),
                          'uncertain': sum(row['verdict'] == 'uncertain' for row in checked),
                          'repeated_distinction_candidates': sorted(repeated),
                          'training_accepted': 0},
              'limitations': ('A reviewer verdict is feature evidence for this exact audio excerpt. '
                              'It does not by itself approve a stable character voice or a training clip; '
                              'transcript, boundaries, speaker, and clean-speech gates remain separate.')}
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_review(args.review, args.output)['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
