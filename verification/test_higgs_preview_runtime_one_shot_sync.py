"""Exercise the real Store bridge using temporary speech/publication fixtures only."""
import json
import unittest

from verification import test_higgs_preview_runtime_sync as fixture
from sync_higgs_preview_runtime import UNCHECKED, sync_preview
from rimsky.store import Store
from rimsky.util import file_digest


class OneShotRuntimeSyncTests(unittest.TestCase):
    setUp = fixture.PreviewRuntimeSyncTests.setUp
    publish = fixture.PreviewRuntimeSyncTests.publish
    sync = fixture.PreviewRuntimeSyncTests.sync

    def one_shot(self, *, complete=False, music=False):
        original = self.publish(three=complete, music=music)
        path = self.generation / 'progress.json'
        progress = json.loads(path.read_text('utf-8'))
        progress.update(UNCHECKED)
        for ready in progress['chapters'].values():
            ready.pop('word_flags')
            ready.update(UNCHECKED)
        self.save(progress)
        return original, progress

    def save(self, progress):
        (self.generation / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')

    def store(self):
        store = Store(self.data)
        self.addCleanup(store.close)
        return store

    def assert_unchecked(self, record):
        for key, value in UNCHECKED.items():
            self.assertEqual(record[key], value, key)
            self.assertIs(type(record[key]), type(value))

    def test_complete_playable_is_explicitly_unchecked_with_exact_speech_and_cached_music(self):
        original, progress = self.one_shot(complete=True, music=True)
        music = progress['chapters'][self.chapter_id]['music']
        before = (self.media.parent / 'scene-music-test.wav').read_bytes()
        result = self.sync()
        generation = self.store().generation(result['generation_id'])
        self.assertEqual('ready', generation['status'])
        self.assertTrue(generation['analysis']['complete'])  # technical coverage only
        self.assertFalse(generation['analysis']['approved'])
        for record in (result, generation['config'], generation['analysis'],
                       generation['analysis']['external_preview']):
            self.assert_unchecked(record)
        pending = generation['analysis']['external_preview']['pending_checks']
        self.assertIsNone(pending['recognition_flags'])
        self.assertIsNone(pending['assignment_flags'])
        self.assertEqual('not_checked', pending['recognition_status'])
        for segment in generation['segments']:
            self.assertEqual('ready', segment['status'])
            self.assert_unchecked(segment['performance'])
            self.assert_unchecked(segment['performance']['external_preview'])
            self.assertIn('not run', segment['warning'])
        rebuilt = b''.join(fixture.pcm(self.data / 'audio' / f"{s['audio_hash']}.wav")
                           for s in generation['segments'])
        self.assertEqual(original, rebuilt)  # no opening inserted into reader/source PCM
        self.assertEqual('ru', generation['config']['opening']['language'])
        self.assertEqual(8, generation['config']['opening']['variants']['music']['duration'])
        self.assertEqual(before, (self.data / 'music' / f"{music['sha256']}.wav").read_bytes())
        self.assertEqual(music['sha256'], file_digest(self.data / 'music' / f"{music['sha256']}.wav"))

    def test_progressive_poll_preserves_unchecked_state_and_one_opening(self):
        self.one_shot()
        first = self.sync()
        store = self.store()
        previous = store.generation(first['generation_id'])
        self.assertEqual('rendering', previous['status'])
        self.assertFalse(previous['analysis']['complete'])
        self.assertEqual('verified-chapter-mix-v1', previous['config']['external_music'])
        self.sync()
        self.assertEqual(previous['revision'], store.generation(first['generation_id'])['revision'])
        self.one_shot(complete=True)
        self.sync()
        polled = store.generation_poll(first['generation_id'], previous['revision'])['generation']
        # Store intentionally filters analysis in its poll API; config and
        # per-segment performance must retain the disclosure independently.
        self.assert_unchecked(polled['config'])
        self.assertEqual(previous['config']['opening'], polled['config']['opening'])
        self.assertEqual(3, len(polled['segments']))
        for segment in polled['segments']:
            self.assert_unchecked(segment['performance'])

    def test_missing_new_music_publication_keeps_existing_cached_cue(self):
        _, progress = self.one_shot(music=True)
        first = self.sync()
        store = self.store()
        before = store.rows('SELECT * FROM music_cues WHERE generation_id=?', (first['generation_id'],))
        progress['chapters'][self.chapter_id].pop('music')
        self.save(progress)
        second = self.sync()
        self.assertEqual(1, second['music_cues'])
        self.assertEqual(before, store.rows('SELECT * FROM music_cues WHERE generation_id=?', (first['generation_id'],)))
        self.assertEqual('verified-chapter-mix-v1', store.generation(first['generation_id'])['config']['external_music'])

    def test_missing_or_conflicting_top_level_state_fails_before_store_creation(self):
        for key in UNCHECKED:
            with self.subTest(key=key):
                _, progress = self.one_shot()
                progress.pop(key)
                self.save(progress)
                with self.assertRaisesRegex(ValueError, 'unchecked state'):
                    self.sync()
                self.assertFalse((self.data / 'library.sqlite3').exists())

    def test_chapter_or_row_cannot_report_zero_errors_or_approval(self):
        _, progress = self.one_shot()
        ready = progress['chapters'][self.chapter_id]
        ready['word_flags'] = 0
        self.save(progress)
        with self.assertRaisesRegex(ValueError, 'word_flags'):
            self.sync()
        self.assertFalse((self.data / 'library.sqlite3').exists())
        ready.pop('word_flags')
        ready['machine_verified'] = True
        self.save(progress)
        with self.assertRaisesRegex(ValueError, 'machine_verified'):
            self.sync()
        ready['machine_verified'] = False
        timeline = self.generation / ready['timeline_asset']['file']
        rows = json.loads(timeline.read_text('utf-8'))
        rows[0]['recognition_status'] = 'passed'
        timeline.write_text(json.dumps(rows), encoding='utf-8')
        ready['timeline_asset']['sha256'] = file_digest(timeline)
        self.save(progress)
        with self.assertRaisesRegex(ValueError, 'recognition_status'):
            self.sync()

    def test_same_pcm_legacy_checkpoint_does_not_reuse_implicit_review_metadata(self):
        self.publish()
        old = self.sync()
        store = self.store()
        old_segments = store.generation(old['generation_id'])['segments']
        self.one_shot()
        new = self.sync()
        self.assertEqual([self.chapter_id], new['changed_chapters'])
        new_segments = store.generation(old['generation_id'])['segments']
        self.assertEqual([s['audio_hash'] for s in old_segments], [s['audio_hash'] for s in new_segments])
        self.assert_unchecked(new_segments[0]['performance'])

    def test_partial_complete_and_real_technical_failure_are_not_mislabeled(self):
        _, progress = self.one_shot()
        progress['status'] = 'complete'
        self.save(progress)
        with self.assertRaisesRegex(ValueError, 'complete published chapters'):
            self.sync()
        progress['status'] = 'technical_failure'
        progress['error'] = 'Native process failed; retained prefix is playable.'
        self.save(progress)
        result = self.sync()
        generation = self.store().generation(result['generation_id'])
        self.assertEqual('held', generation['status'])
        self.assertEqual(progress['error'], generation['error'])
        self.assertEqual('ready', generation['segments'][0]['status'])
        self.assert_unchecked(generation['config'])

    def test_preserves_original_internal_structure_and_native_snapshot(self):
        _, progress = self.one_shot()
        ready = progress['chapters'][self.chapter_id]
        timeline = self.generation / ready['timeline_asset']['file']
        rows = json.loads(timeline.read_text('utf-8'))
        parts = [{'text': rows[1]['text'], 'mode': 'narrator', 'start': 0, 'end': len(rows[1]['text'])}]
        snapshot = {'prompt': 'prepared native input', 'take': 1}
        rows[1].update(performance={'analysis_parts': parts}, synthesis=snapshot)
        timeline.write_text(json.dumps(rows), encoding='utf-8')
        ready['timeline_asset']['sha256'] = file_digest(timeline)
        self.save(progress)
        result = self.sync()
        segment = self.store().generation(result['generation_id'])['segments'][1]
        self.assertEqual(parts, segment['performance']['analysis_parts'])
        self.assertEqual(snapshot, segment['performance']['synthesis_snapshot'])
        self.assertEqual(rows[1]['text'], segment['text'])


if __name__ == '__main__':
    unittest.main()
