"""Current output replacement and interrupted-publication recovery; no TTS."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]
import current_higgs_output as c
from rimsky.util import digest, file_digest


def wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        out.writeframes(b''.join(int(x).to_bytes(2, 'little', signed=True) for x in samples))


class MemoryStore:
    def __init__(self, generation, book):
        self.value, self.source = copy.deepcopy(generation), copy.deepcopy(book)
        self.published = []
    def generation(self, identifier):
        assert identifier == self.value['id']
        return copy.deepcopy(self.value)
    def current_generation(self, book_id):
        assert book_id == self.source['id']
        return copy.deepcopy(self.value)
    def book(self, identifier):
        assert identifier == self.source['id']
        return copy.deepcopy(self.source)
    def publish_current_parts(self, identifier, parts, *, expected_revision, metadata=None):
        if expected_revision != self.value['revision']:
            raise ValueError('Revision conflict')
        incoming = {x['id']: copy.deepcopy(x) for x in parts}
        self.value['segments'] = [incoming.get(x['id'], x) for x in self.value['segments']]
        self.value['revision'] += 1
        self.value.update(copy.deepcopy(metadata or {}))
        self.published.append(copy.deepcopy(parts))
        return self.generation(identifier)
    def close(self):
        pass


class CurrentOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'library'
        self.book_id = 'a' * 32
        self.parts, segments = {}, []
        for index, word in enumerate(('Первый.', 'Второй.')):
            uid = 'u' + str(index)
            synthesis = {'backend': 'higgs_audio_cpp', 'model_sha256': c.MODEL,
                         'text': word, 'prompt': word, 'sampling': {'seed': 1}}
            unit = {'id': uid, 'chapter_id': 'c', 'source_id': uid, 'start': 0,
                    'end': len(word), 'text': word, 'synthesis': synthesis}
            path = self.root / (uid + '.wav')
            wav(path, [0, 0, 10 + index, 20 + index, 30 + index, 0, 0, 0])
            key = file_digest(path)
            destination = self.data / 'audio' / (key + '.wav')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
            self.parts[uid] = {'unit': unit, 'audio_hash': key, 'padding_before': 2,
                               'padding_after': 3, 'speech_frames': 3, 'native': {'old': True}}
            segments.append({'generation_id': 'g', 'id': uid, 'ordinal': index, 'chapter_id': 'c',
                             'source_id': uid, 'start': 0, 'end': len(word), 'text': word, 'spoken': word,
                             'speaker': 'narrator', 'delivery': 'neutral', 'status': 'ready', 'audio_hash': key,
                             'duration': 8 / 24000, 'cues': [], 'warning': '',
                             'performance': {'synthesis_snapshot': synthesis}})
        self.book = {'id': self.book_id, 'source_hash': 'source'}
        self.store = MemoryStore({'id': 'g', 'book_id': self.book_id, 'revision': 4,
                                  'segments': segments, 'status': 'ready', 'analysis': {}}, self.book)
        for segment in segments:
            self.parts[segment['id']]['playback_sha256'] = c._playback_binding(segment)
        self.view = c._seal({'policy': c.POLICY, 'book_id': self.book_id, 'generation_id': 'g',
                            'source_hash': 'source', 'parts': self.parts, 'store_revision': 4,
                            'chapters': ['c'], 'native_manifest': {}})
        c.write(c.current_manifest(self.data, self.book_id), self.view)
        self.new_unit = copy.deepcopy(self.parts['u0']['unit'])
        self.new_unit['synthesis'].update(text='Пе́рвый.', prompt='Пе́рвый.')
        self.raw = self.root / 'job/assets/raw.wav'
        wav(self.raw, [101, 102, 103, 104])
        self.receipt = {'status': 'rendered', 'session': 'physical', 'request_sha256': 'request',
                        'audio_sha256': file_digest(self.raw), 'synthesis_sha256': digest(self.new_unit['synthesis'])}
        self.compact = {'request': {'id': 'u0', 'text': 'Пе́рвый.'}, 'terminal': {'returncode': 0},
                        'worker': {'pid': 123, 'create_time': 1}, 'model_sha256': c.MODEL,
                        'speech_pcm_sha256': hashlib.sha256(b''.join(x.to_bytes(2, 'little') for x in (101, 102, 103, 104))).hexdigest()}
        self.physical = patch.object(c, '_physical', return_value=self.compact)
        self.physical.start()
        self.addCleanup(self.physical.stop)
    def publish(self):
        return c.publish_parts(self.store, self.data, self.view,
                               {'u0': (self.new_unit, self.receipt, self.raw)}, expected_revision=4)
    def test_only_finished_part_changes_without_copying_output_or_unchanged_audio(self):
        old = self.store.generation('g')['segments'][1]
        files = set((self.data / 'audio').glob('*.wav'))
        result, view = self.publish()
        self.assertEqual('g', result['id'])
        self.assertEqual(old, result['segments'][1])
        self.assertEqual(1, len(set((self.data / 'audio').glob('*.wav')) - files))
        self.assertEqual(self.compact, view['parts']['u0']['native'])
        clip = self.data / 'audio' / (result['segments'][0]['audio_hash'] + '.wav')
        with wave.open(str(clip)) as stream:
            self.assertEqual(9, stream.getnframes())
            body = stream.readframes(9)
        self.assertEqual(b'\0' * 4, body[:4])
        self.assertEqual(b'\0' * 6, body[-6:])
        self.assertEqual('complete', c.read(c._intent_path(self.data, self.book_id))['status'])
    def test_same_id_different_source_and_internal_layout_are_rejected(self):
        for key, value in (('source_id', 'other'), ('start', 1), ('text', 'Changed.')):
            with self.subTest(key=key):
                unit = copy.deepcopy(self.new_unit)
                unit[key] = value
                with self.assertRaises(ValueError):
                    c.publish_parts(self.store, self.data, self.view,
                                    {'u0': (unit, self.receipt, self.raw)}, expected_revision=4)
        self.new_unit['render_parts'] = [{'pause': 1}]
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.store.published)
    def test_db_commit_then_manifest_write_failure_recovers_without_new_audio_or_db_write(self):
        with patch.object(c, '_save_view', side_effect=OSError('interrupted disk write')):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(5, self.store.value['revision'])
        files = set((self.data / 'audio').glob('*'))
        recovered = c.load(self.store, self.data, self.book_id)
        self.assertEqual(5, recovered['store_revision'])
        self.assertEqual(self.store.value['segments'][0]['audio_hash'], recovered['parts']['u0']['audio_hash'])
        self.assertEqual(1, len(self.store.published))
        self.assertEqual(files, set((self.data / 'audio').glob('*')))
    def test_failed_db_commit_keeps_old_playable_output(self):
        before = self.store.generation('g')
        with patch.object(self.store, 'publish_current_parts', side_effect=OSError('db interrupted')):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(before, self.store.generation('g'))
        self.assertEqual(self.view, c.load(self.store, self.data, self.book_id))
    def test_pending_intent_cannot_hide_an_unrelated_part_change(self):
        with patch.object(c, '_save_view', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.publish()
        self.store.value['segments'][1]['audio_hash'] = 'b' * 64
        with self.assertRaises(ValueError):
            c.load(self.store, self.data, self.book_id)
    def test_stale_revision_without_intent_is_not_silently_loaded(self):
        for part in self.view['parts'].values():part.pop('playback_sha256')
        c.write(c.current_manifest(self.data, self.book_id), c._seal(self.view))
        self.store.value['revision'] = 5
        with self.assertRaises(ValueError):
            c.load(self.store, self.data, self.book_id)
    def test_conflict_never_changes_current_store_or_existing_manifest(self):
        self.store.value['revision'] = 6
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.view, c.read(c.current_manifest(self.data, self.book_id)))
        self.assertFalse(self.store.published)
    def test_raw_checksum_mismatch_never_publishes(self):
        self.receipt['audio_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.store.published)
    def test_native_session_wav_resolves_the_job_root(self):
        self.physical.stop()
        target = self.root / 'job/native/physical/u0.wav'
        target.parent.mkdir(parents=True)
        target.write_bytes(self.raw.read_bytes())
        with patch.object(c, '_physical', return_value=self.compact) as physical:
            c.replacement_segment(self.data, self.store.value['segments'][0], self.parts['u0'],
                                  self.new_unit, self.receipt, target)
        self.assertEqual(self.root / 'job', physical.call_args.args[0])
    def test_same_id_store_source_mutation_is_never_recovered_as_current(self):
        self.store.value['segments'][0]['start'] = 1
        with self.assertRaises(ValueError):
            c.load(self.store, self.data, self.book_id)
    def adoption_fixture(self):
        folder = self.root / 'obsolete-generation'
        c.write(folder / 'ready.json', {'chapters': ['c'], 'native_manifest': {}})
        c.write(folder / 'effective-listener-units.json', {'units': [p['unit'] for p in self.parts.values()]})
        self.store.source['chapters'] = [{'id': 'c', 'blocks': [{'id': p['unit']['source_id'], 'text': p['unit']['text']}
                                                            for p in self.parts.values()]}]
        for uid, part in self.parts.items():
            c.write(folder / 'units' / (uid + '.json'), {'status': 'rendered',
                    'synthesis_sha256': digest(part['unit']['synthesis'])})
        for segment in self.store.value['segments']:
            segment['performance']['external_preview'] = {'published_start': 2 / 24000,
                'published_end': 5 / 24000, 'clip_frame_start': 0, 'clip_frame_end': 8}
        def physical(base, ready, unit, row):
            index = int(unit['id'][-1])
            body = b''.join(x.to_bytes(2, 'little') for x in (10 + index, 20 + index, 30 + index))
            return {'speech_pcm_sha256': hashlib.sha256(body).hexdigest(),
                    'request': {'voice_ref': 'protected-reference.wav'},
                    'worker': {'command': [str(folder / 'native/old-request.json')]}}
        return folder, physical
    def test_adopt_dry_run_changes_no_current_pointer_audio_or_manifest(self):
        folder, physical = self.adoption_fixture()
        before = {str(p): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        with patch.object(c, 'Store', return_value=self.store), patch.object(c, '_physical', side_effect=physical):
            result = c.adopt(self.data, folder, 'g')
        self.assertEqual('validated', result['status'])
        self.assertEqual(0, result['audio_copies'])
        self.assertFalse(self.store.published)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})
    def test_adoption_db_then_disk_interruption_recovers_without_legacy_ancestry(self):
        folder, physical = self.adoption_fixture()
        with patch.object(c, 'Store', return_value=self.store), patch.object(c, '_physical', side_effect=physical), \
                patch.object(c, '_save_view', side_effect=OSError('after database commit')):
            with self.assertRaises(OSError):
                c.adopt(self.data, folder, 'g', execute=True)
        # The old input tree is now unavailable. The saved intent carries every
        # selected source/input/receipt; worker argv strings are archival only.
        import shutil
        shutil.rmtree(folder)
        with patch.object(c, 'document', side_effect=AssertionError('old ancestry read')):
            recovered = c.load(self.store, self.data, self.book_id)
        self.assertEqual(5, recovered['store_revision'])
        self.assertEqual(2, len(recovered['parts']))
        self.assertNotIn('source', recovered)
        self.assertEqual(1, len(self.store.published))

    def test_adoption_db_failure_cannot_be_mistaken_for_a_committed_pointer(self):
        folder, physical = self.adoption_fixture()
        # Source clips are unchanged by adoption. Matching clips alone cannot
        # prove that the canonical current selection/metadata committed.
        c.current_manifest(self.data, self.book_id).unlink()
        with patch.object(c, 'Store', return_value=self.store), patch.object(c, '_physical', side_effect=physical), \
                patch.object(self.store, 'publish_current_parts', side_effect=OSError('before commit')):
            with self.assertRaises(OSError):
                c.adopt(self.data, folder, 'g', execute=True)
        with self.assertRaisesRegex(ValueError, 'saved publication intent'):
            c.load(self.store, self.data, self.book_id)
        self.assertFalse(c.current_manifest(self.data, self.book_id).exists())

    def test_export_or_rename_revision_reconciles_when_full_playback_binding_is_unchanged(self):
        self.store.value.update(name='Readable book title', revision=5)
        loaded = c.load(self.store, self.data, self.book_id)
        self.assertEqual(5, loaded['store_revision'])
        self.assertEqual(self.view['parts'], loaded['parts'])
        self.assertEqual('complete', c.read(c._intent_path(self.data, self.book_id))['status'])

    def test_metadata_reconciliation_rejects_spoken_duration_cues_or_performance_changes(self):
        for key, val in [('spoken', 'Different'), ('duration', 1), ('cues', [1]),
                         ('performance', {'different': True})]:
            with self.subTest(key=key):
                before = copy.deepcopy(self.store.value)
                self.store.value['segments'][0][key] = val
                self.store.value['revision'] = 5
                with self.assertRaisesRegex(ValueError, 'stale Store revision'):
                    c.load(self.store, self.data, self.book_id)
                self.store.value = before

    def test_rename_current_uses_intent_without_creating_a_new_output_or_audio(self):
        files = set(self.data.rglob('*.wav'))
        generation, view = c.rename_current(self.store, self.data, self.book_id, 'Readable title')
        self.assertEqual('g', generation['id'])
        self.assertEqual('Readable title', generation['name'])
        self.assertEqual(files, set(self.data.rglob('*.wav')))
        self.assertEqual(generation['revision'], view['store_revision'])

    def test_other_writer_part_change_is_rejected_before_stale_manifest_can_replace_it(self):
        self.store.value['segments'][1]['audio_hash'] = 'b' * 64
        self.store.value['revision'] = 5
        files = set(self.data.rglob('*.wav'))
        with self.assertRaisesRegex(ValueError, 'playback part changed'):
            c.publish_parts(self.store, self.data, self.view,
                            {'u0': (self.new_unit, self.receipt, self.raw)}, expected_revision=5)
        self.assertEqual(files, set(self.data.rglob('*.wav')))
        self.assertEqual([], self.store.published)


if __name__ == '__main__':
    unittest.main()
