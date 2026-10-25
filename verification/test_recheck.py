from __future__ import annotations

from contextlib import closing
import copy
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, NEUTRAL_CONTEXT as NEUTRAL, AliasEngine, SyntheticEngine, complete_audio, epub_fixture, pack_fixture, write_wave
from rimsky.analysis import validate_analysis
from rimsky.books import extract_blocks, xml
from rimsky.export import build_export, captions
from rimsky.integrity import accepted_audio, validate_coverage, validate_cues
from rimsky.jobs import Jobs
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, file_digest, write_json
from server import Application


class Recheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'book.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def generation(self):
        return self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_name': 'Verification'})

    def complete(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        return self.store.generation(generation['id'])

    def export(self, generation, format='srt', chapters=None):
        return build_export(self.store, generation['id'], format, chapters, self.root)

    def discard_fixture_fallback(self, generation):
        # These corruption/initial-coverage fixtures have no previous playable
        # output. Real pending replacements deliberately retain that output.
        self.store.execute('DELETE FROM audiobook_replacements WHERE generation_id=?',(generation['id'],))

    def pack(self):
        return pack_fixture(self.root/'pack')

    def test_empty_generation_selection_rejected(self):
        with self.assertRaises(AppError):
            self.store.create_generation(self.book['id'], {}, chapters=[])
        self.assertEqual([], self.store.generation_list(self.book['id']))

    def test_non_list_chapter_selection_rejected(self):
        with self.assertRaises(AppError):
            self.store.create_generation(self.book['id'], {}, chapters='c0001')

    def test_empty_export_selection_rejected(self):
        generation = self.complete()
        with self.assertRaises(AppError):
            self.export(generation, chapters=[])

    def test_missing_source_passage_blocks_export(self):
        generation = self.complete()
        self.store.execute('DELETE FROM segments WHERE generation_id=? AND id=?',
                           (generation['id'], generation['segments'][1]['id']))
        self.discard_fixture_fallback(generation)
        with self.assertRaisesRegex(AppError, 'every expected source'):
            self.export(generation)
        self.assertEqual([], list((self.store.root/'exports').iterdir()))

    def test_reordered_passages_block_export(self):
        generation = self.complete()
        self.store.execute('UPDATE segments SET ordinal=-1 WHERE generation_id=? AND id=?',
                           (generation['id'], generation['segments'][2]['id']))
        with self.assertRaises(AppError):
            self.export(generation)

    def test_changed_source_text_blocks_export(self):
        generation = self.complete()
        self.store.execute("UPDATE segments SET text='changed' WHERE generation_id=? AND id=?",
                           (generation['id'], generation['segments'][0]['id']))
        self.discard_fixture_fallback(generation)
        with self.assertRaises(AppError):
            self.export(generation)

    def test_completed_chapter_exports_without_other_chapters(self):
        generation = self.complete()
        self.store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND chapter_id=?",
                           (generation['id'], self.book['chapters'][1]['id']))
        self.discard_fixture_fallback(generation)
        result = self.export(generation, chapters=[self.book['chapters'][0]['id']])
        self.assertTrue((self.store.root/'exports'/result['file']).is_file())
        with self.assertRaises(AppError):
            self.export(generation)

    def test_source_offsets_must_cover_exactly(self):
        generation = self.generation()
        generation['segments'][0]['start'] = 1
        with self.assertRaises(AppError):
            validate_coverage(self.book, generation['config']['chapters'], generation['segments'])

    def test_missing_audio_blocks_subtitle_export(self):
        generation = self.complete()
        (self.store.root/'audio'/f'{generation["segments"][0]["audio_hash"]}.wav').unlink()
        with self.assertRaises(AppError):
            self.export(generation)

    def test_resealed_invalid_wav_is_not_accepted(self):
        generation = self.complete()
        path = self.store.root/'audio'/f'{generation["segments"][0]["audio_hash"]}.wav'
        path.write_bytes(b'not audio')
        metadata = json.loads(path.with_suffix('.json').read_text())
        metadata['sha256'] = file_digest(path)
        write_json(path.with_suffix('.json'), metadata)
        with self.assertRaises(AppError):
            self.export(generation)

    def test_truncated_audio_is_not_accepted(self):
        generation = self.complete()
        path = self.store.root/'audio'/f'{generation["segments"][0]["audio_hash"]}.wav'
        path.write_bytes(path.read_bytes()[:-20])
        with self.assertRaisesRegex(AppError, 'truncated'):
            accepted_audio(path)

    def test_false_metadata_duration_is_not_accepted(self):
        generation = self.complete()
        path = self.store.root/'audio'/f'{generation["segments"][0]["audio_hash"]}.json'
        metadata = json.loads(path.read_text())
        metadata['duration'] = 20
        write_json(path, metadata)
        with self.assertRaises(AppError):
            self.export(generation)

    def test_export_uses_measured_audio_not_database_duration(self):
        generation = self.complete()
        self.store.execute('UPDATE segments SET duration=50 WHERE generation_id=?', (generation['id'],))
        self.discard_fixture_fallback(generation)
        result = self.export(generation)
        text = (self.store.root/'exports'/result['file']).read_text('utf-8')
        self.assertIn('00:00:08,600', text)
        self.assertNotIn('00:00:50,', text)

    def test_caption_text_is_literal_not_markup(self):
        text = captions([{'duration': 1, 'spoken': 'x < y & z --> q', 'cues': []}])
        self.assertIn('x &lt; y &amp; z --&gt; q', text)

    def test_mixed_sample_rates_are_not_silently_concatenated(self):
        generation = self.complete()
        path = self.store.root/'audio'/f'{generation["segments"][0]["audio_hash"]}.wav'
        metadata = write_wave(path, rate=16000)
        metadata['sha256'] = file_digest(path)
        write_json(path.with_suffix('.json'), metadata)
        with self.assertRaisesRegex(AppError, 'sample rates'):
            self.export(generation, 'm4b')

    def test_failed_chapter_zip_does_not_leave_partial_export(self):
        generation = self.complete()
        def fail(command, **kwargs):
            Path(command[-1]).write_bytes(b'partial')
            return subprocess.CompletedProcess(command, 1, b'', b'deliberate failure')
        with patch('rimsky.export.resolve_binary', return_value='ffmpeg'), patch('rimsky.export.subprocess.run', side_effect=fail):
            with self.assertRaises(AppError):
                self.export(generation, 'chapters')
        self.assertEqual([], list((self.store.root/'exports').iterdir()))
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))

    def test_duplicate_correction_is_rejected(self):
        generation = self.complete()
        change = {'id': generation['segments'][0]['id'], 'spoken': 'Changed'}
        with self.assertRaises(AppError):
            self.store.revise(generation['id'], [change, change])
        self.assertEqual(1, len(self.store.generation_list(self.book['id'])))

    def test_bad_correction_shape_is_rejected(self):
        generation = self.complete()
        with self.assertRaises(AppError):
            self.store.revise(generation['id'], ['bad'])

    def test_position_cannot_reference_another_chapter_source(self):
        with self.assertRaises(AppError):
            self.store.save_position(self.book['id'], {'chapter_id': self.book['chapters'][0]['id'],
                'source_id': self.book['chapters'][1]['blocks'][0]['id'], 'offset': 0})

    def test_position_requires_matching_generation_segment(self):
        generation = self.complete()
        segment = generation['segments'][0]
        with self.assertRaises(AppError):
            self.store.save_position(self.book['id'], {'generation_id': generation['id'],
                'chapter_id': segment['chapter_id'], 'source_id': segment['source_id'],
                'segment_id': 'missing', 'offset': 0})

    def test_deleting_generation_retains_valid_reading_location(self):
        generation = self.complete()
        segment = generation['segments'][0]
        self.store.save_position(self.book['id'], {'generation_id': generation['id'],
            'chapter_id': segment['chapter_id'], 'source_id': segment['source_id'],
            'segment_id': segment['id'], 'offset': .04})
        self.store.delete_generation(generation['id'])
        position = self.store.book(self.book['id'])['position']
        self.assertEqual(segment['source_id'], position['source_id'])
        self.assertNotIn('generation_id', position)
        self.assertEqual(0, position['offset'])

    def test_newer_database_is_not_downgraded(self):
        folder = self.root/'future'
        folder.mkdir()
        with closing(sqlite3.connect(folder/'library.sqlite3')) as connection:
            connection.execute('PRAGMA user_version=6')
        with self.assertRaises(AppError):
            Store(folder)
        with closing(sqlite3.connect(folder/'library.sqlite3')) as connection:
            self.assertEqual(6, connection.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual([], connection.execute('SELECT name FROM sqlite_master').fetchall())

    def test_inline_breaks_keep_word_boundaries(self):
        blocks = extract_blocks(xml(b'<body><p>one<br/>two</p></body>'), 'c1', lambda _: None)
        self.assertEqual('one two', blocks[0]['text'])
        self.assertIn('<br/>', blocks[0]['html'])

    def test_table_cells_keep_word_boundaries(self):
        blocks = extract_blocks(xml(b'<table><tr><td>Mass</td><td>2 kg</td></tr></table>'), 'c1', lambda _: None)
        self.assertEqual('Mass 2 kg', blocks[0]['text'])

    def test_fb2_emphasis_is_preserved(self):
        blocks = extract_blocks(xml(b'<p>A <emphasis>word</emphasis>.</p>'), 'c1', lambda _: None)
        self.assertIn('<em>word</em>', blocks[0]['html'])

    def test_failed_import_leaves_no_partial_library_folder(self):
        before = set((self.store.root/'books').iterdir())
        with self.assertRaises(AppError):
            self.store.import_data(b'<FictionBook><body><section>', 'bad.fb2')
        self.assertEqual(before, set((self.store.root/'books').iterdir()))

    def test_alignment_missing_words_is_rejected(self):
        with self.assertRaises(AppError):
            validate_cues([{'text': 'Hello', 'start': 0, 'end': .5}], 'Hello world', 1)

    def test_alignment_accepts_russian_words_without_punctuation(self):
        cues = [{'text': 'Да', 'start': 0, 'end': .4}, {'text': 'читай', 'start': .5, 'end': .9}]
        self.assertEqual(cues, validate_cues(cues, '— Да, читай!', 1))

    def test_alignment_nan_and_overlap_rejected(self):
        for cues in ([{'text': 'word', 'start': float('nan'), 'end': 1}],
                     [{'text': 'a', 'start': 0, 'end': .7}, {'text': 'b', 'start': .5, 'end': 1}]):
            with self.subTest(cues=cues), self.assertRaises(AppError):
                validate_cues(cues, 'a b', 1)

    def test_bad_alignment_uses_passage_captions_on_export(self):
        generation = self.complete()
        self.store.execute('UPDATE segments SET cues=? WHERE generation_id=?',
            (json.dumps([{'text': 'invented', 'start': 0, 'end': .05}]), generation['id']))
        self.discard_fixture_fallback(generation)
        result = self.export(generation)
        content = (self.store.root/'exports'/result['file']).read_text('utf-8')
        self.assertNotIn('invented', content)
        self.assertIn('Маша', content)

    def test_alignment_worker_reused_for_all_passages(self):
        generation = self.complete()
        instances = []
        class Aligner:
            def __init__(self, binaries, component):
                self.calls = 0
                self.closed = False
                instances.append(self)
            def call(self, method, payload, stop, timeout):
                self.calls += 1
                return [{'text': payload['text'], 'start': 0, 'end': .1}]
            def close(self):
                self.closed = True
        jobs = Jobs(self.store, self.root)
        with patch('rimsky.jobs.Engine', Aligner), patch('rimsky.jobs.resolve_binary', return_value='aligner'):
            jobs._align(generation['id'], {'_root': str(self.root), 'aligner': {'path': 'model'}, 'files': {}, '_stats': {}}, 'Russian', {'device': 'cpu'})
        self.assertEqual(1, len(instances))
        self.assertEqual(len(generation['segments']), instances[0].calls)
        self.assertTrue(instances[0].closed)

    def test_nested_pack_manifest_is_hashed(self):
        pack = self.pack()
        nested = Path(pack['_root'])/'tts'/'pack.json'
        nested.write_text('{}')
        manifest = Path(pack['_root'])/'pack.json'
        data = json.loads(manifest.read_text())
        data['files']['tts/pack.json'] = file_digest(nested)
        write_json(manifest, data)
        load_pack(pack['_root'], verify=True)
        nested.write_text('{"modified": true}')
        with self.assertRaises(AppError):
            load_pack(pack['_root'], verify=True)

    def test_non_object_pack_rejected(self):
        pack = self.pack()
        (Path(pack['_root'])/'pack.json').write_text('[]')
        with self.assertRaises(AppError):
            load_pack(pack['_root'])

    def test_non_hex_checksum_rejected(self):
        pack = self.pack()
        manifest = Path(pack['_root'])/'pack.json'
        value = json.loads(manifest.read_text())
        value['files']['model.gguf'] = 'z' * 64
        write_json(manifest, value)
        with self.assertRaises(AppError):
            load_pack(pack['_root'])

    def test_pack_requires_one_narrator(self):
        pack = self.pack()
        manifest = Path(pack['_root'])/'pack.json'
        value = json.loads(manifest.read_text())
        value['voices'][1]['role'] = 'narrator'
        write_json(manifest, value)
        with self.assertRaises(AppError):
            load_pack(pack['_root'])

    def test_non_boolean_instruction_control_rejected(self):
        pack = self.pack()
        manifest = Path(pack['_root'])/'pack.json'
        value = json.loads(manifest.read_text())
        value['speech']['instruction_control'] = 'false'
        write_json(manifest, value)
        with self.assertRaises(AppError):
            load_pack(pack['_root'])

    def test_invalid_evidence_shape_is_a_validation_failure(self):
        source = {'id': 's', 'source_id': 'p', 'chapter_id': 'c', 'ordinal': 0, 'start': 0, 'text': 'Hello'}
        result = {'characters': [], 'passages': [{'id': 's', 'parts': [{'first': 0, 'last': 0,
            'speaker': 'narrator', 'delivery': 'neutral', 'evidence': [{}], 'context': NEUTRAL}]}]}
        with self.assertRaises(AppError):
            validate_analysis(result, [source], [])

    def test_expression_requires_source_evidence(self):
        source = {'id': 's', 'source_id': 'p', 'chapter_id': 'c', 'ordinal': 0, 'start': 0, 'text': 'Hello'}
        result = {'characters': [], 'passages': [{'id': 's', 'parts': [{'first': 0, 'last': 0,
            'speaker': 'narrator', 'delivery': 'angry', 'evidence': [], 'context': NEUTRAL}]}]}
        with self.assertRaises(AppError):
            validate_analysis(result, [source], [])

    def test_alias_merge_and_cast_commit_are_atomic(self):
        pack = self.pack()
        generation = self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint']})
        self.store.execute('''CREATE TRIGGER stop_cast_commit BEFORE UPDATE OF analysis ON generations
            WHEN json_extract(NEW.analysis,'$.complete')=1 BEGIN SELECT RAISE(ABORT,'verification interruption'); END''')
        engine = AliasEngine()
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        current = self.store.generation(generation['id'])
        self.assertEqual('failed', current['status'])
        self.assertEqual(1, len(engine.reconciled))
        self.assertFalse(current['analysis']['complete'])
        speakers = {s['speaker'] for s in current['segments']} - {'narrator'}
        self.assertEqual(2, len(speakers))

    def test_invalid_worker_audio_is_not_published(self):
        pack = self.pack()
        generation = self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint']})
        class BadEngine(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    Path(payload['output']).write_bytes(b'bad audio')
                    return {'duration': 1, 'samples': 24000, 'sample_rate': 24000}
                return super().call(method, payload, stop)
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: BadEngine())
        jobs.start(generation['id'])
        jobs.thread.join(10)
        current = self.store.generation(generation['id'])
        self.assertEqual('failed', current['status'])
        self.assertFalse(any(s['status'] == 'ready' for s in current['segments']))

    def test_busy_generation_does_not_create_orphan_history(self):
        app = Application(self.root/'other', self.root)
        try:
            book = app.store.import_data(FB2, 'book.fb2')
            app.jobs.active = 'already-running'
            with self.assertRaises(AppError):
                app.dispatch('generate', {'book_id': book['id']})
            self.assertEqual([], app.store.generation_list(book['id']))
        finally:
            app.jobs.active = None
            app.close()

    def test_release_build_preflight_does_not_require_unbuilt_acceptance(self):
        import importlib.util
        source = Path(__file__).parents[1]/'scripts'/'check_release.py'
        spec = importlib.util.spec_from_file_location('release_check', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build = module.release_issues(self.root, require_acceptance=False)
        release = module.release_issues(self.root)
        self.assertFalse(any('release-acceptance' in issue for issue in build))
        self.assertTrue(any('release-acceptance' in issue for issue in release))
        self.assertTrue(build)
