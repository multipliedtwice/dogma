"""Exploratory full joint audit for the final three frozen-gold chapters.

This is intentionally reported as post-test evidence: the untouched partition was
already scored before this contiguous, position-based scope was chosen.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_progressive_speaker_passes as base


OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-v4-exploratory'
POLICY = {
    'schema': 1,
    'name': 'late-chapter-joint-audit-v4-exploratory',
    'scope': 'every modeled span in contiguous chapters c0038, c0039 and c0040',
    'chosen_by': 'book position, not prediction correctness',
    'status': 'post-test exploratory; requires validation on a new book',
    'passes': ['existing Codex primary', 'existing Claude independent',
               'fresh Claude joint chapter', 'fresh Codex joint chapter'],
}
SYSTEM = (
    base.SPEAKER_SYSTEM + ' Resolve these targets as turns from one complete '
    'chapter. The character registry and chapter passages are supplied once. '
    'Track recurring speakers and dialogue order across the chapter. No previous '
    'speaker proposal is supplied. Do not invent a different new person for each '
    'unattributed line, and do not identify a person whom the text leaves unknown. '
    'Return one answer for every target key.'
)


def compact_request(request: dict) -> dict:
    return {key: value for key, value in request.items()
            if key != 'characters'}


def chapter_passages(rows: list[dict], requests: dict[str, dict]) -> list[dict]:
    passages = {}
    for row in rows:
        request = requests[row['request_key']]
        passages[request['target_id']] = request['full_target']
        for item in request.get('context_before', []) + request.get('context_after', []):
            passages[item['id']] = item['text']
    return [{'id': key, 'text': value} for key, value in passages.items()]


def run_provider(path: Path, provider: str, chapter_id: str, rows: list[dict],
                 requests: dict[str, dict], cast: list[dict]) -> dict[str, dict]:
    saved = {'schema': 1, 'policy_sha256': base.stable_hash(POLICY),
             'provider': provider, 'chapter_id': chapter_id, 'answers': {}}
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('policy_sha256') != base.stable_hash(POLICY):
            raise ValueError('Audit cache uses another policy.')
    pending = [row for row in rows
               if row['key'] not in saved['answers'] or
               saved['answers'][row['key']].get('provider_failure')]
    shared_passages = chapter_passages(rows, requests)
    call = base.claude_call if provider == 'claude_cli' else base.codex_call

    def one(batch: list[dict], repair_error: str = '') -> None:
        keys = [row['key'] for row in batch]
        target_rows = [{
            'key': row['key'],
            'case': compact_request(requests[row['request_key']]),
        } for row in batch]
        request = {'chapter_id': chapter_id, 'character_registry': cast,
                   'chapter_passages': shared_passages, 'targets': target_rows}
        system = SYSTEM
        if repair_error:
            system += (' The previous answer failed validation: ' +
                       repair_error[:500] + ' Copy a short exact contiguous quote '
                       'and its exact supplied passage ID.')
        try:
            raw = call(system, request,
                       base.answer_schema(keys, [item['id'] for item in cast]))
            by_key = {item.get('key'): item for item in raw.get('answers', [])
                      if isinstance(item, dict)}
            if len(by_key) != len(keys) or set(by_key) != set(keys):
                raise ValueError('Provider omitted or duplicated target keys.')
            for row in batch:
                answer = dict(by_key[row['key']])
                answer.pop('key')
                evidence = {item['id']: item['text'] for item in shared_passages}
                evidence_valid = True
                try:
                    base.checked_speaker(
                        answer, cast, evidence,
                        requests[row['request_key']].get('possible_addressee', ''))
                except base.AppError as error:
                    # This exploratory score separates identity quality from the
                    # production evidence gate. Other validation failures still
                    # reject the answer and trigger repair.
                    if not str(error).startswith(
                            'Speaker identity needs a short contiguous quote'):
                        raise
                    evidence_valid = False
                answer['evidence_valid'] = evidence_valid
                saved['answers'][row['key']] = answer
            base.write_json(path, saved)
            print(json.dumps({'provider': provider, 'chapter': chapter_id,
                              'saved': len(saved['answers']),
                              'total': len(rows)}), flush=True)
        except (base.AppError, ValueError, KeyError, TypeError) as error:
            if len(batch) > 1:
                middle = len(batch) // 2
                one(batch[:middle])
                one(batch[middle:])
            elif not repair_error:
                one(batch, str(error))
            else:
                saved['answers'][batch[0]['key']] = {
                    'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': '',
                    'provider_failure': str(error)[:500]}
                base.write_json(path, saved)

    for offset in range(0, len(pending), 14):
        one(pending[offset:offset + 14])
    return saved['answers']


def score(rows: list[dict], answers: dict[str, str]) -> dict:
    correct = sum(answers[row['key']] == row['gold'] for row in rows)
    return {'total': len(rows), 'correct': correct,
            'accuracy': round(correct / len(rows), 6)}


def changes(rows: list[dict], before: dict[str, str],
            after: dict[str, str]) -> dict:
    changed = [row for row in rows if before[row['key']] != after[row['key']]]
    effects = Counter()
    for row in changed:
        old = before[row['key']] == row['gold']
        new = after[row['key']] == row['gold']
        effects['fixed_error' if new and not old else
                'introduced_error' if old and not new else
                'changed_correct_to_correct' if old else
                'changed_wrong_to_wrong'] += 1
    return {'changed': len(changed), 'effects': dict(effects)}


def main() -> None:
    base.POLICY = POLICY
    gold, all_rows, requests = base.load_inputs('test')
    rows = [row for row in all_rows if row.get('request_key') and
            row['chapter_id'] in {'c0038', 'c0039', 'c0040'}]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    policy_path = OUTPUT / 'policy.json'
    if policy_path.is_file():
        if base.stable_hash(json.loads(policy_path.read_text(encoding='utf-8'))) != base.stable_hash(POLICY):
            raise ValueError('Frozen exploratory policy differs from this script.')
    else:
        base.write_json(policy_path, POLICY)
    claude, codex = {}, {}
    for chapter_id in ('c0038', 'c0039', 'c0040'):
        chapter_rows = [row for row in rows if row['chapter_id'] == chapter_id]
        claude.update(run_provider(
            OUTPUT / f'claude-{chapter_id}.json', 'claude_cli', chapter_id,
            chapter_rows, requests, gold['cast']))
        codex.update(run_provider(
            OUTPUT / f'codex-{chapter_id}.json', 'codex_cli', chapter_id,
            chapter_rows, requests, gold['cast']))
    primary = {row['key']: row['codex'] for row in rows}
    independent = {row['key']: row['claude'] for row in rows}
    joint_claude = {key: answer['speaker'] for key, answer in claude.items()}
    joint_codex = {key: answer['speaker'] for key, answer in codex.items()}
    # Report the later pair directly and a conservative gate. The gate accepts a
    # changed identity only when both joint providers agree; conflicting pairs hold.
    accepted, held = {}, []
    for row in rows:
        key = row['key']
        if joint_claude[key] == joint_codex[key]:
            accepted[key] = joint_claude[key]
        elif primary[key] == independent[key]:
            accepted[key] = primary[key]
        else:
            held.append(key)
    decided = [row for row in rows if row['key'] in accepted]
    report = {
        'schema': 1, 'policy': POLICY,
        'policy_sha256': base.stable_hash(POLICY),
        'gold_content_sha256': gold['content_sha256'],
        'chapters': ['c0038', 'c0039', 'c0040'],
        'spans': len(rows),
        'scores': {
            'primary_codex': score(rows, primary),
            'initial_claude': score(rows, independent),
            'joint_claude': score(rows, joint_claude),
            'joint_codex': score(rows, joint_codex),
            'joint_pair_agreement': {
                'spans': sum(joint_claude[row['key']] == joint_codex[row['key']]
                             for row in rows),
                'precision': score(
                    [row for row in rows if joint_claude[row['key']] == joint_codex[row['key']]],
                    joint_codex),
            },
            'conservative_gate': {
                'accepted': len(decided), 'held': len(held),
                'coverage': round(len(decided) / len(rows), 6),
                'accepted_precision': score(decided, accepted)['accuracy'],
            },
        },
        'changes_from_primary': {
            'joint_claude': changes(rows, primary, joint_claude),
            'joint_codex': changes(rows, primary, joint_codex),
            'conservative_gate': changes(decided, primary, accepted),
        },
        'evidence_validation': {
            'claude_invalid': sum(not answer.get('evidence_valid', True)
                                  for answer in claude.values()),
            'codex_invalid': sum(not answer.get('evidence_valid', True)
                                 for answer in codex.values()),
        },
        'rows': [{
            'key': row['key'], 'gold': row['gold'], 'primary': primary[row['key']],
            'initial_claude': independent[row['key']],
            'joint_claude': joint_claude[row['key']],
            'joint_codex': joint_codex[row['key']],
            'accepted': accepted.get(row['key']),
        } for row in rows],
    }
    base.write_json(OUTPUT / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items()
                      if key != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
