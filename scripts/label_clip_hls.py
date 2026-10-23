"""Read-only bucket audition derivatives; original WAVs remain the evidence.

No labels, training manifests, source audio or source boundaries are written.
One encoder is shared across HTTP requests. All public assets are immutable.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import wave

PREFIX = '/_clip_hls/'
REPO = Path(__file__).resolve().parents[1]
JS_SHA256 = 'a12e7ee1cd64a69dcdb314157e45dafcba705bfb0b1440b7935cb265d374423e'
ENCODER = threading.BoundedSemaphore(1)
MANAGERS = {}
MANAGERS_LOCK = threading.Lock()
SHA = re.compile('[a-f0-9]{64}\\Z')


class Busy(Exception):
    pass


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


class ClipHLS:
    def __init__(self, root):
        self.root = root.resolve()
        self.cache = self.root / 'training-work/label-clip-hls-v1'
        self.recipe = None

    def tools(self):
        if self.recipe is None:
            tools = {}
            for name in ('ffmpeg', 'ffprobe'):
                bundled = REPO / 'resources/runtime/media' / (name + '.exe')
                path = bundled if bundled.is_file() else Path(shutil.which(name) or '')
                if not path.is_file():
                    raise ValueError('FFmpeg and FFprobe are required for bucket HLS.')
                tools[name] = str(path.resolve())
                tools[name + '_sha256'] = checksum(path)
            self.recipe = {**tools, 'schema': 1, 'codec': 'aac', 'profile': 'aac_low',
                           'requested_bitrate': 192000, 'threads': 2, 'fragment_seconds': 6,
                           'sample_rate_policy': 'preserve', 'channels_policy': 'preserve',
                           'implementation_sha256': checksum(Path(__file__))}
        return self.recipe

    def source_path(self, url, audio_prefix):
        parsed = urlsplit(url)
        clean = unquote(parsed.path)
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not clean.startswith('/')
                or '\\' in clean or '\x00' in clean or '..' in Path(clean).parts or not clean.lower().endswith('.wav')):
            raise ValueError('Expected an allowed original WAV path.')
        # Same narrow media roots as the bucket server; never arbitrary workspace files.
        prefixes = ('/character-rb1/fine-tune-all/precision-v2/',
                    '/character-rb1/fine-tune-all/passage-audio/',
                    '/character-rb2/pilot/clips/', '/character-rb2/full/clips/',
                    '/character-rb3/full/clips/', '/character-rb3/labeling-full/clips/',
                    '/' + audio_prefix.strip('/') + '/clips/',
                    '/manual-labeling/reality-benders-1/audio/')
        prefix = next((p for p in prefixes if clean.startswith(p)), None)
        if prefix is None:
            raise ValueError('Original audio path is outside the bucket media roots.')
        base = self.root if clean.startswith('/manual-labeling/') else self.root / 'training-work'
        path = (base / clean.lstrip('/')).resolve()
        allowed = (base / prefix.lstrip('/')).resolve()
        if not allowed.is_relative_to(base.resolve()) or not path.is_relative_to(allowed) or not path.is_file():
            raise ValueError('Original audio is unavailable.')
        return path, clean

    def source(self, query, audio_prefix):
        if (not set(query) <= {'source', 'sha256', 'first_frame', 'last_frame', 'clip_sha256'}
                or 'source' not in query or any(len(v) != 1 for v in query.values())):
            raise ValueError('Invalid bucket HLS source query.')
        path, url = self.source_path(query['source'][0], audio_prefix)
        if path.stat().st_size > 75_000_000:
            raise ValueError('Original WAV exceeds the bounded clip size.')
        raw = path.read_bytes()
        parent_hash = hashlib.sha256(raw).hexdigest()
        if 'sha256' in query and (not SHA.fullmatch(query['sha256'][0]) or query['sha256'][0] != parent_hash):
            raise ValueError('Original audio checksum changed.')
        try:
            with wave.open(io.BytesIO(raw), 'rb') as audio:
                rate, channels, samples = audio.getframerate(), audio.getnchannels(), audio.getnframes()
                if (audio.getcomptype() != 'NONE' or audio.getsampwidth() != 2 or channels not in (1, 2)
                        or rate not in (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000)
                        or not 0 < samples / rate <= 180):
                    raise ValueError('Bucket HLS requires an original PCM16 clip of at most 180 seconds.')
                pcm = audio.readframes(samples)
                if len(pcm) != samples * channels * 2:
                    raise ValueError('Original WAV is truncated.')
        except (wave.Error, EOFError) as error:
            raise ValueError('Original WAV is invalid.') from error
        identity = {'source_url': url, 'source_audio_sha256': parent_hash, 'sample_rate': rate,
                    'channels': channels, 'samples': samples, 'duration': samples / rate}
        ranges = {'first_frame', 'last_frame', 'clip_sha256'} & set(query)
        if ranges:
            if ranges != {'first_frame', 'last_frame', 'clip_sha256'} or 'sha256' not in query or channels != 1:
                raise ValueError('A split requires the original hash and exact mono frame boundaries.')
            if any(not re.fullmatch(r'0|[1-9][0-9]{0,9}', query[k][0]) for k in ('first_frame', 'last_frame')):
                raise ValueError('Invalid split frame boundaries.')
            first, last = int(query['first_frame'][0]), int(query['last_frame'][0])
            if not 0 <= first < last <= samples:
                raise ValueError('Split frames are outside the original audio.')
            output = io.BytesIO()
            with wave.open(output, 'wb') as child:
                child.setnchannels(1); child.setsampwidth(2); child.setframerate(rate)
                child.writeframes(pcm[first * 2:last * 2])
            raw = output.getvalue()
            child_hash = hashlib.sha256(raw).hexdigest()
            if not SHA.fullmatch(query['clip_sha256'][0]) or child_hash != query['clip_sha256'][0]:
                raise ValueError('Split WAV checksum does not match the canonical browser cut.')
            identity.update(parent_audio_sha256=parent_hash, first_frame=first, last_frame=last,
                            source_audio_sha256=child_hash, samples=last-first, duration=(last-first)/rate)
        return path, raw, identity

    def resolve(self, query, audio_prefix):
        path, raw, identity = self.source(query, audio_prefix)
        spec = {'source': identity, 'recipe': self.tools()}
        key = hashlib.sha256(canonical(spec)).hexdigest()
        destination = self.cache / key
        if (destination / 'receipt.json').is_file():
            return self.existing(key, spec, audio_prefix)
        if not ENCODER.acquire(timeout=20):
            raise Busy('Bucket audio encoder is busy; retry shortly.')
        try:
            if (destination / 'receipt.json').is_file():
                return self.existing(key, spec, audio_prefix)
            self.cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.encode-', dir=self.cache) as temporary:
                folder = Path(temporary)
                source = folder / 'source.wav'; source.write_bytes(raw)
                program = folder / 'program'; program.mkdir()
                playlist = program / 'index.m3u8'
                recipe = spec['recipe']
                command = [recipe['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0', '-vn',
                           '-ar', str(identity['sample_rate']), '-ac', str(identity['channels']), '-c:a', 'aac',
                           '-profile:a', 'aac_low', '-b:a', '192k', '-threads', '2', '-map_metadata', '-1',
                           '-f', 'hls', '-hls_time', '6', '-hls_playlist_type', 'vod', '-hls_segment_type', 'fmp4',
                           '-hls_fmp4_init_filename', 'init.mp4', '-hls_segment_filename', (program / 'f%06d.m4s').as_posix(),
                           '-hls_flags', 'independent_segments', playlist.as_posix()]
                result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
                if result.returncode or not playlist.is_file():
                    raise ValueError('Could not encode the bucket HLS derivative.')
                result = subprocess.run([recipe['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', playlist.as_posix()],
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
                probe = json.loads(result.stdout)
                stream = probe['streams'][0]
                encoded_duration = float(probe['format']['duration'])
                # HLS fMP4 has one AAC-LC priming frame. It is outside the source
                # evidence interval, as is the final padded AAC frame.
                delay = 1024 / identity['sample_rate']
                if (result.returncode or stream['codec_name'] != 'aac' or int(stream['sample_rate']) != identity['sample_rate']
                        or stream['channels'] != identity['channels']
                        or abs(encoded_duration - identity['duration'] - delay) > 1024 / identity['sample_rate'] + .002):
                    raise ValueError('Bucket HLS format or source timeline changed.')
                text = playlist.read_text('utf-8')
                names = [line for line in text.splitlines() if line and not line.startswith('#')]
                if (names != [f'f{i:06d}.m4s' for i in range(len(names))] or not names
                        or '#EXT-X-ENDLIST' not in text or '#EXT-X-PLAYLIST-TYPE:VOD' not in text):
                    raise ValueError('Invalid immutable bucket HLS playlist.')
                text = text.replace('URI="init.mp4"', f'URI="{PREFIX}{key}/init.mp4"')
                text = '\n'.join(PREFIX + key + '/' + line if line in names else line for line in text.splitlines()) + '\n'
                playlist.write_text(text, encoding='utf-8')
                manifest = {'schema': 1, 'key': key, **identity, 'codec': 'aac', 'profile': 'aac_low',
                            'requested_bitrate': 192000, 'encoded_bitrate': int(stream.get('bit_rate') or 0),
                            'encoded_duration': encoded_duration, 'playlist_url': PREFIX + key + '/index.m3u8',
                            'timeline': {'media_start': delay, 'source_start': 0,
                                         'source_duration': identity['duration'], 'encoder_delay_samples': 1024,
                                         'rule': 'clamp-media-to-original-source-v1'},
                            'provenance': {k: v for k, v in recipe.items() if k not in ('ffmpeg', 'ffprobe')}}
                (program / 'manifest.json').write_bytes(canonical(manifest))
                files = {name: {'sha256': checksum(program/name), 'bytes': (program/name).stat().st_size}
                         for name in ('index.m3u8', 'init.mp4', 'manifest.json', *names)}
                receipt = {'schema': 1, 'key': key, 'spec': spec, 'manifest': manifest, 'files': files}
                (program / 'receipt.json').write_bytes(canonical(receipt))
                if checksum(path) != identity.get('parent_audio_sha256', identity['source_audio_sha256']):
                    raise ValueError('Original audio changed during HLS encoding.')
                if destination.exists():
                    return self.existing(key, spec, audio_prefix)
                try:
                    os.replace(program, destination)
                except OSError:
                    # Other bucket-server processes share this derivative cache.
                    # Their complete directory becomes visible atomically; reuse
                    # only an independently validated identical input recipe.
                    if not destination.exists():
                        raise
                    return self.existing(key, spec, audio_prefix)
            return manifest
        finally:
            ENCODER.release()

    def existing(self, key, spec, audio_prefix):
        receipt = self.record(key, audio_prefix)
        if receipt['spec'] != spec:
            raise ValueError('An immutable bucket HLS input recipe changed.')
        files = receipt['files']
        if (not isinstance(files, dict) or not {'index.m3u8', 'init.mp4', 'manifest.json'} <= files.keys()
                or not any(re.fullmatch(r'f[0-9]{6}\.m4s', name) for name in files)):
            raise ValueError('An incomplete immutable bucket HLS derivative already exists.')
        for name, info in files.items():
            if name not in ('index.m3u8', 'init.mp4', 'manifest.json') and not re.fullmatch(r'f[0-9]{6}\.m4s', name):
                raise ValueError('Invalid immutable bucket HLS file inventory.')
            path = self.cache/key/name
            if not path.is_file() or path.stat().st_size != info['bytes'] or checksum(path) != info['sha256']:
                raise ValueError('Existing bucket HLS derivative checksum changed.')
        return receipt['manifest']

    def record(self, key, audio_prefix):
        if not SHA.fullmatch(key):
            raise ValueError('Invalid bucket HLS key.')
        receipt = json.loads((self.cache/key/'receipt.json').read_text('utf-8'))
        if receipt['key'] != key or hashlib.sha256(canonical(receipt['spec'])).hexdigest() != key:
            raise ValueError('Bucket HLS immutable recipe changed.')
        identity = receipt['spec']['source']
        source, _ = self.source_path(identity['source_url'], audio_prefix)
        if checksum(source) != identity.get('parent_audio_sha256', identity['source_audio_sha256']):
            raise ValueError('Original audio changed; the derivative is no longer valid.')
        if receipt['manifest'] != json.loads((self.cache/key/'manifest.json').read_text('utf-8')):
            raise ValueError('Bucket HLS provenance changed.')
        return receipt

    def asset(self, key, name, audio_prefix):
        if name not in ('index.m3u8', 'init.mp4', 'manifest.json') and not re.fullmatch(r'f[0-9]{6}\.m4s', name):
            raise ValueError('Invalid bucket HLS asset.')
        record = self.record(key, audio_prefix)
        info = record['files'].get(name)
        path = self.cache/key/name
        if not info or path.stat().st_size != info['bytes'] or checksum(path) != info['sha256']:
            raise ValueError('Bucket HLS derivative checksum changed.')
        return path


def send_file(handler, path, mime, head_only, immutable=True):
    size = path.stat().st_size
    etag = '"' + checksum(path) + '"'
    if handler.headers.get('If-None-Match') == etag and not handler.headers.get('Range'):
        handler.send_response(304); handler.send_header('ETag', etag); handler.end_headers(); return
    start, end = 0, size-1
    requested = handler.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()):
            handler.send_error(416); return
        if match[1]:
            start = int(match[1]); end = min(int(match[2]), end) if match[2] else end
        else:
            start = max(0, size-int(match[2]))
        if start >= size or end < start:
            handler.send_error(416); return
    handler.send_response(206 if requested else 200)
    for name, value in [('Content-Type', mime), ('Content-Length', str(end-start+1)), ('Accept-Ranges', 'bytes'),
                        ('ETag', etag), ('Cache-Control', 'private, max-age=31536000, immutable' if immutable else 'no-cache')]:
        handler.send_header(name, value)
    if requested: handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    handler.end_headers()
    if not head_only:
        with path.open('rb') as stream:
            stream.seek(start); remaining = end-start+1
            while remaining:
                block = stream.read(min(remaining, 65536))
                if not block: break
                handler.wfile.write(block); remaining -= len(block)


def handle(handler, head_only=False):
    parsed = urlsplit(handler.path)
    if not parsed.path.startswith(PREFIX): return False
    try:
        handler.same_origin()
        if parsed.path == PREFIX + 'hls.min.js':
            path = REPO/'node_modules/hls.js/dist/hls.min.js'
            if checksum(path) != JS_SHA256: raise ValueError('The pinned local HLS client is unavailable.')
            send_file(handler, path, 'text/javascript; charset=utf-8', head_only); return True
        root = handler.workspace_root.resolve()
        with MANAGERS_LOCK:
            manager = MANAGERS.setdefault(root, ClipHLS(root))
        if parsed.path in (PREFIX+'resolve', PREFIX+'playlist.m3u8'):
            query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
            manifest = manager.resolve(query, handler.audio_prefix)
            if parsed.path.endswith('/resolve'):
                raw = canonical(manifest)
                handler.send_response(200); handler.send_header('Content-Type', 'application/json')
                handler.send_header('Cache-Control', 'no-store'); handler.send_header('Content-Length', str(len(raw))); handler.end_headers()
                if not head_only: handler.wfile.write(raw)
            else:
                handler.send_response(302); handler.send_header('Location', manifest['playlist_url'])
                handler.send_header('Cache-Control', 'no-store'); handler.send_header('Content-Length', '0'); handler.end_headers()
            return True
        match = re.fullmatch(re.escape(PREFIX) + r'([a-f0-9]{64})/(index\.m3u8|init\.mp4|manifest\.json|f[0-9]{6}\.m4s)', parsed.path)
        if not match or parsed.query: raise ValueError('Invalid bucket HLS asset path.')
        path = manager.asset(*match.groups(), handler.audio_prefix)
        mime = 'application/vnd.apple.mpegurl' if path.suffix == '.m3u8' else 'application/json' if path.suffix == '.json' else 'audio/mp4'
        send_file(handler, path, mime, head_only)
    except Busy as error:
        handler.send_response(503); handler.send_header('Retry-After', '2'); handler.send_header('Content-Length', '0'); handler.end_headers()
    except (ValueError, OSError, KeyError, IndexError, TypeError, EOFError, subprocess.TimeoutExpired) as error:
        handler.send_error(400, str(error))
    return True
