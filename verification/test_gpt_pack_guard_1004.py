from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, AliasEngine, CastEngine, SyntheticEngine
from test_render_1004 import tone
from test_review_gate_1004 import make_pack
from rimsky import __version__
from rimsky import jobs as jobs_module
from rimsky.jobs import Jobs, plausible
from rimsky.packs import file_state, pack_signature, pack_unchanged, sealed_files, verified_pack
from rimsky.store import Store
from rimsky.util import AppError, EngineTransportError, digest, inside

CHANGED = 'Model pack files changed after verification. Select the pack again in Settings.'
CONFIG = 'tts/config.json'


def rewrite(path, data):
    stat = path.stat()
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def swap(path, data):
    stat = path.stat()
    spare = path.with_name(path.name + '.swap')
    spare.write_bytes(data)
    os.utime(spare, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(spare, path)


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pack = make_pack(self.root / 'pack')
        self.config = Path(self.pack['_root']) / CONFIG

    def mutate(self):
        rewrite(self.config, b'[]')

    def restore(self):
        rewrite(self.config, b'{}')


class StatSignature(Base):
    def changed(self, pack):
        with self.assertRaises(AppError) as raised:
            pack_unchanged(pack)
        self.assertEqual(CHANGED, str(raised.exception))

    def test_stats_keep_every_file_state_field(self):
        root = Path(self.pack['_root'])
        self.assertEqual({relative: file_state(inside(root, relative)) for relative in sorted(sealed_files(self.pack))}, self.pack['_stats'])
        self.assertEqual({4}, {len(state) for state in self.pack['_stats'].values()})
        self.assertIs(self.pack, pack_unchanged(self.pack))

    def test_same_size_change_with_restored_mtime_is_detected(self):
        before = self.pack['_stats'][CONFIG]
        self.mutate()
        after = file_state(self.config)
        self.assertEqual((before[:2], before[3]), (after[:2], after[3]))
        self.assertNotEqual(before[2], after[2])
        self.changed(self.pack)

    def test_replacement_by_rename_with_same_size_and_mtime_is_detected(self):
        before = self.pack['_stats'][CONFIG]
        swap(self.config, b'[]')
        after = file_state(self.config)
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before[3], after[3])
        self.changed(self.pack)

    def test_verification_cache_key_is_unchanged(self):
        root = Path(self.pack['_root'])
        files = [[relative, *file_state(inside(root, relative))] for relative in sorted(sealed_files(self.pack))]
        self.assertEqual(digest([__version__, self.pack['_fingerprint'], file_state(root / 'pack.json'), files]), pack_signature(self.pack))
        cache = {}
        self.assertEqual(self.pack['_fingerprint'], verified_pack(self.pack['_root'], cache)['_fingerprint'])
        self.assertEqual([(self.pack['_root'], pack_signature(self.pack))], [(key, value[0]) for key, value in cache.items()])


class Harness(Base):
    def setUp(self):
        super().setUp()
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.store.update_settings({'cast_review': False, 'device': 'cpu'})
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.engines = []

    def generation(self):
        return self.store.create_generation(self.book['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                              'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False})

    def jobs(self):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: self.engines[-1])
        self.addCleanup(jobs.shutdown)
        return jobs

    def finish(self, jobs, generation, engine, start=None):
        self.engines.append(engine)
        (start or jobs.start)(generation['id'])
        jobs.thread.join(10)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])


class ModelCalls(Harness):
    def test_unchanged_pack_is_checked_around_every_model_call_and_renders(self):
        events = []
        class Logging(AliasEngine):
            def call(self, method, payload, stop):
                events.append(method)
                return super().call(method, payload, stop)
        def checked(pack, **options):
            events.append('check')
            return pack_unchanged(pack, **options)
        with patch.object(jobs_module, 'pack_unchanged', checked):
            done = self.finish(self.jobs(), self.generation(), Logging())
        self.assertEqual('ready', done['status'], done['error'])
        calls = [index for index, event in enumerate(events) if event != 'check']
        self.assertEqual({'probe', 'analyze', 'critique', 'reconcile', 'render'}, {events[index] for index in calls})
        self.assertEqual([], [events[index] for index in calls if events[index - 1:index + 2:2] != ['check', 'check']])

    def test_change_during_an_analysis_call_discards_its_reply(self):
        generation = self.generation()
        second = generation['segments'][-1]['chapter_id']
        untouched = [(segment['id'], segment['speaker'], segment['text']) for segment in generation['segments'] if segment['chapter_id'] == second]
        mutate = self.mutate
        class Tampering(CastEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'analyze' and payload['segments'][0]['chapter_id'] == second:
                    mutate()
                return result
        engine = Tampering()
        done = self.finish(self.jobs(), generation, engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual(0, engine.rendered)
        self.assertEqual(untouched, [(segment['id'], segment['speaker'], segment['text']) for segment in done['segments'] if segment['chapter_id'] == second])
        first = {segment['id'] for segment in generation['segments'] if segment['chapter_id'] != second}
        self.assertEqual(first, set(done['analysis']['completed_sources']))
        self.assertFalse(done['analysis']['complete'])

    def test_change_during_a_failed_analysis_call_reports_the_change(self):
        mutate = self.mutate
        class Hanging(CastEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    self.analyzed += 1
                    mutate()
                    raise EngineTransportError('Local inference exceeded its 1800-second operation limit.', 'timeout')
                return super().call(method, payload, stop)
        engine = Hanging()
        done = self.finish(self.jobs(), self.generation(), engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual(1, engine.analyzed)
        self.assertEqual({}, done['analysis'])

    def test_change_during_an_alias_comparison_discards_its_decision(self):
        engine = AliasEngine(lambda payload: self.mutate() or True)
        done = self.finish(self.jobs(), self.generation(), engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual((1, 0), (len(engine.reconciled), engine.rendered))
        analysis = done['analysis']
        self.assertEqual((False, None), (analysis['complete'], analysis.get('reconcile')))
        self.assertEqual({'stage': 'reconcile', 'done': 0, 'total': 1}, analysis['progress'])
        self.assertEqual(2, len({segment['speaker'] for segment in done['segments']} - {'narrator'}))

    def test_change_during_synthesis_publishes_nothing_and_the_take_is_not_reused(self):
        jobs = self.jobs()
        ready = self.finish(jobs, self.generation(), SyntheticEngine())
        self.assertEqual('ready', ready['status'], ready['error'])
        segment = ready['segments'][0]
        self.assertEqual([.85, 1.15], jobs_module.segment_pauses(segment))
        wav = self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'
        meta = json.loads(wav.with_suffix('.json').read_text('utf-8'))
        original = wav.read_bytes()
        wav.write_bytes(b'damaged')
        for key in segment['performance'].get('render_parts') or []:
            for suffix in ('.wav', '.json'):
                (self.store.root / 'audio' / f'{key}{suffix}').unlink(missing_ok=True)
        saved = sorted(path.name for path in (self.store.root / 'audio').iterdir())
        mutate, texts = self.mutate, []
        class Tampering(SyntheticEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'render' and payload['text'] == segment['spoken']:
                    mutate()
                return result
        class Counting(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    texts.append(payload['text'])
                return super().call(method, payload, stop)
        tampering = Tampering()
        failed = self.finish(jobs, ready, tampering, jobs.repair)
        self.assertEqual(('failed', CHANGED), (failed['status'], failed['error']))
        self.assertEqual(1, tampering.rendered)
        interrupted = failed['segments'][0]
        self.assertEqual(('pending', segment['audio_hash'], ''), (interrupted['status'], interrupted['audio_hash'], interrupted['warning']))
        self.assertEqual(['ready'] * (len(failed['segments']) - 1), [item['status'] for item in failed['segments'][1:]])
        self.assertEqual(saved, sorted(path.name for path in (self.store.root / 'audio').iterdir()))
        self.assertEqual(b'damaged', wav.read_bytes())
        self.assertEqual(meta, json.loads(wav.with_suffix('.json').read_text('utf-8')))
        self.restore()
        resumed = self.finish(jobs, failed, Counting())
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual([segment['spoken']], texts)
        self.assertEqual(original, wav.read_bytes())

    def test_change_during_an_implausible_retake_publishes_nothing(self):
        generation = self.generation()
        target = generation['segments'][0]['spoken']
        mutate = self.mutate
        class Retaking(SyntheticEngine):
            def call(self, method, payload, stop):
                if method != 'render' or payload['text'] != target:
                    return super().call(method, payload, stop)
                self.rendered += 1
                if self.rendered == 2:
                    mutate()
                    raise AppError('Speech output is empty, invalid, silent, or too long.')
                return tone(Path(payload['output']), 90)
        self.assertFalse(plausible(90, target))
        engine = Retaking()
        done = self.finish(self.jobs(), generation, engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual(2, engine.rendered)
        self.assertEqual({('pending', None)}, {(segment['status'], segment['audio_hash']) for segment in done['segments']})

    def test_change_after_the_render_reply_is_caught_before_publishing(self):
        mutate, read = self.mutate, jobs_module.read_audio
        def reading(path, **options):
            mutate()
            return read(path, **options)
        engine = SyntheticEngine()
        with patch.object(jobs_module, 'read_audio', reading):
            done = self.finish(self.jobs(), self.generation(), engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual(1, engine.rendered)
        self.assertEqual({('pending', None)}, {(segment['status'], segment['audio_hash']) for segment in done['segments']})


if __name__ == '__main__':
    unittest.main()
