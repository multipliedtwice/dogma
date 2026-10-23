"""Measure an independent second Qwen speaker pass on the frozen gold set."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.staged_analysis import SPEAKER_SYSTEM, checked_speaker
from rimsky.util import AppError


GOLD = ROOT / 'manual-labeling/speaker-resolution-gold-v1/gold.json'
PROTOCOL = ROOT / 'manual-labeling/speaker-resolution-gold-v1/evaluation-protocol.json'
RUN = ROOT / 'training-work/speaker-resolution-eval-qwen35-v1'
LOCAL = RUN / 'local-results.json'
OUTPUT = RUN / 'local-second-pass-results.json'

VERIFY_SYSTEM = SPEAKER_SYSTEM + (
    ' You are the independent second pass. Re-check the first-pass proposal '
    'against the supplied text and character registry. Keep it only when '
    'supported; otherwise return the corrected identity. Do not choose the person '
    'being addressed. If the evidence is insufficient, return unknown.')


def write_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    os.replace(temporary, path)


def evidence(request: dict) -> dict[str, str]:
    rows = request.get('context_before', []) + request.get('context_after', [])
    result = {item['id']: item['text'] for item in rows}
    result[request['target_id']] = request['full_target']
    return result


def call(model: str, request: dict, schema: dict) -> dict:
    body = json.dumps({
        'model': model, 'stream': False, 'think': False,
        'messages': [
            {'role': 'system', 'content': VERIFY_SYSTEM},
            {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)},
        ],
        'format': schema, 'keep_alive': '30m',
        'options': {'temperature': 0, 'num_ctx': 8192,
                    'num_predict': 140, 'seed': 42},
    }, ensure_ascii=False).encode('utf-8')
    request_http = urllib.request.Request(
        'http://127.0.0.1:11434/api/chat', data=body,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request_http, timeout=900) as response:
        envelope = json.loads(response.read().decode('utf-8'))
    return json.loads(envelope['message']['content'])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--partition', choices=('selection', 'all'), default='selection')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--model', default='qwen3.5:4b-q4_K_M')
    args = parser.parse_args()
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    local = json.loads(LOCAL.read_text(encoding='utf-8'))
    allowed = (set(protocol['selection_case_ids']) if args.partition == 'selection'
               else {case['id'] for case in gold['cases']})
    rows = {}
    for case in local['cases']:
        if case['case_id'] not in allowed:
            continue
        for row in case['speaker_requests']:
            rows.setdefault(row['key'], row)
    saved = {'schema': 1, 'gold_content_sha256': gold['content_sha256'],
             'model': args.model, 'partition': args.partition, 'answers': {}}
    if OUTPUT.is_file():
        saved = json.loads(OUTPUT.read_text(encoding='utf-8'))
        if (saved.get('gold_content_sha256') != gold['content_sha256'] or
                saved.get('model') != args.model):
            raise ValueError('Saved second-pass results use another model or gold set.')
        if args.partition == 'all':
            saved['partition'] = 'all'
        saved['answers'] = {key: value for key, value in saved['answers'].items()
                            if key in rows}
    lock = threading.Lock()

    def process(row: dict) -> dict:
        request = {**row['request'], 'verification': {
            'instruction': ('Independently verify or correct this first-pass speaker '
                            'proposal. Return unknown when it cannot be supported.'),
            'first_pass_proposal': row.get('local_answer', {}),
        }}
        last_error = ''
        for attempt in range(2):
            active = request if not last_error else {**request, 'repair': {
                'validation_error': last_error,
                'instruction': ('Return a valid answer. Never select the person being '
                                'addressed; use unknown if unresolved.'),
            }}
            try:
                answer = call(args.model, active, row['schema'])
                checked_speaker(answer, gold['cast'], evidence(row['request']),
                                row['request'].get('possible_addressee', ''))
                return answer
            except (AppError, ValueError, KeyError, TypeError,
                    urllib.error.URLError) as error:
                last_error = str(error)
        return {'speaker': 'unknown', 'name': '', 'evidence_id': '',
                'evidence_quote': '', 'provider_failure': last_error[:500]}

    pending = [row for key, row in rows.items() if key not in saved['answers']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, row): row for row in pending}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = futures[future]
            with lock:
                saved['answers'][row['key']] = future.result()
                if completed % 10 == 0 or completed == len(pending):
                    write_atomic(OUTPUT, saved)
                    print(json.dumps({'phase': 'local-second-pass',
                                      'complete': len(saved['answers']),
                                      'total': len(rows)}), flush=True)
    # Score the selected partition immediately. The untouched partition is only
    # run if this configuration is retained after model selection.
    spans = [span for case in local['cases'] if case['case_id'] in allowed
             for span in case['spans'] if span.get('request_key') in saved['answers']]
    requests = {row['key']: row for row in rows.values()}
    second_correct = sum(
        saved['answers'][row['request_key']]['speaker'] == row['gold']
        for row in spans)
    agreed = [row for row in spans
              if requests[row['request_key']]['local_answer']['speaker'] ==
              saved['answers'][row['request_key']]['speaker'] != 'unknown'
              and row.get('prediction') != 'unknown'
              and not saved['answers'][row['request_key']].get('provider_failure')]
    agreement_correct = sum(row.get('prediction') == row['gold'] for row in agreed)
    print(json.dumps({
        'partition': args.partition,
        'spans': len(spans),
        'second_pass_correct': second_correct,
        'second_pass_accuracy': (
            round(second_correct / len(spans), 6) if spans else None),
        'agreement_gate': {
            'accepted_spans': len(agreed),
            'coverage': round(len(agreed) / len(spans), 6) if spans else None,
            'correct': agreement_correct,
            'accepted_precision': (
                round(agreement_correct / len(agreed), 6) if agreed else None),
            'review_holds': len(spans) - len(agreed),
        },
    }, indent=2))


if __name__ == '__main__':
    main()
