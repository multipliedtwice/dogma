from __future__ import annotations

import shutil
from pathlib import Path

from rimsky.books import MAX_UPLOAD, import_book
from rimsky.references import inspect_reference
from rimsky.util import AppError, bounded_text, digest, file_digest, require_id, write_json
from .common import checked_manifest, json_read, local_path, new_directory, owned_file, seal_json


def create_corpus(recipe_path: Path, output: Path):
    recipe = json_read(recipe_path)
    if not isinstance(recipe, dict) or recipe.get('schema') != 1:
        raise AppError('Corpus recipe must use schema 1.')
    narrator = recipe.get('narrator', {})
    if not isinstance(narrator, dict):
        raise AppError('One narrator is required per corpus.')
    narrator = {'id': require_id(narrator.get('id')), 'name': bounded_text(narrator.get('name'))}
    rights = recipe.get('rights', {})
    if not isinstance(rights, dict) or rights.get('training_authorized') is not True:
        raise AppError('Record the actual training authorization before importing a training corpus.')
    rights = {'training_authorized': True, 'basis': bounded_text(rights.get('basis'), 4000),
              'redistribution_authorized': rights.get('redistribution_authorized') is True}
    entries = recipe.get('books')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise AppError('Supply books with their audiobook recordings.')
    books, recordings = [], []
    book_ids, audio_ids, source_hashes, recording_hashes = set(), set(), set(), set()
    base = recipe_path.resolve().parent
    with new_directory(output) as stage:
        for entry in entries:
            if not isinstance(entry, dict):
                raise AppError('Invalid book recipe.')
            if entry.get('narrator_id', narrator['id']) != narrator['id']:
                raise AppError('Different narrators require separate corpora and training runs.')
            bid = require_id(entry.get('id'))
            if bid in book_ids:
                raise AppError('Duplicate book ID.')
            book_ids.add(bid)
            source = local_path(base / str(entry.get('path', '')))
            if source.stat().st_size > MAX_UPLOAD:
                raise AppError('Book exceeds the importer size limit.')
            source_hash = file_digest(source)
            if source_hash in source_hashes:
                raise AppError('The same book cannot occur twice in one corpus.')
            source_hashes.add(source_hash)
            raw = source.read_bytes()
            if digest(raw) != source_hash:
                raise AppError('Book changed while copying.')
            book = import_book(raw, source.name, stage / 'books')
            manifest_path = f'books/{book["id"]}/training-book.json'
            write_json(stage / manifest_path, book)
            books.append({'id': bid, 'sha256': source_hash, 'manifest': manifest_path,
                          'title': book['title'], 'language': book['language']})
            tracks = entry.get('recordings')
            if not isinstance(tracks, list) or not tracks:
                raise AppError('Every corpus book needs at least one recording.')
            for track in tracks:
                if not isinstance(track, dict):
                    raise AppError('Invalid recording recipe.')
                if track.get('narrator_id', narrator['id']) != narrator['id']:
                    raise AppError('A recording cannot override the corpus narrator.')
                rid = require_id(track.get('id'))
                if rid in audio_ids:
                    raise AppError('Recording IDs must be unique across the corpus.')
                audio_ids.add(rid)
                audio = local_path(base / str(track.get('path', '')))
                if audio.suffix.lower() not in ('.mp3', '.wav', '.flac', '.ogg', '.m4a'):
                    raise AppError('Unsupported training audio extension.')
                checksum = file_digest(audio)
                if checksum in recording_hashes:
                    raise AppError('Duplicate recordings would contaminate data splits.')
                recording_hashes.add(checksum)
                relative = f'audio/{rid}{audio.suffix.lower()}'
                target = stage / relative
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(audio, target)
                if file_digest(target) != checksum:
                    raise AppError('Recording changed while copying.')
                info = inspect_reference(target)
                recordings.append({'id': rid, 'book_id': bid, 'path': relative,
                                   'sha256': checksum, 'audio': info})
        result = seal_json(stage, 'corpus.json', {'schema': 1, 'narrator': narrator, 'rights': rights,
                                                  'books': books, 'recordings': recordings})
    return {'revision': result['revision'], 'narrator': narrator, 'books': len(books),
            'recordings': len(recordings), 'reviewed_clips': 0, 'models_run': False}


def load_corpus(root: Path):
    root = local_path(root, directory=True)
    return root, checked_manifest(root, 'corpus.json')


def select_recording(corpus, recording_id):
    recording = next((item for item in corpus['recordings'] if item['id'] == recording_id), None)
    if recording is None:
        raise AppError('Recording is not in the selected corpus.')
    return recording


def select_chapter(root, corpus, book_id, chapter_id):
    entry = next((item for item in corpus['books'] if item['id'] == book_id), None)
    if entry is None:
        raise AppError('Unknown corpus book.')
    book = json_read(owned_file(root, entry['manifest']))
    chapter = next((item for item in book['chapters'] if item['id'] == chapter_id), None)
    if chapter is None:
        raise AppError('Unknown chapter. Use the IDs from the prepared book manifest.')
    return book, chapter
