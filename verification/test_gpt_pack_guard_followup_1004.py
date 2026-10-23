from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import CastEngine, SyntheticEngine
from test_gpt_pack_guard_1004 import CHANGED, CONFIG, Harness
from rimsky import jobs as jobs_module
from rimsky.packs import PackChangedError, pack_unchanged
from rimsky.util import AppError


class Followup(Harness):
    def ready(self):
        jobs = self.jobs()
        done = self.finish(jobs, self.generation(), SyntheticEngine())
        self.assertEqual('ready', done['status'], done['error'])
        return jobs, done

    def outside_link(self):
        config = Path(self.pack['_root']) / CONFIG
        copy = self.root / 'outside-config.json'
        copy.write_bytes(config.read_bytes())
        config.unlink()
        os.symlink(copy, config)

    def align(self, jobs, done, mutate_on=None, mutation=None):
        calls = []
        class Process:
            def poll(self):
                return None
        class Aligner:
            def __init__(self, *args, **kwargs):
                self.process = Process()
            def call(self, method, payload, stop, timeout):
                calls.append(payload['text'])
                if len(calls) == mutate_on:
                    mutate_on and self_mutate()
                return [{'text': payload['text'], 'start': 0, 'end': .1}]
            def close(self):
                pass
        self_mutate = mutation or self.mutate
        pack = {**self.pack, 'aligner': {'path': 'tts'}}
        with patch.object(jobs_module, 'resolve_binary', return_value='rimsky-align'), patch.object(jobs_module, 'Engine', Aligner):
            jobs._align(done['id'], pack, 'Russian', {'device': 'cpu'})
        return calls

    def test_unchanged_pack_aligns_every_passage(self):
        jobs, done = self.ready()
        calls = self.align(jobs, done)
        aligned = self.store.generation(done['id'])['segments']
        self.assertEqual(len(calls), sum(bool(segment['cues']) for segment in aligned))
        self.assertTrue(calls)

    def test_change_during_an_alignment_call_stops_without_publishing_its_cues(self):
        jobs, done = self.ready()
        with self.assertRaises(PackChangedError) as raised:
            self.align(jobs, done, mutate_on=1)
        self.assertEqual(CHANGED, str(raised.exception))
        self.assertEqual([], [segment['id'] for segment in self.store.generation(done['id'])['segments'] if segment['cues']])

    def test_outside_symlink_replacing_a_sealed_file_during_alignment_stops_without_cues(self):
        jobs, done = self.ready()
        with self.assertRaises(PackChangedError) as raised:
            self.align(jobs, done, mutate_on=1, mutation=self.outside_link)
        self.assertEqual(CHANGED, str(raised.exception))
        self.assertEqual([], [segment['id'] for segment in self.store.generation(done['id'])['segments'] if segment['cues']])

    def test_outside_symlink_during_synthesis_reports_the_pack_change(self):
        generation = self.generation()
        target = generation['segments'][0]['spoken']
        link = self.outside_link
        class Linking(SyntheticEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'render' and payload['text'] == target:
                    link()
                return result
        failed = self.finish(self.jobs(), generation, Linking())
        self.assertEqual(('failed', CHANGED), (failed['status'], failed['error']))

    def test_symlinks_added_inside_the_pack_are_changes(self):
        root = Path(self.pack['_root'])
        outside = self.root / 'outside-folder'
        outside.mkdir()
        (outside / 'weights.safetensors').write_bytes(b'w')
        for name, target in (('adapter', outside), ('dangling.json', self.root / 'missing.json')):
            with self.subTest(name):
                link = root / 'tts' / name
                try:
                    os.symlink(target, link)
                except OSError as error:
                    if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                        self.skipTest('Windows symlink privilege is unavailable')
                    raise
                with self.assertRaises(PackChangedError):
                    pack_unchanged(self.pack)
                link.unlink()
                self.assertIs(self.pack, pack_unchanged(self.pack))

    def test_file_added_to_the_pack_during_an_analysis_call_stops_preparation(self):
        root = Path(self.pack['_root'])
        class Adding(CastEngine):
            def call(self, method, payload, stop):
                if method == 'analyze' and not (root / 'tts' / 'extra.json').exists():
                    (root / 'tts' / 'extra.json').write_text('{}', 'utf-8')
                return super().call(method, payload, stop)
        failed = self.finish(self.jobs(), self.generation(), Adding())
        self.assertEqual(('failed', CHANGED), (failed['status'], failed['error']))

    def test_operating_system_metadata_added_to_the_pack_is_not_a_change(self):
        (Path(self.pack['_root']) / 'tts' / '.DS_Store').write_bytes(b'finder')
        self.assertIs(self.pack, pack_unchanged(self.pack))

    def test_symlink_loop_during_synthesis_reports_the_change_and_deletes_the_take(self):
        config = Path(self.pack['_root']) / CONFIG
        generation = self.generation()
        target = generation['segments'][0]['spoken']
        written = []
        class Looping(SyntheticEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'render' and payload['text'] == target and not written:
                    written.append(Path(payload['output']))
                    config.unlink()
                    os.symlink(config.name, config)
                return result
        failed = self.finish(self.jobs(), generation, Looping())
        self.assertEqual(('failed', CHANGED), (failed['status'], failed['error']))
        self.assertEqual(1, len(written))
        self.assertFalse(written[0].exists())
        with self.assertRaises(AppError) as raised:
            pack_unchanged(self.pack)
        self.assertEqual(CHANGED, str(raised.exception))


if __name__ == '__main__':
    unittest.main()
