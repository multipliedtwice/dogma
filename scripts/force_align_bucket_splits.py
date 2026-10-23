"""Force-align dash, placeholder and aside clips without reliable local anchors."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', type=Path, default=ROOT / 'training-work/character-rb2-full/fine-tune-all')
    parser.add_argument('--source', type=Path, default=ROOT / 'training-work/character-rb2/full')
    parser.add_argument('--plan', type=Path,
                        help='Use a prepared plan before publishing it to the board.')
    parser.add_argument('--model', type=Path, default=ROOT / 'training-work/models/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--device', default='cuda:0')
    args = parser.parse_args()

    candidate_hash = sha(args.board / 'candidates.jsonl')
    split_plan = json.loads((args.plan or args.board / 'split-plan.json').read_text(encoding='utf-8'))
    if split_plan.get('candidates_sha256') != candidate_hash:
        raise ValueError('Split plan belongs to a different candidate set')
    needed = split_plan.get('evaluation', {}).get('unresolved_for_forced_alignment', [])
    rows = {row['id']: row for row in
            (json.loads(line) for line in (args.board / 'candidates.jsonl').read_text(encoding='utf-8').splitlines())}
    output = args.source / 'forced-split-alignments.json'
    if output.exists():
        document = json.loads(output.read_text(encoding='utf-8'))
        if document.get('schema') != 1 or document.get('candidates_sha256') != candidate_hash:
            raise ValueError('Existing forced alignments belong to a different candidate set')
    else:
        document = {'schema': 1, 'candidates_sha256': candidate_hash,
                    'model': str(args.model.relative_to(ROOT)).replace('\\', '/'),
                    'clips': {}, 'errors': {}}
    pending = [clip_id for clip_id in needed if clip_id not in document['clips']]
    if not pending:
        print(f'No forced split alignments pending; {len(document["clips"])} cached.')
        return

    import torch
    from qwen_asr import Qwen3ForcedAligner
    dtype = torch.float32 if args.device == 'cpu' else torch.float16
    aligner = Qwen3ForcedAligner.from_pretrained(
        str(args.model), device_map=args.device, dtype=dtype, local_files_only=True)
    for index, clip_id in enumerate(pending, 1):
        row = rows[clip_id]
        audio = ROOT / row['exact_audio']
        if sha(audio) != row['audio_sha256']:
            raise ValueError(f'Clip audio changed: {clip_id}')
        try:
            result = aligner.align(audio=str(audio), text=row['source_text'], language='Russian')[0]
            items = [{'text': item.text, 'start': float(item.start_time), 'end': float(item.end_time)}
                     for item in result.items]
            document['clips'][clip_id] = {'audio_sha256': row['audio_sha256'], 'items': items,
                                          'method': 'Qwen3-ForcedAligner-0.6B',
                                          'aligned_at': datetime.now(timezone.utc).isoformat()}
            document['errors'].pop(clip_id, None)
        except Exception as error:
            document['errors'][clip_id] = str(error)
        save(output, document)
        print(f'{index}/{len(pending)} {clip_id}', flush=True)
    print(f'Forced alignments: {len(document["clips"])}; errors: {len(document["errors"])}')


if __name__ == '__main__':
    main()
