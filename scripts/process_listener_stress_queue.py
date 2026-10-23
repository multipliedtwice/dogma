"""Render and publish queued listener stress corrections in listening priority order."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.spoken_pronunciation import ACUTE, unmarked, validate_marks
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.util import AppError, file_digest, now, write_json


WORD = re.compile(r'[А-Яа-яЁё́]+(?:[-‐‑][А-Яа-яЁё́]+)*')
LETTER = 'А-Яа-яЁё́'


def read(path):
    return json.loads(Path(path).read_text('utf8'))


def _same_target(left, right):
    a = left.get('report') or {}
    b = right.get('report') or {}
    fields = ('generation_id', 'segment_id')
    corrections = lambda row: row.get('corrections') or [{key: row.get(key) for key in
                                                           ('selected_text', 'intended_stress', 'text_offset')}]
    return all(a.get(key) == b.get(key) for key in fields) and corrections(a) == corrections(b)


def reuse_applied(folder, item_path, item):
    """Late queue duplicates reuse an already installed identical correction."""
    for path in (folder / 'listener-stress-queue').glob('*.json'):
        if path == item_path:
            continue
        with contextlib.suppress(OSError, ValueError, TypeError):
            prior = read(path)
            if prior.get('status') != 'applied' or not _same_target(prior, item):
                continue
            registry = read(folder / 'spoken-take-revisions.json')['units'][item['report']['segment_id']]
            item.update(status='applied', updated=now(), completed=now(), error='',
                        reused_from=prior['id'], instruction=prior.get('instruction'),
                        instruction_sha256=prior.get('instruction_sha256'),
                        replacement_audio_sha256=registry['replacement']['receipt']['audio_sha256'],
                        human_audio_approval=False)
            write_json(item_path, item)
            return True
    return False


def next_seed_offset(folder, item_path, item):
    """Continue after bounded failed seeds without rewriting their evidence."""
    next_offset = int(item.get('seed_offset', 0))
    queue = folder / 'listener-stress-queue'
    for path in queue.glob('*.json'):
        if path == item_path:
            continue
        with contextlib.suppress(OSError, ValueError, TypeError):
            prior = read(path)
            if prior.get('status') != 'held' or not _same_target(prior, item):
                continue
            instruction = queue / 'instructions' / f"{prior['id']}.json"
            if not instruction.is_file():
                continue
            base = int(read(instruction).get('seed_offset', 0))
            report = folder / 'pronunciation-retry-reports' / f'{file_digest(instruction)}.json'
            attempts = read(report).get('attempts', []) if report.is_file() else []
            if attempts and all(row.get('status') == 'held' for row in attempts):
                next_offset = max(next_offset, base + len(attempts))
    if not 0 <= next_offset <= 1000:
        raise AppError('Listener stress retry exhausted the supported seed range.')
    return next_offset


def _selection_matches(text, selected):
    """Return original-string spans while matching against its unmarked text."""
    positions=[]
    plain=[]
    for index, character in enumerate(text):
        if character == ACUTE:
            continue
        positions.append(index)
        plain.append(character)
    pattern = re.compile(rf'(?<![{LETTER}]){re.escape(selected)}(?![{LETTER}])')
    result=[]
    for match in pattern.finditer(''.join(plain)):
        start=positions[match.start()]
        end=positions[match.end()-1]+1
        while end < len(text) and text[end] == ACUTE:
            end += 1
        result.append((start,end))
    return result


def _locked(path, attempts=21):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    for attempt in range(attempts):
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            if attempt + 1 == attempts:
                handle.close()
                return None
            time.sleep(.1)


def _unlock(handle):
    if handle is None:
        return
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


def _selected_take(folder, unit_id):
    registry = folder / 'spoken-take-revisions.json'
    if registry.exists():
        previous = read(registry).get('units', {}).get(unit_id)
        # A candidate is recorded before the chapter PCM transaction so its
        # evidence survives an install failure.  It becomes selectable only
        # after the exact sentence patch writes its installation proof.
        applied = folder / 'pronunciation-revisions' / unit_id / 'applied.json'
        if previous and applied.is_file():
            return previous['replacement'], True
    matches = []
    for path in folder.glob('c*/chunk-*/**/selected.json'):
        rows = read(path)
        if isinstance(rows, dict) and unit_id in rows:
            matches.append(rows[unit_id])
    if not matches:
        raise AppError(f'The published take for {unit_id} was not found.')
    keys = {(row['receipt']['key'], row['receipt']['audio_sha256']) for row in matches}
    if len(keys) != 1:
        raise AppError(f'Multiple different published takes were found for {unit_id}.')
    return matches[0], False


def stress_native_text(before, report):
    """Apply one or more source-bound marks to one synthesis unit."""
    corrections = report.get('corrections') or [{key: report.get(key) for key in
                                                  ('selected_text', 'intended_stress', 'text_offset')}]
    unit = before['unit']
    source = unit['text']
    # Edit the exact marked input that made the heard audio. Display `spoken`
    # text intentionally omits stress marks and would silently discard all
    # unrelated automatic pronunciation decisions in this sentence.
    spoken = unit['synthesis']['text']
    edits = []
    for correction in corrections:
        selected = correction['selected_text']
        intended = correction['intended_stress']
        if not isinstance(correction.get('text_offset'), int):
            raise AppError('The queued stress word has no exact source-text offset.')
        validate_marks(intended)
        if unmarked(intended) != selected or intended.count(ACUTE) != 1:
            raise AppError('The queued stress choice changes the selected source word.')
        local = correction['text_offset'] - unit['start']
        # A listener may select the lexical component of a compound name
        # (Хакону inside Ар-Хакону). Hyphens are component boundaries; letters
        # are not, so a selection can never bind inside an unrelated word.
        source_matches = _selection_matches(source,selected)
        source_index = next((index for index, match in enumerate(source_matches)
                             if match[0] == local), None)
        if source_index is None:
            raise AppError('The queued stress word is not bound to this synthesis unit.')
        spoken_matches = _selection_matches(spoken,selected)
        if source_index >= len(spoken_matches):
            raise AppError('The selected source word has no unambiguous spoken-text occurrence.')
        target = spoken_matches[source_index]
        edits.append((target[0], target[1], intended))
    native = spoken
    for start, end, intended in sorted(edits, reverse=True):
        native = native[:start] + intended + native[end:]
    if unmarked(native) != unmarked(spoken):
        raise AppError('The stress correction changed the spoken words.')
    return native


def build_revision(folder, item):
    report = item['report']
    before, revised = _selected_take(folder, report['segment_id'])
    manifest = read(folder / 'manifest.json')
    native_text = stress_native_text(before, report)
    request = {
        'schema': 1,
        'generation': folder.name,
        'source_sha256': manifest['source_sha256'],
        'native_text': native_text,
        'before': before,
        'seed_offset': int(item.get('seed_offset', 0)),
        'listener_report': {
            'id': report['id'],
            'generation_id': report['generation_id'],
            'audio_hash': report['audio_hash'],
            'selected_text': report['selected_text'],
            'intended_stress': report['intended_stress'],
            'text_offset': report['text_offset'],
            'corrections': report.get('corrections'),
        },
        'human_audio_approval': False,
    }
    previous_native = before['unit']['synthesis']['text']
    if unmarked(native_text) == unmarked(previous_native):
        request.update(recovery_kind='listener_pronunciation_from_synthesis',
                       previous_native_text=previous_native)
    if revised:
        request['published_before'] = before
    return request


def source_file(data_dir, book_id):
    candidates = [data_dir.parent / 'source/books' / book_id / 'source.fb2',
                  data_dir.parent / 'source/books' / book_id / 'source.epub']
    files = [path for path in candidates if path.is_file()]
    if len(files) != 1:
        raise AppError('The exact source book for the production resync is unavailable.')
    return files[0]


def replacement_environment():
    """Pin new audio to production while the request retains the heard take."""
    pinned = resolve_production_higgs_model(ROOT)
    return {**os.environ, 'RIMSKY_HIGGS_MODEL': str(pinned.path)}


def process(folder, data_dir, generation_id, item_path):
    item = read(item_path)
    if reuse_applied(folder, item_path, item):
        return
    item['seed_offset'] = next_seed_offset(folder, item_path, item)
    item.update(status='running', updated=now(), attempts=int(item.get('attempts', 0)) + 1)
    write_json(item_path, item)
    request = build_revision(folder, item)
    instructions = folder / 'listener-stress-queue/instructions'
    instructions.mkdir(parents=True, exist_ok=True)
    request_path = instructions / f"{item['id']}.json"
    if request_path.exists() and read(request_path) != request:
        raise AppError('The immutable listener stress instruction changed.')
    if not request_path.exists():
        write_json(request_path, request)
    # `request.before` remains the immutable heard-model/audio evidence.  It is
    # deliberately not used as a model selector for newly synthesized audio.
    environment = replacement_environment()
    subprocess.run([
        sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts/revise_higgs_spoken_take.py'),
        '--folder', str(folder), '--request', str(request_path),
    ], cwd=ROOT, env=environment, check=True, timeout=3600)
    subprocess.run([
        sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts/sync_higgs_preview_runtime.py'),
        '--workspace', str(ROOT),
        '--source', str(source_file(data_dir, item['report']['book_id'])),
        '--prepared-book', str(data_dir.parent / 'source/book.json'),
        '--generation', str(folder), '--data-dir', str(data_dir),
    ], cwd=ROOT, check=True, timeout=600)
    registry = read(folder / 'spoken-take-revisions.json')['units'][item['report']['segment_id']]
    item.update(status='applied', updated=now(), completed=now(), error='',
                instruction=str(request_path), instruction_sha256=file_digest(request_path),
                replacement_audio_sha256=registry['replacement']['receipt']['audio_sha256'],
                human_audio_approval=False)
    write_json(item_path, item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--generation-id', required=True)
    args = parser.parse_args()
    folder = args.generation.resolve()
    data_dir = args.data_dir.resolve()
    if folder.name != read(folder / 'progress.json').get('generation'):
        raise AppError('The listener stress queue belongs to another generation.')
    lock = _locked(folder / 'listener-stress-queue/worker.lock')
    if lock is None:
        return
    try:
        queue = folder / 'listener-stress-queue'
        # Re-scan after every batch. A correction can arrive while a take is
        # rendering; the existing owner must consume it even if the newly
        # launched helper observed this lock and exited.
        while True:
            pending = []
            for path in queue.glob('*.json'):
                item = read(path)
                if (item.get('generation_id') == args.generation_id
                        and item.get('status') in ('queued', 'running')):
                    pending.append((item.get('priority', [3, 0, 0]), item.get('created', ''), path.name, path))
            if not pending:
                # Cover the small handoff race between an enqueue and unlock.
                time.sleep(.25)
                pending = []
                for path in queue.glob('*.json'):
                    item = read(path)
                    if (item.get('generation_id') == args.generation_id
                            and item.get('status') in ('queued', 'running')):
                        pending.append((item.get('priority', [3, 0, 0]), item.get('created', ''), path.name, path))
                if not pending:
                    break
            for _, _, _, path in sorted(pending):
                try:
                    process(folder, data_dir, args.generation_id, path)
                except Exception as error:
                    item = read(path)
                    item.update(status='held', updated=now(), error=f'{type(error).__name__}: {error}',
                                traceback=traceback.format_exc(limit=8), human_audio_approval=False)
                    write_json(path, item)
    finally:
        _unlock(lock)


if __name__ == '__main__':
    main()
