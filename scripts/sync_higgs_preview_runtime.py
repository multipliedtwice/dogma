"""Mirror an immutable Higgs preview into the real production reader Store.

The Higgs evaluation renderer publishes chapter-sized WAV files and a timeline.
The production reader plays immutable segment assets from its SQLite-backed
library.  This bridge verifies the source and publication bindings, then splits
the published PCM at gaps between timeline units.  Concatenating the resulting
segment payloads reproduces every source PCM frame exactly; no speech is
resynthesized or transcoded.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.store import Store
from rimsky.util import atomic_write, file_digest as _file_digest, now, write_json

SCHEMA = 1
ENGINE = 'higgs_audio_tts_q8_external_preview'
MUSIC_MODE = 'verified-chapter-mix-v1'
_DIGEST_CACHE = {}


def file_digest(path: Path):
    """Hash immutable assets once per unchanged size/mtime in this watcher."""
    path = Path(path)
    stat = path.stat()
    key = str(path.resolve())
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _DIGEST_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[1]
    value = _file_digest(path)
    _DIGEST_CACHE[key] = (signature, value)
    if len(_DIGEST_CACHE) > 4096:
        _DIGEST_CACHE.clear()
        _DIGEST_CACHE[key] = (signature, value)
    return value


def read_json(path: Path, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)


@contextlib.contextmanager
def sync_lock(data_dir: Path):
    """Serialize repeat/manual syncs without taking the runtime library lease."""
    path = data_dir.resolve() / '.preview-sync.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    acquired = False
    deadline = time.monotonic() + 30
    try:
        while not acquired:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another preview sync still holds the listener library')
                time.sleep(.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _inside(root: Path, relative: str, label: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'{label} leaves its permitted root')
    return path


def _generation_id(book_id: str, generation: str) -> str:
    return hashlib.sha256(f'{book_id}:{generation}:{ENGINE}'.encode()).hexdigest()[:24]


def _published_status(progress: dict) -> str:
    status = progress.get('status')
    if status == 'complete' and not progress.get('held_sections'):
        return 'ready'
    if status in ('rendering', 'queued', 'analyzing'):
        return 'rendering'
    return 'held'


def _source_ranges(book: dict, rows: list[dict]) -> dict[str, tuple[int, int]]:
    blocks = {block['id']: block.get('text', '') for chapter in book['chapters'] for block in chapter['blocks']}
    cursors: dict[str, int] = {}
    result = {}
    previous_end = -1.0
    for row in rows:
        unit = row.get('id')
        source_id = row.get('source_id')
        text = row.get('text')
        if not isinstance(unit, str) or not isinstance(source_id, str) or not isinstance(text, str):
            raise ValueError('Published timeline contains an invalid text unit')
        source = blocks.get(source_id)
        if source is None:
            raise ValueError(f'Published unit {unit} refers to an unknown source block')
        start = source.find(text, cursors.get(source_id, 0))
        if start < 0:
            # A publisher may retain surrounding whitespace in the unit while
            # the book block has already normalized it.  Only the exact trimmed
            # slice is accepted; letters and punctuation may never change.
            trimmed = text.strip()
            start = source.find(trimmed, cursors.get(source_id, 0))
            if start < 0:
                raise ValueError(f'Published unit {unit} is not an ordered slice of its source block')
            text = trimmed
        end = start + len(text)
        result[unit] = (start, end)
        cursors[source_id] = end
        audio_start = float(row.get('start', -1))
        audio_end = float(row.get('end', -1))
        if not 0 <= audio_start < audio_end or audio_start < previous_end:
            raise ValueError('Published timeline is not ordered and non-overlapping')
        previous_end = audio_end
    return result


def _wav_parts(source: Path, rows: list[dict]):
    with wave.open(str(source), 'rb') as audio:
        channels, width, rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
        frames = audio.getnframes()
        compression = audio.getcomptype()
        if channels != 1 or width != 2 or rate != 24000 or compression != 'NONE':
            raise ValueError('Published speech must be mono 16-bit 24 kHz PCM')
        body = audio.readframes(frames)
    boundaries = [0]
    for left, right in zip(rows, rows[1:]):
        middle = (float(left['end']) + float(right['start'])) / 2
        boundary = max(boundaries[-1], min(frames, round(middle * rate)))
        boundaries.append(boundary)
    boundaries.append(frames)
    if len(boundaries) != len(rows) + 1 or any(a >= b for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError('Published timeline cannot be split into non-empty production segments')
    frame_bytes = channels * width
    for index, row in enumerate(rows):
        first, last = boundaries[index], boundaries[index + 1]
        output = io.BytesIO()
        with wave.open(output, 'wb') as clip:
            clip.setnchannels(channels)
            clip.setsampwidth(width)
            clip.setframerate(rate)
            clip.writeframes(body[first * frame_bytes:last * frame_bytes])
        yield row, output.getvalue(), first, last, rate


def _write_audio(data_dir: Path, raw: bytes, samples: int, rate: int):
    key = hashlib.sha256(raw).hexdigest()
    wav = data_dir / 'audio' / f'{key}.wav'
    metadata = wav.with_suffix('.json')
    if wav.exists():
        if file_digest(wav) != key:
            raise ValueError(f'Immutable production audio changed: {wav.name}')
    else:
        atomic_write(wav, raw)
    value = {'sha256': key, 'duration': samples / rate, 'samples': samples,
             'sample_rate': rate, 'warning': '', 'reset_cues': False}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if not metadata.exists() or metadata.read_bytes() != encoded:
        atomic_write(metadata, encoded)
    return key, value['duration']


def _chapter_key(ready: dict) -> str:
    binding = ready.get('timeline_asset') or {}
    return hashlib.sha256(json.dumps({'audio': ready.get('wav_sha256'), 'timeline': binding.get('sha256'),
                                      'units': ready.get('units')}, sort_keys=True).encode()).hexdigest()


def _chapter_reusable(store: Store, generation_id: str, chapter_id: str, checkpoint: dict) -> bool:
    expected = checkpoint.get('segments') or []
    rows = store.rows('SELECT id,audio_hash FROM segments WHERE generation_id=? AND chapter_id=? ORDER BY ordinal',
                      (generation_id, chapter_id))
    return [row['id'] for row in rows] == expected and all(row['audio_hash'] for row in rows)


def _load_publication(workspace: Path, generation_dir: Path, chapter_id: str, ready: dict):
    binding = ready.get('timeline_asset')
    if not isinstance(binding, dict) or binding.get('audio_sha256') != ready.get('wav_sha256'):
        raise ValueError(f'{chapter_id} has no audio-bound immutable timeline')
    timeline = _inside(generation_dir, str(binding.get('file', '')), 'Timeline')
    if not timeline.is_relative_to((generation_dir / chapter_id).resolve()) or file_digest(timeline) != binding.get('sha256'):
        raise ValueError(f'{chapter_id} timeline checksum changed')
    rows = read_json(timeline)
    if not isinstance(rows, list) or not rows or len(rows) != ready.get('units'):
        raise ValueError(f'{chapter_id} timeline coverage changed')
    stream = ready.get('stream')
    prefix = '/character-rb2/full/clips/elirm-iv-mvp/'
    if not isinstance(stream, str) or not stream.startswith(prefix):
        raise ValueError(f'{chapter_id} has no permitted published speech asset')
    media = (workspace / 'training-work' / stream.lstrip('/')).resolve()
    allowed = (workspace / 'training-work/character-rb2/full/clips/elirm-iv-mvp').resolve()
    if not media.is_relative_to(allowed) or file_digest(media) != ready.get('wav_sha256'):
        raise ValueError(f'{chapter_id} published speech checksum changed')
    with wave.open(str(media), 'rb') as audio:
        seconds = audio.getnframes() / audio.getframerate()
    if abs(seconds - float(ready.get('seconds', -1))) > 1 / 24000 or float(rows[-1]['end']) > seconds + 1 / 24000:
        raise ValueError(f'{chapter_id} timeline duration differs from published speech')
    return media, rows


def _music_publications(workspace: Path, chapter_id: str, ready: dict):
    """Verify the immutable, already mixed music assets published with a chapter."""
    entries = ready.get('music') or []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise ValueError(f'{chapter_id} has an invalid music publication')
    ending_seconds = sum(float(entry.get('seconds', 0)) for entry in entries
                         if isinstance(entry, dict) and entry.get('kind') == 'ending')
    prefix = '/character-rb2/full/clips/elirm-iv-mvp/'
    allowed = (workspace / 'training-work/character-rb2/full/clips/elirm-iv-mvp').resolve()
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('kind') not in ('scene_bed', 'ending'):
            raise ValueError(f'{chapter_id} has an invalid music asset')
        stream = entry.get('stream')
        if not isinstance(stream, str) or not stream.startswith(prefix):
            raise ValueError(f'{chapter_id} has no permitted published music asset')
        media = (workspace / 'training-work' / stream.lstrip('/')).resolve()
        sha256 = entry.get('sha256')
        if not media.is_relative_to(allowed) or not isinstance(sha256, str) or file_digest(media) != sha256:
            raise ValueError(f'{chapter_id} published music checksum changed')
        with wave.open(str(media), 'rb') as audio:
            seconds = audio.getnframes() / audio.getframerate()
        published_seconds = float(entry.get('seconds', -1))
        allowed_seconds = [published_seconds]
        # The preview renderer's scene-bed file deliberately contains the
        # ending tail as well; the separately published ending slice lets the
        # live player continue it after narration stops without restarting bed.
        if entry['kind'] == 'scene_bed' and ending_seconds:
            allowed_seconds.append(published_seconds + ending_seconds)
        if not any(abs(seconds - expected) <= 1 / 24000 for expected in allowed_seconds):
            raise ValueError(f'{chapter_id} published music duration changed')
        if entry['kind'] == 'scene_bed' and 'independently verified' not in str(entry.get('qualification', '')):
            raise ValueError(f'{chapter_id} music has no independent verification')
        result.append({**entry, 'path': media, 'file_seconds': seconds, 'seconds': published_seconds})
    return result


def _install_music_file(data_dir: Path, publication: dict):
    """Install without transcoding; use a hard link on the shared local volume."""
    key = publication['sha256']
    folder = data_dir / 'music'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{key}.wav'
    if target.exists():
        if file_digest(target) != key:
            raise ValueError(f'Immutable production music changed: {target.name}')
    else:
        try:
            os.link(publication['path'], target)
        except OSError:
            shutil.copyfile(publication['path'], target)
        if file_digest(target) != key:
            target.unlink(missing_ok=True)
            raise ValueError('Installed production music checksum changed')
    metadata = {'sha256': key, 'spec': {'source': ENGINE, 'mode': MUSIC_MODE},
                'cue': {'kind': publication['kind'], 'stream': publication['stream']}}
    write_json(target.with_suffix('.json'), metadata)
    return key


def _sync_music(store: Store, data_dir: Path, generation_id: str, chapters: dict, publications: dict):
    """Replace generated fallback cues with the verified chapter-length mixes."""
    if not any(publications.values()):
        return 0
    # The bridge can seed a fresh library before MusicJobs has ever started, so
    # create the two additive music tables with the runtime's current schema.
    store.execute('''CREATE TABLE IF NOT EXISTS music_cues (generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
        id TEXT NOT NULL, kind TEXT NOT NULL, chapter_id TEXT NOT NULL, anchor_segment_id TEXT NOT NULL, start REAL NOT NULL,
        mood TEXT NOT NULL, spec_key TEXT NOT NULL, status TEXT NOT NULL, audio_key TEXT, duration REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, style TEXT NOT NULL DEFAULT '', PRIMARY KEY(generation_id,id))''')
    store.execute('''CREATE TABLE IF NOT EXISTS music_scenes (generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
        id TEXT NOT NULL, ordinal INTEGER NOT NULL, chapter_id TEXT NOT NULL, anchor_segment_id TEXT NOT NULL, mood TEXT NOT NULL,
        mood_source TEXT NOT NULL, bed TEXT NOT NULL, ambience TEXT NOT NULL, pattern TEXT NOT NULL, PRIMARY KEY(generation_id,id))''')
    installed = []
    for chapter_id, entries in publications.items():
        segments = store.rows('SELECT id FROM segments WHERE generation_id=? AND chapter_id=? ORDER BY ordinal LIMIT 1',
                              (generation_id, chapter_id))
        if not segments:
            raise ValueError(f'{chapter_id} music has no production timeline anchor')
        anchor = segments[0]['id']
        for entry in entries:
            audio_key = _install_music_file(data_dir, entry)
            kind = 'chapter_mix' if entry['kind'] == 'scene_bed' else 'ending'
            cue_id = f'{kind}:{chapter_id}'
            installed.append((generation_id, cue_id, kind, chapter_id, anchor, float(entry.get('start', 0)),
                              'mixed', hashlib.sha256(f'{MUSIC_MODE}:{audio_key}'.encode()).hexdigest(),
                              'ready', audio_key, entry['seconds'], '', now(), MUSIC_MODE))
    with store.lock, store.db:
        store.db.execute('DELETE FROM music_scenes WHERE generation_id=?', (generation_id,))
        store.db.execute('DELETE FROM music_cues WHERE generation_id=?', (generation_id,))
        store.db.executemany('''INSERT INTO music_cues(generation_id,id,kind,chapter_id,anchor_segment_id,start,mood,
            spec_key,status,audio_key,duration,error,updated,style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', installed)
    return len(installed)


def sync_preview(*, workspace: Path, source: Path, prepared_book: Path, generation_dir: Path,
                 data_dir: Path) -> dict:
    workspace = workspace.resolve()
    source = source.resolve()
    prepared_book = prepared_book.resolve()
    generation_dir = generation_dir.resolve()
    data_dir = data_dir.resolve()
    prepared = read_json(prepared_book)
    source_sha = file_digest(source)
    if prepared.get('source_hash') != source_sha or prepared.get('id') != source_sha[:32]:
        raise ValueError('Prepared book is not bound to the exact original source file')
    progress = read_json(generation_dir / 'progress.json')
    generation_name = progress.get('generation')
    if generation_name != generation_dir.name:
        raise ValueError('Progress belongs to another generation folder')
    chapters = progress.get('chapters')
    if not isinstance(chapters, dict) or not chapters:
        raise ValueError('No published chapters are available for the production reader')
    music_publications = {chapter_id: _music_publications(workspace, chapter_id, ready)
                          for chapter_id, ready in chapters.items()}
    has_music = any(music_publications.values())

    state_path = data_dir / 'preview-sync.json'
    with sync_lock(data_dir):
        # A watcher and a one-shot repair can overlap. Read the checkpoint only
        # after taking the sync lock so neither update is based on stale state.
        # The production runtime still accesses the Store through SQLite's own
        # locking while this filesystem lock serializes sync writers only.
        prior = read_json(state_path) if state_path.exists() else {}
        store = Store(data_dir)
        try:
            existing = store.rows('SELECT id FROM books WHERE id=?', (prepared['id'],))
            book = store.book(prepared['id']) if existing else store.import_data(source.read_bytes(), source.name)
            if book['id'] != prepared['id']:
                raise ValueError('Production importer derived a different book identity')
            imported_blocks = {block['id']: block.get('text', '') for chapter in book['chapters'] for block in chapter['blocks']}
            prepared_blocks = {block['id']: block.get('text', '') for chapter in prepared['chapters'] for block in chapter['blocks']}
            if any(imported_blocks.get(key) != value for key, value in prepared_blocks.items()):
                raise ValueError('Production importer text differs from the prepared narration source')

            generation_id = _generation_id(book['id'], generation_name)
            current = store.rows('SELECT * FROM generations WHERE id=?', (generation_id,))
            chapter_ids = list(chapters)
            config = {'pack_name': 'Fine-tuned Higgs listening generation',
                      'pack_fingerprint': hashlib.sha256(generation_name.encode()).hexdigest(),
                      'chapters': chapter_ids, 'align_words': False, 'engine': ENGINE,
                      **({'external_music': MUSIC_MODE} if has_music else {})}
            # The watcher never replaces or backfills a historical generation's pin.
            if current:
                old_config = json.loads(current[0]['config'])
                if 'opening' in old_config:
                    config['opening'] = old_config['opening']
            else:
                from rimsky.opening import attach_opening
                config['opening'] = attach_opening(store, book)
            status = _published_status(progress)
            assignment_flags = {chapter_id: int(ready.get('assignment_flags', 0) or 0)
                                for chapter_id, ready in chapters.items()}
            held_sections = dict(progress.get('held_sections') or {})
            production_approved = bool(progress.get('production_approved')) and status == 'ready'
            analysis = {'complete': status == 'ready', 'approved': production_approved,
                        'external_preview': {'generation': generation_name,
                                             'effects_policy': progress.get('effects_policy'),
                                             'production_approved': production_approved,
                                             'held_sections': held_sections,
                                             'assignment_flags': assignment_flags,
                                             'limitations': list(progress.get('limitations') or []),
                                             'pending_checks': {
                                                 'held_sections': len(held_sections),
                                                 'assignment_flags': sum(assignment_flags.values())}}}
            created = current[0]['created'] if current else now()
            error = str(progress.get('error') or '')[:4000]
            if not current:
                store.execute('''INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis,error,parent_id)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,NULL)''',
                              (generation_id, book['id'], generation_name, created, now(), status,
                               json.dumps(config, ensure_ascii=False), '[]', json.dumps(analysis, ensure_ascii=False), error))

            next_chapters = {}
            changed_chapters = []
            ordinal_base = 0
            speakers = {}
            for chapter_id, ready in chapters.items():
                if not isinstance(ready, dict):
                    raise ValueError(f'Invalid publication record for {chapter_id}')
                media, rows = _load_publication(workspace, generation_dir, chapter_id, ready)
                ranges = _source_ranges(prepared, rows)
                key = _chapter_key(ready)
                checkpoint = (prior.get('chapters') or {}).get(chapter_id, {})
                reusable = checkpoint.get('key') == key and _chapter_reusable(store, generation_id, chapter_id, checkpoint)
                if reusable:
                    saved = store.rows('SELECT * FROM segments WHERE generation_id=? AND chapter_id=? ORDER BY ordinal',
                                       (generation_id, chapter_id))
                    expected_ordinals = list(range(ordinal_base, ordinal_base + len(saved)))
                    if [row['ordinal'] for row in saved] != expected_ordinals:
                        with store.lock, store.db:
                            store.db.executemany('UPDATE segments SET ordinal=? WHERE generation_id=? AND id=?',
                                                 [(ordinal, generation_id, row['id'])
                                                  for ordinal, row in zip(expected_ordinals, saved)])
                        changed_chapters.append(chapter_id)
                    next_chapters[chapter_id] = checkpoint
                    ordinal_base += len(saved)
                    for row in rows:
                        speakers[str(row.get('speaker') or 'narrator')] = str(row.get('voice') or '')
                    continue

                built = []
                for index, (row, raw, first, last, rate) in enumerate(_wav_parts(media, rows)):
                    audio_hash, duration = _write_audio(data_dir, raw, last - first, rate)
                    start, end = ranges[row['id']]
                    speaker = str(row.get('speaker') or 'narrator')
                    delivery = str(row.get('mood') or 'neutral')
                    speakers[speaker] = str(row.get('voice') or '')
                    performance = {'schema': 1, 'delivery': delivery, 'pace': 'normal', 'ending_pace': None,
                                   'pause_before_seconds': 0, 'pause_after_seconds': 0, 'cues': [],
                                   'source_id': row['source_id'], 'basis': 'immutable-higgs-preview',
                                   'external_preview': {'chapter_audio_sha256': ready['wav_sha256'],
                                                        'timeline_sha256': ready['timeline_asset']['sha256'],
                                                        'published_start': row['start'], 'published_end': row['end'],
                                                        'clip_frame_start': first, 'clip_frame_end': last}}
                    if isinstance(row.get('synthesis'), dict):
                        performance['synthesis_snapshot'] = row['synthesis']
                    built.append({'generation_id': generation_id, 'id': row['id'], 'ordinal': ordinal_base + index,
                                  'chapter_id': chapter_id, 'source_id': row['source_id'], 'start': start, 'end': end,
                                  'text': row['text'], 'spoken': row['text'].strip(), 'speaker': speaker,
                                  'delivery': delivery, 'status': 'ready', 'audio_hash': audio_hash,
                                  'duration': duration, 'cues': '[]', 'warning': '',
                                  'performance': json.dumps(performance, ensure_ascii=False)})
                columns = ('generation_id','id','ordinal','chapter_id','source_id','start','end','text','spoken','speaker',
                           'delivery','status','audio_hash','duration','cues','warning','performance')
                with store.lock, store.db:
                    incoming = {row['id'] for row in built}
                    for stale in store.db.execute('SELECT id FROM segments WHERE generation_id=? AND chapter_id=?',
                                                  (generation_id, chapter_id)).fetchall():
                        if stale['id'] not in incoming:
                            store.db.execute('DELETE FROM segments WHERE generation_id=? AND id=?', (generation_id, stale['id']))
                    placeholders = ','.join('?' for _ in columns)
                    updates = ','.join(f'{column}=excluded.{column}' for column in columns[2:])
                    store.db.executemany(f'''INSERT INTO segments({','.join(columns)}) VALUES({placeholders})
                                             ON CONFLICT(generation_id,id) DO UPDATE SET {updates}''',
                                         [tuple(row[column] for column in columns) for row in built])
                next_chapters[chapter_id] = {'key': key, 'audio_sha256': ready['wav_sha256'],
                                             'timeline_sha256': ready['timeline_asset']['sha256'],
                                             'segments': [row['id'] for row in built]}
                ordinal_base += len(built)
                changed_chapters.append(chapter_id)

            music_cues = _sync_music(store, data_dir, generation_id, chapters, music_publications) if has_music else 0

            cast = [{'id': speaker, 'name': 'Narrator' if speaker == 'narrator' else speaker,
                     'aliases': [], 'description': '', 'evidence': [], 'voice_id': voice, 'voice_name': voice}
                    for speaker, voice in sorted(speakers.items())]
            metadata = (generation_name, now(), status, json.dumps(config, ensure_ascii=False),
                        json.dumps(cast, ensure_ascii=False), json.dumps(analysis, ensure_ascii=False), error, generation_id)
            # CAST is a SQLite keyword; selecting the whole row also keeps this
            # comparison resilient to schema additions in the production Store.
            stored = store.rows('SELECT * FROM generations WHERE id=?', (generation_id,))[0]
            wanted = (generation_name, status, metadata[3], metadata[4], metadata[5], error)
            actual = tuple(stored[key] for key in ('name','status','config','cast','analysis','error'))
            metadata_changed = actual != wanted
            if metadata_changed:
                store.execute('UPDATE generations SET name=?,updated=?,status=?,config=?,cast=?,analysis=?,error=? WHERE id=?', metadata)
            result = {'schema': SCHEMA, 'book_id': book['id'], 'generation_id': generation_id,
                      'generation': generation_name, 'status': status, 'chapters': next_chapters,
                      'segments': sum(len(value['segments']) for value in next_chapters.values()),
                      'music_cues': music_cues,
                      'changed_chapters': changed_chapters, 'metadata_changed': metadata_changed,
                      'synced_at': now()}
            write_json(state_path, result)
            return result
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT)
    parser.add_argument('--source', type=Path, default=ROOT / 'content/04 Элирм IV.fb2')
    parser.add_argument('--prepared-book', type=Path, default=ROOT / 'training-work/elirm-iv-higgs-mvp/source/book.json')
    parser.add_argument('--generation', type=Path, default=ROOT / 'training-work/elirm-iv-higgs-mvp/audio-palette-v3-ch123')
    parser.add_argument('--data-dir', type=Path,
                        default=ROOT / 'training-work/elirm-iv-higgs-mvp/production-listener-library')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=2.0)
    args = parser.parse_args()
    if args.interval < .25:
        parser.error('--interval must be at least 0.25 seconds')
    previous = None
    previous_inputs = None
    while True:
        try:
            progress_path=(args.generation / 'progress.json').resolve()
            if args.watch and not progress_path.is_file():
                previous_inputs=None
                time.sleep(args.interval)
                continue
            watched = (args.source.resolve(), args.prepared_book.resolve(),progress_path)
            input_state = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in watched)
            if args.watch:
                publication = read_json(watched[-1])
                if not publication.get('chapters'):
                    time.sleep(args.interval)
                    continue
            if args.watch and input_state == previous_inputs:
                time.sleep(args.interval)
                continue
            result = sync_preview(workspace=args.workspace, source=args.source, prepared_book=args.prepared_book,
                                  generation_dir=args.generation, data_dir=args.data_dir)
            previous_inputs = input_state
            fingerprint = (result['generation'], result['status'], tuple(result['changed_chapters']), result['segments'])
            if fingerprint != previous or result['changed_chapters'] or result['metadata_changed']:
                summary = {key: result[key] for key in
                           ('schema', 'book_id', 'generation_id', 'generation', 'status', 'segments',
                            'changed_chapters', 'metadata_changed', 'synced_at')}
                print(json.dumps(summary, ensure_ascii=False), flush=True)
            previous = fingerprint
        except Exception as error:
            if not args.watch:
                raise
            print(json.dumps({'sync_error': f'{type(error).__name__}: {error}', 'at': now()}, ensure_ascii=False),
                  file=sys.stderr, flush=True)
        if not args.watch:
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
