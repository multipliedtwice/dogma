"""Reclaim superseded playback encodings while retaining current and in-use files."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import time
from .util import AppError

KEY = re.compile('[a-f0-9]{64}')
GRACE_SECONDS = 30 * 60


@contextmanager
def reading(manager, key):
    with manager.condition:
        manager.cache_readers[key] = manager.cache_readers.get(key, 0) + 1
        path = manager.folder / (key + '.json')
        if path.is_file():
            os.utime(path, None)
    try:
        yield
    finally:
        with manager.condition:
            count = manager.cache_readers[key] - 1
            if count:
                manager.cache_readers[key] = count
            else:
                manager.cache_readers.pop(key, None)


def collect(manager, *, execute=True, grace_seconds=GRACE_SECONDS):
    """Only managed hash-named encodings/maps; never source PCM or encoder scratch.

    Rebuild the most recently requested plan per current book rather than
    treating every old volume/settings map as permanent. Grace covers clients;
    active reads and compression jobs are protected independently of that age.
    """
    cutoff = time.time() - grace_seconds
    result = {'files': 0, 'bytes': 0, 'paths': []}
    with manager.condition:
        kept = set(manager.cache_readers)
        kept.update(task['key'] for task in manager.queue)
        if manager.active:
            kept.add(manager.active['key'])
        references, latest, plans = [], {}, {}
        for path in (manager.folder / 'maps').glob('*.json'):
            try:
                value = json.loads(path.read_text('utf8'))
                context = (value['generation_id'], value['music'], value['music_volume'])
                with manager.store.lock:
                    rows = manager.store.rows('SELECT book_id FROM generations WHERE id=?', (context[0],))
                    current = manager.store.current_generation_id(rows[0]['book_id']) if rows else None
                if current == context[0]:
                    previous = latest.get(current)
                    if previous is None or path.stat().st_mtime_ns > previous[0]:
                        latest[current] = (path.stat().st_mtime_ns, context)
                references.append((path, value, context))
            except (OSError, ValueError, KeyError, TypeError, AppError):
                # Unknown or damaged metadata is diagnosed by playback, not
                # used as authority for destructive cleanup.
                return {**result, 'deferred': 'Invalid playback map'}
        try:
            for _, context in latest.values():
                plans[context] = manager._plan(*context)
                kept.update(chunk['key'] for chunk in plans[context]['chunks'])
        except (AppError, OSError, ValueError, KeyError, TypeError):
            return {**result, 'deferred': 'Current playback plan is unavailable'}
        for path, value, context in references:
            plan = plans.get(context)
            if (plan and value.get('key') == plan['key']) or path.stat().st_mtime >= cutoff:
                kept.update(value.get('chunks', []))
                continue
            result['paths'].append(str(path))
        for path in manager.folder.iterdir():
            key = path.stem if path.is_file() else path.name
            if not KEY.fullmatch(key) or key in kept:
                continue
            companions = [manager.folder / (key + suffix) for suffix in ('.json', '.mp3')]
            directory = manager.folder / key
            if directory.is_dir():
                companions.append(directory)
            existing = [item for item in companions if item.exists()]
            if not existing or max(item.stat().st_mtime for item in existing) >= cutoff:
                continue
            result['paths'].extend(str(item) for item in existing)
        # A key has a metadata file and potentially a directory, visited twice.
        result['paths'] = sorted(set(result['paths']))
        root = manager.folder.resolve()
        for value in result['paths']:
            path = Path(value)
            if (not path.resolve().is_relative_to(root) or path.is_symlink()
                    or getattr(path, 'is_junction', lambda: False)()):
                raise ValueError('Playback cleanup target escapes its managed cache')
            entries = list(path.rglob('*')) if path.is_dir() else [path]
            if any(item.is_symlink() or getattr(item, 'is_junction', lambda: False)() for item in entries):
                raise ValueError('Playback cleanup does not follow linked paths')
            result['files'] += sum(item.is_file() for item in entries)
            result['bytes'] += sum(item.stat().st_size for item in entries if item.is_file())
            if execute:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
    return result
