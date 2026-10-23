"""Evaluate independent joint chapter passes and a high-effort final check."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_progressive_speaker_passes as base


OUTPUT = ROOT / 'training-work/speaker-resolution-progressive-v3'
POLICY = {
    'schema': 1,
    'name': 'progressive-joint-chapter-v3',
    'derived_from': 'v1 and v2 selection partitions only',
    'passes': [
        {'pass': 1, 'name': 'shared_structure'},
        {'pass': 2, 'name': 'codex_primary_low'},
        {'pass': 3, 'name': 'claude_independent_low'},
        {'pass': 4, 'name': 'claude_joint_chapter',
         'scope': 'chapters containing pass-2/pass-3 disagreements'},
        {'pass': 5, 'name': 'codex_joint_chapter_low',
         'scope': 'same unresolved chapter turns, no prior labels'},
        {'pass': 6, 'name': 'codex_focused_high',
         'scope': 'remaining disagreement only'},
    ],
    'acceptance': [
        'Strict deterministic attributions remain accepted.',
        'A modeled identity needs support from both Codex and Claude.',
        'Joint chapter solvers see no earlier proposal labels.',
        'Pass 6 is accepted only when it agrees with a Claude answer.',
        'Multiple cross-provider consensuses or no consensus remain review holds.',
    ],
}

JOINT_SYSTEM = (
    base.SPEAKER_SYSTEM + ' Resolve the supplied targets jointly as turns from the '
    'same chapter. Track recurring speakers and dialogue order across the complete '
    'chapter_passages list. It contains no trusted speaker labels. Do not invent a '
    'new person separately for each unattributed turn when the context supports one '
    'recurring speaker. Do not force distinct unknown people into one character. '
    'Return one answer for every supplied key.'
)

HIGH_SYSTEM = (
    base.SPEAKER_SYSTEM + ' This is a final high-reasoning check for a case on which '
    'earlier chapter analyses disagreed. Prior proposals are fallible and are not '
    'evidence. Trace the dialogue and supplied neighboring context carefully. Return '
    'one answer for every supplied key.'
)


def consensus(candidates: list[tuple[str, str]]) -> str | None:
    origins = defaultdict(set)
    for provider, speaker in candidates:
        origins[speaker].add(provider)
    supported = [speaker for speaker, providers in origins.items()
                 if {'codex', 'claude'} <= providers]
    return supported[0] if len(supported) == 1 else None


def metrics(rows: list[dict], accepted: dict[str, str]) -> dict:
    decided = [row for row in rows if row['key'] in accepted]
    correct = sum(accepted[row['key']] == row['gold'] for row in decided)
    return {'accepted': len(decided), 'held': len(rows) - len(decided),
            'coverage': round(len(decided) / len(rows), 6) if rows else None,
            'accepted_precision': round(correct / len(decided), 6)
                                  if decided else None,
            'correct': correct}


def high_stage(path: Path, rows: list[dict], cast: list[dict]) -> dict[str, dict]:
    base.POLICY = POLICY
    saved = {'schema': 1, 'policy_sha256': base.stable_hash(POLICY),
             'provider': 'codex_cli_high', 'answers': {}}
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('policy_sha256') != base.stable_hash(POLICY):
            raise ValueError('Pass-6 cache uses another policy.')
    pending = [row for row in rows
               if row['key'] not in saved['answers'] or
               saved['answers'][row['key']].get('provider_failure')]

    def one(batch: list[dict], repair_error: str = '') -> None:
        keys = [row['key'] for row in batch]
        system = HIGH_SYSTEM
        if repair_error:
            system += (' The previous response failed validation: ' +
                       repair_error[:500] + ' Copy an exact contiguous evidence '
                       'quote and its exact supplied passage ID.')
        try:
            raw = base.codex_call(
                system, {'cases': batch},
                base.answer_schema(keys, [item['id'] for item in cast]),
                reasoning_effort='high')
            saved['answers'].update(base.checked_batch(raw, batch, cast))
            base.write_json(path, saved)
            print(json.dumps({'stage': path.stem,
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

    for offset in range(0, len(pending), 8):
        one(pending[offset:offset + 8])
    return saved['answers']


def evaluate(partition: str) -> dict:
    base.POLICY = POLICY
    gold, rows, requests = base.load_inputs(partition)
    modeled = [row for row in rows if row.get('request_key')]
    deterministic = [row for row in rows if not row.get('request_key')]
    p2 = {row['key']: row['codex'] for row in rows}
    p3 = {row['key']: row['claude'] for row in rows}
    accepted = {row['key']: p2[row['key']] for row in deterministic}
    for row in modeled:
        if p2[row['key']] == p3[row['key']]:
            accepted[row['key']] = p2[row['key']]
    after3 = dict(accepted)

    initial_disagreements = {row['key'] for row in modeled
                             if row['key'] not in accepted}
    chapter_rows = defaultdict(list)
    for row in modeled:
        chapter_rows[row['chapter_id']].append(row)
    p4_answers, p5_answers = {}, {}
    for chapter_id in sorted({row['chapter_id'] for row in modeled
                              if row['key'] in initial_disagreements}):
        targets = [row for row in chapter_rows[chapter_id]
                   if row['key'] in initial_disagreements]
        chapter_passages = [{
            'key': row['key'], 'text': row['text'],
            'context_before': requests[row['request_key']].get('context_before', []),
            'context_after': requests[row['request_key']].get('context_after', []),
        } for row in chapter_rows[chapter_id]]
        batch = [{
            'key': row['key'], 'chapter_id': chapter_id,
            'request': requests[row['request_key']],
            'chapter_passages': chapter_passages,
        } for row in targets]
        p4_answers.update(base.run_stage(
            OUTPUT / f'{partition}-pass4-{chapter_id}.json', 'claude_cli',
            JOINT_SYSTEM, batch, gold['cast'], batch_size=18))
        p5_answers.update(base.run_stage(
            OUTPUT / f'{partition}-pass5-{chapter_id}.json', 'codex_cli',
            JOINT_SYSTEM, batch, gold['cast'], batch_size=18))
    p4 = {key: answer['speaker'] for key, answer in p4_answers.items()}
    p5 = {key: answer['speaker'] for key, answer in p5_answers.items()}
    for row in modeled:
        if row['key'] not in initial_disagreements:
            continue
        choice = consensus([
            ('codex', p2[row['key']]), ('claude', p3[row['key']]),
            ('claude', p4[row['key']]), ('codex', p5[row['key']]),
        ])
        if choice is not None:
            accepted[row['key']] = choice
    after5 = dict(accepted)

    remaining = {row['key'] for row in rows if row['key'] not in accepted}
    pass6_rows = []
    for row in modeled:
        if row['key'] not in remaining:
            continue
        request = requests[row['request_key']]
        pass6_rows.append({
            'key': row['key'], 'request': request,
            'chapter_passages': [{
                'key': peer['key'], 'text': peer['text'],
                'context_before': requests[peer['request_key']].get(
                    'context_before', []),
                'context_after': requests[peer['request_key']].get(
                    'context_after', []),
            } for peer in chapter_rows[row['chapter_id']]],
            'prior_proposals': {
                'proposal_a': p2[row['key']], 'proposal_b': p3[row['key']],
                'proposal_c': p4[row['key']], 'proposal_d': p5[row['key']],
            },
        })
    p6_answers = high_stage(OUTPUT / f'{partition}-pass6.json', pass6_rows,
                            gold['cast'])
    p6 = {key: answer['speaker'] for key, answer in p6_answers.items()}
    for row in modeled:
        if row['key'] not in remaining:
            continue
        # A high-effort Codex result still needs corroboration from Claude.
        if p6[row['key']] in {p3[row['key']], p4[row['key']]}:
            accepted[row['key']] = p6[row['key']]

    report = {
        'schema': 1, 'partition': partition, 'policy': POLICY,
        'policy_sha256': base.stable_hash(POLICY),
        'gold_content_sha256': gold['content_sha256'],
        'counts': {'spans': len(rows), 'modeled': len(modeled),
                   'deterministic': len(deterministic),
                   'initial_disagreements': len(initial_disagreements),
                   'pass6_cases': len(pass6_rows)},
        'pass2_primary': base.effects(rows, p2, p2),
        'pass3_consensus': metrics(rows, after3),
        'pass5_joint_chapter_consensus': metrics(rows, after5),
        'pass6_final_consensus': metrics(rows, accepted),
        'rows': [{
            'key': row['key'], 'gold': row['gold'],
            'pass2': p2[row['key']], 'pass3': p3[row['key']],
            'pass4': p4.get(row['key']), 'pass5': p5.get(row['key']),
            'pass6': p6.get(row['key']), 'accepted': accepted.get(row['key']),
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
        if base.stable_hash(json.loads(policy_path.read_text(encoding='utf-8'))) != base.stable_hash(POLICY):
            raise ValueError('The frozen v3 policy differs from this script.')
    else:
        base.write_json(policy_path, POLICY)
    report = evaluate(args.partition)
    print(json.dumps({key: value for key, value in report.items()
                      if key != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
