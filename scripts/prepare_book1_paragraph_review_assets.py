"""Expose immutable source MP3s and paragraph text to the Book 1 review UI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'training-work/corpus-rb1-current'
RECORDINGS = ROOT / 'training-work/character-rb1/voice-evidence-atlas/recordings'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', choices=('fine-tune-all','fine-tune-expanded','fine-tune-candidates'),
                        default='fine-tune-all')
    args = parser.parse_args()
    board = ROOT / 'training-work/character-rb1' / args.folder
    candidates = board / 'candidates.jsonl'
    summary = json.loads((board / 'summary.json').read_text(encoding='utf-8'))
    if digest(candidates) != summary['candidates_sha256']:
        raise ValueError('Candidate manifest changed')
    rows = [json.loads(line) for line in candidates.read_text(encoding='utf-8').splitlines() if line]
    source_ids = {row['source_id'] for row in rows}
    recording_ids = {row['recording_id'] for row in rows}
    corpus = json.loads((CORPUS / 'corpus.json').read_text(encoding='utf-8'))
    book = json.loads((CORPUS / corpus['books'][0]['manifest']).read_text(encoding='utf-8'))
    sources = {block['id']: {'text': block['text'], 'chapter_id': chapter['id'],
                             'chapter_title': chapter['title']}
               for chapter in book['chapters'] for block in chapter['blocks'] if block['id'] in source_ids}
    if source_ids != set(sources):
        raise ValueError('Missing source paragraphs')
    RECORDINGS.mkdir(exist_ok=True)
    recording_urls = {}
    for record in corpus['recordings']:
        if record['id'] not in recording_ids:
            continue
        source = CORPUS / record['path']
        target = RECORDINGS / f"{record['id']}.mp3"
        if target.exists():
            if not os.path.samefile(source, target):
                raise ValueError(f'Existing recording reference differs: {target}')
        else:
            os.link(source, target)
        recording_urls[record['id']] = '/' + target.relative_to(ROOT / 'training-work').as_posix()
    if recording_ids != set(recording_urls):
        raise ValueError('Missing source recordings')
    role_anchors = {}
    bank = json.loads((ROOT / 'training-work/character-rb1/provisional-voice-reference-bank.json').read_text(encoding='utf-8'))
    for item in bank['references']:
        references = sorted(item['candidate_references'], key=lambda ref: (bool(ref.get('flags')), -ref['duration']))
        if not references:
            continue
        path = Path(references[0]['audio'])
        if path.is_file() and path.is_relative_to(ROOT / 'training-work'):
            role_anchors[item['character_id']] = {
                'audio': '/' + path.relative_to(ROOT / 'training-work').as_posix(),
                'basis': 'provisional_source_excerpt'}
    atlas = ROOT / 'training-work/character-rb1/voice-evidence-atlas'
    cases = {case['id']: case for case in json.loads((atlas / 'manifest.json').read_text(encoding='utf-8'))['cases']}
    reviewed = json.loads((atlas / 'book1-review-import.json').read_text(encoding='utf-8'))
    for item in reviewed['reviewed_cases']:
        if item.get('verdict') != 'distinct' or not item.get('role_id'):
            continue
        case = cases.get(item['case_id'])
        if case and case.get('source_audio'):
            path = atlas / case['source_audio']
            if path.is_file():
                role_anchors[item['role_id']] = {
                    'audio': '/' + path.relative_to(ROOT / 'training-work').as_posix(),
                    'basis': 'listener_reviewed_source_excerpt'}
    output = {'schema': 1, 'candidates_sha256': summary['candidates_sha256'],
              'recordings': recording_urls, 'sources': sources, 'role_anchors': role_anchors}
    (board / 'paragraph-sources.json').write_text(json.dumps(output, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'paragraphs': len(sources), 'recordings': len(recording_urls)}))


if __name__ == '__main__':
    main()
