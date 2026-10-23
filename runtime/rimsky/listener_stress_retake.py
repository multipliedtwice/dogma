"""Queue source-bound listener stress corrections for an external Higgs preview.

The listener report remains the durable human evidence.  This module only
creates a render request bound to that report and starts the single-owner queue
worker; it never treats the new audio as listener-approved.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .util import AppError, file_digest, now, write_json
from .series_characters import propagation_targets


ROOT = Path(__file__).resolve().parents[2]
ENGINE = 'higgs_audio_tts_q8_external_preview'
ACTIVE = frozenset({'queued', 'running'})


def _external_folder(store, generation):
    preview = generation.get('analysis', {}).get('external_preview') or {}
    name = preview.get('generation')
    if generation.get('config', {}).get('engine') != ENGINE or not isinstance(name, str) or not name:
        return None
    folder = (store.root.parent / name).resolve()
    if not folder.is_relative_to(store.root.parent.resolve()):
        raise AppError('The external listening generation is outside its workspace.')
    manifest = folder / 'manifest.json'
    progress = folder / 'progress.json'
    if not manifest.is_file() or not progress.is_file():
        raise AppError('The external listening generation cannot accept retakes.')
    if json.loads(progress.read_text('utf8')).get('generation') != name:
        raise AppError('The external listening checkpoint belongs to another generation.')
    return folder


def _python():
    # Retakes reuse the production render pipeline, including its pinned
    # Russian stress preprocessor. The generic TTS venv lacks silero_stress.
    for preferred in (ROOT / 'training-work/higgs-production/.venv/Scripts/python.exe',
                      ROOT / 'training-work/.venv-tts/Scripts/python.exe'):
        if preferred.is_file():
            return preferred
    return Path(sys.executable)


def _same_target(row, report):
    target = row.get('report') or {}
    return (row.get('generation_id') == report['generation_id']
            and target.get('segment_id') == report['segment_id']
            and (target.get('corrections') or [{k: target.get(k) for k in ('selected_text','intended_stress','text_offset')}])
                == (report.get('corrections') or [{k: report.get(k) for k in ('selected_text','intended_stress','text_offset')}]))


def _same_request(row, report):
    target = row.get('report') or {}
    return _same_target(row,report) and target.get('audio_hash') == report['audio_hash']


def _priority(store, generation, segment, reported_id):
    if segment['id'] == reported_id:
        return [0, 0, segment.get('ordinal', 0)]
    current = None
    rows = store.rows('SELECT value FROM positions WHERE book_id=?', (generation['book_id'],)) if hasattr(store, 'rows') else []
    if rows:
        with contextlib.suppress(ValueError, TypeError):
            position = json.loads(rows[0]['value'])
            if position.get('generation_id') == generation['id']:
                current = next((row for row in generation['segments'] if row['id'] == position.get('segment_id')), None)
    pivot = current.get('ordinal', 0) if current else 0
    ordinal = segment.get('ordinal', 0)
    return [1 if ordinal >= pivot else 2, abs(ordinal - pivot), ordinal]


def _target_reports(generation, report):
    state = report.get('character_state') or {}
    if state.get('status') != 'updated':
        return [(next(row for row in generation['segments'] if row['id'] == report['segment_id']), report)]
    targets = []
    for target in propagation_targets(generation, state):
        segment = target['segment']
        corrections = target['corrections']
        derived = {**report, 'segment_id': segment['id'], 'chapter_id': segment['chapter_id'],
                   'source_id': segment['source_id'], 'audio_hash': segment['audio_hash'],
                   'passage_text': segment['text'], 'spoken_text': segment['spoken'],
                   'selected_text': corrections[0]['selected_text'],
                   'intended_stress': corrections[0]['intended_stress'],
                   'text_offset': corrections[0]['text_offset'], 'corrections': corrections}
        targets.append((segment, derived))
    return targets or [(next(row for row in generation['segments'] if row['id'] == report['segment_id']), report)]


def _start_worker(folder, data_dir, generation_id):
    logs = folder / 'listener-stress-queue/logs'
    logs.mkdir(parents=True, exist_ok=True)
    output = (logs / 'worker.stdout.log').open('ab')
    errors = (logs / 'worker.stderr.log').open('ab')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        subprocess.Popen([
            str(_python()), '-X', 'utf8', '-u',
            str(ROOT / 'scripts/process_listener_stress_queue.py'),
            '--generation', str(folder), '--data-dir', str(data_dir),
            '--generation-id', generation_id,
        ], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
           creationflags=flags, close_fds=True)
    finally:
        output.close()
        errors.close()


def enqueue_listener_stress_retake(store, report):
    """Return queue metadata, or None when this is not an external stress report."""
    if report.get('kind') != 'stress' or not report.get('intended_stress'):
        return None
    generation = store.generation(report['generation_id'])
    folder = _external_folder(store, generation)
    if folder is None:
        return None
    queue = folder / 'listener-stress-queue'
    queue.mkdir(parents=True, exist_ok=True)
    existing_rows = []
    for path in queue.glob('*.json'):
        with contextlib.suppress(OSError, ValueError, TypeError):
            existing_rows.append(json.loads(path.read_text('utf8')))
    reported = next((row for row in generation['segments'] if row['id'] == report['segment_id']), None)
    applied_original = next((row for row in existing_rows
                             if row.get('status') == 'applied' and _same_target(row,report)), None)
    if (reported is None or reported.get('audio_hash') != report.get('audio_hash')) and not (
            (report.get('character_state') or {}).get('status') == 'updated' and applied_original):
        raise AppError('The reported take changed before its stress retake was queued.')
    created, reused = [], []
    targets = _target_reports(generation, report)
    for index, (segment, target_report) in enumerate(targets):
        duplicate = next((row for row in existing_rows if row.get('status') in ACTIVE | {'applied'}
                          and (_same_request(row, target_report)
                               or row.get('status') == 'applied' and _same_target(row,target_report))), None)
        if duplicate:
            reused.append(duplicate)
            continue
        identifier = report['id'] if segment['id'] == report['segment_id'] else report['id'] + '-' + segment['id']
        kept = {key: target_report.get(key) for key in (
            'id', 'book_id', 'generation_id', 'segment_id', 'chapter_id', 'source_id',
            'selected_text', 'text_offset', 'audio_offset', 'audio_hash', 'passage_text',
            'spoken_text', 'created', 'kind', 'comment', 'intended_stress', 'corrections')}
        item = {
            'schema': 2,
            'id': identifier,
            'status': 'queued',
            'created': now(),
            'updated': now(),
            'priority': _priority(store, generation, segment, report['segment_id']),
            'generation_id': report['generation_id'],
            'external_generation': folder.name,
            'report': kept,
            'series_character': report.get('character_state') if len(targets) > 1 else None,
            'publication': (segment.get('performance') or {}).get('external_preview') or {},
            'human_audio_approval': False,
        }
        path = queue / f"{item['id']}.json"
        if path.exists():
            # A previously held immutable attempt keeps its evidence. A repaired
            # implementation gets a distinct request instead of rewriting it.
            item['id'] = report['id'] + '-retry-' + hashlib.sha256(
                json.dumps([segment['id'], kept.get('corrections'),
                    file_digest(ROOT / 'scripts/process_listener_stress_queue.py'),
                    file_digest(ROOT / 'scripts/revise_higgs_spoken_take.py')],
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            path = queue / f"{item['id']}.json"
            if path.exists():
                raise AppError('A different stress retake already uses this retry identifier.')
        write_json(path, item)
        created.append(item)
    _start_worker(folder, store.root, report['generation_id'])
    first = created[0] if created else reused[0]
    return {'id': first['id'], 'status': first['status'], 'existing': not created,
            'targets': len(targets), 'queued': len(created), 'reused': len(reused),
            'character': (report.get('character_state') or {}).get('canonical_name')}
