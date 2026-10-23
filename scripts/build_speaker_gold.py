"""Freeze or verify the independently reviewed speaker-resolution gold set."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'training-work/character-rb1/reviewed-character-analysis.json'
CAST = ROOT / 'training-work/character-rb1/benchmark/reviewed_cast_v3.json'
BOOK = (ROOT / 'training-work/corpus-rb1-current/books/'
        '676d6ad200230b7cfde56a63fd1d8d28/training-book.json')
OUTPUT = ROOT / 'manual-labeling/speaker-resolution-gold-v1'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def build() -> dict:
    review = json.loads(REVIEW.read_text(encoding='utf-8'))
    cast_doc = json.loads(CAST.read_text(encoding='utf-8'))
    book = json.loads(BOOK.read_text(encoding='utf-8'))
    if sha256(CAST) != review['cast_sha256']:
        raise ValueError('Reviewed cast no longer matches the human review.')
    chapters = {chapter['id']: chapter for chapter in book['chapters']}
    blocks = {block['id']: (chapter, index, block)
              for chapter in book['chapters']
              for index, block in enumerate(chapter['blocks'])}
    cases = []
    speech_spans = 0
    for reviewed in review['passages']:
        chapter, index, block = blocks[reviewed['source_id']]
        text = block['text']
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != reviewed['source_sha256']:
            raise ValueError(f'Reviewed source text changed: {reviewed["source_id"]}')
        parts = []
        cursor = 0
        for part in reviewed['parts']:
            if part['start'] != cursor or not part['start'] < part['end'] <= len(text):
                raise ValueError(f'Invalid reviewed coverage: {reviewed["source_id"]}')
            if part['mode'] not in ('speech', 'narrator'):
                raise ValueError(f'Invalid reviewed mode: {reviewed["source_id"]}')
            if part['mode'] == 'speech':
                speech_spans += 1
            parts.append({**part, 'text': text[part['start']:part['end']]})
            cursor = part['end']
        if cursor != len(text):
            raise ValueError(f'Incomplete reviewed coverage: {reviewed["source_id"]}')
        readable = [item for item in chapter['blocks'] if item.get('text')]
        readable_index = next(i for i, item in enumerate(readable)
                              if item['id'] == block['id'])
        context = lambda items: [
            {'id': item['id'], 'source_id': item['id'], 'text': item['text']}
            for item in items]
        cases.append({
            'id': reviewed['source_id'],
            'book_id': 'reality-benders-1',
            'chapter_id': chapter['id'],
            'chapter_title': chapter['title'],
            'source_id': reviewed['source_id'],
            'source_sha256': reviewed['source_sha256'],
            'text': text,
            'before': context(readable[max(0, readable_index - 2):readable_index]),
            'after': context(readable[readable_index + 1:readable_index + 3]),
            'parts': parts,
            'review_type': reviewed.get('review_type', ''),
            'review_note': reviewed.get('note', ''),
            'review_evidence': reviewed.get('evidence', []),
        })
    payload = {
        'schema': 1,
        'name': 'Reality Benders speaker resolution gold v1',
        'frozen_at': datetime.now(timezone.utc).isoformat(),
        'policy': {
            'labels': 'Independent manual text-side decisions only.',
            'book1': 'All exact reviewed speaker parts are scored.',
            'book2': ('Not scored until the stratified sample receives independent '
                      'human speaker decisions.'),
            'tuning': 'Escalation rules must not be tuned against this set before scoring.',
        },
        'sources': {
            'review': {'path': str(REVIEW.relative_to(ROOT)), 'sha256': sha256(REVIEW)},
            'cast': {'path': str(CAST.relative_to(ROOT)), 'sha256': sha256(CAST)},
            'book': {'path': str(BOOK.relative_to(ROOT)), 'sha256': sha256(BOOK),
                     'source_hash': book['source_hash']},
        },
        'counts': {'cases': len(cases), 'speech_spans': speech_spans,
                   'characters': len(cast_doc['characters'])},
        'cast': cast_doc['characters'],
        'cases': cases,
    }
    payload['content_sha256'] = encoded_hash({
        key: value for key, value in payload.items()
        if key not in ('frozen_at', 'content_sha256')})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    target = OUTPUT / 'gold.json'
    if args.verify:
        if not target.is_file():
            raise FileNotFoundError(target)
        saved = json.loads(target.read_text(encoding='utf-8'))
        rebuilt = build()
        for key in ('frozen_at',):
            rebuilt[key] = saved.get(key)
        if saved != rebuilt:
            raise ValueError('Frozen speaker gold set differs from its reviewed sources.')
        print(json.dumps({'verified': True, **saved['counts'],
                          'content_sha256': saved['content_sha256']}, indent=2))
        return
    if target.exists():
        raise FileExistsError('Gold set is already frozen; use --verify.')
    payload = build()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    manifest = {'schema': 1, 'file': 'gold.json', 'sha256': sha256(target),
                'content_sha256': payload['content_sha256'], **payload['counts']}
    (OUTPUT / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'frozen': True, **manifest}, indent=2))


if __name__ == '__main__':
    main()
