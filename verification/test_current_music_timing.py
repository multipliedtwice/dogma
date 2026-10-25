import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from rimsky.music import imported_cue_timing, MusicJobs


class CurrentMusicTimingTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(id='mix:c1', kind='chapter_mix', chapter_id='c1', anchor_segment_id='s1',
                          start=0., duration=100., audio_key='a'*64, status='ready'),
                     dict(id='ending:c1', kind='ending', chapter_id='c1', anchor_segment_id='s1',
                          start=100., duration=10., audio_key='b'*64, status='ready')]
        self.segments = [dict(id='s1', chapter_id='c1', ordinal=0, duration=40.),
                         dict(id='s2', chapter_id='c1', ordinal=1, duration=50.)]

    def test_ending_follows_shorter_or_longer_replacement_without_changing_assets(self):
        short = imported_cue_timing(self.rows, self.segments)
        self.assertEqual((90., 90.), (short[0]['duration'], short[1]['start']))
        self.segments[1]['duration'] = 75.
        long = imported_cue_timing(self.rows, self.segments)
        self.assertEqual((100., 115.), (long[0]['duration'], long[1]['start']))
        self.assertEqual(100., self.rows[1]['start'])
        self.assertEqual(self.rows[1]['audio_key'], long[1]['audio_key'])

    def test_export_uses_its_captured_parts_instead_of_live_replacement_duration(self):
        jobs = MusicJobs.__new__(MusicJobs)
        jobs.store = SimpleNamespace(settings=lambda: {'music': True, 'music_volume': .9}, rows=lambda *args: self.rows)
        jobs.folder = Path('music')
        jobs._intact = lambda key: True
        jobs._released = lambda gid: ([], {'c1'}, set())
        layers = jobs.export_overlays('generation', self.segments)
        self.assertEqual(90., layers[-1]['start'])
        self.assertEqual(90., layers[0]['duration'])
        self.assertEqual(.9, layers[-1]['gain'])


if __name__ == '__main__':
    unittest.main()
