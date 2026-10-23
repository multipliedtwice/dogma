from __future__ import annotations

from contextlib import ExitStack
import ctypes
import errno
import io
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import FB2, complete_audio, write_wave
from rimsky import audio_source, export as export_module, integrity
from rimsky.audio_source import open_audio_pair, open_shared_read
from rimsky.export import build_export
from rimsky.util import AppError, atomic_replace, file_digest, write_json
from server import Application
import server


class FinalRepairs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root)
        self.store = self.app.store
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.chapter = self.book['chapters'][0]
        self.position = {'chapter_id': self.chapter['id'], 'source_id': self.chapter['blocks'][1]['id'], 'offset': 2400}

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def completed(self):
        generation = self.store.create_generation(self.book['id'], {
            'pack_path': 'unused', 'pack_fingerprint': 'fixture', 'pack_name': 'Verification', 'device': 'cpu',
        })
        complete_audio(self.store, generation)
        return self.store.generation(generation['id'])

    def export(self, generation):
        return build_export(self.store, generation['id'], 'vtt', None, self.root)

    def unlocked(self, operation=lambda: None):
        """Probe from another thread: an RLock re-entry would hide the defect."""
        result = []
        def probe():
            if not self.store.lock.acquire(timeout=1):
                result.append(False)
                return
            self.store.lock.release()
            try:
                operation()
                result.append(True)
            except BaseException as error:
                result.append(error)
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'An unrelated library operation stalled behind export I/O')
        self.assertEqual([True], result, 'Heavy asset work held store.lock or a concurrent operation failed')

    def clean_failed_export(self):
        self.assertEqual([], list((self.store.root / 'work').iterdir()))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))

    def test_source_relative_reading_position_round_trips_alongside_legacy_pixels(self):
        for fraction in (0, .517, 1):
            value = {**self.position, 'source_fraction': fraction}
            self.assertEqual(value, self.store.save_reading_position(self.book['id'], value))
            self.assertEqual(value, self.store.book(self.book['id'])['reading_position'])

    def test_legacy_reading_position_remains_unchanged(self):
        self.store.save_reading_position(self.book['id'], self.position)
        self.assertEqual(self.position, self.store.book(self.book['id'])['reading_position'])
        self.assertNotIn('source_fraction', self.store.book(self.book['id'])['reading_position'])

    def test_invalid_source_relative_values_cannot_overwrite_a_valid_position(self):
        saved = {**self.position, 'source_fraction': .5}
        self.store.save_reading_position(self.book['id'], saved)
        for fraction in (True, False, None, '0.5', [], -1, 1.01, float('nan'), float('inf')):
            with self.subTest(fraction=fraction), self.assertRaises(AppError):
                self.store.save_reading_position(self.book['id'], {**self.position, 'source_fraction': fraction})
            self.assertEqual(saved, self.store.book(self.book['id'])['reading_position'])
        for source in ('', None, self.book['chapters'][1]['blocks'][0]['id']):
            with self.subTest(source=source), self.assertRaises(AppError):
                self.store.save_reading_position(self.book['id'], {**saved, 'source_id': source})

    def test_cross_device_copy_allows_playback_polling_and_reading_saves(self):
        generation = self.completed()
        key = generation['segments'][0]['audio_hash']
        copied, validated = [], []
        copy = export_module.shutil.copyfileobj
        read = integrity.read_audio
        def other_operations():
            self.store.generation_poll(generation['id'])
            self.store.save_reading_position(self.book['id'], {**self.position, 'source_fraction': .5})
            with self.app.open_audio(key) as source:
                self.assertEqual(b'RIFF', source.read(4))
        def copying(source, target, length):
            self.unlocked(other_operations)
            copied.append(target.name)
            return copy(source, target, length)
        def validating(path, **kwargs):
            self.unlocked()
            validated.append(path)
            return read(path, **kwargs)
        with patch.object(export_module.os, 'link', side_effect=OSError(errno.EXDEV, 'Cross-device link')), \
                patch.object(export_module.shutil, 'copyfileobj', side_effect=copying), \
                patch.object(integrity, 'read_audio', side_effect=validating):
            result = self.export(generation)
        self.assertTrue(copied)
        self.assertTrue(validated)
        self.assertTrue((self.store.root / 'exports' / result['file']).is_file())
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_full_file_hashing_and_linking_are_outside_store_lock(self):
        generation = self.completed()
        link, digest = os.link, integrity.hashlib.file_digest
        def linking(source, destination):
            self.unlocked()
            return link(source, destination)
        def hashing(source, algorithm):
            self.unlocked()
            return digest(source, algorithm)
        with patch.object(export_module.os, 'link', side_effect=linking), \
                patch.object(integrity.hashlib, 'file_digest', side_effect=hashing):
            self.export(generation)

    def test_fallback_copy_survives_deletion_after_handle_capture(self):
        generation = self.completed()
        def missing(source, destination):
            self.unlocked()
            source.unlink()
            source.with_suffix('.json').unlink()
            raise FileNotFoundError(source)
        with patch.object(export_module.os, 'link', side_effect=missing):
            result = self.export(generation)
        self.assertIn('WEBVTT', (self.store.root / 'exports' / result['file']).read_text('utf-8'))
        self.assertEqual([], list((self.store.root / 'audio').iterdir()))

    def replace_pair(self, source):
        replacement = source.with_name('replacement.wav')
        meta = write_wave(replacement, seconds=.2)
        meta['sha256'] = file_digest(replacement)
        write_json(replacement.with_suffix('.json'), meta)
        atomic_replace(replacement, source)
        atomic_replace(replacement.with_suffix('.json'), source.with_suffix('.json'))

    def test_fallback_copy_keeps_old_audio_and_metadata_after_atomic_repair(self):
        generation = self.completed()
        captured = []
        freeze = export_module.freeze_audio
        def repaired(source, destination):
            self.replace_pair(source)
            raise OSError(errno.EPERM, 'Hardlink refused')
        def frozen(snapshot):
            value = freeze(snapshot)
            captured.append(value['duration'])
            return value
        with patch.object(export_module.os, 'link', side_effect=repaired), \
                patch.object(export_module, 'freeze_audio', side_effect=frozen):
            self.export(generation)
        self.assertTrue(captured)
        self.assertEqual({.1}, set(captured))

    def test_successful_link_to_replaced_path_is_not_accepted_as_the_original(self):
        generation = self.completed()
        link, freeze = os.link, export_module.freeze_audio
        captured = []
        def repaired(source, destination):
            self.replace_pair(source)
            return link(source, destination)
        def frozen(snapshot):
            value = freeze(snapshot)
            captured.append(value['duration'])
            return value
        with patch.object(export_module.os, 'link', side_effect=repaired), \
                patch.object(export_module, 'freeze_audio', side_effect=frozen):
            self.export(generation)
        self.assertEqual({.1}, set(captured))

    def test_filesystem_without_file_identity_uses_the_retained_copy(self):
        generation = self.completed()
        copy = export_module.shutil.copyfileobj
        source = self.store.root / 'audio' / f'{generation["segments"][0]["audio_hash"]}.wav'
        destination = self.root / 'snapshot.wav'
        with open_audio_pair(source) as (audio, metadata), \
                patch.object(export_module.os, 'fstat', return_value=types.SimpleNamespace(st_ino=0)), \
                patch.object(export_module.shutil, 'copyfileobj', wraps=copy) as copying:
            export_module.capture_audio(source, destination, audio, metadata)
        self.assertEqual(.1, integrity.accepted_audio(destination)['duration'])
        copying.assert_called_once()

    def test_later_uncaptured_repair_refuses_mixed_old_rows_and_new_audio(self):
        generation = self.completed()
        first, second = generation['segments'][:2]
        self.assertNotEqual(first['audio_hash'], second['audio_hash'])
        capture = export_module.capture_audio
        def change_later(source, destination, audio, metadata):
            capture(source, destination, audio, metadata)
            self.store.execute('UPDATE segments SET duration=? WHERE generation_id=? AND id=?',
                               (.2, generation['id'], second['id']))
        with patch.object(export_module, 'capture_audio', side_effect=change_later):
            with self.assertRaisesRegex(AppError, 'Audio changed'):
                self.export(generation)
        self.clean_failed_export()

    def test_concurrent_generation_deletion_does_not_publish_or_leak_export(self):
        generation = self.completed()
        freeze = export_module.freeze_audio
        removed = []
        def deleting(snapshot):
            if not removed:
                removed.append(self.store.delete_generation(generation['id']))
            return freeze(snapshot)
        with patch.object(export_module, 'freeze_audio', side_effect=deleting):
            with self.assertRaises(AppError) as raised:
                self.export(generation)
        self.assertEqual(export_module.EXPORT_ORPHANED, str(raised.exception))
        self.assertEqual([], removed[0]['cleanup_warnings'])
        self.clean_failed_export()

    def test_copy_failure_closes_both_captured_handles_and_removes_partial_files(self):
        generation = self.completed()
        opened = []
        original = audio_source.open_shared_read
        def opening(path):
            handle = original(path)
            opened.append(handle)
            return handle
        def copying(source, target, length):
            target.write(b'partial')
            raise OSError('disk full')
        with patch.object(audio_source, 'open_shared_read', side_effect=opening), \
                patch.object(export_module.os, 'link', side_effect=OSError('no link')), \
                patch.object(export_module.shutil, 'copyfileobj', side_effect=copying):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.export(generation)
        self.assertEqual(2, len(opened))
        self.assertTrue(all(handle.closed for handle in opened))
        self.clean_failed_export()

    def test_missing_metadata_closes_the_already_opened_audio_handle(self):
        source = self.store.root / 'audio' / 'orphan.wav'
        write_wave(source)
        opened = []
        original = audio_source.open_shared_read
        def opening(path):
            handle = original(path)
            opened.append(handle)
            return handle
        with patch.object(audio_source, 'open_shared_read', side_effect=opening):
            with self.assertRaisesRegex(AppError, 'metadata'):
                with open_audio_pair(source):
                    self.fail('Missing metadata was accepted')
        self.assertEqual(1, len(opened))
        self.assertTrue(opened[0].closed)

    def test_live_capture_handles_are_bounded_even_for_many_segments(self):
        generation = self.completed()
        opened = []
        original = audio_source.open_shared_read
        def opening(path):
            self.assertLess(sum(not handle.closed for handle in opened), 2)
            handle = original(path)
            opened.append(handle)
            return handle
        with patch.object(audio_source, 'open_shared_read', side_effect=opening):
            self.export(generation)
        self.assertGreater(len(opened), 2)
        self.assertTrue(all(handle.closed for handle in opened))

    def test_playback_validation_uses_retained_pair_without_store_lock(self):
        generation = self.completed()
        key = generation['segments'][0]['audio_hash']
        path = self.store.root / 'audio' / f'{key}.wav'
        expected = path.read_bytes()
        accepted = server.accepted_audio
        def replace_then_validate(source, **kwargs):
            self.unlocked()
            self.replace_pair(source)
            return accepted(source, **kwargs)
        with patch.object(server, 'accepted_audio', side_effect=replace_then_validate):
            with self.app.open_audio(key) as handle:
                self.assertEqual(expected, handle.read())
            self.assertTrue(handle.closed)

    def test_playback_response_handle_survives_concurrent_generation_deletion(self):
        generation = self.completed()
        key = generation['segments'][0]['audio_hash']
        with self.app.open_asset('audio', key) as (source, mime):
            self.assertEqual('audio/wav', mime)
            self.store.delete_generation(generation['id'])
            self.assertEqual(b'RIFF', source.read(4))
        self.assertTrue(source.closed)
        self.assertEqual([], list((self.store.root / 'audio').iterdir()))


    def test_http_audio_opens_and_validates_one_retained_pair(self):
        generation = self.completed()
        key = generation['segments'][0]['audio_hash']
        with patch.object(server, 'accepted_audio', wraps=server.accepted_audio) as validate:
            with self.app.open_asset('audio', key) as (source, mime):
                self.assertEqual('audio/wav', mime)
                self.assertEqual(b'RIFF', source.read(4))
        self.assertEqual(1, validate.call_count)

    def test_http_audio_retains_admitted_file_when_deleted_during_validation(self):
        generation = self.completed()
        key = generation['segments'][0]['audio_hash']
        path = self.store.root / 'audio' / f'{key}.wav'
        expected = path.read_bytes()
        validate = server.accepted_audio
        def deleting(path, **kwargs):
            result = validate(path, **kwargs)
            self.store.delete_generation(generation['id'])
            return result
        with patch.object(server, 'accepted_audio', side_effect=deleting):
            with self.app.open_asset('audio', key) as (source, mime):
                self.assertEqual(expected, source.read())
        self.assertTrue(source.closed)
        self.assertEqual([], list((self.store.root / 'audio').iterdir()))

    def deletion_during_export(self, kind):
        generation = self.completed()
        snapshot_ready = threading.Event()
        release_snapshot = threading.Event()
        publisher_done = threading.Event()
        deleted, errors = [], []
        method = 'generation' if kind == 'generation' else 'rows'
        read = getattr(self.store, method)
        def snapshot(*args, **kwargs):
            result = read(*args, **kwargs)
            if threading.current_thread() is deletion and (kind == 'generation' or args[0].startswith('SELECT exports.file')):
                snapshot_ready.set()
                if not release_snapshot.wait(5):
                    raise AssertionError('Deletion was not released')
            return result
        def deleting():
            try:
                result = (self.store.delete_generation(generation['id']) if kind == 'generation'
                          else self.store.delete_book(self.book['id']))
                deleted.append(result)
            except BaseException as error:
                errors.append(error)
        def publishing():
            try:
                self.export(generation)
            except AppError as error:
                if 'not found' not in str(error).lower():
                    errors.append(error)
            except BaseException as error:
                errors.append(error)
            finally:
                publisher_done.set()
        deletion = threading.Thread(target=deleting)
        publisher = threading.Thread(target=publishing)
        with patch.object(self.store, method, side_effect=snapshot):
            deletion.start()
            try:
                self.assertTrue(snapshot_ready.wait(5))
                publisher.start()
                if self.store.lock.acquire(timeout=.2):
                    self.store.lock.release()
                    self.assertTrue(publisher_done.wait(5))
            finally:
                release_snapshot.set()
                deletion.join(5)
                if publisher.ident is not None:
                    publisher.join(5)
        self.assertFalse(deletion.is_alive())
        self.assertFalse(publisher.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(deleted))
        self.assertEqual([], deleted[0]['cleanup_warnings'])
        self.clean_failed_export()

    def test_generation_deletion_and_export_publication_cannot_leave_orphans(self):
        self.deletion_during_export('generation')

    def test_book_deletion_and_export_publication_cannot_leave_orphans(self):
        self.deletion_during_export('book')


class WindowsReadHandle(unittest.TestCase):
    """Exercise ownership and Win32 flags portably; native platform gates remain separate."""
    def fake_windows(self):
        kernel = types.SimpleNamespace(CreateFileW=Mock(return_value=123), CloseHandle=Mock())
        crt = types.SimpleNamespace(open_osfhandle=Mock(return_value=456))
        stream = io.BytesIO(b'original')
        operating_system = types.SimpleNamespace(name='nt', O_RDONLY=0, O_BINARY=0x8000, O_NOINHERIT=0x80,
                                                 fdopen=Mock(return_value=stream), close=Mock())
        self.addCleanup(stream.close)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(ctypes, 'WinDLL', return_value=kernel, create=True))
        stack.enter_context(patch.object(audio_source, 'os', operating_system))
        stack.enter_context(patch.dict('sys.modules', {'msvcrt': crt}))
        return kernel, crt, operating_system, stream

    def test_windows_read_allows_delete_and_replace_without_inheriting_handle(self):
        path = Path('retained.wav')
        kernel, crt, operating_system, stream = self.fake_windows()
        self.assertIs(stream, open_shared_read(path))
        kernel.CreateFileW.assert_called_once_with(str(path), 0x80000000, 7, None, 3, 0x80, None)
        crt.open_osfhandle.assert_called_once_with(123, 0x8080)
        operating_system.fdopen.assert_called_once_with(456, 'rb')
        kernel.CloseHandle.assert_not_called()

    def test_failed_native_open_does_not_close_an_invalid_handle(self):
        path = Path('missing.wav')
        kernel, crt, _, _ = self.fake_windows()
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with patch.object(ctypes, 'get_last_error', return_value=2, create=True), \
                patch.object(ctypes, 'WinError', return_value=OSError('not found'), create=True):
            with self.assertRaisesRegex(OSError, 'not found'):
                open_shared_read(path)
        crt.open_osfhandle.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_crt_conversion_failure_closes_untransferred_native_handle(self):
        path = Path('retained.wav')
        kernel, crt, operating_system, _ = self.fake_windows()
        crt.open_osfhandle.side_effect = OSError('conversion failed')
        with self.assertRaisesRegex(OSError, 'conversion failed'):
            open_shared_read(path)
        kernel.CloseHandle.assert_called_once_with(123)
        operating_system.close.assert_not_called()

    def test_stream_conversion_failure_closes_crt_descriptor_not_native_handle_twice(self):
        path = Path('retained.wav')
        kernel, _, operating_system, _ = self.fake_windows()
        operating_system.fdopen.side_effect = OSError('stream failed')
        with self.assertRaisesRegex(OSError, 'stream failed'):
            open_shared_read(path)
        operating_system.close.assert_called_once_with(456)
        kernel.CloseHandle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
