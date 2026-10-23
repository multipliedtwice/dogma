from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'runtime'), str(Path(__file__).resolve().parent)]

from test_core import SyntheticEngine, pack_fixture, write_wave
from rimsky.export import build_export
from rimsky.integrity import validate_coverage
from rimsky.jobs import Jobs, base_segments
from rimsky.preparation import default_chapters
from rimsky.store import Store
from rimsky.util import AnalysisCapacityError, AppError


class ControlledFlowEngine(SyntheticEngine):
    def __init__(self, fail_after=None):
        super().__init__(fail_after=fail_after)
        self.capacity_events = 0
        self.rendered_texts = []

    def call(self, method, payload, stop):
        if method == 'analyze' and len(payload['segments']) > 20:
            self.capacity_events += 1
            raise AnalysisCapacityError('Controlled capacity limit; not measured model capacity')
        if method == 'render':
            if self.fail_after is not None and self.rendered >= self.fail_after:
                raise AppError('Controlled interruption for persisted-audio recovery')
            self.rendered += 1
            self.rendered_texts.append(payload['text'])
            return write_wave(Path(payload['output']), seconds=.2)
        return super().call(method, payload, stop)


def finish(jobs, generation_id):
    jobs.start(generation_id)
    jobs.thread.join(150)
    if jobs.thread.is_alive():
        jobs.shutdown()
        raise RuntimeError('Controlled flow did not finish within its verification budget')
    return jobs.store.generation(generation_id)


def check(path):
    started = time.monotonic()
    raw = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix='rimsky-flow-') as temporary:
        root = Path(temporary)
        store = Store(root / 'library')
        store.update_settings({'cast_review': False})
        jobs = None
        try:
            book = store.import_data(raw, path.name)
            pack = pack_fixture(root / 'controlled-pack')
            bases = base_segments(book, default_chapters(book))
            generation = store.create_generation(book['id'], {
                'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                'pack_name': pack['name'], 'device': 'cpu', 'align_words': False,
            })
            first = ControlledFlowEngine(fail_after=50)
            jobs = Jobs(store, root, lambda _: first)
            paused = finish(jobs, generation['id'])
            assert paused['status'] == 'failed', paused['error']
            preserved = {s['id']: s['audio_hash'] for s in paused['segments'] if s['status'] == 'ready'}
            assert len(preserved) >= 50
            frozen_cast = paused['cast']
            jobs.shutdown()
            store.close()
            store = Store(root / 'library')
            second = ControlledFlowEngine()
            jobs = Jobs(store, root, lambda _: second)
            completed = finish(jobs, generation['id'])
            assert completed['status'] == 'ready', completed['error']
            assert second.analyzed == 0
            assert completed['cast'] == frozen_cast
            assert all(s['audio_hash'] == preserved[s['id']] for s in completed['segments'] if s['id'] in preserved)
            assert not set(first.rendered_texts) & set(second.rendered_texts)
            validate_coverage(book, completed['config']['chapters'], completed['segments'])
            assert len(completed['segments']) == len(bases)
            assert sum(len(s['text']) for s in completed['segments']) == book['preparation']['narration_characters']
            exports = {}
            generated_count = first.rendered + second.rendered
            for kind in ('m4b', 'mp3', 'chapters', 'srt', 'vtt'):
                artifact = build_export(store, completed['id'], kind, None, root)
                exports[kind] = {'bytes': artifact['size']}
            assert first.rendered + second.rendered == generated_count
            correction = store.revise(completed['id'], [{
                'id': completed['segments'][0]['id'],
                'spoken': completed['segments'][0]['spoken'] + ' Контрольная правка.',
            }])
            third = ControlledFlowEngine()
            jobs.engine_factory = lambda _: third
            revised = finish(jobs, correction['id'])
            assert revised['status'] == 'ready', revised['error']
            assert third.rendered == 1 and third.analyzed == 0
            assert store.generation(completed['id'])['segments'] == completed['segments']
            validate_coverage(book, revised['config']['chapters'], revised['segments'])
            first_chapter = completed['config']['chapters'][0]
            selection = build_export(store, revised['id'], 'm4b', [first_chapter], root)
            original = root / 'library' / 'books' / book['id'] / 'source.epub'
            assert original.read_bytes() == raw
            return {
                'basis': 'Uploaded EPUB; controlled narrator-only annotations and synthetic 0.2-second tones. No real speech or text model.',
                'source_sha256': hashlib.sha256(raw).hexdigest(),
                'sections': len(book['chapters']), 'selected_narration_sections': len(completed['config']['chapters']),
                'source_characters': sum(len(s['text']) for s in completed['segments']),
                'segments': len(completed['segments']), 'accepted_analysis_batches': first.analyzed,
                'controlled_capacity_events': first.capacity_events,
                'preserved_ready_segments_after_restart': len(preserved),
                'unique_synthetic_renders': generated_count, 'resumed_analysis_calls': second.analyzed,
                'exports_without_inference': exports,
                'correction': {'new_audio_segments': third.rendered, 'analysis_calls': third.analyzed,
                               'selected_chapter_m4b_bytes': selection['size'], 'original_unchanged': True},
                'source_unchanged': True, 'complete_ordered_source_coverage': True,
                'local_harness_seconds': round(time.monotonic() - started, 3),
                'limits': 'Not a speech-quality, speaker-attribution, target-hardware or native application benchmark. All temporary book/audio files are removed.',
            }
        finally:
            if jobs:
                jobs.shutdown()
            store.close()


def main():
    parser = argparse.ArgumentParser(description='Verify EPUB-to-export software flow using clearly synthetic audio; never substitute this for real-model acceptance.')
    parser.add_argument('epub', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = json.dumps(check(args.epub), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(report + '\n', 'utf-8')
    print(report)


if __name__ == '__main__':
    main()
