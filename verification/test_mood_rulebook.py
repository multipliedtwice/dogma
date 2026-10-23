"""Mood rulebook: complete drafts, playable examples, separate validated feedback."""
from __future__ import annotations

import functools
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_mood_rulebook as builder  # noqa: E402
import serve_label_pilot as service  # noqa: E402


def unit(ident, mood, book='reality-benders-2-full', kind='clip', voice='narrator_baseline'):
    return {'id': ident, 'mood': mood, 'book': book, 'unit': kind, 'voice': voice, 'text': f'Текст {ident}. ',
            'audio_sha256': f'{ident}-sha'}


class MoodRulebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = self.root = Path(self.temp.name)
        clips, audio = root / 'clips', root / 'audio'
        clips.mkdir(); audio.mkdir()
        for name in ('a', 'b', 'parent', 'c'):
            (clips / f'{name}.wav').write_bytes(b'RIFF')
        (audio / 'old-sha.wav').write_bytes(b'RIFF')
        review = root / 'review.json'
        review.write_text(json.dumps({'decisions': {'parent': {'split_review': {'parts': {'parent~0-9': {
            'split_source': {'first_frame': 24000, 'last_frame': 72000, 'sample_rate': 24000}}}}}}}), encoding='utf-8')
        evidence = root / 'evidence.json'
        evidence.write_text(json.dumps({'captured_at': 'fixture', 'moods': {'calm': {'books': {'reality-benders-2-full': 3}}},
                                        'units': [unit('a', 'calm'), unit('b', 'calm', voice='masculine_2'),
                                                  unit('parent~0-9', 'calm', kind='split_part'), unit('c', 'neutral'),
                                                  {**unit('old', 'neutral', book='reality-benders-1'), 'audio_sha256': 'old-sha'},
                                                  unit('missing', 'neutral')]}), encoding='utf-8')
        profiles = root / 'profiles.json'
        profiles.write_text(json.dumps({'moods': {'calm': {'clips': 40, 'sound_vs_own_voice_baseline_z': {
            'level_db': -0.1, 'pitch_st': 0.45, 'periodic_fraction': -0.31}}}}), encoding='utf-8')
        for item in (patch.object(builder, 'ROOT', root), patch.object(builder, 'BOOK2_CLIPS', clips),
                     patch.object(builder, 'BOOK1_AUDIO', audio), patch.object(builder, 'BOOK2_REVIEW', review),
                     patch.object(service, 'ROOT', root)):
            item.start(); self.addCleanup(item.stop)
        self.book = builder.build(evidence, profiles, 3)
        served = root / 'training-work/character-rb2-full/fine-tune-all/mood-rulebook'
        served.mkdir(parents=True)
        (served / 'rulebook.json').write_text(json.dumps(self.book, ensure_ascii=False), encoding='utf-8')
        bound = type('Bound', (service.Handler,), {'page': '/character-rb2-full/fine-tune-all/', 'board_folder': 'character-rb2-full',
                                                  'audio_prefix': 'character-rb2/full'})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(bound, directory=str(root / 'training-work')))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.url = self.origin + '/character-rb2-full/fine-tune-all/mood-rulebook/'

    def entry(self, mood):
        return next(item for item in self.book['entries'] if item['mood'] == mood)

    def send(self, value, origin=None):
        request = urllib.request.Request(self.url + 'feedback', json.dumps(value).encode(),
                                         {'Content-Type': 'application/json', 'Origin': origin or self.origin})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_every_board_mood_has_a_draft_and_examples_play_their_own_range(self):
        self.assertEqual(sorted(builder.display_names()), sorted(item['mood'] for item in self.book['entries']))
        calm = self.entry('calm')
        self.assertEqual(['Book 2'], sorted({example['book'] for example in calm['examples']}))
        part = next(example for example in calm['examples'] if example['kind'] == 'split part')
        self.assertEqual(('/character-rb2/full/clips/parent.wav', 1.0, 3.0), (part['audio'], part['start'], part['end']))
        self.assertEqual({'narrator_baseline', 'masculine_2'}, {e['voice'] for e in calm['examples'] if e['kind'] == 'clip'})
        neutral = self.entry('neutral')
        self.assertEqual({'c', 'old'}, {example['id'] for example in neutral['examples']})
        self.assertEqual('/manual-labeling/reality-benders-1/audio/old-sha.wav',
                         next(e['audio'] for e in neutral['examples'] if e['id'] == 'old'))
        self.assertEqual(['median pitch (semitones)', 'periodic-frame share (pitch detector)'],
                         [t['measure'] for t in calm['measured']['tendencies']])
        for item in self.book['entries']:
            for section in item['sections']:
                takes = section['examples'] + [part['example'] for part in (section['baseline'], section['rival']) if part and part['example']]
                self.assertEqual(1, len({(take['book'], take['voice']) for take in takes}), section['speaker'])
        narrator = next(section for section in calm['sections'] if section['speaker'].startswith('Narrator · narrator_baseline · Book 2'))
        self.assertEqual(('neutral', 'c'), (narrator['baseline']['mood'], narrator['baseline']['example']['id']))
        alone = next(section for section in calm['sections'] if 'masculine_2' in section['speaker'])
        self.assertIsNone(alone['baseline'])
        self.assertEqual('No examples yet', self.entry('mocking')['confidence'])
        self.assertIn('not a breathiness measure', self.book['measurement_note'])

    def test_feedback_is_validated_saved_separately_and_latest_wins(self):
        base = {'version': self.book['version'], 'mood': 'calm', 'verdict': 'revise', 'meaning_revision': 'Мягче.',
                'distinction': 'Медленнее', 'note': '', 'flagged_examples': ['a']}
        self.send(base)
        saved = self.send({**base, 'verdict': 'approve', 'flagged_examples': []})
        self.assertEqual('approve', saved['feedback']['calm']['verdict'])
        with urllib.request.urlopen(self.url + 'progress') as response:
            self.assertEqual(['calm'], list(json.load(response)['feedback']))
        for bad in ({**base, 'version': 'old'}, {**base, 'verdict': 'yes'}, {**base, 'mood': 'nope'},
                    {**base, 'flagged_examples': ['c']}, {**base, 'note': 'x' * 2001}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.send(bad)
            self.assertEqual(400, error.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.send(base, origin='https://elsewhere.example')
        self.assertEqual(400, error.exception.code)
        lines = (self.root / f"training-work/character-rb2-full/mood-rulebook/feedback-{self.book['version']}.jsonl").read_text(encoding='utf-8').splitlines()
        self.assertEqual(2, len(lines))
        self.assertFalse((self.root / 'manual-labeling').exists())


if __name__ == '__main__':
    unittest.main()
