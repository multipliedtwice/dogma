"""Score the production speaker resolver against the frozen reviewed gold set."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.claude_analysis import structured_call as claude_structured_call
from rimsky.codex_analysis import structured_call as codex_structured_call
from rimsky.speaker_resolution import TIE_BREAKER_SYSTEM
from rimsky.staged_analysis import (SPEAKER_SYSTEM, checked_speaker,
                                    staged_proposal_with_details)
from rimsky.util import AppError


GOLD = ROOT / 'manual-labeling/speaker-resolution-gold-v1/gold.json'
PROTOCOL = ROOT / 'manual-labeling/speaker-resolution-gold-v1/evaluation-protocol.json'
PACK = ROOT / 'training-work/book1-provisional-pack'
OUTPUT = ROOT / 'training-work/speaker-resolution-eval-v1'
LOCAL_CACHE = OUTPUT / 'local-cache.jsonl'
LOCAL_RESULTS = OUTPUT / 'local-results.json'
TIE_RESULTS = OUTPUT / 'tie-results.json'
CODEX_RESULTS = OUTPUT / 'codex-results.json'
CLAUDE_RESULTS = OUTPUT / 'claude-results.json'
SECOND_PASS_RESULTS = OUTPUT / 'local-second-pass-results.json'
REPORT = OUTPUT / 'report.json'


def configure_output(run_id: str) -> None:
    global OUTPUT, LOCAL_CACHE, LOCAL_RESULTS, TIE_RESULTS, CODEX_RESULTS, CLAUDE_RESULTS, SECOND_PASS_RESULTS, REPORT
    if not run_id.replace('-', '').replace('_', '').isalnum():
        raise ValueError('Run ID must contain only letters, digits, dashes or underscores.')
    OUTPUT = (ROOT / 'training-work/speaker-resolution-eval-v1' if run_id == 'bonsai'
              else ROOT / f'training-work/speaker-resolution-eval-{run_id}-v1')
    LOCAL_CACHE = OUTPUT / 'local-cache.jsonl'
    LOCAL_RESULTS = OUTPUT / 'local-results.json'
    TIE_RESULTS = OUTPUT / 'tie-results.json'
    CODEX_RESULTS = OUTPUT / 'codex-results.json'
    CLAUDE_RESULTS = OUTPUT / 'claude-results.json'
    SECOND_PASS_RESULTS = OUTPUT / 'local-second-pass-results.json'
    REPORT = OUTPUT / 'report.json'


def stable_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    os.replace(temporary, path)


def read_cache(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    cache = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            cache[row['key']] = row['answer']
    return cache


def append_cache(path: Path, key: str, answer: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'key': key, 'answer': answer},
                                ensure_ascii=False) + '\n')


def ollama_digest(model_name: str) -> str:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=30) as response:
        tags = json.loads(response.read().decode('utf-8'))
    matches = [item['digest'] for item in tags.get('models', [])
               if item.get('name') == model_name or item.get('model') == model_name]
    if len(matches) != 1:
        raise ValueError(f'Ollama model is unavailable or ambiguous: {model_name}')
    return matches[0]


def gold_modes(case: dict, chunks: list[dict]) -> list[str]:
    modes = []
    cursor = 0
    for chunk in chunks:
        start = chunk.get('start', cursor)
        end = chunk.get('end', start + len(chunk['text']))
        if case['text'][start:end] != chunk['text']:
            raise ValueError(f'Chunk text does not preserve {case["id"]}.')
        scored = []
        for part in case['parts']:
            overlap = max(0, min(end, part['end']) - max(start, part['start']))
            scored.append((overlap, part['mode']))
        overlap, mode = max(scored)
        if overlap <= 0:
            raise ValueError(f'Gold structure does not cover {case["id"]}.')
        modes.append(mode)
        cursor = end
    return modes


def evidence_for(request: dict) -> dict[str, str]:
    rows = request.get('context_before', []) + request.get('context_after', [])
    evidence = {item['id']: item['text'] for item in rows}
    evidence[request['target_id']] = request['full_target']
    return evidence


def map_gold(case: dict, records: list[dict], speaker_requests: list[dict]) -> list[dict]:
    output = []
    for index, part in enumerate(item for item in case['parts']
                                 if item['mode'] == 'speech'):
        ranked = []
        for record_index, record in enumerate(records):
            overlap = max(0, min(part['end'], record['end']) -
                          max(part['start'], record['start']))
            ranked.append((overlap, record_index, record))
        overlap, record_index, record = max(ranked, default=(0, -1, None))
        if not record or overlap / max(1, part['end'] - part['start']) < .5:
            output.append({'key': f'{case["id"]}:{index}', 'gold': part['speaker'],
                           'prediction': 'missing', 'tier': 'missing', 'triggers': [],
                           'status': 'review', 'start': part['start'], 'end': part['end']})
            continue
        prediction = (record['primary_speaker'] if record.get('triggers')
                      else record['speaker'])
        request = next((row for row in speaker_requests
                        if row['record_index'] == record_index), None)
        output.append({
            'key': f'{case["id"]}:{index}', 'case_id': case['id'],
            'chapter_id': case['chapter_id'], 'start': part['start'],
            'end': part['end'], 'text': part['text'], 'gold': part['speaker'],
            'prediction': prediction, 'tier': record['tier'],
            'triggers': record.get('triggers', []), 'status': record['status'],
            'attribution_name_hints': record.get('attribution_name_hints', []),
            'request_key': request['key'] if request else None,
        })
    return output


def local_phase(limit: int | None = None, backend: str = 'ollama',
                workers: int = 4,
                model_name: str = 'digitsflow/bonsai-8b:latest') -> None:
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    model_path = PACK / 'models/text/model.gguf'
    model_sha256 = (ollama_digest(model_name) if backend == 'ollama'
                    else file_hash(model_path))
    model = None
    if backend == 'llama_cpp':
        from llama_cpp import Llama
        model = Llama(model_path=str(model_path), n_ctx=8192, n_gpu_layers=-1,
                      verbose=False, seed=42)
    cache = read_cache(LOCAL_CACHE)
    cache_lock = threading.Lock()
    completed = {}
    if LOCAL_RESULTS.is_file():
        saved = json.loads(LOCAL_RESULTS.read_text(encoding='utf-8'))
        if saved.get('gold_content_sha256') != gold['content_sha256']:
            raise ValueError('Saved local results use another gold set.')
        completed = {row['case_id']: row for row in saved.get('cases', [])}
    started = time.time()
    selected_cases = gold['cases'][:limit] if limit else gold['cases']

    def process_case(case: dict) -> dict:
        speaker_requests = []

        def infer(system, request, schema, max_tokens):
            if 'chunks' in request:
                return {'modes': gold_modes(case, request['chunks'])}
            if 'sentences' in request:
                return {'enumerations': [False] * len(request['sentences'])}
            key = stable_hash({'system': system, 'request': request,
                               'schema': schema,
                               'model': model_sha256,
                               'backend': backend})
            with cache_lock:
                answer = cache.get(key)
            if answer is None:
                messages = [{'role': 'system', 'content': system},
                            {'role': 'user', 'content': json.dumps(
                                request, ensure_ascii=False)}]
                try:
                    if backend == 'ollama':
                        body = json.dumps({
                            'model': model_name, 'stream': False, 'think': False,
                            'messages': messages, 'format': schema,
                            'keep_alive': '30m',
                            'options': {'temperature': 0, 'num_ctx': 8192,
                                        'num_predict': max_tokens, 'seed': 42},
                        }, ensure_ascii=False).encode('utf-8')
                        call = urllib.request.Request(
                            'http://127.0.0.1:11434/api/chat', data=body,
                            headers={'Content-Type': 'application/json'}, method='POST')
                        with urllib.request.urlopen(call, timeout=900) as response:
                            result = json.loads(response.read().decode('utf-8'))
                        raw = result['message']['content']
                    else:
                        response = model.create_chat_completion(
                            messages=messages,
                            response_format={'type': 'json_object', 'schema': schema},
                            temperature=0, max_tokens=max_tokens)
                        raw = response['choices'][0]['message']['content']
                    answer = json.loads(raw)
                except (json.JSONDecodeError, KeyError):
                    answer = {}
                with cache_lock:
                    cache[key] = answer
                    append_cache(LOCAL_CACHE, key, answer)
            if 'speech' in request and 'repair' not in request:
                speaker_requests.append({'key': key, 'record_index': -1,
                                         'system': system, 'request': request,
                                         'schema': schema, 'local_answer': answer})
            return answer

        captured_ties = []

        def capture_tie(system, request, schema, max_tokens):
            captured_ties.append({'record_index': len(captured_ties),
                                  'system': system, 'request': request,
                                  'schema': schema})
            return {'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': ''}

        source = {'id': case['id'], 'source_id': case['source_id'], 'start': 0,
                  'end': len(case['text']), 'text': case['text'], 'ordinal': 0,
                  'chapter_id': case['chapter_id'], 'kind': 'paragraph'}
        records = []
        staged_proposal_with_details(
            [source], gold['cast'], case['before'], case['after'], infer,
            include_context=False, tie_breaker_infer=capture_tie,
            resolution_log=records)
        modeled_records = [index for index, record in enumerate(records)
                           if record['tier'] != 'explicit_attribution']
        if len(modeled_records) != len(speaker_requests):
            raise ValueError(f'Speaker request mapping failed for {case["id"]}.')
        for request, record_index in zip(speaker_requests, modeled_records):
            request['record_index'] = record_index
        # The tie callback is invoked in resolution order. Attach the exact
        # production request to its corresponding triggered record.
        tie_iter = iter(captured_ties)
        tie_requests = []
        for record_index, record in enumerate(records):
            if record.get('triggers'):
                captured = next(tie_iter)
                captured['record_index'] = record_index
                captured['key'] = stable_hash({'system': captured['system'],
                                               'request': captured['request'],
                                               'schema': captured['schema']})
                tie_requests.append(captured)
        return {
            'case_id': case['id'], 'records': records,
            'speaker_requests': speaker_requests, 'tie_requests': tie_requests,
            'spans': map_gold(case, records, speaker_requests),
        }

    pending = [case for case in selected_cases if case['id'] not in completed]
    pool_size = 1 if backend == 'llama_cpp' else max(1, workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = {executor.submit(process_case, case): case for case in pending}
        for finished, future in enumerate(concurrent.futures.as_completed(futures), 1):
            case = futures[future]
            completed[case['id']] = future.result()
            if finished % 10 == 0 or finished == len(pending):
                payload = {'schema': 1, 'gold_content_sha256': gold['content_sha256'],
                           'model': str(model_path.relative_to(ROOT)),
                           'model_sha256': model_sha256,
                           'backend': backend,
                           'backend_model': model_name if backend == 'ollama' else None,
                           'workers': pool_size,
                           'cases': [completed[key] for key in
                                     [item['id'] for item in gold['cases']]
                                     if key in completed]}
                OUTPUT.mkdir(parents=True, exist_ok=True)
                write_json_atomic(LOCAL_RESULTS, payload)
                elapsed = time.time() - started
                print(json.dumps({'phase': 'local', 'cases': len(completed),
                                  'total': len(gold['cases']),
                                  'elapsed_seconds': round(elapsed, 1)}), flush=True)
    print(json.dumps({'phase': 'local', 'complete': True,
                      'cases': len(completed)}, indent=2))


def codex_schema(keys: list[str], cast_ids: list[str]) -> dict:
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
                        'evidence_quote': {'type': 'string',
                                           'maxLength': 200}}}}}}


def external_batches(rows: list[dict], system: str, output: Path,
                     gold: dict, batch_size: int, provider: str) -> None:
    row_keys = {row['key'] for row in rows}
    saved = {'schema': 1, 'gold_content_sha256': gold['content_sha256'],
             'provider': provider, 'provider_metadata': {}, 'answers': {}}
    if output.is_file():
        saved = json.loads(output.read_text(encoding='utf-8'))
        if saved.get('gold_content_sha256') != gold['content_sha256']:
            raise ValueError('Saved Codex results use another gold set.')
        # Request hashes change when a production resolver input changes. Keep
        # reusable answers and discard stale benchmark-only cache entries.
        prior_keys = set(saved['answers'])
        saved['answers'] = {key: value for key, value in saved['answers'].items()
                            if key in row_keys}
        if set(saved['answers']) != prior_keys:
            write_json_atomic(output, saved)
    pending = [row for row in rows if row['key'] not in saved['answers']]
    cast_ids = [item['id'] for item in gold['cast']]
    def save_batch(batch: list[dict], *, repair_error: str = '') -> None:
        keys = [row['key'] for row in batch]
        request = {'cases': [{'key': row['key'], 'case': row['request']}
                             for row in batch]}
        if repair_error:
            request['repair'] = {
                'instruction': ('The previous answer failed validation. Return exactly one '
                                'valid answer for every supplied key. Copy a short contiguous '
                                'evidence quote from the supplied passage. If the identity '
                                'cannot be resolved without violating the rule, return unknown.'),
                'validation_error': repair_error,
            }
        metadata = {}
        call = claude_structured_call if provider == 'claude_cli' else codex_structured_call
        if provider == 'claude_cli':
            answer = call(
                system + ' Resolve every numbered case independently and return one '
                'answer for every supplied key.', request, codex_schema(keys, cast_ids),
                metadata=metadata)
            saved['provider_metadata']['cli_version'] = metadata.get('cli_version', '')
            saved['provider_metadata']['models'] = sorted(set(
                saved['provider_metadata'].get('models', [])) |
                set(metadata.get('model_usage', {})))
            saved['provider_metadata']['total_cost_usd'] = round(
                saved['provider_metadata'].get('total_cost_usd', 0) +
                float(metadata.get('cost_usd') or 0), 6)
        else:
            answer = call(
                system + ' Resolve every numbered case independently and return one '
                'answer for every supplied key.', request, codex_schema(keys, cast_ids))
        answers = answer.get('answers', [])
        by_key = {item.get('key'): item for item in answers
                  if isinstance(item, dict)}
        if len(answers) != len(keys) or set(by_key) != set(keys):
            raise ValueError('Codex batch omitted or duplicated a case key.')
        checked = {}
        for row in batch:
            item = dict(by_key[row['key']])
            item.pop('key')
            checked_speaker(item, gold['cast'], evidence_for(row['request']),
                            row['request'].get('possible_addressee', ''))
            checked[row['key']] = item
        saved['answers'].update(checked)
        write_json_atomic(output, saved)
        print(json.dumps({'phase': output.stem, 'complete': len(saved['answers']),
                          'total': len(rows)}), flush=True)

    def run_batch(batch: list[dict]) -> None:
        try:
            save_batch(batch)
        except (AppError, ValueError, KeyError, TypeError) as error:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                print(json.dumps({'phase': output.stem, 'batch_repair': len(batch),
                                  'reason': str(error)[:240]}), flush=True)
                run_batch(batch[:midpoint])
                run_batch(batch[midpoint:])
                return
            print(json.dumps({'phase': output.stem, 'single_repair': batch[0]['key'],
                              'reason': str(error)[:240]}), flush=True)
            try:
                save_batch(batch, repair_error=str(error))
            except (AppError, ValueError, KeyError, TypeError) as repair_error:
                # Production must hold a repeatedly invalid segment for review;
                # never abort the book and never pretend the provider resolved it.
                saved['answers'][batch[0]['key']] = {
                    'speaker': 'unknown', 'name': '', 'evidence_id': '',
                    'evidence_quote': '',
                    'provider_failure': str(repair_error)[:500],
                }
                write_json_atomic(output, saved)
                print(json.dumps({'phase': output.stem,
                                  'provider_failure': batch[0]['key'],
                                  'complete': len(saved['answers']),
                                  'total': len(rows)}), flush=True)

    for offset in range(0, len(pending), batch_size):
        run_batch(pending[offset:offset + batch_size])


def external_phase(kind: str, batch_size: int) -> None:
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    local = json.loads(LOCAL_RESULTS.read_text(encoding='utf-8'))
    if kind == 'tie':
        rows = [row for case in local['cases'] for row in case['tie_requests']]
        external_batches(rows, TIE_BREAKER_SYSTEM, TIE_RESULTS, gold, batch_size,
                         'codex_cli')
    else:
        unique = {}
        for case in local['cases']:
            for row in case['speaker_requests']:
                unique.setdefault(row['key'], row)
        provider, output = (('claude_cli', CLAUDE_RESULTS) if kind == 'claude'
                            else ('codex_cli', CODEX_RESULTS))
        external_batches(list(unique.values()), SPEAKER_SYSTEM, output,
                         gold, batch_size, provider)


def metrics(rows: list[dict], field: str) -> dict:
    total = len(rows)
    correct = sum(row.get(field) == row['gold'] for row in rows)
    return {'total': total, 'correct': correct,
            'accuracy': round(correct / total, 6) if total else None}


def evaluation_summary(rows: list[dict]) -> dict:
    escalated = [row for row in rows if row['triggers']]
    not_escalated = [row for row in rows if not row['triggers']]
    changed = [row for row in escalated if row['local_plus_tie'] != row['local']]
    unchanged = [row for row in escalated if row['local_plus_tie'] == row['local']]
    change_effect = Counter()
    for row in changed:
        before = row['local'] == row['gold']
        after = row['local_plus_tie'] == row['gold']
        change_effect['fixed_error' if after and not before else
                      'introduced_error' if before and not after else
                      'changed_correct_to_correct' if before and after else
                      'changed_wrong_to_wrong'] += 1
    trigger_rows = defaultdict(list)
    for row in escalated:
        for trigger in row['triggers']:
            trigger_rows[trigger].append(row)
    modeled = [row for row in rows if row.get('request_key')]
    consensus_accepted = [row for row in modeled
                          if row['local_two_pass'] != 'unknown']
    cli_agreement = [row for row in modeled if row['codex'] == row['claude']]
    cli_disagreement = [row for row in modeled if row['codex'] != row['claude']]
    return {
        'spans': len(rows),
        'scores': {
            'local_raw_before_validation': metrics(rows, 'local_raw'),
            'local': metrics(rows, 'local'),
            'local_two_pass_agreement_gate': metrics(rows, 'local_two_pass'),
            'local_plus_tie_breaker': metrics(rows, 'local_plus_tie'),
            'codex_with_deterministic_rules': metrics(rows, 'codex'),
            'claude_with_deterministic_rules': metrics(rows, 'claude'),
        },
        'local_two_pass_gate': {
            'modeled_spans': len(modeled),
            'accepted_spans': len(consensus_accepted),
            'coverage': (round(len(consensus_accepted) / len(modeled), 6)
                         if modeled else None),
            'accepted_precision': metrics(
                consensus_accepted, 'local_two_pass')['accuracy'],
            'review_holds': len(modeled) - len(consensus_accepted),
        },
        'cli_agreement_gate': {
            'modeled_spans': len(modeled),
            'accepted_spans': len(cli_agreement),
            'coverage': (round(len(cli_agreement) / len(modeled), 6)
                         if modeled else None),
            'accepted_precision': metrics(cli_agreement, 'codex')['accuracy'],
            'review_holds': len(cli_disagreement),
            'disagreements': {
                'codex_correct': sum(row['codex'] == row['gold']
                                     for row in cli_disagreement),
                'claude_correct': sum(row['claude'] == row['gold']
                                      for row in cli_disagreement),
                'both_wrong': sum(row['codex'] != row['gold'] and
                                  row['claude'] != row['gold']
                                  for row in cli_disagreement),
            },
        },
        'escalation': {
            'spans': len(escalated),
            'rate': round(len(escalated) / len(rows), 6) if rows else None,
            'not_escalated': metrics(not_escalated, 'local'),
            'changed_by_tie_breaker': {
                'local_before': metrics(changed, 'local'),
                'after_tie_breaker': metrics(changed, 'local_plus_tie'),
            },
            'unchanged_by_tie_breaker': {
                'local_before': metrics(unchanged, 'local'),
                'after_tie_breaker': metrics(unchanged, 'local_plus_tie'),
            },
            'change_effect': dict(change_effect),
            'by_trigger': {key: {
                'count': len(value),
                'local': metrics(value, 'local'),
                'local_plus_tie_breaker': metrics(value, 'local_plus_tie'),
            } for key, value in sorted(trigger_rows.items())},
        },
        'missing_predictions': sum(row['local'] == 'missing' for row in rows),
        'provider_failures': {
            'codex': sum(bool(row.get('codex_provider_failure')) for row in rows),
            'claude': sum(bool(row.get('claude_provider_failure')) for row in rows),
            'tie_breaker': sum(bool(row.get('tie_provider_failure')) for row in rows),
            'local_second_pass': sum(bool(row.get('second_provider_failure'))
                                     for row in rows),
        },
        'validation_changed_identity': sum(row['local_raw'] != row['local']
                                           for row in rows),
    }


def report_phase() -> None:
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    if protocol.get('gold_content_sha256') != gold['content_sha256']:
        raise ValueError('Evaluation protocol uses another gold set.')
    local = json.loads(LOCAL_RESULTS.read_text(encoding='utf-8'))
    tie = json.loads(TIE_RESULTS.read_text(encoding='utf-8'))
    codex = json.loads(CODEX_RESULTS.read_text(encoding='utf-8'))
    claude = json.loads(CLAUDE_RESULTS.read_text(encoding='utf-8'))
    second = json.loads(SECOND_PASS_RESULTS.read_text(encoding='utf-8'))
    selected_model = protocol['selected_local_model']
    if (local.get('backend_model') != selected_model['name'] or
            local.get('model_sha256') != selected_model['ollama_digest']):
        raise ValueError('Local results do not use the frozen selected model.')
    expected_case_ids = {case['id'] for case in gold['cases']}
    actual_case_ids = {case['case_id'] for case in local.get('cases', [])}
    if actual_case_ids != expected_case_ids:
        raise ValueError('Local results do not cover the complete frozen gold set.')
    if (tie.get('gold_content_sha256') != gold['content_sha256'] or
            codex.get('gold_content_sha256') != gold['content_sha256'] or
            claude.get('gold_content_sha256') != gold['content_sha256']):
        raise ValueError('External results use another gold set.')
    expected_tie_keys = {request['key'] for case in local['cases']
                         for request in case['tie_requests']}
    expected_codex_keys = {request['key'] for case in local['cases']
                           for request in case['speaker_requests']}
    if set(tie.get('answers', {})) != expected_tie_keys:
        raise ValueError('Tie-breaker results are incomplete or contain extra cases.')
    if set(codex.get('answers', {})) != expected_codex_keys:
        raise ValueError('Codex baseline results are incomplete or contain extra cases.')
    if set(claude.get('answers', {})) != expected_codex_keys:
        raise ValueError('Claude baseline results are incomplete or contain extra cases.')
    if (second.get('gold_content_sha256') != gold['content_sha256'] or
            second.get('model') != selected_model['name'] or
            set(second.get('answers', {})) != expected_codex_keys):
        raise ValueError('Local second-pass results are incomplete or use another model.')
    spans = [dict(row) for case in local['cases'] for row in case['spans']]
    raw_local = {request['key']: request.get('local_answer', {})
                 for case in local['cases'] for request in case['speaker_requests']}
    request_to_codex = codex['answers']
    request_to_claude = claude['answers']
    request_to_second = second['answers']
    tie_answers = tie['answers']
    tie_key_by_span = {}
    for case in local['cases']:
        for request in case['tie_requests']:
            candidates = [span for span in case['spans']
                          if span['triggers'] and
                          max(0, min(span['end'], case['records'][request['record_index']]['end']) -
                              max(span['start'], case['records'][request['record_index']]['start'])) > 0]
            for span in candidates:
                tie_key_by_span[span['key']] = request['key']
    for row in spans:
        row['local'] = row.pop('prediction')
        request_key = row.get('request_key')
        raw_answer = raw_local.get(request_key, {})
        row['local_raw'] = raw_answer.get('speaker', row['local'])
        row['codex'] = (request_to_codex.get(request_key, {}).get('speaker')
                        if request_key else row['local'])
        row['codex_provider_failure'] = bool(
            request_key and request_to_codex.get(request_key, {}).get('provider_failure'))
        row['claude'] = (request_to_claude.get(request_key, {}).get('speaker')
                         if request_key else row['local'])
        row['claude_provider_failure'] = bool(
            request_key and request_to_claude.get(request_key, {}).get('provider_failure'))
        second_answer = request_to_second.get(request_key, {})
        row['local_two_pass'] = (
            row['local'] if not request_key else
            row['local'] if raw_answer.get('speaker') == second_answer.get('speaker')
            else 'unknown')
        row['second_provider_failure'] = bool(
            request_key and second_answer.get('provider_failure'))
        tie_key = tie_key_by_span.get(row['key'])
        row['local_plus_tie'] = (tie_answers.get(tie_key, {}).get('speaker')
                                 if tie_key else row['local'])
        row['tie_provider_failure'] = bool(
            tie_key and tie_answers.get(tie_key, {}).get('provider_failure'))
    selection_ids = set(protocol['selection_case_ids'])
    test_ids = set(protocol['test_case_ids'])
    if selection_ids & test_ids or selection_ids | test_ids != {
            case['id'] for case in gold['cases']}:
        raise ValueError('Evaluation protocol does not partition the frozen gold set.')
    selection_rows = [row for row in spans if row['case_id'] in selection_ids]
    test_rows = [row for row in spans if row['case_id'] in test_ids]
    report = {
        'schema': 1, 'gold_content_sha256': gold['content_sha256'],
        'evaluation_protocol_sha256': protocol['content_sha256'],
        'gold_counts': gold['counts'],
        'partitions': {
            'model_selection': evaluation_summary(selection_rows),
            'untouched_test': evaluation_summary(test_rows),
            'all_frozen_gold': evaluation_summary(spans),
        },
        'rows': spans,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json_atomic(REPORT, report)
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'},
                     ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('local', 'tie', 'codex', 'claude', 'report'))
    parser.add_argument('--batch-size', type=int, default=12)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--backend', choices=('ollama', 'llama_cpp'), default='ollama')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--run-id', default='bonsai')
    parser.add_argument('--ollama-model', default='digitsflow/bonsai-8b:latest')
    args = parser.parse_args()
    configure_output(args.run_id)
    if args.phase == 'local':
        local_phase(args.limit, args.backend, args.workers, args.ollama_model)
    elif args.phase in ('tie', 'codex', 'claude'):
        external_phase(args.phase, args.batch_size)
    else:
        report_phase()


if __name__ == '__main__':
    main()
