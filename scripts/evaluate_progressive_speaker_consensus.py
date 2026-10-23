"""Evaluate a conservative progressive consensus policy after the v1 pilot.

This v2 policy was fixed from the selection-set finding that an anchored final
judge preserved its own earlier errors. The untouched test is run only after the
policy and prompts have been written to disk.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_progressive_speaker_passes as base


OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-v2'
POLICY = {
    'schema': 1,
    'name': 'progressive-chapter-speaker-v2',
    'derived_from': 'progressive-chapter-speaker-v1 selection partition only',
    'passes': [
        {'pass': 1, 'name': 'shared_structure'},
        {'pass': 2, 'name': 'codex_primary'},
        {'pass': 3, 'name': 'claude_independent'},
        {'pass': 4, 'name': 'claude_chapter_audit',
         'scope': 'pass-2/pass-3 disagreements'},
        {'pass': 5, 'name': 'codex_blind_chapter_audit',
         'scope': 'still unresolved; no prior answer is shown'},
        {'pass': 6, 'name': 'claude_blind_chapter_audit',
         'scope': 'still unresolved; no prior answer is shown'},
    ],
    'acceptance': [
        'A strict deterministic attribution remains accepted.',
        'A modeled identity is accepted only after Codex and Claude agree.',
        'Two answers from the same provider never form sufficient consensus.',
        'Unresolved cases remain review holds; the last pass never wins by position.',
    ],
}

BLIND_CODEX_SYSTEM = (
    base.SPEAKER_SYSTEM + ' Make an independent chapter-level check. No earlier '
    'answer is supplied. The chapter passage list is context, not evidence by '
    'itself; resolve the target from its supplied passage and neighboring text. '
    'Return one answer for every supplied key.'
)
BLIND_CLAUDE_SYSTEM = BLIND_CODEX_SYSTEM


def proposal_rows(modeled: list[dict], requests: dict[str, dict],
                  keys: set[str], chapter_text: dict[str, list[dict]]) -> list[dict]:
    return [{
        'key': row['key'], 'chapter_id': row['chapter_id'],
        'request': requests[row['request_key']],
        'chapter_passages': chapter_text[row['chapter_id']],
    } for row in modeled if row['key'] in keys]


def cross_provider_consensus(candidates: list[tuple[str, str]]) -> str | None:
    """Return an identity supported by at least one Codex and one Claude pass."""
    providers = defaultdict(set)
    for provider, speaker in candidates:
        providers[speaker].add(provider)
    accepted = [speaker for speaker, origins in providers.items()
                if {'codex', 'claude'} <= origins]
    return accepted[0] if len(accepted) == 1 else None


def gate_metrics(rows: list[dict], accepted: dict[str, str]) -> dict:
    decided = [row for row in rows if row['key'] in accepted]
    correct = sum(accepted[row['key']] == row['gold'] for row in decided)
    return {
        'accepted': len(decided), 'held': len(rows) - len(decided),
        'coverage': round(len(decided) / len(rows), 6) if rows else None,
        'accepted_precision': round(correct / len(decided), 6) if decided else None,
        'correct': correct,
    }


def evaluate(partition: str) -> dict:
    base.POLICY = POLICY
    gold, rows, requests = base.load_inputs(partition)
    codex_answers = base.load_provider_answers('codex')
    claude_answers = base.load_provider_answers('claude')
    modeled = [row for row in rows if row.get('request_key')]
    deterministic = [row for row in rows if not row.get('request_key')]
    p2 = {row['key']: row['codex'] for row in rows}
    p3 = {row['key']: row['claude'] for row in rows}

    chapter_text = defaultdict(list)
    for row in modeled:
        chapter_text[row['chapter_id']].append(
            {'key': row['key'], 'text': row['text']})

    disagreements = {row['key'] for row in modeled
                     if p2[row['key']] != p3[row['key']]}
    p4_answers = {}
    for chapter_id in sorted({row['chapter_id'] for row in modeled
                              if row['key'] in disagreements}):
        targets = [row for row in modeled
                   if row['chapter_id'] == chapter_id and
                   row['key'] in disagreements]
        batch = [{
            'key': row['key'], 'chapter_id': chapter_id,
            'request': requests[row['request_key']],
            'current_proposal': p2[row['key']],
            'chapter_proposals': [
                {'key': peer['key'], 'text': peer['text'],
                 'current_speaker': p2[peer['key']]}
                for peer in modeled if peer['chapter_id'] == chapter_id],
        } for row in targets]
        p4_answers.update(base.run_stage(
            OUTPUT / f'{partition}-pass4-{chapter_id}.json', 'claude_cli',
            base.CONTINUITY_SYSTEM, batch, gold['cast'], batch_size=18))
    p4 = {key: answer['speaker'] for key, answer in p4_answers.items()}

    accepted = {row['key']: p2[row['key']] for row in deterministic}
    for row in modeled:
        choice = cross_provider_consensus([
            ('codex', p2[row['key']]), ('claude', p3[row['key']]),
            ('claude', p4.get(row['key'], p3[row['key']])),
        ])
        if choice is not None:
            accepted[row['key']] = choice
    after4 = dict(accepted)

    unresolved = {row['key'] for row in rows if row['key'] not in accepted}
    pass5_rows = proposal_rows(modeled, requests, unresolved, chapter_text)
    p5_answers = base.run_stage(
        OUTPUT / f'{partition}-pass5.json', 'codex_cli', BLIND_CODEX_SYSTEM,
        pass5_rows, gold['cast'])
    p5 = {key: answer['speaker'] for key, answer in p5_answers.items()}
    for row in modeled:
        if row['key'] not in unresolved:
            continue
        choice = cross_provider_consensus([
            ('codex', p2[row['key']]), ('codex', p5[row['key']]),
            ('claude', p3[row['key']]), ('claude', p4[row['key']]),
        ])
        if choice is not None:
            accepted[row['key']] = choice
    after5 = dict(accepted)

    unresolved = {row['key'] for row in rows if row['key'] not in accepted}
    pass6_rows = proposal_rows(modeled, requests, unresolved, chapter_text)
    p6_answers = base.run_stage(
        OUTPUT / f'{partition}-pass6.json', 'claude_cli', BLIND_CLAUDE_SYSTEM,
        pass6_rows, gold['cast'])
    p6 = {key: answer['speaker'] for key, answer in p6_answers.items()}
    for row in modeled:
        if row['key'] not in unresolved:
            continue
        choice = cross_provider_consensus([
            ('codex', p2[row['key']]), ('codex', p5[row['key']]),
            ('claude', p3[row['key']]), ('claude', p4[row['key']]),
            ('claude', p6[row['key']]),
        ])
        if choice is not None:
            accepted[row['key']] = choice

    report = {
        'schema': 1, 'partition': partition, 'policy': POLICY,
        'policy_sha256': base.stable_hash(POLICY),
        'gold_content_sha256': gold['content_sha256'],
        'counts': {'spans': len(rows), 'modeled': len(modeled),
                   'deterministic': len(deterministic),
                   'initial_disagreements': len(disagreements)},
        'pass2_primary': base.effects(rows, p2, p2),
        'pass3_cross_provider_gate': gate_metrics(
            rows, {**{row['key']: p2[row['key']] for row in deterministic},
                   **{row['key']: p2[row['key']] for row in modeled
                      if p2[row['key']] == p3[row['key']]}}),
        'pass4_chapter_consensus': gate_metrics(rows, after4),
        'pass5_blind_consensus': gate_metrics(rows, after5),
        'pass6_final_consensus': gate_metrics(rows, accepted),
        'rows': [{
            'key': row['key'], 'gold': row['gold'], 'pass2': p2[row['key']],
            'pass3': p3[row['key']], 'pass4': p4.get(row['key']),
            'pass5': p5.get(row['key']), 'pass6': p6.get(row['key']),
            'accepted': accepted.get(row['key']),
        } for row in rows],
    }
    base.write_json(OUTPUT / f'{partition}-report.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('partition', choices=('selection', 'test'))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    policy_path = OUTPUT / 'policy.json'
    if policy_path.is_file():
        existing = json.loads(policy_path.read_text(encoding='utf-8'))
        if base.stable_hash(existing) != base.stable_hash(POLICY):
            raise ValueError('The frozen v2 policy differs from this script.')
    else:
        base.write_json(policy_path, POLICY)
    report = evaluate(args.partition)
    print(json.dumps({key: value for key, value in report.items()
                      if key != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
