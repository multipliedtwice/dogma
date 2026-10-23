from __future__ import annotations
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

import test_playback_streams as stream_tests
from test_core import write_wave
from rimsky.export import run_encoder
from rimsky.playback_hls import PlaybackHLS
from rimsky.util import AppError, file_digest
from server import Application, handler_for


class HLSPlaybackTests(stream_tests.PlaybackStreamTests):
    def setUp(self):
        super().setUp()
        self.streams.shutdown()
        self.streams = PlaybackHLS(self.store, Path('resources/runtime'), self.streams.music)
        self.addCleanup(self.streams.shutdown)

    def test_progressive_append_seals_published_short_chunk(self):
        # HLS must not turn a sentence-at-a-time producer into sentence programs.
        original = self.generation['segments']
        first_chapter = original[0]['chapter_id']
        self.store.execute("UPDATE generations SET status='rendering' WHERE id=?", (self.generation['id'],))
        for segment in original[1:]:
            self.store.execute("UPDATE segments SET status='pending',duration=0 WHERE generation_id=? AND id=?", (self.generation['id'], segment['id']))
        for ready_count in (1, 2):
            if ready_count == 2:
                segment = original[1]
                self.store.execute("UPDATE segments SET status='ready',duration=? WHERE generation_id=? AND id=?", (segment['duration'], self.generation['id'], segment['id']))
            before = self.store.generation(self.generation['id'])
            ordinary = stream_tests.PlaybackStreams._plan(self.streams, self.generation['id'], False, 1.)
            manifest = self.streams.request(self.generation['id'])
            self.assertEqual(ordinary['holds'], manifest['holds'])
            self.assertEqual(1, len(manifest['chunks']))
            chunk = manifest['chunks'][0]
            self.assertEqual('pending', chunk['status'])
            self.assertEqual('chapter_buffering', chunk['pending_reason'])
            self.assertIsNone(self.streams._record(chunk['key']))
            self.assertEqual(before, self.store.generation(self.generation['id']))
        # A complete short chapter is playable immediately, even while the next
        # chapter and the generation remain pending/rendering.
        for segment in original:
            if segment['chapter_id'] == first_chapter:
                self.store.execute("UPDATE segments SET status='ready',duration=? WHERE generation_id=? AND id=?", (segment['duration'], self.generation['id'], segment['id']))
        manifest = self.wait()
        self.assertEqual(1, len(manifest['chunks']))
        self.assertEqual('ready', manifest['chunks'][0]['status'], manifest)
        self.assertEqual(3, len(manifest['chunks'][0]['parts']))
        self.assertLess(manifest['chunks'][0]['duration'], 300)
        self.assertNotIn('pending_reason', manifest['chunks'][0])
        self.assertTrue(manifest['holds'])

    def test_five_minute_incomplete_prefix_publishes_and_preserves_its_program(self):
        original = self.generation['segments']
        first_chapter = original[0]['chapter_id']
        self.store.execute("UPDATE generations SET status='rendering' WHERE id=?", (self.generation['id'],))
        tone = np.sin(2 * np.pi * 440 * np.arange(150 * 24000) / 24000).astype('float32') * .03
        for segment in original[:2]:
            path = self.store.audio_path(segment['audio_hash'])
            sf.write(path, tone, 24000, subtype='PCM_16')
            path.with_suffix('.json').write_text(json.dumps({'sha256': file_digest(path), 'duration': 150,
                'samples': len(tone), 'sample_rate': 24000}), encoding='utf-8')
            self.store.execute('UPDATE segments SET duration=150 WHERE generation_id=? AND id=?', (self.generation['id'], segment['id']))
        for segment in original[2:]:
            self.store.execute("UPDATE segments SET status='pending',duration=0 WHERE generation_id=? AND id=?", (self.generation['id'], segment['id']))
        first = self.wait()['chunks'][0]
        self.assertEqual('ready', first['status'], first)
        self.assertEqual(300, first['duration'])
        for segment in original[2:]:
            if segment['chapter_id'] == first_chapter:
                self.store.execute("UPDATE segments SET status='ready',duration=? WHERE generation_id=? AND id=?", (segment['duration'], self.generation['id'], segment['id']))
        extended = self.wait()
        self.assertEqual(first, extended['chunks'][0])
        self.assertEqual(2, len(extended['chunks']))
        self.assertEqual('ready', extended['chunks'][1]['status'])
        with self.streams.open(first['key']) as source:
            self.assertIn(b'#EXT-X-ENDLIST', source.read())

    def test_active_external_prefix_requires_full_book_coverage_for_short_program(self):
        # An external bridge may not have inserted its continuation rows yet.
        first = self.generation['segments'][0]
        self.store.execute("DELETE FROM segments WHERE generation_id=? AND id!=?", (self.generation['id'], first['id']))
        self.store.execute("UPDATE generations SET status='rendering' WHERE id=?", (self.generation['id'],))
        manifest = self.streams.request(self.generation['id'])
        self.assertEqual('chapter_buffering', manifest['chunks'][0]['pending_reason'])
        self.assertEqual('pending', manifest['chunks'][0]['status'])

    def test_one_continuous_aac_program_per_chapter_with_verified_fragments(self):
        for segment in self.generation['segments']:
            path = self.store.audio_path(segment['audio_hash'])
            info = write_wave(path, seconds=4)
            info['sha256'] = file_digest(path)
            path.with_suffix('.json').write_text(json.dumps(info), encoding='utf-8')
            self.store.execute('UPDATE segments SET duration=4 WHERE generation_id=? AND id=?', (self.generation['id'], segment['id']))
        self.generation = self.store.generation(self.generation['id'])
        manifest = self.wait()
        self.assertEqual('hls', manifest['transport'])
        chapters = list(dict.fromkeys(s['chapter_id'] for s in self.generation['segments']))
        self.assertEqual(chapters, [p['chapter_id'] for p in manifest['chunks']])
        for program in manifest['chunks']:
            self.assertTrue(program['hls'])
            self.assertEqual('ready', program['status'], program)
            with self.streams.open(program['key']) as source:
                playlist = source.read().decode()
            self.assertIn('#EXT-X-ENDLIST', playlist)
            self.assertIn('#EXT-X-PLAYLIST-TYPE:VOD', playlist)
            self.assertIn('?kind=playback_hls_init&id=' + program['key'], playlist)
            self.assertNotIn(str(self.store.root), playlist)
            record = self.streams._record(program['key'])
            self.assertEqual('accepted PCM WAV; no lossy source transcode', record['input_format'])
            with self.streams.open(program['key'], 'init') as source:
                encoded = bytearray(source.read())
            for index in range(record['fragments']):
                with self.streams.open(program['key'], 'segment', str(index)) as source:
                    encoded.extend(source.read())
            joined = Path(self.temporary.name) / (program['key'] + '.mp4')
            joined.write_bytes(encoded)
            recipe = self.streams._recipe()
            probe = run_encoder([recipe['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(joined)], timeout=15)
            stream = json.loads(probe.stdout)['streams'][0]
            self.assertEqual('aac', stream['codec_name'])
            self.assertEqual('48000', stream['sample_rate'])
            self.assertEqual(1, stream['channels'])
            decoded = run_encoder([recipe['ffmpeg'], '-v', 'error', '-i', str(joined), '-f', 'f32le', '-ac', '1', '-ar', '48000', '-'], timeout=15)
            self.assertEqual(0, decoded.returncode)
            samples = np.frombuffer(decoded.stdout, dtype='<f4')
            self.assertAlmostEqual(program['duration'], len(samples) / 48000, delta=.08)
            # Both sentence joins and 6s HLS boundaries share this ONE AAC timeline.
            fragment_boundaries, offset = [], 0.
            for line in playlist.splitlines():
                if line.startswith('#EXTINF:'):
                    offset += float(line.split(':', 1)[1].rstrip(','))
                    if offset < program['duration'] - .1:
                        fragment_boundaries.append(offset)
            self.assertTrue(fragment_boundaries)
            for second in [*range(4, int(program['duration']), 4), *fragment_boundaries]:
                boundary = round(second * 48000)
                window = samples[boundary-240:boundary+240]
                self.assertLess(float(np.max(np.abs(np.diff(window)))), .01)
                self.assertGreater(float(np.sqrt(np.mean(window ** 2))), .01)

    def test_fragment_indices_and_corruption_fail_closed(self):
        manifest = self.wait()
        program = manifest['chunks'][0]
        for index in (-1, '-1', '../file', '1.0', '01', '1000000', True, None):
            with self.subTest(index=index), self.assertRaises(AppError):
                with self.streams.open(program['key'], 'segment', index):
                    pass
        path = self.streams.folder / program['key'] / 'f000000.m4s'
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(AppError, 'checksum mismatch'):
            with self.streams.open(program['key'], 'segment', 0):
                pass

    def test_authenticated_http_ranges_cover_playlist_init_and_fragment(self):
        manifest = self.wait()
        key = manifest['chunks'][0]['key']
        app = Application.__new__(Application)
        app.store, app.playback_hls = self.store, self.streams
        http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, 'hls-test-token'))
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            for kind in ('playback_hls', 'playback_hls_init', 'playback_hls_segment'):
                url = f'http://127.0.0.1:{http.server_port}/asset?kind={kind}&id={key}&index=0'
                with urlopen(Request(url, headers={'Authorization': 'Bearer hls-test-token', 'Range': 'bytes=0-15'}), timeout=5) as response:
                    self.assertEqual(206, response.status)
                    self.assertEqual(16, len(response.read()))
                    self.assertEqual('bytes', response.headers['Accept-Ranges'])
                    self.assertIn('immutable', response.headers['Cache-Control'])
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(url), timeout=5)
                self.assertEqual(401, denied.exception.code)
        finally:
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    unittest.main()
