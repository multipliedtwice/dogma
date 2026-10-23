import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.books import import_book
from rimsky.store import Store
from rimsky.util import file_digest
from sync_higgs_preview_runtime import sync_preview


def write_wave(path, samples, rate=24000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b''.join(int(value).to_bytes(2, 'little', signed=True) for value in samples))


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        return audio.readframes(audio.getnframes())


class PreviewRuntimeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'content/book.fb2'
        self.source.parent.mkdir()
        self.source.write_text('''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><book-title>Тест</book-title><author><first-name>Автор</first-name></author><lang>ru</lang></title-info></description><body><section><title><p>Глава 1</p></title><p>Первое слово. Второе слово. Третье слово.</p></section></body></FictionBook>''', encoding='utf-8')
        prepared = import_book(self.source.read_bytes(), self.source.name, self.root / 'prepared-assets')
        self.prepared = self.root / 'training-work/source/book.json'
        self.prepared.parent.mkdir(parents=True)
        self.prepared.write_text(json.dumps(prepared, ensure_ascii=False), encoding='utf-8')
        chapter = prepared['chapters'][0]
        self.chapter_id = chapter['id']
        self.heading = chapter['blocks'][0]
        self.paragraph = chapter['blocks'][1]
        self.generation = self.root / 'training-work/generation-1'
        (self.generation / self.chapter_id).mkdir(parents=True)
        self.media = self.root / 'training-work/character-rb2/full/clips/elirm-iv-mvp/chapter.wav'
        self.data = self.root / 'listener-library'

    def publish(self, three=False, music=False, synthesis=None):
        count = 24000 * (4 if three else 3)
        samples = [((index * 97) % 20000) - 10000 for index in range(count)]
        write_wave(self.media, samples)
        rows = [
            {'id': f'{self.chapter_id}-u0000', 'start': .25, 'end': .75, 'source_id': self.heading['id'],
             'text': self.heading['text'], 'speaker': 'narrator', 'voice': 'opening', 'mood': 'neutral'},
            {'id': f'{self.chapter_id}-u0001', 'start': 1.0, 'end': 1.8, 'source_id': self.paragraph['id'],
             'text': 'Первое слово. ', 'speaker': 'narrator', 'voice': 'opening', 'mood': 'neutral'},
        ]
        if three:
            rows.append({'id': f'{self.chapter_id}-u0002', 'start': 2.1, 'end': 3.5,
                         'source_id': self.paragraph['id'], 'text': 'Второе слово. Третье слово.',
                         'speaker': 'hero', 'voice': 'male_clear', 'mood': 'firm'})
        if synthesis:
            rows[1]['synthesis'] = synthesis
        timeline = self.generation / self.chapter_id / 'bound.timeline.json'
        timeline.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        ready = {'id': self.chapter_id, 'seconds': count / 24000, 'complete': three, 'units': len(rows),
                 'stream': '/character-rb2/full/clips/elirm-iv-mvp/chapter.wav',
                 'wav_sha256': file_digest(self.media), 'word_flags': 0,
                 'timeline_asset': {'file': f'{self.chapter_id}/bound.timeline.json', 'sha256': file_digest(timeline),
                                    'audio_sha256': file_digest(self.media)}}
        progress = {'generation': self.generation.name, 'status': 'complete' if three else 'rendering',
                    'effects_policy': 'disabled', 'chapters': {self.chapter_id: ready}}
        if music:
            music_path = self.media.parent / 'scene-music-test.wav'
            write_wave(music_path, [value // 2 for value in samples])
            ready['music'] = {'kind': 'scene_bed', 'start': 0, 'seconds': count / 24000,
                              'sha256': file_digest(music_path),
                              'stream': '/character-rb2/full/clips/elirm-iv-mvp/scene-music-test.wav',
                              'qualification': 'Shared app scene layout; independently verified YuE2 phrases and procedural ambience.'}
        (self.generation / 'progress.json').write_text(json.dumps(progress), encoding='utf-8')
        return pcm(self.media)

    def sync(self):
        return sync_preview(workspace=self.root, source=self.source, prepared_book=self.prepared,
                            generation_dir=self.generation, data_dir=self.data)

    def test_seeds_real_store_and_preserves_every_published_pcm_frame(self):
        original = self.publish()
        result = self.sync()
        self.assertEqual(result['segments'], 2)
        self.assertEqual(result['changed_chapters'], [self.chapter_id])
        store = Store(self.data)
        self.addCleanup(store.close)
        generation = store.generation(result['generation_id'])
        self.assertEqual('ru', generation['config']['opening']['language'])
        self.assertEqual(8, generation['config']['opening']['variants']['music']['duration'])
        self.assertEqual(generation['config']['engine'], 'higgs_audio_tts_q8_external_preview')
        self.assertEqual(generation['status'], 'rendering')
        self.assertFalse(generation['analysis']['complete'])
        self.assertFalse(generation['analysis']['approved'])
        self.assertEqual([(row['start'], row['end']) for row in generation['segments']],
                         [(0, len(self.heading['text'])), (0, len('Первое слово. '))])
        rebuilt = b''.join(pcm(self.data / 'audio' / f"{row['audio_hash']}.wav") for row in generation['segments'])
        self.assertEqual(rebuilt, original)
        for row in generation['segments']:
            meta = json.loads((self.data / 'audio' / f"{row['audio_hash']}.json").read_text('utf-8'))
            self.assertEqual(meta['sha256'], row['audio_hash'])
            self.assertFalse(meta['reset_cues'])

    def test_repeat_is_idempotent_and_later_publication_advances_revision(self):
        self.publish()
        first = self.sync()
        store = Store(self.data)
        self.addCleanup(store.close)
        revision = store.generation(first['generation_id'])['revision']
        opening = store.generation(first['generation_id'])['config']['opening']
        # Keep a normal production Store connection open while the independent
        # sync connection repeats and advances the library.
        second = self.sync()
        self.assertEqual(second['changed_chapters'], [])
        self.assertEqual(store.generation(first['generation_id'])['revision'], revision)
        self.assertIsNone(store.generation_poll(first['generation_id'], revision)['generation'])

        original = self.publish(three=True)
        third = self.sync()
        self.assertEqual(third['segments'], 3)
        generation = store.generation(first['generation_id'])
        self.assertEqual(opening, generation['config']['opening'])
        polled = store.generation_poll(first['generation_id'], revision)
        self.assertGreater(generation['revision'], revision)
        self.assertEqual(polled['revision'], generation['revision'])
        self.assertFalse(polled['full'])
        self.assertEqual([row['id'] for row in polled['generation']['segments']],
                         [row['id'] for row in generation['segments']])
        self.assertEqual(polled['removed_segment_ids'], [])
        self.assertEqual(generation['status'], 'ready')
        self.assertTrue(generation['analysis']['complete'])
        self.assertFalse(generation['analysis']['approved'])
        self.assertEqual(generation['segments'][2]['start'], len('Первое слово. '))
        rebuilt = b''.join(pcm(self.data / 'audio' / f"{row['audio_hash']}.wav") for row in generation['segments'])
        self.assertEqual(rebuilt, original)

    def test_existing_legacy_generation_is_never_backfilled_by_watcher(self):
        self.publish()
        first = self.sync()
        store = Store(self.data)
        self.addCleanup(store.close)
        generation = store.generation(first['generation_id'])
        config = dict(generation['config'])
        config.pop('opening')
        store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(config), generation['id']))
        before = [(s['id'], s['audio_hash']) for s in generation['segments']]
        self.sync()
        after = store.generation(first['generation_id'])
        self.assertNotIn('opening', after['config'])
        self.assertEqual(before, [(s['id'], s['audio_hash']) for s in after['segments']])

    def test_copies_exact_synthesis_snapshot_and_rebuilds_when_timeline_changes(self):
        self.publish()
        first = self.sync()
        snapshot = {'prompt': 'Всё рассла́блено.', 'key': 'c-u0001', 'take_audio_sha256': 'ab' * 32,
                    'automatic_pronunciation': {'rule': 'x'}, 'phrase_pronunciation': None}
        self.publish(synthesis=snapshot)
        second = self.sync()
        self.assertEqual(second['changed_chapters'], [self.chapter_id])
        store = Store(self.data)
        self.addCleanup(store.close)
        segments = store.generation(second['generation_id'])['segments']
        self.assertNotIn('synthesis_snapshot', segments[0]['performance'])
        self.assertEqual(segments[1]['performance']['synthesis_snapshot'], snapshot)

    def test_rejects_a_source_that_no_longer_matches_preparation(self):
        self.publish()
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'exact original source'):
            self.sync()
        self.assertFalse((self.data / 'library.sqlite3').exists())

    def test_imports_verified_chapter_mix_for_production_playback(self):
        self.publish(music=True)
        result = self.sync()
        self.assertEqual(result['music_cues'], 1)
        store = Store(self.data)
        self.addCleanup(store.close)
        generation = store.generation(result['generation_id'])
        self.assertEqual(generation['config']['external_music'], 'verified-chapter-mix-v1')
        cue = store.rows('SELECT * FROM music_cues WHERE generation_id=?', (result['generation_id'],))[0]
        self.assertEqual((cue['id'], cue['kind'], cue['status']),
                         (f'chapter_mix:{self.chapter_id}', 'chapter_mix', 'ready'))
        self.assertEqual(file_digest(self.data / 'music' / f"{cue['audio_key']}.wav"), cue['audio_key'])


if __name__ == '__main__':
    unittest.main()
