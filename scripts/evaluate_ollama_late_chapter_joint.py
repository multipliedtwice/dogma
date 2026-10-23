"""Exploratory Ollama one-pass and two-pass late-chapter speaker benchmark.

Uses the same position-selected c0038-c0040 scope as the Codex/Claude chapter
audit. Gold is loaded only after provider answers are persisted for scoring.
No corpus labels are written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_progressive_speaker_passes as base
import evaluate_late_chapter_joint_audit as late


OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-ollama-exploratory'
CHAPTERS = ('c0038', 'c0039', 'c0040')
SYSTEM_INITIAL = (
    base.SPEAKER_SYSTEM + ' Resolve these targets as turns from one complete '
    'chapter. Track recurring speakers and dialogue order across the supplied '
    'chapter. Do not invent a person whom the text leaves unknown. Return one '
    'answer for every target key.'
)
SYSTEM_VERIFY = (
    base.SPEAKER_SYSTEM + ' This is an independent second pass over turns from one '
    'complete chapter. The first-pass proposals are fallible hints, not evidence. '
    'Re-read the chapter, keep a proposal only when supported, and correct it or '
    'return unknown otherwise. Return one answer for every target key.'
)
SYSTEM_INDEPENDENT = (
    base.SPEAKER_SYSTEM + ' Independently resolve these difficult targets as turns '
    'from one complete chapter. No earlier answer is supplied. Track dialogue order '
    'and recurring speakers, but choose unknown whenever the supplied book text does '
    'not identify the speaker. Return one answer for every target key.'
)


def safe_model_name(model: str) -> str:
    return ''.join(char if char.isalnum() else '-' for char in model).strip('-')


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    os.replace(temporary, path)


def target_case(request: dict) -> dict:
    return {key: request[key] for key in (
        'target_id', 'speech', 'full_target', 'possible_addressee',
        'attribution_name_hints', 'narrative_leadin') if key in request}


def call_ollama(model: str, system: str, request: dict, schema: dict,
                *, seed: int) -> dict:
    body = json.dumps({
        'model': model, 'stream': False, 'think': False,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)},
        ],
        'format': schema, 'keep_alive': '30m',
        'options': {'temperature': 0, 'num_ctx': 32768,
                    'num_predict': 2400, 'seed': seed},
    }, ensure_ascii=False).encode('utf-8')
    http = urllib.request.Request(
        'http://127.0.0.1:11434/api/chat', data=body,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(http, timeout=1800) as response:
        envelope = json.loads(response.read().decode('utf-8'))
    return json.loads(envelope['message']['content'])


def run_pass(path: Path, model: str, pass_name: str, rows: list[dict],
             context_rows: list[dict], requests: dict[str, dict], cast: list[dict],
             first: dict[str, dict] | None = None) -> dict[str, dict]:
    policy = {'schema': 1, 'model': model, 'pass': pass_name,
              'chapters': list(CHAPTERS), 'num_ctx': 32768,
              'selection_uses_gold': False}
    saved = {'schema': 1, 'policy': policy,
             'policy_sha256': base.stable_hash(policy), 'answers': {}}
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('policy_sha256') != base.stable_hash(policy):
            raise ValueError(f'{path.name} uses another frozen policy.')
    system = (SYSTEM_INITIAL if pass_name == 'initial' else
              SYSTEM_VERIFY if pass_name == 'verify' else SYSTEM_INDEPENDENT)
    seed = 42 if pass_name == 'initial' else 314159

    for chapter_id in CHAPTERS:
        chapter_rows = [row for row in rows if row['chapter_id'] == chapter_id]
        pending = [row for row in chapter_rows if row['key'] not in saved['answers']]
        if not pending:
            continue
        passages = late.chapter_passages(
            [row for row in context_rows if row['chapter_id'] == chapter_id], requests)
        evidence = {item['id']: item['text'] for item in passages}

        def process(batch: list[dict]) -> None:
            keys = [row['key'] for row in batch]
            targets = []
            for row in batch:
                item = {'key': row['key'],
                        'case': target_case(requests[row['request_key']])}
                if first is not None:
                    proposal = first[row['key']]
                    item['first_pass_proposal'] = {
                        'speaker': proposal['speaker'],
                        'name': proposal.get('name', ''),
                    }
                targets.append(item)
            request = {'chapter_id': chapter_id, 'character_registry': cast,
                       'chapter_passages': passages, 'targets': targets}
            try:
                raw = call_ollama(
                    model, system, request,
                    base.answer_schema(keys, [item['id'] for item in cast]), seed=seed)
                by_key = {item.get('key'): item for item in raw.get('answers', [])
                          if isinstance(item, dict)}
                if set(by_key) != set(keys):
                    raise ValueError('Ollama omitted or duplicated target keys.')
                for row in batch:
                    answer = dict(by_key[row['key']])
                    answer.pop('key')
                    validation_valid = True
                    validation_error = ''
                    try:
                        base.checked_speaker(
                            answer, cast, evidence,
                            requests[row['request_key']].get('possible_addressee', ''))
                    except base.AppError as error:
                        # Identity quality and production validation are reported
                        # separately, as in the Codex/Claude exploratory audit.
                        validation_valid = False
                        validation_error = str(error)[:300]
                    answer['validation_valid'] = validation_valid
                    if validation_error:
                        answer['validation_error'] = validation_error
                    saved['answers'][row['key']] = answer
                write_json(path, saved)
                print(json.dumps({'model': model, 'pass': pass_name,
                                  'chapter': chapter_id,
                                  'saved': len(saved['answers']),
                                  'total': len(rows)}), flush=True)
            except Exception as error:
                if len(batch) > 1:
                    middle = len(batch) // 2
                    process(batch[:middle])
                    process(batch[middle:])
                    return
                saved['answers'][batch[0]['key']] = {
                    'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': '', 'evidence_valid': True,
                    'provider_failure': str(error)[:500],
                }
                write_json(path, saved)

        # Batches retain full chapter context while keeping structured output small.
        for offset in range(0, len(pending), 10):
            process(pending[offset:offset + 10])
    return saved['answers']


def score(rows: list[dict], answers: dict[str, str]) -> dict:
    correct = sum(answers[row['key']] == row['gold'] for row in rows)
    return {'total': len(rows), 'correct': correct,
            'accuracy': round(correct / len(rows), 6)}


def changes(rows: list[dict], before: dict[str, str], after: dict[str, str]) -> dict:
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--scope', choices=('late162', 'hard10'), default='late162')
    args = parser.parse_args()
    gold, all_rows, requests = base.load_inputs('test')
    context_rows = [row for row in all_rows if row.get('request_key') and
                    row['chapter_id'] in CHAPTERS]
    rows = context_rows
    if args.scope == 'hard10':
        prior = json.loads((ROOT / 'training-work/speaker-resolution-progressive-v4-exploratory/report.json').read_text(encoding='utf-8'))
        selected = {row['key'] for row in prior['rows']
                    if len({row['primary'], row['initial_claude'],
                            row['joint_claude'], row['joint_codex']}) > 1}
        rows = [row for row in context_rows if row['key'] in selected]
    folder = OUTPUT / safe_model_name(args.model)
    if args.scope != 'late162':
        folder /= args.scope
    initial_raw = run_pass(folder / 'initial.json', args.model, 'initial', rows,
                           context_rows, requests, gold['cast'])
    verify_raw = run_pass(folder / 'verify.json', args.model, 'verify', rows,
                          context_rows, requests, gold['cast'], first=initial_raw)
    initial = {key: answer['speaker'] for key, answer in initial_raw.items()}
    verify = {key: answer['speaker'] for key, answer in verify_raw.items()}
    agreed_rows = [row for row in rows if initial[row['key']] == verify[row['key']]]
    agreed = {row['key']: initial[row['key']] for row in agreed_rows}
    report = {
        'schema': 1, 'model': args.model, 'scope': args.scope,
        'chapters': list(CHAPTERS),
        'spans': len(rows), 'gold_content_sha256': gold['content_sha256'],
        'initial': score(rows, initial),
        'verify': score(rows, verify),
        'verify_changes': changes(rows, initial, verify),
        'agreement_gate': {
            'accepted': len(agreed_rows), 'held': len(rows) - len(agreed_rows),
            'coverage': round(len(agreed_rows) / len(rows), 6),
            'accepted_precision': score(agreed_rows, agreed)['accuracy'],
        },
        'provider_failures': {
            'initial': sum(bool(answer.get('provider_failure'))
                           for answer in initial_raw.values()),
            'verify': sum(bool(answer.get('provider_failure'))
                          for answer in verify_raw.values()),
        },
        'invalid_validation': {
            'initial': sum(not answer.get('validation_valid', True)
                           for answer in initial_raw.values()),
            'verify': sum(not answer.get('validation_valid', True)
                          for answer in verify_raw.values()),
        },
    }
    write_json(folder / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
