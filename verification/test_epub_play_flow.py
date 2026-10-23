from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import SyntheticEngine, epub_fixture, pack_fixture
from rimsky.integrity import accepted_audio, validate_coverage
from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import AppError
from server import Application


class EpubPlaybackRepair(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root / 'binaries')
        self.store = self.app.store
        self.store.update_settings({'cast_review': False})
        self.addCleanup(self.app.close)
        self.book = self.store.import_data(epub_fixture(), 'playback.epub')
        self.pack = pack_fixture(self.root / 'pack')
        self.engine = SyntheticEngine()
        self.app.jobs.engine_factory = lambda _: self.engine
        self.generation = self.store.create_generation(self.book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False,
        })
        self.app.jobs.start(self.generation['id'])
        self.wait()
        self.original = self.store.generation(self.generation['id'])
        self.assertEqual('ready', self.original['status'], self.original['error'])
        self.renders = self.engine.rendered
        self.analyses = self.engine.analyzed

    def wait(self):
        self.app.jobs.thread.join(10)
        self.assertFalse(self.app.jobs.thread.is_alive())

    def repair(self):
        result = self.app.dispatch('repair_audio', {'id': self.generation['id']})
        self.wait()
        return self.store.generation(result['id'])

    def audio(self, segment=None):
        key = (segment or self.original['segments'][0])['audio_hash']
        return self.store.root / 'audio' / f'{key}.wav'

    def drop_parts(self, segment=None):
        for key in ((segment or self.original['segments'][0]).get('performance') or {}).get('render_parts') or []:
            for suffix in ('.wav', '.json'):
                (self.store.root / 'audio' / f'{key}{suffix}').unlink(missing_ok=True)

    def assert_preserved(self, repaired):
        self.assertEqual('ready', repaired['status'], repaired['error'])
        self.assertEqual(self.original['cast'], repaired['cast'])
        self.assertEqual(self.original['config'], repaired['config'])
        self.assertEqual(self.original['analysis'], repaired['analysis'])
        self.assertEqual(1, len(self.store.generation_list(self.book['id'])))
        validate_coverage(self.book, repaired['config']['chapters'], repaired['segments'])
        self.assertEqual(self.analyses, self.engine.analyzed)

    def test_missing_saved_audio_can_be_repaired_in_place(self):
        self.audio().unlink()
        self.drop_parts()
        with self.assertRaises(AppError):
            self.app.asset('audio', self.original['segments'][0]['audio_hash'])
        repaired = self.repair()
        self.assert_preserved(repaired)
        self.assertEqual(self.renders + 1, self.engine.rendered)
        self.assertEqual(self.original['segments'][1:], repaired['segments'][1:])
        self.app.asset('audio', repaired['segments'][0]['audio_hash'])

    def test_corrupt_saved_audio_is_regenerated_not_accepted(self):
        self.audio().write_bytes(b'not a WAV')
        self.drop_parts()
        repaired = self.repair()
        self.assert_preserved(repaired)
        self.assertEqual(self.renders + 1, self.engine.rendered)
        self.assertFalse(accepted_audio(self.audio())['reset_cues'])

    def test_healthy_audio_check_needs_no_model_and_no_new_inference(self):
        shutil.rmtree(self.root / 'pack')
        repaired = self.repair()
        self.assertEqual(self.original, repaired)
        self.assertEqual(self.renders, self.engine.rendered)

    def test_missing_pack_does_not_mutate_saved_generation_on_repair(self):
        self.audio().unlink()
        shutil.rmtree(self.root / 'pack')
        with self.assertRaises(AppError):
            self.repair()
        self.assertEqual(self.original, self.store.generation(self.generation['id']))
        self.assertEqual(self.renders, self.engine.rendered)

    def test_corrupted_pack_cannot_be_used_for_repair(self):
        self.audio().unlink()
        (self.root / 'pack/model.gguf').write_bytes(b'changed bytes')
        with self.assertRaisesRegex(AppError, 'checksum'):
            self.repair()
        self.assertEqual(self.original, self.store.generation(self.generation['id']))

    def test_database_duration_mismatch_reuses_valid_audio_and_clears_wrong_cues(self):
        first = self.original['segments'][0]
        self.store.execute('UPDATE segments SET duration=4,cues=? WHERE generation_id=? AND id=?',
                           (json.dumps([{'text': first['spoken'], 'start': 0, 'end': 4}]), self.generation['id'], first['id']))
        repaired = self.repair()
        self.assert_preserved(repaired)
        self.assertEqual(self.renders, self.engine.rendered)
        self.assertAlmostEqual(first['duration'], repaired['segments'][0]['duration'])
        self.assertEqual([], repaired['segments'][0]['cues'])

    def test_healthy_word_cues_survive_checking_and_another_segment_repair(self):
        second = self.original['segments'][1]
        cues = [{'text': second['spoken'], 'start': 0, 'end': second['duration']}]
        self.store.execute('UPDATE segments SET cues=? WHERE generation_id=? AND id=?',
                           (json.dumps(cues), self.generation['id'], second['id']))
        self.audio().unlink()
        self.drop_parts()
        repaired = self.repair()
        self.assertEqual(cues, repaired['segments'][1]['cues'])
        self.assertEqual(self.renders + 1, self.engine.rendered)

    def test_pending_metadata_publication_finishes_without_new_synthesis(self):
        path = self.audio().with_suffix('.json')
        meta = json.loads(path.read_text())
        path.write_text(json.dumps({**meta, 'reset_cues': True}))
        repaired = self.repair()
        self.assert_preserved(repaired)
        self.assertEqual(self.renders, self.engine.rendered)
        self.assertFalse(accepted_audio(self.audio())['reset_cues'])

    def test_repair_refuses_incomplete_source_coverage_before_mutating(self):
        self.store.execute('DELETE FROM segments WHERE generation_id=? AND id=?',
                           (self.generation['id'], self.original['segments'][0]['id']))
        before = self.store.generation(self.generation['id'])
        with self.assertRaises(AppError):
            self.repair()
        self.assertEqual(before, self.store.generation(self.generation['id']))
        self.assertEqual(self.renders, self.engine.rendered)

    def test_repair_cannot_interrupt_another_active_generation(self):
        self.app.jobs.active = 'different-generation'
        try:
            with self.assertRaisesRegex(AppError, 'Pause'):
                self.repair()
        finally:
            self.app.jobs.active = None
        self.assertEqual(self.original, self.store.generation(self.generation['id']))

    def test_repair_refuses_unfinished_analysis(self):
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', ('{}', self.generation['id']))
        with self.assertRaisesRegex(AppError, 'analysis'):
            self.repair()
        self.assertEqual(self.renders, self.engine.rendered)

    def test_repair_failure_remains_resumable_without_new_casting(self):
        self.audio().unlink()
        with patch.object(self.app.jobs.engine_factory(self.root), 'call', side_effect=AppError('Controlled render failure')):
            repaired = self.repair()
            self.assertEqual('failed', repaired['status'])
        self.app.jobs.start(self.generation['id'])
        self.wait()
        self.assert_preserved(self.store.generation(self.generation['id']))

    def test_repair_preserves_listening_position_and_previous_exports(self):
        second = self.original['segments'][1]
        position = {'generation_id': self.generation['id'], 'chapter_id': second['chapter_id'],
                    'source_id': second['source_id'], 'segment_id': second['id'], 'offset': .04}
        self.store.save_position(self.book['id'], position)
        exported = self.app.dispatch('export', {'id': self.generation['id'], 'format': 'vtt'})
        path = self.store.root / 'exports' / exported['file']
        content = path.read_bytes()
        self.audio().unlink()
        self.repair()
        self.assertEqual(position, self.store.book(self.book['id'])['position'])
        self.assertEqual(content, path.read_bytes())

    def test_closed_scheduler_rejects_audio_repair(self):
        self.app.jobs.shutdown()
        with self.assertRaisesRegex(AppError, 'closing'):
            self.repair()

    def test_failed_finalization_with_all_audio_reuses_it_when_repaired(self):
        self.store.status(self.generation['id'], 'failed', 'Controlled finalization interruption')
        repaired = self.repair()
        self.assert_preserved(repaired)
        self.assertEqual(self.renders, self.engine.rendered)

    def test_repair_recovers_shared_audio_without_recasting_other_version(self):
        revised = self.store.revise(self.generation['id'], [{'id': self.original['segments'][-1]['id'],
            'spoken': self.original['segments'][-1]['spoken'] + ' Fixture correction.'}])
        self.app.jobs.start(revised['id'])
        self.wait()
        other = self.store.generation(revised['id'])
        self.assertEqual('ready', other['status'])
        before = self.engine.rendered
        self.audio().unlink()
        self.drop_parts()
        repaired = self.repair()
        self.assertEqual('ready', repaired['status'])
        self.assertEqual(before + 1, self.engine.rendered)
        self.assertEqual(other['cast'], self.store.generation(revised['id'])['cast'])
        self.assertEqual(other['segments'][0]['audio_hash'], repaired['segments'][0]['audio_hash'])
        self.app.asset('audio', other['segments'][0]['audio_hash'])

    def test_repair_checkpoint_database_failure_does_not_partially_reset_segments(self):
        import sqlite3
        first, second = self.original['segments'][:2]
        self.audio(first).unlink()
        self.audio(second).unlink()
        self.store.execute(f"CREATE TRIGGER reject_repair BEFORE UPDATE OF status ON segments WHEN NEW.id='{second['id']}' BEGIN SELECT RAISE(ABORT,'controlled repair failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.repair()
        self.assertEqual(self.original, self.store.generation(self.generation['id']))
        self.assertEqual(self.renders, self.engine.rendered)
