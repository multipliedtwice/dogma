"""Combine the two disjoint Book 1 clip pools into one review queue."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'training-work/character-rb1'
SOURCES = [BASE / 'fine-tune-candidates', BASE / 'fine-tune-expanded']
OUTPUT = BASE / 'fine-tune-all'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    rows = []
    source_hashes = {}
    for folder in SOURCES:
        summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
        path = folder / 'candidates.jsonl'
        if digest(path) != summary['candidates_sha256']:
            raise ValueError(f'Changed candidate source: {folder}')
        source_hashes[folder.name] = summary['candidates_sha256']
        rows.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line)
    ids = [row['id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate candidate IDs')
    by_recording = defaultdict(list)
    for row in rows:
        by_recording[row['recording_id']].append(row)
    for recording, group in by_recording.items():
        group.sort(key=lambda row: row['audio_start'])
        for left, right in zip(group, group[1:]):
            if left['audio_end'] > right['audio_start'] + .02:
                raise ValueError(f'Overlapping candidates in {recording}')
    rows.sort(key=lambda row: (row['recording_id'], row['audio_start'], row['id']))
    OUTPUT.mkdir(exist_ok=True)
    target = OUTPUT / 'candidates.jsonl'
    target.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    summary = {'schema': 1, 'book': 'reality-benders-1',
               'purpose': 'whole-book review queue; no clips accepted for training',
               'source_hours': 11.23, 'sources_sha256': source_hashes,
               'candidates_sha256': digest(target), 'candidate_count': len(rows),
               'candidate_hours': round(sum(row['audio_end']-row['audio_start'] for row in rows)/3600, 2),
               'heldout_chapters': ['c0019', 'c0038'],
               'train_count': sum(row['split'] == 'train' for row in rows),
               'validation_count': sum(row['split'] == 'validation' for row in rows),
               'by_role': dict(Counter(row['role'] for row in rows)),
               'by_origin': dict(Counter(row['origin'] for row in rows)),
               'by_flag': dict(Counter(flag for row in rows for flag in row.get('flags', []))),
               'base_voice_unassigned': sum(row['base_voice_id_proposed'] is None for row in rows),
               'training_accepted': 0}
    (OUTPUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
