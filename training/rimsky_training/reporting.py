from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rimsky.util import AppError
from .common import checked_manifest, local_path, number, iter_rows


def dataset_report(path: Path):
    root = local_path(path, directory=True)
    dataset = checked_manifest(root, 'dataset.json')
    counts = {'train': dataset.get('training_clips'), 'validation': dataset.get('validation_clips')}
    groups = {name: {'count': 0, 'seconds': 0., 'books': set(), 'chapters': set(), 'delivery': defaultdict(float)} for name in counts}
    for row in iter_rows(root / 'provenance.jsonl'):
        split = row.get('split')
        if split not in groups:
            raise AppError('Dataset report contains an unrecognized partition.')
        group = groups[split]
        duration = number(row.get('decoded_audio', {}).get('duration'), 'decoded clip duration', .001, 31)
        group['count'] += 1
        group['seconds'] += duration
        group['books'].add(row['book_id'])
        group['chapters'].add((row['book_id'], row['chapter_id']))
        group['delivery'][str(row.get('delivery', 'unknown'))] += duration
    partitions = {}
    for name, group in groups.items():
        if not group['count'] or group['count'] != counts[name]:
            raise AppError('Dataset report has inconsistent reviewed split counts.')
        partitions[name] = {'clips': group['count'], 'decoded_seconds': round(group['seconds'], 6),
                            'decoded_hours': round(group['seconds'] / 3600, 6),
                            'books': len(group['books']), 'chapters': len(group['chapters']),
                            'delivery_seconds': {key: round(value, 6) for key, value in sorted(group['delivery'].items())}}
    return {'dataset_revision': dataset['revision'], 'narrator': dataset['narrator'],
            'performance': dataset['performance'], 'partitions': partitions,
            'scope': 'Reviewed data quantities, not a quality score or an assurance that training will improve speech.',
            'instruction_control_trained': False}
