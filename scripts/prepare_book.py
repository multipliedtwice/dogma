from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.books import MAX_UPLOAD, import_book
from rimsky.preparation import speech_sources
from rimsky.util import AppError, write_json


def prepare(source: Path, destination: Path | None = None) -> dict:
    if not source.is_file() or source.stat().st_size > MAX_UPLOAD:
        raise AppError('Choose an existing EPUB/FB2 file of at most 50 MiB.')
    with source.open('rb') as handle:
        raw = handle.read(MAX_UPLOAD + 1)
    parent = None
    if destination is not None:
        destination = destination.absolute()
        if destination.exists() or destination.is_symlink():
            raise AppError('The preparation output directory already exists; nothing was overwritten.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent = destination.parent
    with tempfile.TemporaryDirectory(prefix='.rimsky-prepare-', dir=parent) as temporary:
        staging = Path(temporary) / 'bundle'
        staging.mkdir()
        book = import_book(raw, source.name, staging / 'books')
        report = book['preparation']
        report = {**report, 'book': {'id': book['id'], 'title': book['title'], 'author': book['author'],
                                   'language': book['language'], 'format': book['format']}}
        if destination is not None:
            write_json(staging / 'book.json', book)
            write_json(staging / 'preparation.json', report)
            with (staging / 'speech-inputs.jsonl').open('w', encoding='utf-8') as handle:
                for segment in speech_sources(book, report['default_chapters']):
                    handle.write(json.dumps(segment, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            destination.mkdir()
            try:
                for child in staging.iterdir():
                    child.rename(destination / child.name)
            except BaseException:
                shutil.rmtree(destination, ignore_errors=True)
                raise
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare a local EPUB/FB2 for narration without models or network access.')
    parser.add_argument('book', type=Path)
    parser.add_argument('--output', type=Path, help='Create a new directory with source, assets, manifest and exact narration inputs.')
    args = parser.parse_args()
    try:
        report = prepare(args.book, args.output)
    except (AppError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    # Keep redirected output decodable through legacy Windows console code
    # pages. JSON escapes preserve the exact Unicode data.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
