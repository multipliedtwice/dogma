"""Split unrendered long passages in the paused book 1 listening draft."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.books import split_text
from rimsky.integrity import validate_coverage
from rimsky.store import Store
from rimsky.util import AppError, now


def refine(data_dir: Path, generation_id: str, limit: int = 420) -> tuple[int, int]:
    store = Store(data_dir)
    try:
        generation = store.generation(generation_id)
        if generation['status'] != 'paused' or not generation['analysis'].get('provisional'):
            raise AppError('Only a paused provisional listening draft can be refined.')
        book = store.book(generation['book_id'])
        replacements = []
        simulated = []
        for segment in generation['segments']:
            if segment['status'] != 'pending' or len(segment['text']) <= limit:
                simulated.append(segment)
                continue
            if segment['text'] != segment['spoken']:
                raise AppError('A long passage has edited speech; review it before splitting.')
            parts = split_text(segment['text'], limit)
            if len(parts) >= 100:
                raise AppError('A passage has too many speech pieces for its reserved order range.')
            children = []
            for index, (relative_start, relative_end, text) in enumerate(parts):
                start = segment['start'] + relative_start
                end = segment['start'] + relative_end
                child = {**segment, 'id': f"{segment['source_id']}-{start}-{end}",
                         'ordinal': segment['ordinal'] + index, 'start': start, 'end': end,
                         'text': text, 'spoken': text, 'status': 'pending',
                         'audio_hash': None, 'duration': 0, 'cues': []}
                children.append(child)
            replacements.append((segment, children))
            simulated.extend(children)
        validate_coverage(book, generation['config']['chapters'], simulated)
        if replacements:
            with store.lock, store.db:
                for original, children in replacements:
                    store.db.execute('DELETE FROM segments WHERE generation_id=? AND id=?',
                                     (generation_id, original['id']))
                    store.db.executemany(
                        'INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [(generation_id, child['id'], child['ordinal'], child['chapter_id'],
                          child['source_id'], child['start'], child['end'], child['text'],
                          child['spoken'], child['speaker'], child['delivery'], 'pending', None,
                          0, '[]', child['warning']) for child in children])
                store.db.execute('UPDATE generations SET updated=? WHERE id=?',
                                 (now(), generation_id))
        result = store.generation(generation_id)
        validate_coverage(book, generation['config']['chapters'], result['segments'])
        return len(replacements), len(result['segments'])
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('generation_id')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'training-work/book1-listening-app')
    args = parser.parse_args()
    changed, total = refine(args.data_dir, args.generation_id)
    print(f'Split {changed} pending long passages; {total} total passages now cover the book.')
