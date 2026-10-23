"""Blind listening check: hidden labels, separate answers, per-label agreement."""
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
import build_narration_listening_check as builder  # noqa: E402
import serve_label_pilot as service  # noqa: E402


def fixture_rows():
    rows, labels = {}, {}
    for chapter, calm, normal in (('c1', 6, 3), ('c2', 4, 4), ('c3', 9, 0), ('c4', 0, 5)):
        for label, count in (('calm', calm), ('neutral', normal)):
            for n in range(count):
                cid = f'{chapter}-{label}-{n}'
                rows[cid] = {'id': cid, 'chapter_id': chapter, 'source_id': f'{chapter}-p{n}', 'paragraph_text': f'{chapter} {n}',
                             'text_start': 0 if label == 'calm' else 5, 'source_text': f'Текст {cid}. ',
                             'exact_audio': f'training-work/character-rb2/full/clips/{cid}.wav', 'exact_audio_sha256': 'x'}
                labels[cid] = label
    return rows, labels


class ListeningCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows, self.labels = fixture_rows()
        board = self.root / 'training-work/character-rb2-full'
        self.patches = [patch.object(builder, 'BOARD', board), patch.object(builder, 'SERVED', board / 'fine-tune-all'),
                        patch.object(builder, 'eligible', lambda seed: (self.rows, self.labels.get, list(self.rows))),
                        patch.object(builder.style, 'INPUTS', {}), patch.object(service, 'ROOT', self.root)]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        self.summary = builder.build_check(seed=7, per_label=6, replace=False)
        self.manifest = json.loads((board / 'fine-tune-all/listening-check/manifest.json').read_text(encoding='utf-8'))
        bound = type('Bound', (service.Handler,), {'page': '/character-rb2-full/fine-tune-all/', 'board_folder': 'character-rb2-full',
                                                  'audio_prefix': 'character-rb2/full'})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(bound, directory=str(self.root / 'training-work')))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}/character-rb2-full/fine-tune-all/listening-check/'

    def answer(self, item, answer, origin=None):
        value = {'experiment_id': self.manifest['experiment_id'], 'item': item['item'], 'clip_id': item['clip_id'], 'answer': answer}
        request = urllib.request.Request(self.url + 'answer', json.dumps(value).encode(),
                                         {'Content-Type': 'application/json', 'Origin': origin or self.url.split('/character')[0]})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_selection_is_balanced_within_mixed_chapters_and_hides_labels(self):
        chosen = [item['clip_id'] for item in self.manifest['items']]
        self.assertEqual({'calm': 6, 'neutral': 6}, self.summary['labels'])
        mixed = [cid for cid in chosen if self.rows[cid]['chapter_id'] in ('c1', 'c2')]
        self.assertEqual(Counter_of(self.labels, mixed)['calm'], Counter_of(self.labels, mixed)['neutral'])
        served = json.dumps(self.manifest)
        for word in ('calm"', 'saved_label', 'mood', 'bucket'):
            self.assertNotIn(word, served.replace('"calm", "neutral", "uncertain", "neither"', ''))
        self.assertTrue((builder.BOARD / 'listening-check' / f"key-{self.manifest['experiment_id']}.json").is_file())
        with urllib.request.urlopen(self.url) as page:
            self.assertIn(b'Narration listening check', page.read())
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(self.url.replace('fine-tune-all/listening-check/', 'listening-check/'))
        self.assertEqual(404, missing.exception.code)
        with self.assertRaises(SystemExit):
            builder.build_check(seed=7, per_label=6, replace=False)

    def test_answers_are_saved_separately_and_validated(self):
        items = self.manifest['items']
        self.assertEqual([1], self.answer(items[0], 'uncertain')['answered'])
        self.assertEqual([1, 2], self.answer(items[1], 'calm')['answered'])
        with urllib.request.urlopen(self.url + 'progress') as response:
            self.assertEqual({'experiment_id': self.manifest['experiment_id'], 'answered': [1, 2]}, json.load(response))
        for bad in ({**items[2], 'clip_id': items[3]['clip_id']}, items[2]):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.answer(bad, 'loud' if bad is items[2] else 'calm')
            self.assertEqual(400, error.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.answer(items[2], 'calm', origin='https://elsewhere.example')
        self.assertEqual(400, error.exception.code)
        self.assertFalse((self.root / 'manual-labeling').exists())

    def test_compare_waits_for_every_answer_and_report_counts_each_label(self):
        with self.assertRaises(SystemExit):
            builder.build_compare(pairs=3, seed=7)
        for item in self.manifest['items']:
            saved = self.labels[item['clip_id']]
            self.answer(item, 'neutral' if saved == 'calm' else 'neutral')
        first = self.manifest['items'][0]
        self.answer(first, self.labels[first['clip_id']])
        result = builder.report()
        self.assertEqual(6, result['neutral']['same_as_saved'])
        expected_calm = 1 if self.labels[first['clip_id']] == 'calm' else 0
        self.assertEqual(expected_calm, result['calm']['same_as_saved'])
        self.assertEqual(6, result['calm']['answered'])
        low, high = result['neutral']['agreement_of_all_answered_95ci']
        self.assertTrue(0.5 < low < high == 1.0)
        compare = builder.build_compare(pairs=3, seed=7)
        self.assertGreaterEqual(compare['pairs'], 1)
        pairs = json.loads((builder.SERVED / 'listening-compare/manifest.json').read_text(encoding='utf-8'))['pairs']
        for pair in pairs:
            self.assertEqual({'calm', 'neutral'}, {take['label'] for take in pair['takes']})


def Counter_of(labels, clips):
    from collections import Counter
    return Counter(labels[c] for c in clips)


if __name__ == '__main__':
    unittest.main()
