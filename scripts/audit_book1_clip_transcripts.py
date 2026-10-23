"""Independently transcribe the *cut WAVs* in the Book 1 review pool.

The original quality screen compared two texts that shared the same source ASR
pass. This audit listens to each finished training asset instead. Results are
checkpointed so a long run can resume without changing review decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'training'))
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky_training.pairing import compound_tokens  # noqa: E402

FOLDER = ROOT / 'training-work/character-rb1/fine-tune-all'
CHECKPOINT = FOLDER / 'clip-asr-audit.jsonl'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def lexical(text: str) -> list[str]:
    return [word for word, _, _ in compound_tokens(text)]


def compare(expected: str, heard: str) -> dict:
    left, right = lexical(expected), lexical(heard)
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    edits = [{'kind': kind, 'expected': left[a:b], 'heard': right[c:d]}
             for kind, a, b, c, d in matcher.get_opcodes() if kind != 'equal']
    return {'ratio': round(matcher.ratio(), 4), 'expected_words': len(left),
            'heard_words': len(right), 'first_word_match': bool(left and right and left[0] == right[0]),
            'last_word_match': bool(left and right and left[-1] == right[-1]), 'edits': edits}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=0, help='Stop after this many new clips; zero means all')
    parser.add_argument('--sample', type=int, default=0, help='Evenly sample this many screen-pass clips')
    parser.add_argument('--screen-pass-only', action='store_true', help='Audit only clips shown by the old review board')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--max-padded-seconds', type=float, default=32,
                        help='Cap batch size times longest audio duration to avoid GPU OOM')
    parser.add_argument('--repairs', type=Path, help='Use recut WAVs in a versioned repair report')
    parser.add_argument('--checkpoint', type=Path)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DO_NOT_TRACK='1')
    manifest = FOLDER / 'candidates.jsonl'
    rows = [json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines() if line]
    quality = json.loads((FOLDER / 'quality-screen.json').read_text(encoding='utf-8'))
    if quality['candidates_sha256'] != digest(manifest):
        raise ValueError('Quality screen belongs to a different candidate manifest')
    if args.repairs:
        repairs = json.loads(args.repairs.read_text(encoding='utf-8'))
        if repairs['candidates_sha256'] != digest(manifest):
            raise ValueError('Repairs belong to a different candidate manifest')
        rows = [{**row, **repairs['clips'][row['id']]}
                for row in rows if repairs['clips'].get(row['id'], {}).get('status') == 'recut_unverified']
        args.checkpoint = args.checkpoint or args.repairs.parent / 'clip-asr-audit.jsonl'
    else:
        args.checkpoint = args.checkpoint or CHECKPOINT
    passed = [row for row in rows if quality['clips'][row['id']]['status'] == 'screen_pass']
    if args.sample:
        if args.sample >= len(passed):
            selected = passed
        else:
            selected = [passed[round(index * (len(passed) - 1) / (args.sample - 1))]
                        for index in range(args.sample)] if args.sample > 1 else passed[:1]
    elif args.screen_pass_only:
        selected = passed
    else:
        selected = rows
    completed = {}
    if args.checkpoint.is_file():
        for line in args.checkpoint.read_text(encoding='utf-8').splitlines():
            if line:
                result = json.loads(line)
                completed[result['id']] = result
    pending = [row for row in selected if row['id'] not in completed]
    pending.sort(key=lambda row: row['audio_end']-row['audio_start'])
    print(json.dumps({'selected': len(selected), 'completed': len(selected)-len(pending),
                      'pending': len(pending), 'checkpoint': str(args.checkpoint)}, ensure_ascii=True), flush=True)
    if not pending:
        return
    import torch
    from qwen_asr import Qwen3ASRModel
    device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    model = Qwen3ASRModel.from_pretrained(
        str(ROOT / 'training-work/models/Qwen3-ASR-0.6B'), device_map=device,
        dtype=torch.bfloat16 if device.startswith('cuda') else torch.float32,
        attn_implementation='sdpa', local_files_only=True)
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    if args.limit:
        pending = pending[:args.limit]
    batches = []
    batch = []
    for row in pending:
        duration = row['audio_end']-row['audio_start']
        if batch and (len(batch) >= args.batch_size or (len(batch)+1)*duration > args.max_padded_seconds):
            batches.append(batch)
            batch = []
        batch.append(row)
    if batch:
        batches.append(batch)
    with args.checkpoint.open('a', encoding='utf-8') as output:
        offset = 0
        for batch in batches:
            paths = [ROOT / row['audio'] for row in batch]
            for row, audio in zip(batch, paths):
                if digest(audio) != row['audio_sha256']:
                    raise ValueError(f'Changed clip: {row["id"]}')
            heard_batch = model.transcribe([str(audio) for audio in paths], language='Russian')
            if len(heard_batch) != len(batch):
                raise ValueError('ASR batch size mismatch')
            for index, (row, heard_item) in enumerate(zip(batch, heard_batch), offset + 1):
                heard = heard_item.text
                comparison = compare(row['source_text'], heard)
                result = {'id': row['id'], 'audio_sha256': row['audio_sha256'],
                          'expected': row['source_text'], 'heard': heard, **comparison}
                output.write(json.dumps(result, ensure_ascii=False) + '\n')
                output.flush()
                print(json.dumps({'index': index, 'id': row['id'],
                                  'ratio': comparison['ratio'],
                                  'first_word_match': comparison['first_word_match'],
                                  'last_word_match': comparison['last_word_match'],
                                  'edits': comparison['edits']}, ensure_ascii=True), flush=True)
            offset += len(batch)


if __name__ == '__main__':
    main()
