"""One current book output, with replaceable parts and compact sound receipts.

The Store chooses the current output. This disk manifest mirrors that choice;
it is not a second generation registry. Adopting an existing output copies no
audio, and replacing a part never copies an unchanged chapter or output tree.
"""
from __future__ import annotations

import copy
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import wave
from functools import lru_cache
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.store import Store
from rimsky.util import atomic_write, digest, file_digest, now

POLICY = 'current-book-output-v1'
MODEL = '78f09d20145ab79b8a6d425bc68fe104e3c624a7aea687bb0ff31c3a186374d1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


@lru_cache(maxsize=512)
def _document(content_sha256, raw):
    return json.loads(raw.decode('utf-8-sig'))


def document(path, expected=None):
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None and actual != expected:
        raise ValueError('Physical input ancestry changed before compaction')
    # Never expose the shared parsed cache to caller mutation.
    return copy.deepcopy(_document(actual, raw))


def write(path, value):
    atomic_write(Path(path), json.dumps(value, ensure_ascii=False, indent=2).encode('utf8'))


def output_root(data_dir, book_id):
    if len(book_id) != 32 or any(c not in '0123456789abcdef' for c in book_id):
        raise ValueError('Invalid current book identity')
    return Path(data_dir).resolve().parent / ('current-book-' + book_id)


def current_manifest(data_dir, book_id):
    return output_root(data_dir, book_id) / 'current-output.json'


def _intent_path(data_dir, book_id):
    return output_root(data_dir, book_id) / 'publication-intent.json'


def _seal(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


def _same_parts(view, generation):
    segments = {row['id']: row for row in generation['segments']}
    return set(segments) == set(view['parts']) and all(
        segments[uid]['status'] == 'ready'
        and all(segments[uid].get(key) == part['unit'].get(key)
                for key in ('id', 'chapter_id', 'source_id', 'start', 'end', 'text'))
        and segments[uid]['audio_hash'] == part['audio_hash']
        and segments[uid]['performance'].get('synthesis_snapshot') == part['unit']['synthesis']
        and (not part.get('playback_sha256') or part['playback_sha256'] == _playback_binding(segments[uid]))
        for uid, part in view['parts'].items())


def _playback_binding(segment):
    from rimsky.current_audiobook import FIELDS
    return digest({key: segment[key] for key in FIELDS})


def _save_view(data_dir, view):
    write(current_manifest(data_dir, view['book_id']), _seal(view))
    _controls(data_dir, view)


def _controls(data_dir, view):
    root = output_root(data_dir, view['book_id'])
    counts = {cid: sum(p['unit']['chapter_id'] == cid for p in view['parts'].values()) for cid in view['chapters']}
    write(root / 'ready.json', {'policy': POLICY, 'unit_count': len(view['parts']), 'chapter_counts': counts,
          'chapters': view['chapters'], 'native_manifest': view['native_manifest'],
          'current_output_manifest': 'current-output.json'})
    write(root / 'manifest.json', {'render_policy': POLICY, 'book_id': view['book_id'], 'source_sha256': view['source_hash']})
    write(root / 'progress.json', {'generation': root.name, 'status': 'complete', 'policy': POLICY,
          'revision': view['store_revision'], 'chapters': {cid: {'complete': True, 'units': counts[cid],
          'seconds': sum((p['padding_before'] + p['speech_frames'] + p['padding_after']) / 24000
                         for p in view['parts'].values() if p['unit']['chapter_id'] == cid)} for cid in counts}})


def _finish_intent(data_dir, view):
    write(_intent_path(data_dir, view['book_id']), {'policy': POLICY, 'status': 'complete',
          'generation_id': view['generation_id'], 'store_revision': view['store_revision'],
          'manifest_sha256': view['sha256']})


def _reserve(data_dir, view, expected_revision, metadata=None):
    # Prepared bytes exist before the SQLite transaction. A crash after the
    # transaction can rebuild this disk view solely from the committed parts.
    write(_intent_path(data_dir, view['book_id']), _seal({'policy': POLICY, 'status': 'pending',
          'expected_revision': expected_revision, 'view': copy.deepcopy(view),
          'metadata': copy.deepcopy(metadata or {})}))


def _audio(data_dir, audio_hash):
    if len(audio_hash) != 64 or any(c not in '0123456789abcdef' for c in audio_hash):
        raise ValueError('Invalid current audio identity')
    path = Path(data_dir) / 'audio' / (audio_hash + '.wav')
    if file_digest(path) != audio_hash:
        raise ValueError('Current playable audio checksum changed')
    with wave.open(str(path)) as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (24000, 1, 2):
            raise ValueError('Current speech must be mono PCM16 at 24 kHz')
        return path, stream.getnframes()


def _block_map(book):
    return {b['id']: b['text'] for chapter in book['chapters'] for b in chapter['blocks']}


_BINARY_SESSION = ContextVar('current_higgs_binary_validation_session', default=None)


def _pinned_read(path):
    if os.name != 'nt':return Path(path).open('rb')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # Read sharing only: deny writes and atomic replacement while validated.
    handle = create(str(Path(path).resolve()), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise
    try:return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def _identity(stat):
    # Windows ctime is creation time and CRT/path views can differ; pinned
    # deny-write/delete handles supply the mutation guard there. POSIX ctime
    # detects in-place edits even when a caller restores the old mtime.
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns if os.name != 'nt' else None)


def _unchanged_binary(path, record):
    if (_identity(os.fstat(record['handle'].fileno())) != record['identity']
            or _identity(Path(path).stat()) != record['identity']):
        raise ValueError('Pinned physical model/runner changed during validation')


@contextmanager
def binary_validation_session():
    """Hash once while held handles pin each binary for this operation only."""
    if _BINARY_SESSION.get() is not None:
        yield
        return
    with ExitStack() as handles:
        state = {'handles': handles, 'files': {}}
        token = _BINARY_SESSION.set(state)
        try:
            yield
            for path, record in state['files'].items():_unchanged_binary(path, record)
        finally:
            _BINARY_SESSION.reset(token)


def _binary(path, expected):
    path = Path(path).resolve()
    state = _BINARY_SESSION.get()
    if state is None:
        with binary_validation_session():return _binary(path, expected)
    record = state['files'].get(str(path))
    if record is None:
        handle = state['handles'].enter_context(_pinned_read(path))
        record = {'handle': handle, 'identity': _identity(os.fstat(handle.fileno())),
                  'sha256': hashlib.file_digest(handle, 'sha256').hexdigest()}
        state['files'][str(path)] = record
    _unchanged_binary(path, record)
    if record['sha256'] != expected:raise ValueError('Selected physical model/runner checksum changed')


def _physical(folder, ready, unit, row):
    """Compact a selected physical request while its old evidence is reachable."""
    if _BINARY_SESSION.get() is None:
        with binary_validation_session():return _physical(folder, ready, unit, row)
    seen = set()
    while folder not in seen:
        seen.add(folder)
        native = folder / 'native' / row['session']
        if (native / 'physical-result.json').is_file():
            requests = document(native / 'requests.json')['requests']
            selected = [x for x in requests if x['id'] == unit['id']]
            from render_higgs_book_trial import sequence_request
            if (len(selected) != 1 or digest(selected[0]) != row['request_sha256']
                    or selected[0] != sequence_request(unit['id'], unit['synthesis'])):
                raise ValueError('Selected physical request changed')
            worker = document(native / 'worker.json')
            import psutil
            try:
                if psutil.Process(worker['pid']).create_time() == worker['create_time']:
                    raise ValueError('Selected physical worker is still live')
            except psutil.NoSuchProcess:
                pass
            command = worker['command']
            _binary(command[command.index('--model') + 1], MODEL)
            _binary(command[0], unit['synthesis']['runner_sha256'])
            terminal = document(native / 'physical-result.json')
            if not isinstance(terminal.get('returncode'), int):
                raise ValueError('Selected physical request is not terminal')
            if (native / 'unit-result.json').exists() and terminal['returncode'] != 0:
                raise ValueError('Selected singleton did not finish successfully')
            raw = native / (unit['id'] + '.wav')
            if file_digest(raw) != row['audio_sha256']:
                raise ValueError('Selected physical WAV checksum changed')
            with wave.open(str(raw)) as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (24000, 1, 2):
                    raise ValueError('Selected physical WAV format changed')
                pcm = stream.readframes(stream.getnframes())
            return {'session': row['session'], 'request': selected[0],
                    'request_sha256': row['request_sha256'], 'terminal': terminal,
                    'worker': worker, 'model_sha256': MODEL,
                    'runner_sha256': unit['synthesis']['runner_sha256'],
                    'selected_status': 'rendered', 'raw_audio_sha256': row['audio_sha256'],
                    'synthesis_sha256': digest(unit['synthesis']),
                    'speech_pcm_sha256': hashlib.sha256(pcm).hexdigest(),
                    'technical_attempt': row.get('technical_attempt', 1)}
        parent = ready.get('listener_stress_derivation') or {}
        if not parent.get('base') or not parent.get('base_ready_sha256'):
            break
        folder = Path(parent['base']).resolve()
        path = folder / 'ready.json'
        ready = document(path, parent['base_ready_sha256'])
    raise ValueError('Selected physical receipt is unavailable for compaction')


def _unit_state(unit, segment, receipt, data_dir):
    if receipt.get('status') != 'rendered' or receipt['synthesis_sha256'] != digest(unit['synthesis']):
        raise ValueError('Current unit differs from its rendered request')
    if segment['performance'].get('synthesis_snapshot') != unit['synthesis']:
        raise ValueError('Current Store playback differs from the selected native request')
    path, frames = _audio(data_dir, segment['audio_hash'])
    binding = segment['performance'].get('external_preview') or {}
    first = round(binding['published_start'] * 24000) - binding['clip_frame_start']
    last = round(binding['published_end'] * 24000) - binding['clip_frame_start']
    if not 0 <= first < last <= frames:
        raise ValueError('Current speech bounds exceed its playable clip')
    with wave.open(str(path)) as stream:
        body = stream.readframes(frames)
    if any(body[:first * 2]) or any(body[last * 2:]):
        raise ValueError('Current padding contains unbound speech')
    return {'unit': unit, 'audio_hash': segment['audio_hash'], 'padding_before': first,
            'padding_after': frames - last, 'speech_frames': last - first,
            'speech_pcm_sha256': hashlib.sha256(body[first * 2:last * 2]).hexdigest()}


def _adopt(data_dir, generation_dir, generation_id, *, execute=False, manifest_preview=None):
    """Validate a migration; --execute is deliberately a separate caller choice."""
    data_dir, folder = Path(data_dir).resolve(), Path(generation_dir).resolve()
    ready = read(folder / 'ready.json')
    effective = read(folder / 'effective-listener-units.json')['units']
    store = Store(data_dir)
    try:
        generation = store.generation(generation_id)
        book = store.book(generation['book_id'])
        blocks = _block_map(book)
        segments = {x['id']: x for x in generation['segments']}
        if len(segments) != len(effective) or generation['status'] != 'ready':
            raise ValueError('Migration requires a complete playable current output')
        parts = {}
        for original in effective:
            unit = copy.deepcopy(original)
            segment = segments[unit['id']]
            if blocks[unit['source_id']][unit['start']:unit['end']] != unit['text']:
                raise ValueError('Current unit is not bound to its exact book source')
            receipt = read(folder / 'units' / (unit['id'] + '.json'))
            if unit['synthesis']['backend'] == 'nonverbal_asset':
                path, frames = _audio(data_dir, segment['audio_hash'])
                part = {'unit': unit, 'audio_hash': segment['audio_hash'], 'padding_before': 0,
                        'padding_after': 0, 'speech_frames': frames,
                        'native': {'selected_status': 'nonverbal', 'source_asset': unit['synthesis']}}
            else:
                if unit['synthesis']['model_sha256'] != MODEL:
                    raise ValueError('Current output uses an unapproved speech checkpoint')
                part = _unit_state(unit, segment, receipt, data_dir)
                part['native'] = _physical(folder, ready, unit, receipt)
                if part['speech_pcm_sha256'] != part['native']['speech_pcm_sha256']:
                    raise ValueError('Current playback contains different physical speech')
            part['playback_sha256'] = _playback_binding(segment)
            parts[unit['id']] = part
        value = {'policy': POLICY, 'book_id': book['id'], 'generation_id': generation_id,
                 'source_hash': book['source_hash'],
                 'native_manifest': ready['native_manifest'], 'chapters': ready['chapters'],
                 'parts': parts, 'unit_order': [x['id'] for x in effective],
                 'store_revision': generation['revision'], 'updated': now()}
        _seal(value)
        if manifest_preview is not None:
            write(Path(manifest_preview), value)
        result = {'status': 'validated', 'book_id': book['id'], 'generation_id': generation_id,
                  'parts': len(parts), 'audio_copies': 0, 'output': str(output_root(data_dir, book['id'])),
                  'manifest_sha256': value['sha256'], 'manifest_bytes': len(json.dumps(value, ensure_ascii=False).encode()),
                  'current_audio_files': len({part['audio_hash'] for part in parts.values()}),
                  'source_hash': value['source_hash'], 'legacy_output_dependency_after_adoption': False,
                  'reference_files': sorted({part['native']['request']['voice_ref'] for part in parts.values()
                                             if part['native'].get('request')})}
        if execute:
            # Selection is canonical in the Store; the manifest is its disk view.
            analysis = copy.deepcopy(generation['analysis'])
            analysis.setdefault('external_preview', {})['generation'] = output_root(data_dir, book['id']).name
            analysis['current_output'] = {'policy': POLICY, 'root': str(output_root(data_dir, book['id']))}
            _reserve(data_dir, value, generation['revision'], {'analysis': analysis})
            _controls(data_dir, value)
            from rimsky.current_audiobook import FIELDS
            first = {key: generation['segments'][0][key] for key in FIELDS}
            current = store.publish_current_parts(generation_id, [first], expected_revision=generation['revision'],
                                                  metadata={'analysis': analysis})
            value['store_revision'] = current['revision']
            _save_view(data_dir, value)
            _finish_intent(data_dir, value)
            result.update(status='adopted', manifest_sha256=value['sha256'],
                          revision=value['store_revision'])
        return result
    finally:
        store.close()


def _load(store, data_dir, book_id):
    path = current_manifest(data_dir, book_id)
    pending = _intent_path(data_dir, book_id)
    intent = read(pending) if pending.exists() else None
    current = store.current_generation(book_id)
    if intent and intent.get('status') == 'pending':
        if intent.get('sha256') != digest({k: v for k, v in intent.items() if k != 'sha256'}):
            raise ValueError('Current publication intent checksum changed')
        proposed = intent['view']
        if (proposed.get('book_id') != book_id or current is None
                or current['id'] != proposed['generation_id']):
            raise ValueError('Current output adoption is incomplete; resume its saved intent')
        if (_same_parts(proposed, current)
                and all(current.get(key) == val for key, val in intent.get('metadata', {}).items())
                and current['revision'] > intent['expected_revision']):
            proposed['store_revision'] = current['revision']
            _save_view(data_dir, proposed)
            _finish_intent(data_dir, proposed)
        elif not path.exists() or current['revision'] != intent['expected_revision']:
            raise ValueError('Current parts changed outside the saved publication intent')
    if not path.exists():
        return None
    value = read(path)
    if value.get('policy') != POLICY or value.get('book_id') != book_id or value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'}):
        raise ValueError('Current output manifest checksum/identity changed')
    if current is None or current['id'] != value['generation_id']:
        raise ValueError('Disk output does not match the canonical current book')
    if current['revision'] < value['store_revision'] or not _same_parts(value, current):
        raise ValueError('Current disk output has a stale Store revision; recover its saved intent')
    if value['source_hash'] != store.book(book_id)['source_hash']:
        raise ValueError('Current output source changed')
    missing = any(not part.get('playback_sha256') for part in value['parts'].values())
    if missing and current['revision'] != value['store_revision']:
        raise ValueError('An older current manifest needs its exact saved playback binding before metadata reconciliation')
    if missing or current['revision'] != value['store_revision']:
        by_id = {row['id']: row for row in current['segments']}
        for uid, part in value['parts'].items():
            part['playback_sha256'] = _playback_binding(by_id[uid])
        value.update(store_revision=current['revision'], updated=now())
        _save_view(data_dir, value)
        _finish_intent(data_dir, value)
    return value


def prepared_spoken(unit):
    """Recover reader text from the exact final synthesis pronunciation proof.

    A unit's ``spoken`` may be the reusable number-expanded baseline. Final
    automatic and human marks belong to the prephonetic source, while the
    closed pronoun spellings remain model input only.
    """
    synthesis = unit['synthesis']
    native = synthesis.get('text')
    if not isinstance(native, str) or not native.strip():
        raise ValueError('Replacement has no bound native text')
    phonetic = synthesis.get('russian_phonetics')
    if phonetic is not None:
        from rimsky import russian_pronunciation as phonetics
        if (not isinstance(phonetic, dict) or phonetic.get('rule') != phonetics.RULE
                or not isinstance(phonetic.get('prephonetic_source'), str)
                or not isinstance(phonetic.get('records'), list)):
            raise ValueError('Replacement lacks its exact prephonetic reader binding')
        spoken = phonetic['prephonetic_source']
        phonetics.validate(spoken, native, phonetic['records'])
        if 'prepared_spoken' in unit and unit['prepared_spoken'] != spoken:
            raise ValueError('Replacement reader text contradicts its phonetic proof')
        return spoken
    spoken = unit.get('prepared_spoken', unit.get('spoken', unit.get('text')))
    if not isinstance(spoken, str) or spoken != native:
        raise ValueError('Replacement reader text is not bound to its native input')
    return spoken


def replacement_segment(data_dir, old, part, unit, receipt, raw_path):
    """Write one replacement clip, retaining its source-owned structural pauses."""
    if receipt['status'] != 'rendered' or receipt['synthesis_sha256'] != digest(unit['synthesis']):
        raise ValueError('Replacement request is not bound to successful physical audio')
    if unit['synthesis']['model_sha256'] != MODEL or file_digest(raw_path) != receipt['audio_sha256']:
        raise ValueError('Replacement model/audio identity changed')
    binding = ('id', 'chapter_id', 'source_id', 'start', 'end', 'text')
    if any(unit.get(key) != part['unit'].get(key) or unit.get(key) != old.get(key) for key in binding):
        raise ValueError('Replacement changed its retained source binding')
    if unit.get('render_parts') or unit['synthesis'].get('render_parts') or old['performance'].get('render_parts'):
        raise ValueError('A part with an internal pause layout requires its layout-aware renderer')
    spoken = prepared_spoken(unit)
    with wave.open(str(raw_path)) as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (24000, 1, 2):
            raise ValueError('Invalid replacement speech format')
        speech = stream.readframes(stream.getnframes())
    pcm = b'\0\0' * part['padding_before'] + speech + b'\0\0' * part['padding_after']
    memory = io.BytesIO()
    with wave.open(memory, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(pcm)
    from sync_higgs_preview_runtime import _write_audio
    audio_hash, duration = _write_audio(Path(data_dir), memory.getvalue(), len(pcm) // 2, 24000)
    from rimsky.current_audiobook import FIELDS
    segment = {key: copy.deepcopy(old[key]) for key in FIELDS}
    performance = segment['performance']
    performance.pop('external_preview', None)
    performance['basis'] = POLICY
    performance['synthesis_snapshot'] = unit['synthesis']
    performance['current_output'] = {'policy': POLICY, 'part_revision': digest({'input': unit['synthesis'], 'audio': audio_hash}),
                                     'raw_audio_sha256': receipt['audio_sha256'], 'request_sha256': receipt['request_sha256']}
    # Word timestamps belong to the previous take. Recognition is off here;
    # leave this replacement unaligned rather than reusing old timing.
    segment.update(audio_hash=audio_hash, duration=duration, status='ready', spoken=spoken,
                   performance=performance, cues=[])
    raw_path = Path(raw_path).resolve()
    if raw_path.parent.name == 'assets':
        physical_root = raw_path.parent.parent
    elif raw_path.parent.name == receipt['session'] and raw_path.parent.parent.name == 'native':
        physical_root = raw_path.parent.parent.parent
    else:
        raise ValueError('Replacement WAV is outside its reserved physical job')
    native = _physical(physical_root, {}, unit, receipt)
    if native['speech_pcm_sha256'] != hashlib.sha256(speech).hexdigest():
        raise ValueError('Replacement clip differs from its selected physical speech')
    updated = {**part, 'unit': unit, 'audio_hash': audio_hash, 'speech_frames': len(speech) // 2,
               'speech_pcm_sha256': hashlib.sha256(speech).hexdigest(),
               'native': native, 'playback_sha256': _playback_binding(segment)}
    return segment, updated


def _publish_parts(store, data_dir, view, changes, *, expected_revision):
    """Commit finished parts together; incomplete parts retain current playback."""
    current = store.generation(view['generation_id'])
    if current['book_id'] != view['book_id']:
        raise ValueError('Replacement belongs to another book')
    if not _same_parts(view, current):
        raise ValueError('A current playback part changed before this replacement; reload its publication')
    if not changes:
        return current, view
    by_id = {x['id']: x for x in current['segments']}
    built, revised = [], copy.deepcopy(view)
    for uid, (unit, receipt, raw_path) in changes.items():
        if uid not in view['parts'] or unit['id'] != uid:
            raise ValueError('Replacement is outside current source coverage')
        segment, part = replacement_segment(data_dir, by_id[uid], view['parts'][uid], unit, receipt, raw_path)
        built.append(segment)
        revised['parts'][uid] = part
    _reserve(data_dir, revised, expected_revision)
    published = store.publish_current_parts(view['generation_id'], built, expected_revision=expected_revision)
    revised.update(store_revision=published['revision'], updated=now())
    _save_view(data_dir, revised)
    _finish_intent(data_dir, revised)
    return published, revised


def adopt(data_dir, generation_dir, generation_id, *, execute=False, manifest_preview=None):
    if not execute:
        with binary_validation_session():
            return _adopt(data_dir, generation_dir, generation_id, manifest_preview=manifest_preview)
    store = Store(Path(data_dir))
    try:
        book_id = store.generation(generation_id)['book_id']
    finally:
        store.close()
    from higgs_published_timeline import publication_lock
    with publication_lock(output_root(data_dir, book_id)), binary_validation_session():
        return _adopt(data_dir, generation_dir, generation_id, execute=True, manifest_preview=manifest_preview)


def load(store, data_dir, book_id):
    if not current_manifest(data_dir, book_id).exists() and not _intent_path(data_dir, book_id).exists():
        return None
    from higgs_published_timeline import publication_lock
    with publication_lock(output_root(data_dir, book_id)):
        return _load(store, data_dir, book_id)


def publish_parts(store, data_dir, view, changes, *, expected_revision):
    from higgs_published_timeline import publication_lock
    with publication_lock(output_root(data_dir, view['book_id'])), binary_validation_session():
        return _publish_parts(store, data_dir, view, changes, expected_revision=expected_revision)


def rename_current(store, data_dir, book_id, name):
    """Update the current title with the same recoverable publication intent."""
    from higgs_published_timeline import publication_lock
    from rimsky.current_audiobook import FIELDS
    with publication_lock(output_root(data_dir, book_id)):
        view = _load(store, data_dir, book_id)
        generation = store.generation(view['generation_id'])
        if generation.get('name') == name:
            return generation, view
        _reserve(data_dir, view, generation['revision'], {'name': name})
        part = {key: generation['segments'][0][key] for key in FIELDS}
        published = store.publish_current_parts(generation['id'], [part],
            expected_revision=generation['revision'], metadata={'name': name})
        view.update(store_revision=published['revision'], updated=now())
        _save_view(data_dir, view)
        _finish_intent(data_dir, view)
        return published, view


def publish_completed(data_dir, folder, units, book_id, chapter_id=None):
    """Publish available replacement parts without concatenating a chapter.

    Return None for a book which has not adopted current output yet. A missing
    replacement receipt leaves its existing playable part in place.
    """
    store = Store(Path(data_dir))
    try:
        view = load(store, data_dir, book_id)
        if view is None:
            return None
        changes = {}
        for unit in units:
            if chapter_id is not None and unit['chapter_id'] != chapter_id:
                continue
            path = Path(folder) / 'units' / (unit['id'] + '.json')
            if not path.exists():
                continue
            receipt = read(path)
            if receipt.get('status') != 'rendered':
                continue
            selected = receipt.get('effective_unit', unit)
            prior = view['parts'].get(unit['id'])
            if prior is None:
                raise ValueError('Replacement is outside current book source coverage')
            if (prior['native'].get('raw_audio_sha256') == receipt.get('audio_sha256')
                    and prior['unit']['synthesis'] == selected['synthesis']):
                continue
            changes[unit['id']] = (selected, receipt, Path(folder) / receipt['audio'])
        generation = store.generation(view['generation_id'])
        published, view = publish_parts(store, data_dir, view, changes, expected_revision=generation['revision'])
        return {'generation_id': published['id'], 'revision': published['revision'],
                'published_ids': list(changes), 'retained_parts': len(view['parts']) - len(changes),
                'copied_audio': 0, 'output': str(output_root(data_dir, book_id))}
    finally:
        store.close()


def cleanup_job_audio(store, data_dir, view, job, receipts):
    """Remove only successful temporary takes already retained in current parts."""
    import psutil
    job = Path(job).resolve()
    parent = output_root(data_dir, view['book_id']) / 'work'
    if job.parent != parent or len(job.name) != 24 or any(x not in '0123456789abcdef' for x in job.name):
        raise ValueError('Temporary audio cleanup is outside its current book job')
    if read(job / 'terminal.json').get('status') != 'applied':
        raise ValueError('An incomplete correction must retain its temporary evidence')
    latest = store.generation(view['generation_id'])
    if not _same_parts(view, latest):
        raise ValueError('Temporary audio is not safely represented by the current book')
    candidates = {}
    for uid, receipt in receipts.items():
        part = view['parts'][uid]
        native = part['native']
        if (receipt.get('status') != 'rendered' or native.get('session') != receipt['session']
                or native.get('raw_audio_sha256') != receipt['audio_sha256']
                or native.get('synthesis_sha256') != digest(part['unit']['synthesis'])):
            raise ValueError('Temporary take does not match its committed compact receipt')
        _audio(data_dir, part['audio_hash'])
        worker = native['worker']
        try:
            if abs(psutil.Process(worker['pid']).create_time() - worker['create_time']) < .01:
                raise ValueError('Temporary native worker is still alive')
        except psutil.NoSuchProcess:
            pass
        session = job / 'native' / receipt['session']
        if session.resolve().parent != (job / 'native').resolve():
            raise ValueError('Temporary native session escapes its reserved job')
        if session.exists():
            if not (session / 'physical-result.json').exists():
                raise ValueError('Temporary native session is not terminal')
            bound = read(session / 'worker.json')
            if bound != worker:
                raise ValueError('Temporary worker identity changed')
            reservation = read(session / 'physical-start.json')
            if (reservation.get('unit') != part['unit']
                    or reservation.get('request_sha256') != receipt['request_sha256']
                    or read(session / 'physical-result.json') != native['terminal']):
                raise ValueError('Temporary native reservation/terminal changed')
        for path in (job / receipt['audio'], session / (uid + '.wav')):
            resolved = path.resolve()
            if not resolved.is_relative_to(job) or resolved.suffix.lower() != '.wav':
                raise ValueError('Temporary WAV escapes its reserved current job')
            if resolved.exists():
                if file_digest(resolved) != receipt['audio_sha256']:
                    raise ValueError('Temporary generated WAV changed before cleanup')
                candidates[str(resolved)] = resolved.stat().st_size
    proof = {'status': 'prepared', 'files': candidates, 'bytes': sum(candidates.values()),
             'generation_id': view['generation_id'], 'revision': latest['revision'],
             'compact_manifest_sha256': view['sha256']}
    write(job / 'temporary-audio-cleanup.json', proof)
    for path in candidates:
        Path(path).unlink()
    proof['status'] = 'complete'
    write(job / 'temporary-audio-cleanup.json', proof)
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--generation', required=True, type=Path)
    parser.add_argument('--generation-id', required=True)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--manifest-preview', type=Path)
    args = parser.parse_args()
    result = adopt(args.data_dir, args.generation, args.generation_id, execute=args.execute,
                   manifest_preview=args.manifest_preview)
    if args.result:
        write(args.result, result)
    print(json.dumps(result, ensure_ascii=False))
