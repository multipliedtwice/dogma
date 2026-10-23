from __future__ import annotations
import json
from pathlib import Path
import tempfile
import time
import unittest
import numpy as np
import soundfile as sf

from test_core import FB2, complete_audio
from rimsky.playback_streams import PlaybackStreams
from rimsky.export import run_encoder
from rimsky.store import Store
from rimsky.util import AppError, file_digest


class _Music:
    def status(self, generation_id):
        return {'cues': [], 'scenes': []}


class PlaybackStreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.addCleanup(self.store.close)
        book = self.store.import_data(FB2, 'book.fb2')
        self.generation = self.store.create_generation(book['id'], {})
        complete_audio(self.store, self.generation)
        self.generation = self.store.generation(self.generation['id'])
        self.streams = PlaybackStreams(self.store, Path('resources/runtime'), _Music(), chunk_seconds=2)
        self.addCleanup(self.streams.shutdown)

    def wait(self, **options):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            manifest = self.streams.request(self.generation['id'], **options)
            if manifest['chunks'] and all(c['status'] in ('ready', 'failed') for c in manifest['chunks']):
                return manifest
            time.sleep(.05)
        self.fail('Compression did not finish in its bounded test time')

    def test_compressed_chunks_keep_source_map_and_are_immutable(self):
        manifest = self.wait()
        self.assertTrue(all(c['status'] == 'ready' for c in manifest['chunks']), manifest)
        source = {s['id']: s for s in self.generation['segments']}
        for chunk in manifest['chunks']:
            for part in chunk['parts']:
                self.assertEqual(source[part['segment_id']]['audio_hash'], part['audio_hash'])
                self.assertEqual(source[part['segment_id']]['duration'], part['duration'])
            with self.streams.open(chunk['key']) as audio:
                self.assertGreater(len(audio.read()), 100)
        repeated = self.wait()
        self.assertEqual(manifest, repeated)
        self.assertEqual(self.generation['segments'], self.store.generation(self.generation['id'])['segments'])

    def test_music_pending_is_explicit_and_never_silently_omitted(self):
        manifest = self.streams.request(self.generation['id'], True, 1)
        self.assertTrue(manifest['holds'])
        self.assertTrue(all(c['status'] == 'pending' for c in manifest['chunks']))

    def test_changed_source_invalidates_old_asset_authorization(self):
        manifest = self.wait()
        chunk = manifest['chunks'][0]
        first = chunk['parts'][0]['segment_id']
        self.store.execute("UPDATE segments SET status='review' WHERE generation_id=? AND id=?", (self.generation['id'], first))
        fresh = self.streams.request(self.generation['id'])
        self.assertNotEqual(manifest['key'], fresh['key'])
        self.assertTrue(any(h['id'] == first for h in fresh['holds']))
        with self.assertRaises(AppError):
            with self.streams.open(chunk['key']):
                pass

    def install_music(self):
        folder = self.store.root / 'music'
        folder.mkdir(exist_ok=True)
        temporary = folder / 'source.wav'
        data = np.sin(2 * np.pi * 137 * np.arange(24000 * 20) / 24000).astype('float32') * .05
        sf.write(temporary, data, 24000, subtype='PCM_16')
        key = file_digest(temporary)
        temporary.rename(folder / (key + '.wav'))
        (folder / (key + '.json')).write_text(json.dumps({'sha256': key}), encoding='utf-8')
        chapters = list(dict.fromkeys(s['chapter_id'] for s in self.generation['segments']))
        owner = self
        class Music:
            start = 0
            def status(self, gid):
                return {'scenes': [], 'cues': [{'id': 'chapter_mix:' + chapter, 'kind': 'chapter_mix',
                         'chapter_id': chapter, 'anchor_segment_id': next(s['id'] for s in owner.generation['segments'] if s['chapter_id'] == chapter),
                         'start': self.start, 'duration': 20, 'status': 'ready', 'audio_key': key} for chapter in chapters]}
        self.streams.music = Music()
        return key

    def test_progressive_append_seals_published_short_chunk(self):
        original = self.generation['segments']
        first_chapter = original[0]['chapter_id']
        for segment in original[2:]:
            self.store.execute("UPDATE segments SET status='pending',duration=0 WHERE generation_id=? AND id=?", (self.generation['id'], segment['id']))
        first = self.wait()
        chunk = first['chunks'][0]
        self.assertEqual(2, len(chunk['parts']))
        for segment in original[2:]:
            if segment['chapter_id'] == first_chapter:
                self.store.execute("UPDATE segments SET status='ready',duration=? WHERE generation_id=? AND id=?", (segment['duration'], self.generation['id'], segment['id']))
        extended = self.wait()
        self.assertEqual(chunk, extended['chunks'][0])
        self.assertGreater(len(extended['chunks']), len(first['chunks']))
        self.assertEqual([s['id'] for s in original if s['chapter_id'] == first_chapter],
                         [p['segment_id'] for c in extended['chunks'] for p in c['parts']])

    def test_same_music_bytes_with_changed_layout_invalidate_published_mix(self):
        self.install_music()
        first = self.wait(music=True)
        self.assertTrue(all(c['status'] == 'ready' for c in first['chunks']), first)
        self.streams.music.start = .025
        second = self.streams.request(self.generation['id'], True, 1)
        self.assertNotEqual(first['key'], second['key'])
        self.assertNotEqual(first['chunks'][0]['key'], second['chunks'][0]['key'])
        with self.assertRaises(AppError):
            with self.streams.open(first['chunks'][0]['key']):
                pass

    def test_music_track_continues_across_chunk_and_progressive_edges(self):
        key = self.install_music()
        path = self.store.root / 'music' / (key + '.wav')
        layer = {'key': key, 'at': 0, 'duration': 20, 'kind': 'bed', 'gain': 1, 'window': None}
        outputs = []
        class Stop:
            def is_set(self): return False
        for start, chapter_end in [(3, 5), (5, 7)]:
            output = Path(self.temporary.name) / f'track-{start}.wav'
            self.streams._mix_track({'start': start, 'duration': 2, 'chapter_duration': chapter_end, 'layers': [layer]},
                                   {key: path}, output, Stop())
            encoded = output.with_suffix('.mp3')
            ffmpeg = self.streams._recipe()['ffmpeg']
            result = run_encoder([ffmpeg, '-v', 'error', '-y', '-i', str(output), '-c:a', 'libmp3lame', '-b:a', '128k', '-threads', '2', str(encoded)], timeout=15)
            self.assertEqual(0, result.returncode)
            decoded = run_encoder([ffmpeg, '-v', 'error', '-i', str(encoded), '-f', 'f32le', '-ar', '24000', '-ac', '1', '-'], timeout=15)
            self.assertEqual(0, decoded.returncode)
            outputs.append(np.frombuffer(decoded.stdout, dtype='<f4'))
        joined = np.concatenate(outputs)
        windows = [float(np.sqrt(np.mean(part ** 2))) for part in np.array_split(joined, 16)]
        self.assertGreater(min(windows) / max(windows), .90)
        self.assertLess(abs(float(outputs[1][0] - outputs[0][-1])), .01)


if __name__ == '__main__':
    unittest.main()
