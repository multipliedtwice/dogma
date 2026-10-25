from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from test_core import FB2, epub_fixture
from rimsky.integrity import accepted_audio
from rimsky.opening import (AUDIO_PINS, MANIFEST_SHA256, load_opening_pack, matching_language,
                            opening_hashes, select_opening, verify_opening_audio)
from rimsky.store import Store
from rimsky.util import AppError
from server import Application

ROOT = Path(__file__).resolve().parents[1]


class OpeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / 'library')
        self.book = self.store.import_data(FB2, 'book.fb2')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self):
        return self.store.create_generation(self.book['id'], {})

    def test_approved_pack_provenance_and_always_musical_generation(self):
        pack, _ = load_opening_pack()
        self.assertEqual('78f09d20145ab79b8a6d425bc68fe104e3c624a7aea687bb0ff31c3a186374d1', pack['model_sha256'])
        self.assertEqual('58a4f3284b0312cee9834a6753bc63168de4c3fffc962a992fa79516de5d29b6', pack['reference_sha256'])
        generation = self.generation()
        opening = generation['config']['opening']
        self.assertEqual(MANIFEST_SHA256, opening['manifest_sha256'])
        self.assertEqual('independent_witness_exact_match', opening['checks']['speech'])
        self.assertEqual('ru', opening['language'])
        mixed = select_opening(generation, music_enabled=False)
        self.assertEqual(mixed, select_opening(generation, music_enabled=True))
        self.assertEqual('music', mixed['variant'])
        self.assertEqual(8, mixed['duration'])
        self.assertEqual(AUDIO_PINS['ru']['music'], mixed['audio_hash'])
        actual = verify_opening_audio(self.store.audio_path(mixed['audio_hash']), mixed)
        self.assertEqual(2, actual['channels'])
        with self.assertRaises(AppError):
            accepted_audio(self.store.audio_path(mixed['audio_hash']))
        # No branded source passage, ordinal, caption cue or chapter was invented.
        self.assertTrue(generation['segments'])
        self.assertTrue(all(s['source_id'] in {b['id'] for c in self.book['chapters'] for b in c['blocks']}
                            for s in generation['segments']))
        self.assertNotIn(opening['text'], [s['text'] for s in generation['segments']])

    def test_language_is_declared_and_matching_without_script_guessing(self):
        for language in ('ru', 'rus', 'Russian', 'ru-RU'):
            self.assertEqual('ru', matching_language({'language': language}))
        for language in ('en', 'eng', 'English', 'en-GB'):
            self.assertEqual('en', matching_language({'language': language}))
        for language in ('de', 'de-DE', 'und', 'mul', '', None):
            with self.subTest(language=language), self.assertRaises(AppError) as raised:
                matching_language({'language': language, 'chapters': [{'text': 'This Latin text is German.'}]})
            self.assertEqual('opening_language_required', raised.exception.code)
        english_book = self.store.import_data(epub_fixture(), 'english.epub')
        english = self.store.create_generation(english_book['id'], {})
        self.assertEqual('en', english['config']['opening']['language'])
        self.assertEqual('asr_exact_match', english['config']['opening']['checks']['speech'])
        bad = self.store._book_manifest(self.book['id'])
        bad['language'] = 'de'
        self.store.execute('UPDATE books SET manifest=? WHERE id=?', (json.dumps(bad), self.book['id']))
        count = len(self.store.rows('SELECT id FROM generations'))
        with self.assertRaisesRegex(AppError, 'declared language: de'):
            self.generation()
        self.assertEqual(count, len(self.store.rows('SELECT id FROM generations')))

    def test_server_authorizes_only_referenced_opening_and_checks_stereo(self):
        generation = self.generation()
        selected = select_opening(generation)
        app = Application.__new__(Application)
        app.store = self.store
        with app.open_audio(selected['audio_hash']) as source:
            self.assertEqual(b'RIFF', source.read(4))
        self.store.execute('UPDATE generations SET config=? WHERE id=?', ('{}', generation['id']))
        with self.assertRaisesRegex(AppError, 'Completed audio not found'):
            with app.open_audio(selected['audio_hash']):
                pass

    def test_cleanup_protects_shared_pin_until_last_generation_is_deleted(self):
        first = self.generation()
        other = self.store.import_data(FB2.replace(b'<book-title>',b'<book-title>Other '),'other.fb2')
        second = self.store.create_generation(other['id'],{})
        self.assertEqual(first['config']['opening'], second['config']['opening'])
        hashes = opening_hashes(first)
        self.store.delete_generation(first['id'])
        self.assertTrue(all(self.store.audio_path(key).exists() for key in hashes))
        self.store.delete_generation(second['id'])
        self.assertTrue(all(not self.store.audio_path(key).exists() for key in hashes))

    def test_position_round_trip_requires_matching_key_music_and_normal_anchor(self):
        generation = self.generation()
        segment = generation['segments'][0]
        position = {'generation_id': generation['id'], 'chapter_id': segment['chapter_id'],
                    'source_id': segment['source_id'], 'segment_id': segment['id'], 'offset': 0,
                    'opening': {'key': generation['config']['opening']['key'], 'offset': 2.5,
                                'complete': False, 'variant': 'music'}}
        self.assertEqual(position, self.store.save_position(self.book['id'], position))
        for patch in ({'key': '0' * 64}, {'offset': 8.01}, {'offset': True}, {'offset': float('nan')},
                      {'complete': 1}, {'variant': 'speech'}, {'extra': 1}):
            bad = copy.deepcopy(position)
            bad['opening'].update(patch)
            with self.subTest(patch=patch), self.assertRaises(AppError):
                self.store.save_position(self.book['id'], bad)
        bad = copy.deepcopy(position)
        bad.pop('generation_id')
        with self.assertRaises(AppError):
            self.store.save_position(self.book['id'], bad)

    def test_missing_corrupt_pack_and_library_metadata_fail_closed(self):
        resource_root = Path(self.temporary.name) / 'resources'
        with self.assertRaises(AppError):
            load_opening_pack(resource_root)
        shutil.copytree(ROOT / 'resources/branding', resource_root / 'branding')
        asset = resource_root / 'branding/dogma-opening-v1/ru-music.wav'
        asset.write_bytes(asset.read_bytes()[:-2])
        with self.assertRaises(AppError):
            load_opening_pack(resource_root)
        selected = select_opening(self.generation())
        metadata = self.store.audio_path(selected['audio_hash']).with_suffix('.json')
        saved = json.loads(metadata.read_text('utf-8'))
        saved['duration'] = 7
        metadata.write_text(json.dumps(saved), encoding='utf-8')
        with self.assertRaises(AppError):
            verify_opening_audio(metadata.with_suffix('.wav'), selected)

    def test_legacy_revision_attaches_once_to_current_output_without_new_pin(self):
        generation = self.generation()
        config = {k: v for k, v in generation['config'].items() if k != 'opening'}
        self.store.execute('UPDATE generations SET config=?,analysis=? WHERE id=?',
                           (json.dumps(config), json.dumps({'complete': True}), generation['id']))
        self.assertIsNone(select_opening(self.store.generation(generation['id'])))
        legacy = self.store.generation(generation['id'])
        revised = self.store.revise(generation['id'], [{'id': generation['segments'][0]['id'], 'spoken': 'New pronunciation.'}])
        self.assertIsNotNone(select_opening(revised))
        self.assertEqual(generation['id'],revised['id'])
        self.assertEqual(select_opening(revised),select_opening(self.store.generation(generation['id'])))
        self.assertIsNone(select_opening(legacy))
        again = self.store.revise(revised['id'], [{'id': revised['segments'][0]['id'], 'spoken': 'Another pronunciation.'}])
        self.assertEqual(revised['config']['opening'], again['config']['opening'])


if __name__ == '__main__':
    unittest.main()
