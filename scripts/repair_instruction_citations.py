"""Repair written delivery instructions after their evidence packets were rebuilt.

Removes citations that are no longer in the packet, refreshes each mood's
support from the packet, and moves a mood to sparse_moods when it has fewer
than 3 choices or no remaining example. Every change is recorded in the file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def repair(packet: dict, written: dict) -> list[str]:
    allowed = {item['id'] for items in packet['examples'].values() for item in items}
    counts = {mood: item['count'] for mood, item in packet['mood_counts'].items()}
    changes, kept = [], []
    baseline = written.get('baseline') or {}
    lost = [item for item in baseline.get('evidence_ids', []) if item not in allowed]
    if lost:
        baseline['evidence_ids'] = [item for item in baseline['evidence_ids'] if item in allowed]
        changes.append(f'baseline: removed evidence {lost}')
    for entry in written.get('moods', []):
        mood = entry.get('mood')
        for key in ('examples', 'counterexamples'):
            gone = [item['id'] for item in entry.get(key, []) if item.get('id') not in allowed]
            if gone:
                entry[key] = [item for item in entry[key] if item.get('id') in allowed]
                changes.append(f'{mood}: removed {key} {gone}')
        if entry.get('support') != counts.get(mood, 0):
            changes.append(f"{mood}: support {entry.get('support')} -> {counts.get(mood, 0)}")
            entry['support'] = counts.get(mood, 0)
        if counts.get(mood, 0) < 3 or not entry.get('examples'):
            written.setdefault('sparse_moods', []).append(
                {'mood': mood, 'count': counts.get(mood, 0),
                 'note': 'Moved from a full entry: its evidence was an unresolved split part (prefilled voice) or '
                         'too few development choices remained after the rebuild. Former guidance: '
                         + '; '.join(entry.get('choose_when', [])[:2])})
            changes.append(f'{mood}: moved to sparse_moods')
            continue
        kept.append(entry)
    written['moods'] = kept
    known = {item['mood'] for item in written.get('sparse_moods', [])} | {item['mood'] for item in kept}
    for mood, count in counts.items():
        if mood not in known:
            written['sparse_moods'].append({'mood': mood, 'count': count, 'note': 'Present after the rebuild; no guidance written yet.'})
            changes.append(f'{mood}: added to sparse_moods')
    if changes:
        written.setdefault('repairs', []).extend(changes)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    report = {}
    for path in sorted((args.folder / 'instructions').glob('*.json')):
        packet_path = args.folder / 'instruction-packets' / path.name
        if not packet_path.is_file():
            report[path.name] = ['packet no longer exists']
            continue
        written = json.loads(path.read_text(encoding='utf-8'))
        changes = repair(json.loads(packet_path.read_text(encoding='utf-8')), written)
        if changes:
            path.write_bytes((json.dumps(written, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
        report[path.name] = changes
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
