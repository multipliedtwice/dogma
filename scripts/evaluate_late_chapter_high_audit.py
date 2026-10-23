"""Exploratory high-effort sixth pass over late-chapter disagreements.

The target set is selected without gold: any span where the four existing
speaker proposals in the v4 late-chapter audit are not identical. Results are
diagnostic only and must not modify corpus labels or production policy.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_progressive_speaker_passes as base
import evaluate_late_chapter_joint_audit as late


SOURCE = ROOT / 'training-work/speaker-resolution-progressive-v4-exploratory/report.json'
OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-v5-high-exploratory'
POLICY = {
    'schema': 1,
    'name': 'late-chapter-focused-high-audit-v5-exploratory',
    'source': str(SOURCE.relative_to(ROOT)),
    'selection': 'all late-chapter spans where any of the four existing proposals differ',
    'selection_uses_gold': False,
    'provider': 'codex_cli',
    'reasoning_effort': 'high',
    'status': 'post-test exploratory; never writes labels',
}
SYSTEM = (
    base.SPEAKER_SYSTEM + ' This is a final focused audit of difficult turns from '
    'one complete chapter. Re-read the supplied chapter text and track dialogue '
    'order and recurring speakers. Earlier proposals are fallible navigation hints, '
    'not evidence. Choose unknown when the supplied book text does not identify the '
    'speaker. Return one answer for every target key.'
)


def score(rows: list[dict], answers: dict[str, str]) -> dict:
    correct = sum(answers[row['key']] == row['gold'] for row in rows)
    return {'total': len(rows), 'correct': correct,
            'accuracy': round(correct / len(rows), 6) if rows else None}


def changes(rows: list[dict], before: dict[str, str], after: dict[str, str]) -> dict:
    changed = [row for row in rows if before[row['key']] != after[row['key']]]
    effects = Counter()
    for row in changed:
        was = before[row['key']] == row['gold']
        now = after[row['key']] == row['gold']
        effects['fixed_error' if now and not was else
                'introduced_error' if was and not now else
                'changed_correct_to_correct' if was else
                'changed_wrong_to_wrong'] += 1
    return {'changed': len(changed), 'effects': dict(effects)}


def run_chapter(path: Path, chapter_id: str, rows: list[dict], requests: dict[str, dict],
                cast: list[dict]) -> dict[str, dict]:
    saved = {'schema': 1, 'policy_sha256': base.stable_hash(POLICY),
             'chapter_id': chapter_id, 'answers': {}}
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('policy_sha256') != base.stable_hash(POLICY):
            raise ValueError('High-audit cache uses another frozen policy.')
    pending = [row for row in rows if row['key'] not in saved['answers']]
    if not pending:
        return saved['answers']
    passages = late.chapter_passages(rows, requests)
    keys = [row['key'] for row in pending]
    request = {
        'chapter_id': chapter_id,
        'character_registry': cast,
        'chapter_passages': passages,
        'targets': [{
            'key': row['key'],
            'case': late.compact_request(requests[row['request_key']]),
            'earlier_proposals': {
                'primary_codex': row['primary'],
                'independent_claude': row['initial_claude'],
                'joint_claude': row['joint_claude'],
                'joint_codex': row['joint_codex'],
            },
        } for row in pending],
    }
    raw = base.codex_call(
        SYSTEM, request, base.answer_schema(keys, [item['id'] for item in cast]),
        reasoning_effort='high')
    by_key = {item.get('key'): item for item in raw.get('answers', [])
              if isinstance(item, dict)}
    if set(by_key) != set(keys):
        raise ValueError('High audit omitted or duplicated target keys.')
    evidence = {item['id']: item['text'] for item in passages}
    for row in pending:
        answer = dict(by_key[row['key']])
        answer.pop('key')
        evidence_valid = True
        try:
            base.checked_speaker(answer, cast, evidence,
                                 requests[row['request_key']].get('possible_addressee', ''))
        except base.AppError as error:
            if not str(error).startswith('Speaker identity needs a short contiguous quote'):
                raise
            evidence_valid = False
        answer['evidence_valid'] = evidence_valid
        saved['answers'][row['key']] = answer
    base.write_json(path, saved)
    print(json.dumps({'chapter': chapter_id, 'saved': len(saved['answers'])}), flush=True)
    return saved['answers']


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    gold, all_rows, requests = base.load_inputs('test')
    source_by_key = {row['key']: row for row in source['rows']}
    selected_keys = {
        row['key'] for row in source['rows']
        if len({row['primary'], row['initial_claude'], row['joint_claude'],
                row['joint_codex']}) > 1
    }
    rows = []
    for row in all_rows:
        if row['key'] not in selected_keys:
            continue
        merged = dict(row)
        merged.update(source_by_key[row['key']])
        rows.append(merged)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    policy_path = OUTPUT / 'policy.json'
    if policy_path.is_file():
        if json.loads(policy_path.read_text(encoding='utf-8')) != POLICY:
            raise ValueError('Frozen high-audit policy differs from this script.')
    else:
        base.write_json(policy_path, POLICY)
    raw = {}
    for chapter_id in sorted({row['chapter_id'] for row in rows}):
        chapter_rows = [row for row in rows if row['chapter_id'] == chapter_id]
        raw.update(run_chapter(OUTPUT / f'codex-high-{chapter_id}.json', chapter_id,
                               chapter_rows, requests, gold['cast']))
    high = {key: answer['speaker'] for key, answer in raw.items()}
    primary = {row['key']: row['primary'] for row in rows}
    joint = {row['key']: row['joint_codex'] for row in rows}
    report = {
        'schema': 1, 'policy': POLICY,
        'policy_sha256': base.stable_hash(POLICY),
        'gold_content_sha256': gold['content_sha256'],
        'selected_spans': len(rows),
        'scores_on_selected_disagreements': {
            'primary_codex': score(rows, primary),
            'joint_codex': score(rows, joint),
            'codex_high_focused': score(rows, high),
        },
        'changes': {
            'high_from_primary': changes(rows, primary, high),
            'high_from_joint_codex': changes(rows, joint, high),
        },
        'evidence_invalid': sum(not answer.get('evidence_valid', True)
                                for answer in raw.values()),
        'rows': [{
            'key': row['key'], 'chapter_id': row['chapter_id'], 'gold': row['gold'],
            'primary': row['primary'], 'initial_claude': row['initial_claude'],
            'joint_claude': row['joint_claude'], 'joint_codex': row['joint_codex'],
            'codex_high': high[row['key']],
            'evidence_valid': raw[row['key']].get('evidence_valid', True),
        } for row in rows],
    }
    base.write_json(OUTPUT / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
