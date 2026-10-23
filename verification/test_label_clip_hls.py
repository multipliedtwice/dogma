"""Bucket transport tests use temporary clips; protected labels never participate."""
import functools
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import label_clip_hls as hls
import serve_label_pilot as service


class ClipHLSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original_root = service.ROOT; service.ROOT = self.root
        self.addCleanup(setattr, service, 'ROOT', self.original_root)
        self.audio = self.root/'training-work/character-rb2/full/clips/fixture.wav'
        self.audio.parent.mkdir(parents=True)
        random = np.random.default_rng(187)
        self.pcm = (np.convolve(random.normal(0, 1, 8*24000), np.ones(7)/7, mode='same')*.08*32767).astype('<i2')
        self.write_wave(self.audio, self.pcm.tobytes(), 24000)
        self.source_url = '/character-rb2/full/clips/fixture.wav'
        self.source_sha = hls.checksum(self.audio)
        labels = self.root/'manual-labeling/reality-benders-2/review.json'
        labels.parent.mkdir(parents=True); labels.write_bytes(b'{"decisions":{"human":"untouched"}}')
        self.protected = {self.audio: self.audio.read_bytes(), labels: labels.read_bytes()}
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(service.Handler, directory=str(self.root/'training-work')))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    @staticmethod
    def write_wave(path, pcm, rate, channels=1):
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(channels); audio.setsampwidth(2); audio.setframerate(rate); audio.writeframes(pcm)

    def resolve(self, **extra):
        query = {'source': self.source_url, 'sha256': self.source_sha, **extra}
        with urlopen(self.url+hls.PREFIX+'resolve?'+urlencode(query), timeout=20) as response:
            return json.load(response)

    def read(self, path, **headers):
        with urlopen(Request(self.url+path, headers=headers), timeout=20) as response:
            return response.read(), response.headers

    def decode(self, manifest):
        base = hls.PREFIX+manifest['key']+'/'
        playlist = self.read(manifest['playlist_url'])[0].decode()
        raw = bytearray(self.read(base+'init.mp4')[0])
        fragments = [line for line in playlist.splitlines() if line and not line.startswith('#')]
        for fragment in fragments: raw.extend(self.read(fragment)[0])
        target = self.root/'joined.mp4'; target.write_bytes(raw)
        recipe = hls.MANAGERS[self.root].tools()
        result = subprocess.run([recipe['ffmpeg'], '-v', 'error', '-i', str(target), '-f', 's16le', '-'], capture_output=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        return np.frombuffer(result.stdout, '<i2'), playlist

    def test_hls_preserves_source_identity_and_proves_aac_timeline(self):
        manifest = self.resolve()
        self.assertEqual(self.source_sha, manifest['source_audio_sha256'])
        self.assertEqual((24000, 1, 8), (manifest['sample_rate'], manifest['channels'], manifest['duration']))
        self.assertEqual('aac', manifest['codec'])
        self.assertEqual(192000, manifest['requested_bitrate'])
        decoded, playlist = self.decode(manifest)
        self.assertIn('#EXT-X-ENDLIST', playlist)
        self.assertIn('#EXT-X-PLAYLIST-TYPE:VOD', playlist)
        self.assertNotIn(str(self.root), playlist)
        # fMP4 retains AAC's 1024-sample priming frame. The helper must subtract
        # this media offset and cap listening evidence at the original 8 seconds.
        delay = int(np.argmax(np.correlate(decoded[:8192].astype(float), self.pcm[:4096].astype(float), mode='valid')))
        self.assertEqual(1024, delay)
        self.assertAlmostEqual(delay/24000, manifest['timeline']['media_start'])
        self.assertEqual(0, manifest['timeline']['source_start'])
        self.assertEqual(8, manifest['timeline']['source_duration'])
        self.assertGreaterEqual(len(decoded), len(self.pcm)+delay)
        self.assertEqual(manifest, self.resolve())
        for path, raw in self.protected.items(): self.assertEqual(raw, path.read_bytes())
        receipt = self.root/'training-work/label-clip-hls-v1'/manifest['key']/'receipt.json'
        self.assertTrue(receipt.is_file())

    def test_split_exact_pcm_identity_and_bounds_are_preserved(self):
        first, last = 1234, 130000
        child = io.BytesIO()
        with wave.open(child, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(self.pcm[first:last].tobytes())
        child_hash = hashlib.sha256(child.getvalue()).hexdigest()
        manifest = self.resolve(first_frame=first, last_frame=last, clip_sha256=child_hash)
        self.assertEqual(child_hash, manifest['source_audio_sha256'])
        self.assertEqual(self.source_sha, manifest['parent_audio_sha256'])
        self.assertEqual((first, last), (manifest['first_frame'], manifest['last_frame']))
        self.assertEqual((last-first)/24000, manifest['duration'])
        for extra in ({'first_frame': first, 'last_frame': last, 'clip_sha256': '0'*64},
                      {'first_frame': -1, 'last_frame': last, 'clip_sha256': child_hash},
                      {'first_frame': first, 'last_frame': len(self.pcm)+1, 'clip_sha256': child_hash}):
            with self.subTest(extra=extra), self.assertRaises(HTTPError) as raised: self.resolve(**extra)
            self.assertEqual(400, raised.exception.code)

    def test_stereo_original_sample_rate_and_channel_count_are_preserved(self):
        rate = 44100
        t = np.arange(rate*3)/rate
        stereo = (np.column_stack((np.sin(2*np.pi*337*t), np.sin(2*np.pi*731*t)))*2000).astype('<i2')
        self.write_wave(self.audio, stereo.tobytes(), rate, channels=2)
        self.source_sha = hls.checksum(self.audio)
        original = self.audio.read_bytes()
        manifest = self.resolve()
        self.assertEqual((rate, 2, 3), (manifest['sample_rate'], manifest['channels'], manifest['duration']))
        self.assertAlmostEqual(1024/rate, manifest['timeline']['media_start'])
        decoded, _ = self.decode(manifest)
        decoded = decoded.reshape(-1, 2)[1024:1024+len(stereo)]
        self.assertEqual(stereo.shape, decoded.shape)
        for channel in (0, 1):
            self.assertGreater(float(np.corrcoef(stereo[:, channel], decoded[:, channel])[0, 1]), .99)
        self.assertEqual(original, self.read(self.source_url)[0])

    def test_assets_ranges_etags_and_source_invalidation(self):
        manifest = self.resolve()
        for name in ('index.m3u8', 'init.mp4', 'f000000.m4s', 'manifest.json'):
            path = hls.PREFIX+manifest['key']+'/'+name
            with urlopen(Request(self.url+path, headers={'Range': 'bytes=0-15'})) as response:
                self.assertEqual(206, response.status); self.assertEqual(16, len(response.read()))
                self.assertIn('immutable', response.headers['Cache-Control'])
                etag = response.headers['ETag']
            with self.assertRaises(HTTPError) as unchanged:
                urlopen(Request(self.url+path, headers={'If-None-Match': etag}))
            self.assertEqual(304, unchanged.exception.code)
        self.audio.write_bytes(self.audio.read_bytes()+b'changed')
        with self.assertRaises(HTTPError) as changed: self.read(manifest['playlist_url'])
        self.assertEqual(400, changed.exception.code)

    def test_corrupt_derivatives_and_arbitrary_paths_fail_closed(self):
        for source in ('/manual-labeling/reality-benders-2/review.json', 'https://example.org/a.wav',
                       '/character-rb2/full/clips/../secret.wav', '/character-rb2/full/clips/%2e%2e/secret.wav',
                       '/character-rb2/full/clips/fixture.wav?filter=evil', '/character-rb2/full/clips\\fixture.wav'):
            with self.subTest(source=source), self.assertRaises(HTTPError) as raised: self.resolve(source=source)
            self.assertEqual(400, raised.exception.code)
        manifest = self.resolve()
        fragment = self.root/'training-work/label-clip-hls-v1'/manifest['key']/'f000000.m4s'
        fragment.write_bytes(b'corrupt')
        with self.assertRaises(HTTPError) as corrupt:
            self.read(hls.PREFIX+manifest['key']+'/f000000.m4s')
        self.assertEqual(400, corrupt.exception.code)

    def test_busy_encoder_is_retryable_and_local_client_is_pinned(self):
        with patch.object(hls, 'ENCODER') as encoder:
            encoder.acquire.return_value = False
            with self.assertRaises(HTTPError) as busy: self.resolve()
            self.assertEqual(503, busy.exception.code)
            self.assertEqual('2', busy.exception.headers['Retry-After'])
        client, headers = self.read(hls.PREFIX+'hls.min.js')
        self.assertEqual(hls.JS_SHA256, hashlib.sha256(client).hexdigest())
        self.assertIn('javascript', headers['Content-Type'])
        self.assertEqual('aac', self.resolve()['codec'])

    def test_independent_managers_racing_publication_reuse_only_valid_program(self):
        first, second = hls.ClipHLS(self.root), hls.ClipHLS(self.root)
        query = {'source': [self.source_url], 'sha256': [self.source_sha]}
        publication = threading.Barrier(2)
        replace = hls.os.replace
        def publish_together(source, destination):
            # Both managers have already passed destination.exists() here.
            publication.wait(timeout=10)
            return replace(source, destination)
        with patch.object(hls, 'ENCODER', threading.BoundedSemaphore(2)), patch.object(hls.os, 'replace', publish_together):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(manager.resolve, query, service.Handler.audio_prefix) for manager in (first, second)]
                results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        key = results[0]['key']
        self.assertEqual([key], [p.name for p in first.cache.iterdir() if p.is_dir()])
        self.assertEqual(results[0], first.resolve(query, service.Handler.audio_prefix))
        fragment = first.cache/key/'f000000.m4s'
        fragment.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            second.resolve(query, service.Handler.audio_prefix)


if __name__ == '__main__': unittest.main()
