"""Assemble per-character delivery policies: statistics plus written decision instructions.

Combines character-policies.json (development statistics, frozen voices) with
the validated instruction files, and applies production rules that the
labelling history must not leak into: Calm versus Normal is never chosen by
book, chapter or labelling session, and any instruction line that depends on
those is listed and excluded from runtime prompts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_instruction_packets import validate_instructions  # noqa: E402

WORKFLOW = re.compile(r'\bBook\s*[123]\b|\bc00\d\d\b|\bchapter\s+c?\d', re.I)
RULES = {
    'baseline_register': ('Calm and Normal are baseline registers. Never choose between them by book, chapter or labelling '
                          'session: in the evidence, Book 1 Normal labels are mostly accepted suggestions while Book 2 '
                          'labels are active choices. The narrator\'s narration defaults to Calm; each character defaults '
                          'to their development baseline. Use the other register only where that character\'s instructions '
                          'give a textual reason.'),
    'departures': ('Depart from the baseline only when the text gives the reason the instructions describe. When a line '
                   'carries an explicit expressive signal (a threat, a plea, a shouted order, an emotional reaction or the '
                   'narrator naming the delivery) but the mood is uncertain, flag it for further analysis instead of '
                   'flattening it to the baseline.'),
    'per_sentence': 'Moods are judged sentence by sentence; neighbouring sentences in one speech can differ.',
    'attribution': 'The narrator\'s words around a quote (рассмеялся, посерьёзнел, перебил, шёпотом) are the strongest cue.',
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def excluded_lines(written: dict) -> list[dict]:
    found = []

    def check(where, text):
        if isinstance(text, str) and WORKFLOW.search(text):
            found.append({'where': where, 'text': text})
    check('summary', written.get('summary'))
    check('baseline.description', (written.get('baseline') or {}).get('description'))
    for entry in written.get('moods', []):
        for key in ('choose_when', 'avoid_when'):
            for n, line in enumerate(entry.get(key, [])):
                check(f"{entry['mood']}.{key}[{n}]", line)
        for n, versus in enumerate(entry.get('versus', [])):
            check(f"{entry['mood']}.versus[{n}]", versus.get('how_to_tell'))
    return found


def assemble(folder: Path) -> dict:
    stats = json.loads((folder / 'character-policies.json').read_text(encoding='utf-8'))
    performers = {}
    for path in sorted((folder / 'instructions').glob('*.json')):
        packet = json.loads((folder / 'instruction-packets' / path.name).read_text(encoding='utf-8'))
        written = json.loads(path.read_text(encoding='utf-8'))
        problems = validate_instructions(packet, written)
        if problems:
            raise SystemExit(f'{path.name} does not validate: {problems[:3]}')
        identity, route = written['identity'], written['route']
        route_stats = stats['performers'][identity]['routes'][route]
        baseline = 'calm' if identity == 'narrator' and route == 'narration' else route_stats['baseline_mood']
        performers.setdefault(identity, {})[route] = {
            'instructions_file': f'instructions/{path.name}', 'instructions_sha256': sha(path),
            'production_baseline': baseline, 'frozen_voice': route_stats['voice']['frozen'],
            'statistics': {key: route_stats[key] for key in ('development_choices', 'mood_distribution', 'evidence_limits')},
            'instructions': written, 'excluded_from_runtime_prompts': excluded_lines(written)}
    covered = {(who, route) for who, routes in performers.items() for route in routes}
    uncovered = [{'identity': who, 'route': route, 'development_choices': item['development_choices'],
                  'frozen_voice': item['voice']['frozen'], 'baseline': item['baseline_mood']}
                 for who, value in stats['performers'].items() for route, item in value['routes'].items() if (who, route) not in covered]
    return {'schema': 1, 'built_from': 'development chapters only', 'rules': RULES,
            'identities': stats['identities'], 'ambiguous_names': stats.get('ambiguous_names', {}),
            'performers': performers,
            'baseline_only_performers': sorted(uncovered, key=lambda item: -item['development_choices']),
            'inputs': {'character-policies.json': sha(folder / 'character-policies.json')}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    policies = assemble(folder)
    data = (json.dumps(policies, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
    (folder / 'delivery-policies.json').write_bytes(data)
    print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'performers_with_instructions': sum(len(r) for r in policies['performers'].values()),
                      'baseline_only': len(policies['baseline_only_performers']),
                      'excluded_lines': sum(len(r['excluded_from_runtime_prompts']) for v in policies['performers'].values() for r in v.values())},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
