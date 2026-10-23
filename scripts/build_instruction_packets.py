"""Development-only evidence packets for writing per-character delivery instructions.

One packet per identity and presentation route with enough development choices.
Packets never contain sealed test chapters; validate_instructions() checks that
written instructions cite only IDs from their own packet and only known moods.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_mood_rulebook import ENTRIES, display_names  # noqa: E402

MIN_CHOICES = 15
PER_MOOD = 20
PER_BASELINE = 30


def slug(identity: str, route: str) -> str:
    return hashlib.sha256(f'{identity}|{route}'.encode()).hexdigest()[:10] + f'-{route}'


def trim(text, head: bool) -> str:
    text = (text or '').strip()
    return text[-300:] if head else text[:200]


def packets(folder: Path) -> dict:
    sealed = json.loads((folder / 'split.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (folder / 'assignment-evidence.jsonl').read_text(encoding='utf-8').splitlines() if line]
    development = [row for row in rows if row['chapter_id'] in sealed['books'][row['book']]['development_chapters']]
    names = display_names()
    groups = defaultdict(list)
    for row in development:
        groups[(row['identity'], row['route'])].append(row)
    built = {}
    for (identity, route), items in sorted(groups.items(), key=lambda item: -len(item[1])):
        if len(items) < MIN_CHOICES or identity == 'unresolved_part' or identity == 'unknown' and route != 'mechanical':
            continue
        moods = Counter(row['mood'] for row in items)
        examples = {}
        for mood, count in moods.most_common():
            chosen = sorted((row for row in items if row['mood'] == mood), key=lambda row: hashlib.sha256(row['id'].encode()).hexdigest())
            limit = PER_BASELINE if mood in ('calm', 'neutral') else PER_MOOD
            examples[mood] = [{'id': row['id'], 'book': row['book'][-6:].replace('ders-1', 'Book 1').replace('2-full', 'Book 2'),
                               'text': row['text'].strip(), 'before': trim(row['before'], True), 'after': trim(row['after'], False),
                               'paragraph': row['paragraph'] if row['paragraph'] and row['paragraph'].strip() != row['text'].strip() else ''}
                              for row in chosen[:limit]]
        built[slug(identity, route)] = {
            'identity': identity, 'route': route, 'development_choices': len(items),
            'mood_counts': {mood: {'count': n, 'name': names.get(mood, mood)} for mood, n in moods.most_common()},
            'definitions': {mood: {'name': names.get(mood, mood), 'meaning': ENTRIES[mood][0], 'nearest': ENTRIES[mood][1],
                                   'boundary': ENTRIES[mood][2]} for mood in moods},
            'examples': examples}
    return built


def validate_instructions(packet: dict, written: dict) -> list[str]:
    allowed = {item['id'] for items in packet['examples'].values() for item in items}
    labelled = {item['id']: mood for mood, items in packet['examples'].items() for item in items}
    problems = []
    if written.get('identity') != packet['identity'] or written.get('route') != packet['route']:
        problems.append('identity or route does not match the packet')
    for entry in written.get('moods', []):
        if entry.get('mood') not in ENTRIES:
            problems.append(f"unknown mood {entry.get('mood')}")
        for item in entry.get('examples', []):
            if item.get('id') not in allowed:
                problems.append(f"{entry.get('mood')}: example {item.get('id')} is not in this packet")
            elif labelled[item['id']] != entry.get('mood'):
                problems.append(f"{entry.get('mood')}: example {item['id']} is labelled {labelled[item['id']]}")
        for item in entry.get('counterexamples', []):
            if item.get('id') not in allowed:
                problems.append(f"{entry.get('mood')}: counterexample {item.get('id')} is not in this packet")
            elif labelled[item['id']] == entry.get('mood'):
                problems.append(f"{entry.get('mood')}: counterexample {item['id']} has the same mood")
        for versus in entry.get('versus', []):
            if versus.get('mood') not in ENTRIES:
                problems.append(f"{entry.get('mood')}: unknown competing mood {versus.get('mood')}")
    for item in (written.get('baseline') or {}).get('evidence_ids', []):
        if item not in allowed:
            problems.append(f'baseline evidence {item} is not in this packet')
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path)
    parser.add_argument('--validate', action='store_true', help='Check written instructions against their packets')
    args = parser.parse_args()
    folder = args.folder.resolve()
    target = folder / 'instruction-packets'
    if args.validate:
        report = {}
        for path in sorted((folder / 'instructions').glob('*.json')):
            packet = json.loads((target / path.name).read_text(encoding='utf-8'))
            report[path.name] = validate_instructions(packet, json.loads(path.read_text(encoding='utf-8')))
        print(json.dumps(report, ensure_ascii=False, indent=1))
        sys.exit(1 if any(report.values()) else 0)
    target.mkdir(exist_ok=True)
    built = packets(folder)
    for name, packet in built.items():
        (target / f'{name}.json').write_bytes((json.dumps(packet, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
    print(json.dumps({name: [p['identity'], p['route'], p['development_choices'], len(p['mood_counts']),
                             round((target / f'{name}.json').stat().st_size / 1024)] for name, p in built.items()},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
