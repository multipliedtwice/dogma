"""Progressive compressed playback from immutable, accepted saved audio only.

No synthesis, corpus writes, source rows, timing changes or branding prefix.
One bounded worker captures verified input files and publishes atomic MP3 chunks.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from .audio_source import open_audio_pair, open_shared_read
from .export import capture_audio, run_encoder
from .integrity import accepted_audio
from .packs import resolve_binary
from .playback_cache import collect as collect_cache, reading as cache_reading
from .util import AppError, atomic_write, digest, file_digest, require_id

CHUNK_SECONDS = 300
QUEUE_LIMIT = 64
ENCODING_LOCK = threading.Lock()


def _read(path):
    try:
        value = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError) as error:
        raise AppError('Saved playback input metadata is missing or invalid.') from error
    if not isinstance(value, dict):
        raise AppError('Saved playback input metadata is invalid.')
    return value


def _write(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def _relevant_layers(layers, start, duration):
    end = start + duration
    chosen = []
    for layer in layers:
        if layer['at'] >= end or layer['at'] + layer['duration'] <= start:
            continue
        item = copy.deepcopy(layer)
        if 'transitions' in item:
            item['transitions'] = [w for w in item['transitions'] if w['end'] > start and w['start'] - 6 < end]
        chosen.append(item)
    return chosen


class _Stop:
    def __init__(self, manager, task):
        self.manager, self.task = manager, task

    def is_set(self):
        return self.manager.closing.is_set() or self.manager.desired.get(self.task['generation_id']) != self.task['map_key']


class PlaybackStreams:
    def __init__(self, store, binaries, music, *, chunk_seconds=CHUNK_SECONDS, folder_name='playback'):
        self.store, self.binaries, self.music = store, binaries, music
        self.chunk_seconds = chunk_seconds
        self.folder = store.root / folder_name
        self.folder.mkdir(exist_ok=True)
        (self.folder / 'maps').mkdir(exist_ok=True)
        self.condition = threading.Condition()
        self.closing = threading.Event()
        self.queue, self.queued, self.contexts, self.desired = [], set(), {}, {}
        self.active = None
        self.cache_readers = {}
        self.cache_collected_at = 0.
        self.recipe = None
        self.thread = threading.Thread(target=self._worker, name='dogma-playback-compression', daemon=True)
        self.thread.start()

    def _recipe(self):
        if self.recipe is None:
            ffmpeg, probe = resolve_binary('ffmpeg', self.binaries), resolve_binary('ffprobe', self.binaries)
            if not ffmpeg or not probe:
                raise AppError('FFmpeg and FFprobe are required for continuous playback.')
            self.recipe = {'schema': 1, 'codec': 'libmp3lame', 'bitrate': '128k', 'sample_rate': 24000,
                           'channels': 1, 'threads': 2, 'chunk_seconds': self.chunk_seconds,
                           'ffmpeg': str(Path(ffmpeg).resolve()), 'ffmpeg_sha256': file_digest(Path(ffmpeg)),
                           'ffprobe': str(Path(probe).resolve()), 'ffprobe_sha256': file_digest(Path(probe)),
                           'implementation_sha256': file_digest(Path(__file__)),
                           'music_implementation_sha256': file_digest(Path(__file__).with_name('music.py')),
                           'transition_implementation_sha256': file_digest(Path(__file__).with_name('narration_transitions.py'))}
        return self.recipe

    def _source(self, segment):
        key = segment.get('audio_hash')
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
            raise AppError('Saved passage does not have an immutable audio key.')
        path = self.store.root / 'audio' / (key + '.wav')
        metadata = _read(path.with_suffix('.json'))
        if (not path.is_file() or not re.fullmatch('[a-f0-9]{64}', str(metadata.get('sha256', '')))
                or metadata.get('reset_cues', False) is not False
                or type(metadata.get('samples')) is not int or metadata['samples'] <= 0
                or type(metadata.get('sample_rate')) is not int or not 8000 <= metadata['sample_rate'] <= 192000
                or type(metadata.get('duration')) not in (int, float) or not math.isfinite(metadata['duration'])
                or abs(metadata['duration'] - segment['duration']) > 1 / metadata['sample_rate']):
            raise AppError('Saved passage metadata does not match its source timeline.')
        return {'segment_id': segment['id'], 'audio_hash': key, 'sha256': metadata['sha256'],
                'duration': segment['duration'], 'samples': metadata['samples'], 'sample_rate': metadata['sample_rate']}

    def _layers(self, gid, chapter_id, segments, snapshot, volume):
        from .music import scene_layers
        from .narration_transitions import bridge_layers, segment_windows
        cues = snapshot.get('cues') or []
        times, end = {}, 0.
        for segment in segments:
            times[segment['id']] = end
            end += float(segment.get('duration') or 0)
        imported = [c for c in cues if c['chapter_id'] == chapter_id and c['kind'] == 'chapter_mix']
        ending = next((c for c in cues if c['chapter_id'] == chapter_id and c['kind'] == 'ending'), None)
        if imported:
            if any(c['status'] != 'ready' or not c.get('audio_key') for c in imported):
                raise AppError('Chapter music is awaiting acceptance or rendering.')
            layers = [{'key': c['audio_key'], 'at': times[c['anchor_segment_id']] + c['start'],
                       'duration': c['duration'], 'window': None, 'kind': 'bed'} for c in imported]
        else:
            scenes = [s for s in snapshot.get('scenes', []) if s['chapter_id'] == chapter_id]
            if not scenes or any(not s.get('released') for s in scenes):
                raise AppError('Chapter music is awaiting a verified scene layout.')
            required = {f"ambience:{s['ambience']}" for s in scenes}
            relevant = [c for c in cues if c['id'] in required or any(c['id'].startswith('bed:' + s['bed'] + ':') for s in scenes)]
            if not relevant or any(c['status'] != 'ready' or not c.get('audio_key') for c in relevant):
                raise AppError('Chapter music contains pending or failed takes.')
            takes = {c['id']: {'audio_key': c['audio_key'], 'duration': c['duration']} for c in relevant}
            layers = bridge_layers(scene_layers(scenes, takes, times, end, ending), scenes, takes, times, segment_windows(segments))
        if ending:
            if ending['status'] != 'ready' or not ending.get('audio_key'):
                raise AppError('Chapter ending music is pending.')
            layers.append({'key': ending['audio_key'], 'at': times[ending['anchor_segment_id']] + ending['start'],
                           'duration': ending['duration'], 'window': None, 'kind': 'ending'})
        for layer in layers:
            layer.setdefault('kind', 'bed')
            layer['gain'] = volume
        return layers, end

    def _plan(self, gid, music, volume):
        generation = self.store.playback_generation(gid)
        recipe = self._recipe()
        snapshot = self.music.status(gid) if music and volume else {}
        holds, chunks = [], []
        if generation['status'] != 'ready':
            holds.append({'id': 'generation:' + gid, 'status': generation['status'],
                          'error': generation.get('error', ''),
                          'pending_checks': (generation.get('analysis', {}).get('external_preview') or {}).get('pending_checks', {})})
        chapters = list(dict.fromkeys(s['chapter_id'] for s in generation['segments']))
        published = []
        for path in self.folder.glob('*.json'):
            try:
                record = _read(path)
            except AppError:
                # A superseded record may have been collected after glob().
                if not path.exists():
                    continue
                raise
            spec = record.get('spec') or {}
            if (record.get('status') == 'ready' and spec.get('recipe') == recipe
                    and spec.get('music') == music and spec.get('music_volume') == volume
                    and record.get('key') == digest(spec)):
                published.append(record)
        for chapter_id in chapters:
            segments = [s for s in generation['segments'] if s['chapter_id'] == chapter_id]
            layers, chapter_end, music_error = [], sum(float(s.get('duration') or 0) for s in segments), None
            if music and volume:
                try:
                    layers, chapter_end = self._layers(gid, chapter_id, segments, snapshot, volume)
                except (AppError, KeyError, TypeError, ValueError) as error:
                    music_error = str(error)
                    holds.append({'id': 'music:' + chapter_id, 'chapter_id': chapter_id, 'status': 'pending', 'error': music_error})
            run, run_start, clock = [], 0., 0.
            positions, source_infos, chapter_clock = {}, {}, 0.
            for segment in segments:
                positions[segment['id']] = chapter_clock
                chapter_clock += float(segment.get('duration') or 0)
            by_id = {s['id']: s for s in segments}

            def source_of(segment):
                if segment['id'] not in source_infos:
                    source_infos[segment['id']] = self._source(segment)
                return source_infos[segment['id']]

            sealed = {}
            for record in published:
                old = record['spec']
                if old.get('chapter_id') != chapter_id or not old.get('parts'):
                    continue
                if abs(positions.get(old['parts'][0]['segment_id'], -1) - old['start']) > .00001:
                    continue
                if old['duration'] > old['speech_duration'] and abs(old['chapter_duration'] - chapter_end) > .00001:
                    continue
                if _relevant_layers(layers, old['start'], old['duration']) != _relevant_layers(old['layers'], old['start'], old['duration']):
                    continue
                try:
                    if any(p['segment_id'] not in by_id or by_id[p['segment_id']]['status'] != 'ready'
                           or source_of(by_id[p['segment_id']]) != {k: v for k, v in p.items() if k != 'start'}
                           for p in old['parts']):
                        continue
                    if any(key not in {l['key'] for l in layers}
                           or _read(self.store.root / 'music' / (key + '.json')).get('sha256') != value
                           for key, value in old['music_files'].items()):
                        continue
                except (AppError, OSError, ValueError, TypeError):
                    continue
                # Once served, a short progressive tail is a sealed playback unit.
                # Appended source starts a NEW chunk; corrections still invalidate it.
                previous = sealed.get(round(old['start'], 6))
                if previous is None or len(old['parts']) < len(previous['spec']['parts']):
                    sealed[round(old['start'], 6)] = record

            def flush():
                nonlocal run, run_start
                if not run:
                    return
                speech_duration = sum(p['duration'] for p in run)
                end = run_start + speech_duration
                tail = max([0.] + [l['at'] + l['duration'] - chapter_end for l in layers if l['kind'] == 'ending']) if abs(end - chapter_end) < .001 else 0.
                duration = speech_duration + tail
                chosen_layers = _relevant_layers(layers, run_start, duration)
                music_files = {}
                for layer in chosen_layers:
                    key = layer['key']
                    if not re.fullmatch('[a-f0-9]{64}', str(key)):
                        raise AppError('Music has an invalid immutable audio key.')
                    metadata = _read(self.store.root / 'music' / (key + '.json'))
                    if not re.fullmatch('[a-f0-9]{64}', str(metadata.get('sha256', ''))):
                        raise AppError('Music metadata is missing its verified checksum.')
                    music_files[key] = metadata['sha256']
                offset, parts = 0., []
                for part in run:
                    parts.append({**part, 'start': offset})
                    offset += part['duration']
                spec = {'schema': 1, 'chapter_id': chapter_id, 'start': run_start, 'duration': duration,
                        'speech_duration': speech_duration, 'chapter_duration': chapter_end,
                        'parts': parts, 'music': music, 'music_volume': volume,
                        'layers': chosen_layers, 'music_files': music_files, 'recipe': recipe}
                prior = sealed.get(round(run_start, 6))
                if prior and parts == prior['spec']['parts'] and not music_error:
                    spec = prior['spec']
                chunks.append({'key': digest(spec), 'spec': spec, 'blocked': music_error})
                run = []

            for segment in segments:
                duration = float(segment.get('duration') or 0)
                if segment['status'] != 'ready' or not segment.get('audio_hash') or duration <= 0:
                    flush()
                    holds.append({'id': segment['id'], 'chapter_id': chapter_id, 'status': segment['status'], 'error': segment.get('warning', '')})
                else:
                    try:
                        source = source_of(segment)
                    except (AppError, OSError, ValueError, TypeError) as error:
                        flush()
                        holds.append({'id': segment['id'], 'chapter_id': chapter_id, 'status': 'failed', 'error': str(error)})
                    else:
                        if run and sum(p['duration'] for p in run) + duration > self.chunk_seconds:
                            flush()
                        if not run:
                            run_start = clock
                        run.append(source)
                        prior = sealed.get(round(run_start, 6))
                        if prior and len(run) == len(prior['spec']['parts']):
                            flush()
                clock += duration
            flush()
        shape = {'schema': 1, 'generation_id': gid, 'music': music, 'music_volume': volume,
                 'chunks': [c['key'] for c in chunks], 'holds': holds}
        return {**shape, 'key': digest(shape), 'chunks': chunks}

    def _record(self, key):
        path = self.folder / (key + '.json')
        if not path.exists():
            return None
        record = _read(path)
        if record.get('key') != key or digest(record.get('spec')) != key:
            raise AppError('Continuous playback metadata checksum mismatch.')
        return record

    def request(self, generation_id, music=False, music_volume=1., priority_segment_id=None, chapter_id=None):
        require_id(generation_id)
        if type(music) is not bool or type(music_volume) not in (int, float) or not math.isfinite(music_volume) or not 0 <= music_volume <= 2:
            raise AppError('Invalid continuous playback music settings.')
        plan = self._plan(generation_id, music, float(music_volume))
        with self.condition:
            self.desired[generation_id] = plan['key']
            self.contexts[generation_id] = plan
            focus = next((i for i, c in enumerate(plan['chunks']) if any(p['segment_id'] == priority_segment_id for p in c['spec']['parts'])), None)
            priority_chapter = plan['chunks'][focus]['spec']['chapter_id'] if focus is not None else chapter_id
            preferred = lambda item: (item[0] != focus, item[1]['spec']['chapter_id'] != priority_chapter,
                                      focus is not None and item[0] < focus, item[0])
            ordered = sorted(enumerate(plan['chunks']), key=preferred) if priority_segment_id or chapter_id else list(enumerate(plan['chunks']))
            plan['_order'] = [item[1]['key'] for item in ordered]
            self.queue = [task for task in self.queue if task['generation_id'] != generation_id]
            self.queued = {task['key'] for task in self.queue}
            for _, chunk in ordered:
                if len(self.queue) >= QUEUE_LIMIT:
                    break
                if chunk['blocked'] or chunk['key'] in self.queued or self.active and self.active['key'] == chunk['key']:
                    continue
                if self._record(chunk['key']) is None:
                    self.queue.append({'generation_id': generation_id, 'map_key': plan['key'], **chunk})
                    self.queued.add(chunk['key'])
            _write(self.folder / 'maps' / (plan['key'] + '.json'), {'generation_id': generation_id, 'music': music, 'music_volume': float(music_volume), 'key': plan['key'], 'chunks': [c['key'] for c in plan['chunks']]})
            self.condition.notify_all()
        if time.monotonic() - self.cache_collected_at > 300:
            self.prune_cache()
        return self._public(plan)

    def prune_cache(self, *, execute=True, grace_seconds=1800):
        result = collect_cache(self, execute=execute, grace_seconds=grace_seconds)
        if execute:
            self.cache_collected_at = time.monotonic()
        return result

    def _public(self, plan):
        chunks, errors = [], []
        for chunk in plan['chunks']:
            spec = chunk['spec']
            record = self._record(chunk['key'])
            state = 'pending' if chunk['blocked'] or record is None else record['status']
            item = {'key': chunk['key'], 'chapter_id': spec['chapter_id'], 'start': spec['start'], 'duration': spec['duration'],
                    'status': state, 'parts': [{k: p[k] for k in ('segment_id', 'audio_hash', 'start', 'duration')} for p in spec['parts']]}
            if record and state == 'ready':
                item['audio_hash'] = record['audio_sha256']
            if record and state == 'failed':
                item['error'] = record['error']
                errors.append(record['error'])
            chunks.append(item)
        ready = sum(c['status'] == 'ready' for c in chunks)
        status = ('failed' if chunks and all(c['status'] == 'failed' for c in chunks)
                  else 'ready' if chunks and ready == len(chunks) and not plan['holds']
                  else 'partial' if ready or plan['holds'] else 'failed' if errors else 'pending')
        return {'schema': 1, 'key': plan['key'], 'status': status, 'music': plan['music'], 'music_volume': plan['music_volume'],
                'chunks': chunks, 'holds': plan['holds'], **({'error': '; '.join(dict.fromkeys(errors))} if errors else {})}

    def _worker(self):
        while not self.closing.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.queue or self.closing.is_set())
                if self.closing.is_set():
                    break
                task = self.queue.pop(0)
                self.queued.discard(task['key'])
                self.active = task
            stop = _Stop(self, task)
            try:
                while not stop.is_set():
                    if ENCODING_LOCK.acquire(timeout=.2):
                        try:
                            if not stop.is_set():
                                self._build(task, stop)
                        finally:
                            ENCODING_LOCK.release()
                        break
            except Exception as error:
                if not stop.is_set():
                    _write(self.folder / (task['key'] + '.json'), {'key': task['key'], 'spec': task['spec'], 'status': 'failed', 'error': str(error)[:2000]})
            finally:
                with self.condition:
                    self.active = None
                    for gid, plan in self.contexts.items():
                        if self.desired.get(gid) != plan['key']:
                            continue
                        by_key = {c['key']: c for c in plan['chunks']}
                        for key in plan.get('_order', by_key):
                            if len(self.queue) >= QUEUE_LIMIT:
                                break
                            chunk = by_key[key]
                            if key not in self.queued and not chunk['blocked'] and self._record(key) is None:
                                self.queue.append({'generation_id': gid, 'map_key': plan['key'], **chunk})
                                self.queued.add(key)

    def _build(self, task, stop):
        with self.store.audio_lease(p['audio_hash'] for p in task['spec']['parts']):
            self._build_leased(task, stop)

    def _build_leased(self, task, stop):
        spec, key = task['spec'], task['key']
        with tempfile.TemporaryDirectory(prefix='encode-', dir=self.folder) as temporary:
            folder = Path(temporary)
            snapshots = []
            for index, part in enumerate(spec['parts']):
                if stop.is_set():
                    return
                source = self.store.root / 'audio' / (part['audio_hash'] + '.wav')
                snapshot = folder / f'speech-{index:04d}.wav'
                with self.store.lock, open_audio_pair(source) as (audio, metadata):
                    capture_audio(source, snapshot, audio, metadata)
                actual = accepted_audio(snapshot)
                if (actual['reset_cues'] or actual['sha256'] != part['sha256'] or actual['samples'] != part['samples']
                        or actual['sample_rate'] != part['sample_rate'] or abs(actual['duration'] - part['duration']) > 1 / actual['sample_rate']):
                    raise AppError('Accepted source audio changed before continuous playback compression.')
                snapshots.append(snapshot)
            if len({p['sample_rate'] for p in spec['parts']}) != 1:
                raise AppError('Saved passages use different sample rates; continuous playback was not published.')
            listing = folder / 'speech.txt'
            listing.write_text(''.join("file '" + p.name + "'\n" for p in snapshots), encoding='utf-8')
            command = [spec['recipe']['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                       '-f', 'concat', '-safe', '1', '-i', str(listing)]
            if spec['layers']:
                music_paths = {}
                for music_key, expected in spec['music_files'].items():
                    source = self.store.root / 'music' / (music_key + '.wav')
                    snapshot = folder / ('music-' + music_key + '.wav')
                    with self.store.lock, open_audio_pair(source) as (audio, metadata):
                        capture_audio(source, snapshot, audio, metadata)
                    if file_digest(snapshot, stop) != expected or _read(snapshot.with_suffix('.json')).get('sha256') != expected:
                        raise AppError('Verified music changed before continuous playback compression.')
                    music_paths[music_key] = snapshot
                self._mix_track(spec, music_paths, folder / 'music.wav', stop)
                command += ['-i', str(folder / 'music.wav'), '-filter_complex',
                            '[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]', '-map', '[mixed]']
            destination = folder / 'chunk.mp3'
            command += ['-t', str(spec['duration']), '-ar', '24000', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '128k',
                        '-threads', '2', '-map_metadata', '-1', str(destination)]
            result = run_encoder(command, stop, timeout=300)
            if result.returncode or not destination.is_file():
                raise AppError('Continuous playback encoding failed: ' + result.stderr.decode('utf-8', 'replace')[-1000:])
            result = run_encoder([spec['recipe']['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(destination)], stop, timeout=30)
            details = json.loads(result.stdout)
            streams = [s for s in details.get('streams', []) if s.get('codec_type') == 'audio']
            measured = float(details['format']['duration'])
            if result.returncode or len(streams) != 1 or streams[0]['codec_name'] != 'mp3' or not math.isfinite(measured) or abs(measured - spec['duration']) > .2:
                raise AppError('Compressed playback did not preserve the complete saved-audio duration.')
            live = self._plan(task['generation_id'], spec['music'], spec['music_volume'])
            if stop.is_set() or not any(c['key'] == key for c in live['chunks']):
                if self.desired.get(task['generation_id']) == task['map_key']:
                    self.desired[task['generation_id']] = live['key']
                return
            record = {'key': key, 'spec': spec, 'status': 'ready', 'audio_sha256': file_digest(destination),
                      'bytes': destination.stat().st_size, 'duration': spec['duration'], 'encoded_duration': measured}
            atomic_write(self.folder / (key + '.mp3'), destination.read_bytes())
            _write(self.folder / (key + '.json'), record)

    def _mix_track(self, spec, paths, destination, stop):
        import numpy as np
        import soundfile as sf
        from .music import window_gain
        from .narration_transitions import envelope, POLICY
        rate = 24000
        first_frame = round(spec['start'] * rate)
        frames = round(spec['duration'] * rate)
        chapter_end = round(spec['chapter_duration'] * rate)
        with ExitStack() as stack:
            sources = {key: stack.enter_context(sf.SoundFile(path)) for key, path in paths.items()}
            if any(s.samplerate not in (24000, 48000) for s in sources.values()):
                raise AppError('Verified music has an unsupported sample rate.')
            target = stack.enter_context(sf.SoundFile(destination, 'w', rate, 1, 'PCM_16'))
            for first in range(0, frames, rate * 10):
                if stop.is_set():
                    raise AppError('Continuous playback compression was superseded.')
                last = min(frames, first + rate * 10)
                mixed = np.zeros(last - first, 'float32')
                absolute_first, absolute_last = first_frame + first, first_frame + last
                for layer in spec['layers']:
                    source = sources[layer['key']]
                    ratio = source.samplerate // rate
                    begin = round(layer['at'] * rate)
                    end = begin + source.frames // ratio
                    if layer['kind'] != 'ending':
                        end = min(end, chapter_end)
                    low, high = max(begin, absolute_first), min(end, absolute_last)
                    if low >= high:
                        continue
                    source.seek((low - begin) * ratio)
                    raw = source.read((high - low) * ratio, dtype='float32', always_2d=True).mean(axis=1)
                    piece = raw if ratio == 1 else (raw[0::2] + raw[1::2]) / 2
                    t = np.arange(low, high) / rate
                    piece *= layer['gain']
                    if layer['kind'] == 'transition':
                        piece *= envelope(layer['transition'], t)
                    elif layer['kind'] != 'ending':
                        if layer.get('window'):
                            piece *= window_gain(layer['window'], t)
                        for transition in layer.get('transitions', []):
                            piece *= 1 - np.clip(envelope(transition, t) / POLICY['speech_gain'], 0, 1)
                    mixed[low - absolute_first:high - absolute_first] += piece
                target.write(np.clip(mixed, -1, 1))

    @contextmanager
    def open(self, key):
        require_id(key)
        if not re.fullmatch('[a-f0-9]{64}', key):
            raise AppError('Invalid continuous playback key.')
        record = self._record(key)
        if not record or record['status'] != 'ready':
            raise AppError('Continuous playback audio is not ready.')
        references = []
        for path in (self.folder / 'maps').glob('*.json'):
            value = _read(path)
            if key in value.get('chunks', []):
                references.append(value)
        authorized = False
        for reference in references:
            try:
                plan = self._plan(reference['generation_id'], reference['music'], reference['music_volume'])
            except AppError:
                continue
            if any(c['key'] == key and not c['blocked'] for c in plan['chunks']):
                authorized = True
                break
        if not authorized:
            raise AppError('Continuous playback audio is no longer referenced by the current saved source.')
        with cache_reading(self, key), open_shared_read(self.folder / (key + '.mp3')) as source:
            if os.fstat(source.fileno()).st_size != record['bytes'] or hashlib.file_digest(source, 'sha256').hexdigest() != record['audio_sha256']:
                raise AppError('Continuous playback audio checksum mismatch.')
            source.seek(0)
            yield source

    def shutdown(self):
        self.closing.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=10)
