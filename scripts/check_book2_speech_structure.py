"""Proposal-only speech/narrator structure check for the full Book 2 board.

The frozen Book 2 context was produced without the focused speech-structure
pass, so an author remark between two remarks can inherit the speaker
(c0031-p00040: «— Дипломат уважительно поклонился…» given to Иван Лозовский).
This tool classifies complete, deduplicated board paragraphs with the
application's own structure pass and compares the result with each board clip.

  plan     (default, offline) list the paragraphs and the exact amount of book
           text a run would send; nothing leaves the machine.
  --run    send planned paragraphs to the named CLI provider through the
           application's isolated analysis path; resumable checkpoint.
  --compare (offline) turn saved answers into a proposal overlay and report.

Outputs live under training-work and never change the canonical review, the
frozen context or the board. Overlay flags are structural evidence: passive
playback acceptance must not hide them, and a human speaker verdict always wins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.speech_structure import needs_structure_analysis  # noqa: E402

CANDIDATES = ROOT / 'training-work/character-rb2-full/fine-tune-all/candidates.jsonl'
CONTEXT = ROOT / 'training-work/character-rb2/full/context.json'
REVIEW = ROOT / 'manual-labeling/reality-benders-2-full/review.json'
OUT = ROOT / 'training-work/character-rb2-full/structure-check'
NEIGHBOURS = 2


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []


def paragraph_order(context: dict) -> tuple[list[str], dict[str, str]]:
    """Source paragraphs in reading order with their text, from the frozen context."""
    pieces: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for passage in sorted(context['passages'], key=lambda item: item['ordinal']):
        if passage['source_id'] not in pieces:
            order.append(passage['source_id'])
        pieces.setdefault(passage['source_id'], []).append((passage['start'], passage['text']))
    return order, {sid: ''.join(text for _, text in sorted(parts)) for sid, parts in pieces.items()}


def plan(candidates: Path, context_path: Path, scope: str) -> dict:
    rows = read_jsonl(candidates)
    order, texts = paragraph_order(json.loads(context_path.read_text(encoding='utf-8')))
    position = {sid: index for index, sid in enumerate(order)}
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row['source_id'], text_hash(row['paragraph_text'])), []).append(row)
    paragraphs = []
    for (source_id, digest), clips in sorted(groups.items(), key=lambda item: (position.get(item[0][0], 1 << 30), item[0])):
        text = clips[0]['paragraph_text']
        if not needs_structure_analysis(text) or scope == 'character' and not any(
                clip['role'] == 'character' for clip in clips):
            continue
        at = position.get(source_id)
        before = order[max(0, at - NEIGHBOURS):at] if at is not None else []
        after = order[at + 1:at + 1 + NEIGHBOURS] if at is not None else []
        paragraphs.append({'key': f'{source_id}_{digest[:16]}', 'source_id': source_id,
                           'chapter_id': clips[0]['chapter_id'], 'text_sha256': digest, 'characters': len(text),
                           'clip_ids': sorted(clip['id'] for clip in clips),
                           'before_ids': [sid for sid in before if sid in texts],
                           'after_ids': [sid for sid in after if sid in texts]})
    return {'schema': 1, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'scope': scope,
            'candidates_sha256': text_hash(candidates.read_text(encoding='utf-8')), 'paragraphs': paragraphs,
            'totals': {'paragraphs': len(paragraphs), 'characters': sum(item['characters'] for item in paragraphs),
                       'clips': sum(len(item['clip_ids']) for item in paragraphs)}}


def payload_for(item: dict, paragraphs: dict[str, str], texts: dict[str, str]) -> dict:
    return {'segments': [{'id': item['key'], 'text': paragraphs[item['key']]}],
            'before': [{'id': sid, 'text': texts[sid]} for sid in item['before_ids']],
            'after': [{'id': sid, 'text': texts[sid]} for sid in item['after_ids']]}


def run(plan_doc: dict, candidates: Path, context_path: Path, answers: Path, provider: str, structured_call,
        limit: int | None = None) -> int:
    from rimsky.cli_analysis import structure
    rows = read_jsonl(candidates)
    if text_hash(candidates.read_text(encoding='utf-8')) != plan_doc['candidates_sha256']:
        raise SystemExit('Board candidates changed since the plan; make a new plan.')
    _, texts = paragraph_order(json.loads(context_path.read_text(encoding='utf-8')))
    paragraphs = {f"{row['source_id']}_{text_hash(row['paragraph_text'])[:16]}": row['paragraph_text'] for row in rows}
    done = {(item['key'], item['text_sha256']) for item in read_jsonl(answers)}
    answers.parent.mkdir(parents=True, exist_ok=True)
    sent = 0
    for item in plan_doc['paragraphs']:
        if (item['key'], item['text_sha256']) in done:
            continue
        if limit is not None and sent >= limit:
            break
        fixed = structure(payload_for(item, paragraphs, texts), structured_call)
        record = {'key': item['key'], 'source_id': item['source_id'], 'text_sha256': item['text_sha256'],
                  'provider': provider, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                  'chunks': [{'start': chunk['start'], 'end': chunk['end'], 'mode': chunk['mode']}
                             for chunk in fixed[item['key']]]}
        with answers.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        sent += 1
    return sent


def clip_modes(clip: dict, chunks: list[dict]) -> list[str]:
    text = clip['paragraph_text']
    modes = []
    for chunk in chunks:
        left, right = max(clip['text_start'], chunk['start']), min(clip['text_end'], chunk['end'])
        if left < right and text[left:right].strip(' \t\n—–-«»“”„"') and chunk['mode'] not in modes:
            modes.append(chunk['mode'])
    return modes


def human_speaker(decision: dict) -> str | None:
    if decision.get('speaker_verdict') == 'no':
        return decision.get('corrected_character_id') or None
    return 'confirmed' if decision.get('speaker_verdict') == 'yes' else None


def compare(plan_doc: dict, candidates: Path, answers: Path, review_path: Path) -> dict:
    rows = {row['id']: row for row in read_jsonl(candidates)}
    decisions = json.loads(review_path.read_text(encoding='utf-8')).get('decisions', {})
    saved = {(item['key'], item['text_sha256']): item for item in read_jsonl(answers)}
    overlay, counts, providers = [], Counter(), Counter()
    for item in plan_doc['paragraphs']:
        answer = saved.get((item['key'], item['text_sha256']))
        if answer is None:
            counts['paragraphs_unanswered'] += 1
            continue
        providers[answer['provider']] += 1
        for clip_id in item['clip_ids']:
            clip = rows[clip_id]
            modes = clip_modes(clip, answer['chunks'])
            decision = decisions.get(clip_id, {})
            split = bool((decision.get('split_review') or {}).get('enabled'))
            flag = ('mixed_in_one_clip' if len(modes) > 1 and not split
                    else 'possible_author_remark' if modes == ['narrator'] and clip['role'] == 'character'
                    else 'possible_speech' if modes == ['speech'] and clip['role'] != 'character' else '')
            if not flag:
                counts['consistent' if modes else 'no_lexical_overlap'] += 1
                continue
            human = human_speaker(decision)
            counts[flag] += 1
            counts['human_decided' if human else 'needs_listening'] += 1
            overlay.append({'clip_id': clip_id, 'source_id': clip['source_id'], 'chapter_id': clip['chapter_id'],
                            'flag': flag, 'structure_modes': modes, 'board_role': clip['role'],
                            'board_character_id': clip.get('character_id') or '',
                            'proposed_role': {'possible_author_remark': 'narrator', 'possible_speech': 'character',
                                              'mixed_in_one_clip': 'split'}[flag],
                            'human_speaker_decision': human, 'provider': answer['provider'],
                            'text': clip['source_text'].strip()})
    return {'schema': 1, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'kind': 'proposal_only',
            'scope': plan_doc['scope'], 'providers': dict(providers), 'counts': dict(counts), 'clips': overlay,
            'limits': 'Structure classification is a model proposal. It never changes saved speaker, voice or mood '
                      'decisions; passive playback acceptance does not clear these flags; listen before deciding.'}


def provider_call(name: str):
    if name == 'codex':
        from rimsky.codex_analysis import structured_call
    else:
        from rimsky.claude_analysis import structured_call
    return structured_call


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scope', choices=('all', 'character'), default='all',
                        help='all board paragraphs with dashes or quotes, or only those holding a character clip')
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--run', action='store_true', help='Send planned paragraphs to --provider (online)')
    parser.add_argument('--provider', choices=('codex', 'claude'))
    parser.add_argument('--limit', type=int, help='Send at most this many paragraphs in this run')
    parser.add_argument('--compare', action='store_true', help='Build the proposal overlay from saved answers')
    args = parser.parse_args()
    plan_path, answers = args.out / f'plan-{args.scope}.json', args.out / f'answers-{args.scope}.jsonl'
    if args.compare:
        report = compare(json.loads(plan_path.read_text(encoding='utf-8')), CANDIDATES, answers, REVIEW)
        path = args.out / f'overlay-{args.scope}.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(json.dumps({'overlay': str(path), 'counts': report['counts']}, ensure_ascii=False, indent=1))
        return
    if args.run:
        if not args.provider:
            raise SystemExit('Name the provider with --provider; book text will be sent to it.')
        sent = run(json.loads(plan_path.read_text(encoding='utf-8')), CANDIDATES, CONTEXT, answers,
                   args.provider, provider_call(args.provider), args.limit)
        print(json.dumps({'answers': str(answers), 'sent_now': sent}, ensure_ascii=False))
        return
    doc = plan(CANDIDATES, CONTEXT, args.scope)
    args.out.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    totals = doc['totals']
    print(json.dumps({'plan': str(plan_path), **totals,
                      'disclosure': f"--run would send {totals['paragraphs']} complete paragraphs "
                                    f"({totals['characters']} characters of book text) plus up to {NEIGHBOURS} "
                                    'neighbouring paragraphs each (400 characters) to the chosen online provider.'},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
