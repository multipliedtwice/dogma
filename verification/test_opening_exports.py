"""Real-encoder checks for the approved musical branding, without model inference."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from test_core import FB2, complete_audio
from rimsky.export import build_export, run_encoder
from rimsky.opening import attach_opening, select_opening
from rimsky.store import Store
from rimsky.util import AppError


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and FFprobe required')
class OpeningExports(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.opening = attach_opening(self.store, self.book)
        self.generation = self.store.create_generation(self.book['id'], {
            'device': 'cpu', 'pack_name': 'Verification', 'opening': self.opening,
        })
        self.gid = self.generation['id']
        complete_audio(self.store, self.generation)
        self.generation = self.store.generation(self.gid)
        self.chapters = self.generation['config']['chapters']
        self.speech = sum(s['duration'] for s in self.generation['segments'])
        self.variant = select_opening(self.generation, False)
        self.assertEqual('music', self.variant['variant'])
        self.master = self.store.root / 'audio' / f'{self.variant["audio_hash"]}.wav'
        self.reference, rate = sf.read(self.master, always_2d=True)
        self.assertEqual(24000, rate)
        self.reference = self.reference.mean(axis=1)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def export(self, kind, chapters=None, **options):
        result = build_export(self.store, self.gid, kind, chapters, self.root, **options)
        return self.store.root / 'exports' / result['file']

    def probe(self, path):
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_chapters',
                                 '-show_format', '-of', 'json', str(path)],
                                capture_output=True, check=True)
        return json.loads(result.stdout)

    def decode(self, path):
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                                 '-ac', '1', '-ar', '24000', '-f', 'f32le', '-'],
                                capture_output=True, check=True)
        return np.frombuffer(result.stdout, '<f4')

    def assert_branding_prefix(self, path):
        audio = self.decode(path)
        # Ignore codec boundary padding; compare the audible musical/voice master.
        wanted, actual = self.reference[2400:-2400], audio[2400:len(self.reference)-2400]
        self.assertGreater(np.corrcoef(wanted, actual)[0, 1], .97)
        self.assertAlmostEqual(float(np.sqrt(np.mean(wanted ** 2))),
                               float(np.sqrt(np.mean(actual ** 2))), delta=.006)
        # The first real narration passage keeps its PCM clock and pitch after
        # the stereo master; a demuxer channel/rate mismatch would corrupt this.
        start, stop = round(8.02 * 24000), round(8.08 * 24000)
        expected_speech = np.sin(2 * np.pi * 440 * np.arange(480, 1920) / 24000)
        self.assertGreater(np.corrcoef(expected_speech, audio[start:stop])[0, 1], .9)

    def test_m4b_contains_one_branding_chapter_and_correct_book_seek_points(self):
        path = self.export('m4b')
        self.assert_branding_prefix(path)
        info = self.probe(path)
        self.assertAlmostEqual(8 + self.speech, float(info['format']['duration']), delta=.15)
        entries = info['chapters']
        self.assertEqual(len(self.chapters) + 1, len(entries))
        self.assertEqual('Dogma', entries[0]['tags']['title'])
        self.assertEqual((0.0, 8.0), (float(entries[0]['start_time']), float(entries[0]['end_time'])))
        position = 8.0
        for chapter_id, entry in zip(self.chapters, entries[1:]):
            self.assertAlmostEqual(position, float(entry['start_time']), delta=.003)
            position += sum(s['duration'] for s in self.generation['segments'] if s['chapter_id'] == chapter_id)
            self.assertAlmostEqual(position, float(entry['end_time']), delta=.003)

    def test_mp3_keeps_the_musical_opening_with_background_music_disabled(self):
        self.store.update_settings({'music': False})
        path = self.export('mp3', music=lambda _: [])
        self.assert_branding_prefix(path)
        self.assertAlmostEqual(8 + self.speech, float(self.probe(path)['format']['duration']), delta=.15)

    def test_chapter_zip_prefixes_first_chapter_only_and_shifts_only_its_captions(self):
        path = self.export('chapters')
        with zipfile.ZipFile(path) as archive:
            audio_names = sorted(name for name in archive.namelist() if name.endswith('.mp3'))
            self.assertEqual(len(self.chapters), len(audio_names))
            for index, (chapter, name) in enumerate(zip(self.chapters, audio_names)):
                target = self.root / name
                target.write_bytes(archive.read(name))
                duration = sum(s['duration'] for s in self.generation['segments'] if s['chapter_id'] == chapter)
                self.assertAlmostEqual(duration + (8 if index == 0 else 0),
                                       float(self.probe(target)['format']['duration']), delta=.15)
                captions = archive.read(name.replace('.mp3', '.vtt')).decode()
                self.assertIn('00:00:08.000 -->' if index == 0 else '00:00:00.000 -->', captions)
                if index == 0:
                    self.assert_branding_prefix(target)

    def test_first_chapter_selection_gets_branding_and_later_selection_does_not(self):
        for index in (0, 1):
            with self.subTest(chapter=index):
                chapter = self.chapters[index]
                duration = sum(s['duration'] for s in self.generation['segments'] if s['chapter_id'] == chapter)
                path = self.export('mp3', [chapter])
                self.assertAlmostEqual(duration + (8 if index == 0 else 0),
                                       float(self.probe(path)['format']['duration']), delta=.15)
                for kind in ('srt', 'vtt'):
                    text = self.export(kind, [chapter]).read_text('utf-8')
                    separator = ',' if kind == 'srt' else '.'
                    self.assertIn(f'00:00:{8 if index == 0 else 0:02}{separator}000 -->', text)
                    self.assertNotIn(self.opening['text'], text)

    def test_generation_starting_at_a_later_book_chapter_still_has_one_opening(self):
        generation = self.store.create_generation(self.book['id'], {'device': 'cpu'}, chapters=[self.chapters[1]])
        complete_audio(self.store, generation)
        self.gid = generation['id']
        duration = sum(s['duration'] for s in self.store.generation(self.gid)['segments'])
        path = self.export('mp3')
        self.assertAlmostEqual(8 + duration, float(self.probe(path)['format']['duration']), delta=.15)
        self.assert_branding_prefix(path)

    def test_background_layers_start_after_branding_and_do_not_mix_over_it(self):
        layer = self.root / 'bed.wav'
        sf.write(layer, np.ones(24000, dtype=np.float32) * .1, 24000, subtype='PCM_16')
        first = self.generation['segments'][0]['id']
        path = self.export('mp3', music=lambda _: [{
            'path': str(layer), 'anchor_segment_id': first, 'start': 0,
            'duration': 1, 'gain': 1, 'kind': 'bed', 'window': [0, self.speech, .01],
        }])
        self.assert_branding_prefix(path)
        self.assertAlmostEqual(8 + self.speech, float(self.probe(path)['format']['duration']), delta=.15)

    def test_corrupt_opening_fails_closed_without_publishing(self):
        self.master.write_bytes(b'corrupt opening')
        for kind in ('mp3', 'srt'):
            with self.subTest(kind=kind), self.assertRaises(AppError):
                self.export(kind)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))

    def test_malformed_opening_pin_is_rejected_even_for_later_chapter_selection(self):
        config = {**self.generation['config'], 'opening': None}
        self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(config), self.gid))
        for chapters in (None, [self.chapters[1]]):
            with self.subTest(chapters=chapters), self.assertRaises(AppError):
                self.export('srt', chapters)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))

    def test_atomic_replacement_after_capture_cannot_switch_the_export_opening(self):
        replaced = False

        def encoder(*args, **kwargs):
            nonlocal replaced
            if not replaced:
                replacement = self.master.with_suffix('.replacement')
                replacement.write_bytes(b'unapproved replacement')
                os.replace(replacement, self.master)
                replaced = True
            return run_encoder(*args, **kwargs)

        with patch('rimsky.export.run_encoder', side_effect=encoder):
            path = self.export('mp3')
        self.assertTrue(replaced)
        self.assert_branding_prefix(path)

    def test_legacy_generation_keeps_its_original_audio_and_caption_timeline(self):
        config = {k: v for k, v in self.generation['config'].items() if k != 'opening'}
        self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(config), self.gid))
        path = self.export('mp3')
        self.assertAlmostEqual(self.speech, float(self.probe(path)['format']['duration']), delta=.15)
        self.assertIn('00:00:00.000 -->', self.export('vtt').read_text('utf-8'))


if __name__ == '__main__':
    unittest.main()
