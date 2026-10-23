"""Evaluate a progressively deeper chapter speaker-analysis policy.

The frozen selection partition is used to decide whether the policy deserves one
run on the untouched test partition. Gold labels are used only for scoring after
provider answers have been saved; they are never included in a provider request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.claude_analysis import structured_call as claude_call
from rimsky.codex_analysis import structured_call as codex_call
from rimsky.staged_analysis import SPEAKER_SYSTEM, checked_speaker
from rimsky.util import AppError


BASE = ROOT / 'training-work/speaker-resolution-eval-qwen35-v1'
GOLD = ROOT / 'manual-labeling/speaker-resolution-gold-v1/gold.json'
PROTOCOL = ROOT / 'manual-labeling/speaker-resolution-gold-v1/evaluation-protocol.json'
OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-v1'

POLICY = {
    'schema': 1,
    'name': 'progressive-chapter-speaker-v1',
    'passes': [
        {'pass': 1, 'name': 'shared_structure',
         'provider': 'shared deterministic/model structure component'},
        {'pass': 2, 'name': 'primary', 'provider': 'codex_cli'},
        {'pass': 3, 'name': 'independent_check', 'provider': 'claude_cli'},
        {'pass': 4, 'name': 'disagreement_adjudication', 'provider': 'codex_cli',
         'scope': 'only pass-2/pass-3 disagreements'},
        {'pass': 5, 'name': 'chapter_continuity_audit', 'provider': 'claude_cli',
         'scope': 'modeled spans, grouped by chapter'},
        {'pass': 6, 'name': 'audit_adjudication', 'provider': 'codex_cli',
         'scope': 'only pass-5 proposed changes'},
    ],
    'rules': [
        'Provider prompts never receive gold answers.',
        'Strict deterministic attributions are not reopened by later passes.',
        'A later answer does not silently win: every disagreement is adjudicated.',
        'Unknown is a valid answer when the supplied text does not identify a speaker.',
    ],
}

ADJUDICATOR_SYSTEM = (
    SPEAKER_SYSTEM + ' You are adjudicating two prior proposals. They are fallible '
    'and are not evidence. Re-read the supplied book context and select the speaker '
    'supported by that context. Prefer unknown over an invented identity. Return one '
    'answer for every supplied key.'
)

CONTINUITY_SYSTEM = (
    SPEAKER_SYSTEM + ' Audit a group of passages from one chapter. Use the supplied '
    'chapter proposal list only to notice continuity patterns; those proposals are '
    'fallible and are not evidence. Resolve each target from its own supplied text '
    'and context. Do not force all nearby turns to one recurring character. Return '
    'one answer for every supplied key.'
)

FINAL_SYSTEM = (
    SPEAKER_SYSTEM + ' Adjudicate the current decision against a later chapter-audit '
    'proposal. Neither proposal is evidence. Re-read the supplied book text and '
    'context, preserve a correct current decision, and change it only when the text '
    'supports the audit proposal. Return one answer for every supplied key.'
)


def stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    os.replace(temporary, path)


def answer_schema(keys: list[str], cast_ids: list[str]) -> dict:
    return {'type': 'object', 'additionalProperties': False,
            'required': ['answers'], 'properties': {'answers': {
                'type': 'array', 'minItems': len(keys), 'maxItems': len(keys),
                'items': {'type': 'object', 'additionalProperties': False,
                    'required': ['key', 'speaker', 'name', 'evidence_id',
                                 'evidence_quote'],
                    'properties': {
                        'key': {'type': 'string', 'enum': keys},
                        'speaker': {'type': 'string',
                                    'enum': cast_ids + ['new', 'unknown']},
                        'name': {'type': 'string', 'maxLength': 120},
                        'evidence_id': {'type': 'string'},
                        'evidence_quote': {'type': 'string', 'maxLength': 200},
                    }}}}}


def evidence(request: dict) -> dict[str, str]:
    rows = request.get('context_before', []) + request.get('context_after', [])
    result = {row['id']: row['text'] for row in rows}
    result[request['target_id']] = request['full_target']
    return result


def validation_evidence(item: dict) -> dict[str, str]:
    """All book text supplied for one joint decision, including peer passages."""
    result = evidence(item['request'])
    for passage in item.get('chapter_passages', []):
        if isinstance(passage, dict) and isinstance(passage.get('key'), str):
            if isinstance(passage.get('text'), str):
                result[passage['key']] = passage['text']
            for neighbor in (passage.get('context_before', []) +
                             passage.get('context_after', [])):
                if (isinstance(neighbor, dict) and
                        isinstance(neighbor.get('id'), str) and
                        isinstance(neighbor.get('text'), str)):
                    result[neighbor['id']] = neighbor['text']
    return result


def load_inputs(partition: str) -> tuple[dict, list[dict], dict[str, dict]]:
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    report = json.loads((BASE / 'report.json').read_text(encoding='utf-8'))
    local = json.loads((BASE / 'local-results.json').read_text(encoding='utf-8'))
    if protocol['gold_content_sha256'] != gold['content_sha256']:
        raise ValueError('Protocol and gold set differ.')
    ids = set(protocol['selection_case_ids' if partition == 'selection'
                       else 'test_case_ids'])
    rows = [dict(row) for row in report['rows'] if row['case_id'] in ids]
    requests = {}
    for case in local['cases']:
        if case['case_id'] not in ids:
            continue
        for item in case['speaker_requests']:
            requests[item['key']] = item['request']
    return gold, rows, requests


def load_provider_answers(name: str) -> dict[str, dict]:
    return json.loads((BASE / f'{name}-results.json').read_text(
        encoding='utf-8'))['answers']


def checked_batch(raw: dict, batch: list[dict], cast: list[dict]) -> dict[str, dict]:
    keys = [item['key'] for item in batch]
    answers = raw.get('answers', [])
    by_key = {answer.get('key'): answer for answer in answers
              if isinstance(answer, dict)}
    if len(answers) != len(keys) or set(by_key) != set(keys):
        raise ValueError('Provider omitted or duplicated a case key.')
    result = {}
    for item in batch:
        answer = dict(by_key[item['key']])
        answer.pop('key')
        checked_speaker(answer, cast, validation_evidence(item),
                        item['request'].get('possible_addressee', ''))
        result[item['key']] = answer
    return result


def run_stage(path: Path, provider: str, system: str, rows: list[dict],
              cast: list[dict], *, batch_size: int = 12) -> dict[str, dict]:
    policy_hash = stable_hash(POLICY)
    saved = {'schema': 1, 'policy_sha256': policy_hash,
             'provider': provider, 'answers': {}}
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('policy_sha256') != policy_hash:
            raise ValueError(f'{path.name} uses another policy.')
    pending = [row for row in rows
               if row['key'] not in saved['answers'] or
               saved['answers'][row['key']].get('provider_failure')]
    call = claude_call if provider == 'claude_cli' else codex_call

    def one(batch: list[dict]) -> None:
        keys = [item['key'] for item in batch]
        request = {'cases': [{key: item[key] for key in item
                              if key != 'validation'} for item in batch]}
        try:
            raw = call(system, request, answer_schema(keys,
                       [item['id'] for item in cast]))
            saved['answers'].update(checked_batch(raw, batch, cast))
            write_json(path, saved)
            print(json.dumps({'stage': path.stem, 'saved': len(saved['answers']),
                              'total': len(rows)}), flush=True)
        except (AppError, ValueError, KeyError, TypeError) as error:
            if len(batch) > 1:
                middle = len(batch) // 2
                one(batch[:middle])
                one(batch[middle:])
                return
            try:
                repair_system = (system + ' The previous response failed validation: ' +
                                 str(error)[:500] + ' Return the same decision again '
                                 'with a short exact contiguous evidence quote copied '
                                 'from one supplied passage and its exact passage ID.')
                raw = call(repair_system, request, answer_schema(keys,
                           [item['id'] for item in cast]))
                saved['answers'].update(checked_batch(raw, batch, cast))
            except (AppError, ValueError, KeyError, TypeError) as repair_error:
                saved['answers'][batch[0]['key']] = {
                    'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': '',
                    'provider_failure': str(repair_error)[:500]}
            write_json(path, saved)

    for offset in range(0, len(pending), batch_size):
        one(pending[offset:offset + batch_size])
    return saved['answers']


def effects(rows: list[dict], before: dict[str, str],
            after: dict[str, str]) -> dict:
    changed = [row for row in rows if before[row['key']] != after[row['key']]]
    effect = Counter()
    for row in changed:
        was = before[row['key']] == row['gold']
        now = after[row['key']] == row['gold']
        effect['fixed_error' if now and not was else
               'introduced_error' if was and not now else
               'changed_correct_to_correct' if was else
               'changed_wrong_to_wrong'] += 1
    correct = sum(after[row['key']] == row['gold'] for row in rows)
    return {'total': len(rows), 'correct': correct,
            'accuracy': round(correct / len(rows), 6) if rows else None,
            'changed': len(changed), 'change_effect': dict(effect)}


def evaluate(partition: str) -> dict:
    gold, rows, requests = load_inputs(partition)
    codex = load_provider_answers('codex')
    claude = load_provider_answers('claude')
    modeled = [row for row in rows if row.get('request_key')]
    deterministic = [row for row in rows if not row.get('request_key')]
    stage2 = {row['key']: row['codex'] for row in rows}
    stage3 = {row['key']: row['claude'] for row in rows}

    disagreements = []
    for row in modeled:
        if stage2[row['key']] == stage3[row['key']]:
            continue
        disagreements.append({
            'key': row['key'], 'request': requests[row['request_key']],
            'primary_proposal': codex[row['request_key']],
            'independent_proposal': claude[row['request_key']],
        })
    stage4_answers = run_stage(
        OUTPUT / f'{partition}-pass4.json', 'codex_cli', ADJUDICATOR_SYSTEM,
        disagreements, gold['cast'])
    stage4 = dict(stage2)
    for item in disagreements:
        stage4[item['key']] = stage4_answers[item['key']]['speaker']

    # The continuity auditor sees a compact view of all current decisions in the
    # same chapter, plus full evidence only for the targets it must answer.
    by_chapter = defaultdict(list)
    for row in modeled:
        by_chapter[row['chapter_id']].append(row)
    audit_rows = []
    for chapter_id, chapter_rows in sorted(by_chapter.items()):
        overview = [{'key': row['key'], 'text': row['text'],
                     'current_speaker': stage4[row['key']]}
                    for row in chapter_rows]
        for row in chapter_rows:
            audit_rows.append({
                'key': row['key'], 'chapter_id': chapter_id,
                'request': requests[row['request_key']],
                'current_proposal': stage4[row['key']],
                'chapter_proposals': overview,
            })
    # Keep chapter groups together where possible without producing unbounded calls.
    stage5_answers = {}
    for chapter_id, chapter_rows in sorted(by_chapter.items()):
        batch = [row for row in audit_rows if row['chapter_id'] == chapter_id]
        answers = run_stage(
            OUTPUT / f'{partition}-pass5-{chapter_id}.json', 'claude_cli',
            CONTINUITY_SYSTEM, batch, gold['cast'], batch_size=18)
        stage5_answers.update(answers)
    stage5 = dict(stage4)
    for row in modeled:
        stage5[row['key']] = stage5_answers[row['key']]['speaker']

    proposed_changes = []
    for row in modeled:
        if stage5[row['key']] == stage4[row['key']]:
            continue
        proposed_changes.append({
            'key': row['key'], 'request': requests[row['request_key']],
            'current_proposal': stage4[row['key']],
            'chapter_audit_proposal': stage5_answers[row['key']],
        })
    stage6_answers = run_stage(
        OUTPUT / f'{partition}-pass6.json', 'codex_cli', FINAL_SYSTEM,
        proposed_changes, gold['cast'])
    stage6 = dict(stage4)
    for item in proposed_changes:
        stage6[item['key']] = stage6_answers[item['key']]['speaker']

    agreement = [row for row in modeled
                 if stage2[row['key']] == stage3[row['key']]]
    result = {
        'schema': 1, 'partition': partition,
        'policy': POLICY, 'policy_sha256': stable_hash(POLICY),
        'gold_content_sha256': gold['content_sha256'],
        'counts': {'spans': len(rows), 'modeled': len(modeled),
                   'deterministic': len(deterministic),
                   'pass3_disagreements': len(disagreements),
                   'pass5_proposed_changes': len(proposed_changes)},
        'pass2_primary': effects(rows, stage2, stage2),
        'pass3_agreement_gate': {
            'accepted': len(agreement), 'held': len(modeled) - len(agreement),
            'coverage': round(len(agreement) / len(modeled), 6) if modeled else None,
            'accepted_precision': round(sum(stage2[row['key']] == row['gold']
                                            for row in agreement) / len(agreement), 6)
                                  if agreement else None,
        },
        'pass4_adjudicated': effects(rows, stage2, stage4),
        'pass5_raw_continuity_audit': effects(rows, stage4, stage5),
        'pass6_final_adjudicated': effects(rows, stage4, stage6),
        'rows': [{
            'key': row['key'], 'case_id': row['case_id'],
            'chapter_id': row['chapter_id'], 'gold': row['gold'],
            'pass2': stage2[row['key']], 'pass3': stage3[row['key']],
            'pass4': stage4[row['key']], 'pass5': stage5[row['key']],
            'pass6': stage6[row['key']],
        } for row in rows],
    }
    write_json(OUTPUT / f'{partition}-report.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('partition', choices=('selection', 'test'))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    policy_path = OUTPUT / 'policy.json'
    if policy_path.is_file():
        existing = json.loads(policy_path.read_text(encoding='utf-8'))
        if stable_hash(existing) != stable_hash(POLICY):
            raise ValueError('The progressive policy is already frozen differently.')
    else:
        write_json(policy_path, POLICY)
    report = evaluate(args.partition)
    print(json.dumps({key: value for key, value in report.items()
                      if key != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
