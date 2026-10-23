"""Standard AAC/fMP4 HLS programs encoded directly from accepted PCM.

One chapter/run uses one encoder and continuous timestamps. No MP3 input,
per-sentence decoder switches, synthesis, source edits or branded prefix.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .audio_source import open_audio_pair, open_shared_read
from .export import capture_audio, run_encoder
from .integrity import accepted_audio, validate_coverage
from .playback_streams import PlaybackStreams, _read, _write
from .util import AppError, digest, file_digest, require_id


class PlaybackHLS(PlaybackStreams):
    def __init__(self, store, binaries, music):
        # A chapter/run is one HLS program; only HLS fragments have 6s boundaries.
        super().__init__(store, binaries, music, chunk_seconds=86400, folder_name='playback-hls')

    def _recipe(self):
        value = super()._recipe()
        return {**value, 'codec': 'aac', 'profile': 'aac_low', 'sample_rate': 48000,
                'container': 'hls-fmp4', 'fragment_seconds': 6, 'playlist_type': 'vod',
                'hls_implementation_sha256': file_digest(Path(__file__))}

    def _plan(self, gid, music, volume):
        plan = super()._plan(gid, music, volume)
        generation = self.store.generation(gid)
        book = self.store._book_manifest(generation['book_id'])
        active = generation['status'] in ('queued', 'analyzing', 'casting', 'rendering', 'aligning')
        for chapter_id in dict.fromkeys(c['spec']['chapter_id'] for c in plan['chunks']):
            segments = [s for s in generation['segments'] if s['chapter_id'] == chapter_id]
            chunks = [c for c in plan['chunks'] if c['spec']['chapter_id'] == chapter_id]
            complete = (all(s['status'] == 'ready' for s in segments)
                        and {s['id'] for s in segments} == {p['segment_id'] for c in chunks for p in c['spec']['parts']})
            if complete:
                try:
                    # External previews may contain only the ready prefix rows.
                    # Compare with the book, rather than treating those rows as a chapter.
                    validate_coverage(book, [chapter_id], segments)
                except AppError:
                    complete = False
            continuation = any(s['status'] in ('pending', 'queued', 'rendering', 'aligning') for s in segments)
            if complete or not (active or continuation):
                continue
            for chunk in chunks:
                if chunk['spec']['speech_duration'] < 300 and not chunk['blocked']:
                    chunk['blocked'] = 'Buffering at least five minutes of accepted chapter audio, or the complete chapter.'
                    chunk['pending_reason'] = 'chapter_buffering'
        # Buffering is transport readiness, never a source quality hold.
        plan['key'] = digest({'source_map': plan['key'], 'buffering': [
            c['key'] for c in plan['chunks'] if c.get('pending_reason') == 'chapter_buffering']})
        return plan

    def _public(self, plan):
        value = super()._public(plan)
        value['transport'] = 'hls'
        internal = {c['key']: c for c in plan['chunks']}
        for chunk in value['chunks']:
            chunk['hls'] = True
            if internal[chunk['key']].get('pending_reason'):
                chunk['pending_reason'] = internal[chunk['key']]['pending_reason']
                chunk['pending_message'] = internal[chunk['key']]['blocked']
        return value

    def _build(self, task, stop):
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
                    raise AppError('Accepted PCM changed before HLS encoding.')
                snapshots.append(snapshot)
            if len({p['sample_rate'] for p in spec['parts']}) != 1:
                raise AppError('Accepted passages use different sample rates; no HLS program was published.')
            listing = folder / 'speech.txt'
            listing.write_text(''.join("file '" + path.name + "'\n" for path in snapshots), encoding='utf-8')
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
                        raise AppError('Approved music changed before HLS encoding.')
                    music_paths[music_key] = snapshot
                self._mix_track(spec, music_paths, folder / 'music.wav', stop)
                command += ['-i', str(folder / 'music.wav'), '-filter_complex',
                            '[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]', '-map', '[mixed]']
            program = folder / 'program'
            program.mkdir()
            playlist = program / 'index.m3u8'
            command += ['-t', str(spec['duration']), '-ar', '48000', '-ac', '1', '-c:a', 'aac', '-profile:a', 'aac_low',
                        '-b:a', '128k', '-threads', '2', '-map_metadata', '-1', '-f', 'hls', '-hls_time', '6',
                        '-hls_playlist_type', 'vod', '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
                        '-hls_segment_filename', (program / 'f%06d.m4s').as_posix(), '-hls_flags', 'independent_segments', playlist.as_posix()]
            result = run_encoder(command, stop, timeout=600)
            if result.returncode or not playlist.is_file():
                raise AppError('PCM-to-AAC HLS encoding failed: ' + result.stderr.decode('utf-8', 'replace')[-1000:])
            # FFmpeg's HLS relative-URI resolver needs forward-slash paths on Windows.
            result = run_encoder([spec['recipe']['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', playlist.as_posix()], stop, timeout=30)
            try:
                details = json.loads(result.stdout)
                streams = [s for s in details['streams'] if s.get('codec_type') == 'audio']
                measured = float(details['format']['duration'])
                if (result.returncode or len(streams) != 1 or streams[0]['codec_name'] != 'aac'
                        or int(streams[0]['sample_rate']) != 48000 or streams[0]['channels'] != 1
                        or not math.isfinite(measured) or abs(measured - spec['duration']) > .2):
                    raise ValueError('AAC format or duration differs')
            except (ValueError, KeyError, TypeError) as error:
                raise AppError('HLS validation did not preserve the complete accepted PCM timeline: '
                               + result.stderr.decode('utf-8', 'replace')[-500:]) from error
            original = playlist.read_text('utf-8')
            if '#EXT-X-ENDLIST' not in original or '#EXT-X-PLAYLIST-TYPE:VOD' not in original:
                raise AppError('Only immutable completed VOD playlists may be published.')
            names = [line.strip() for line in original.splitlines() if line.strip() and not line.startswith('#')]
            if not names or names != [f'f{i:06d}.m4s' for i in range(len(names))] or not (program / 'init.mp4').is_file():
                raise AppError('HLS fragment order or initialization metadata is invalid.')
            rewritten = []
            for line in original.splitlines():
                if line.startswith('#EXT-X-MAP:'):
                    line = f'#EXT-X-MAP:URI="?kind=playback_hls_init&id={key}"'
                elif line in names:
                    line = f'?kind=playback_hls_segment&id={key}&index={names.index(line)}'
                rewritten.append(line)
            playlist.write_text('\n'.join(rewritten) + '\n', encoding='utf-8')
            files = {}
            for name in ['index.m3u8', 'init.mp4', *names]:
                path = program / name
                files[name] = {'sha256': file_digest(path), 'bytes': path.stat().st_size}
            live = self._plan(task['generation_id'], spec['music'], spec['music_volume'])
            if stop.is_set() or not any(c['key'] == key for c in live['chunks']):
                if self.desired.get(task['generation_id']) == task['map_key']:
                    self.desired[task['generation_id']] = live['key']
                return
            destination = self.folder / key
            if destination.exists():
                if any(not (destination / name).is_file() or file_digest(destination / name) != info['sha256'] for name, info in files.items()):
                    raise AppError('An immutable HLS program already exists with different bytes.')
            else:
                os.replace(program, destination)
            _write(self.folder / (key + '.json'), {'key': key, 'spec': spec, 'status': 'ready', 'hls': True,
                   'audio_sha256': files['index.m3u8']['sha256'], 'duration': spec['duration'], 'encoded_duration': measured,
                   'files': files, 'fragments': len(names), 'input_format': 'accepted PCM WAV; no lossy source transcode'})

    @contextmanager
    def open(self, key, asset='playlist', index=None):
        require_id(key)
        if not re.fullmatch('[a-f0-9]{64}', key):
            raise AppError('Invalid HLS program key.')
        record = self._record(key)
        if not record or record['status'] != 'ready' or record.get('hls') is not True:
            raise AppError('HLS program is not ready.')
        authorized = False
        for path in (self.folder / 'maps').glob('*.json'):
            reference = _read(path)
            if key not in reference.get('chunks', []):
                continue
            try:
                plan = self._plan(reference['generation_id'], reference['music'], reference['music_volume'])
            except AppError:
                continue
            if any(c['key'] == key and not c['blocked'] for c in plan['chunks']):
                authorized = True
                break
        if not authorized:
            raise AppError('HLS program is no longer referenced by the current accepted source.')
        if asset == 'playlist':
            name = 'index.m3u8'
        elif asset == 'init':
            name = 'init.mp4'
        elif asset == 'segment':
            if type(index) is int:
                number = index
            elif isinstance(index, str) and re.fullmatch(r'0|[1-9][0-9]{0,5}', index):
                number = int(index)
            else:
                raise AppError('Invalid HLS fragment index.')
            if not 0 <= number < record['fragments']:
                raise AppError('HLS fragment index is outside the approved program.')
            name = f'f{number:06d}.m4s'
        else:
            raise AppError('Unknown HLS asset.')
        info = record['files'].get(name)
        if not isinstance(info, dict):
            raise AppError('HLS fragment metadata is missing.')
        with open_shared_read(self.folder / key / name) as source:
            if os.fstat(source.fileno()).st_size != info['bytes'] or hashlib.file_digest(source, 'sha256').hexdigest() != info['sha256']:
                raise AppError('Immutable HLS asset checksum mismatch.')
            source.seek(0)
            yield source
