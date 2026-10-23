"""Fail closed if a listening draft can join audio inside a sentence."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.books import sentence_spans
from rimsky.store import Store
from render_book1_higgs_half import TAG, clean_spoken, PRONUNCIATION_HINTS


def audit(generation_id: str, prompts: Path, data: Path) -> dict:
    store = Store(data)
    try:
        generation = store.generation(generation_id)
        book = store.book(generation['book_id'])
        source = {block['id']: block['text'] for chapter in book['chapters']
                  for block in chapter['blocks']}
        by_source = defaultdict(list)
        problems = []
        for segment in generation['segments']:
            by_source[segment['source_id']].append(segment)
            path = prompts / segment['chapter_id'] / 'input' / f"{segment['id']}.txt"
            if not path.is_file():
                problems.append(f"{segment['id']}: missing prompt")
                continue
            prompt = path.read_text(encoding='utf-8')
            if len(prompt) >= 4096:
                problems.append(f"{segment['id']}: backend would split prompt")
            plain = TAG.sub('', prompt)
            for spelling, hint in PRONUNCIATION_HINTS.items():
                plain = plain.replace(hint, spelling)
            normal = lambda value: re.sub(r'\s+', ' ', value).strip()
            if normal(plain) != normal(clean_spoken(segment['spoken'])):
                problems.append(f"{segment['id']}: prompt changed source words")
        for source_id, rows in by_source.items():
            original = source[source_id]
            if ''.join(row['text'] for row in rows) != original:
                problems.append(f'{source_id}: source coverage changed')
            edges = {0, len(original)} | {edge for span in sentence_spans(original) for edge in span}
            for row in rows:
                if row['start'] not in edges or row['end'] not in edges:
                    problems.append(f"{row['id']}: mid-sentence audio seam")
                if row['text'] != original[row['start']:row['end']]:
                    problems.append(f"{row['id']}: incorrect source offsets")
        return {'generation_id': generation_id, 'segments': len(generation['segments']),
                'sources': len(by_source), 'max_prompt_chars': max((len(path.read_text(encoding='utf-8'))
                    for path in prompts.glob('c*/input/*.txt')), default=0),
                'problems': problems}
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('generation_id')
    parser.add_argument('--prompts', type=Path, default=ROOT / 'training-work/higgs-tts-3-pilot/book1-half-sentence')
    parser.add_argument('--data', type=Path, default=ROOT / 'training-work/book1-listening-app')
    args = parser.parse_args()
    result = audit(args.generation_id, args.prompts, args.data)
    print(json.dumps({**result, 'problems': result['problems'][:20]}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['problems']))
