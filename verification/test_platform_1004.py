from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'verification'))

import rimsky.packs as packs
import server as runtime_server
from rimsky import __version__
from rimsky.instance import library_lease
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, write_json
from server import LOCK_FREE, Application, handler_for
from test_core import FB2, pack_fixture
from test_review_repairs_core import lock_free

SERVER = ROOT / 'runtime' / 'server.py'
CANCELLED = 'Export cancelled. No incomplete output was published.'


def script(name):
    spec = importlib.util.spec_from_file_location(f'platform_1004_{name}', ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def post(port, token, op, data=None, timeout=10):
    request = Request(f'http://127.0.0.1:{port}/api', data=json.dumps({'op': op, 'data': data or {}}).encode(),
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as error:
        return json.loads(error.read())


class Served(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.token = secrets.token_urlsafe(32)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.app, self.token))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.temporary.cleanup()

    def call(self, op, data=None, timeout=10):
        return post(self.server.server_port, self.token, op, data, timeout)

    def background(self, op, data=None):
        results = []
        thread = threading.Thread(target=lambda: results.append(self.call(op, data, 20)), daemon=True)
        thread.start()
        return thread, results


class StartupHandshake(unittest.TestCase):
    def run_server(self, *arguments):
        return subprocess.run([sys.executable, str(SERVER), *arguments], input='', capture_output=True, text=True, encoding='utf-8', timeout=60)

    def handshake_error(self, result):
        self.assertNotEqual(0, result.returncode)
        lines = result.stdout.splitlines()
        self.assertEqual(1, len(lines), result.stdout)
        reply = json.loads(lines[0])
        self.assertEqual({'protocol', 'error'}, set(reply))
        self.assertEqual(1, reply['protocol'])
        return reply['error']

    def test_version_prints_json_before_any_library_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = Path(temporary) / 'absent'
            result = self.run_server('--version', '--data-dir', str(absent))
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({'version': __version__}, json.loads(result.stdout))
            self.assertFalse(absent.exists())

    def test_library_held_by_another_process_is_reported_in_the_handshake(self):
        with tempfile.TemporaryDirectory() as temporary, library_lease(Path(temporary)):
            result = self.run_server('--data-dir', temporary)
        self.assertEqual('This library is already open in another Dogma process.', self.handshake_error(result))
        self.assertIn('already open', result.stderr)

    def test_newer_library_is_reported_in_the_handshake(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = sqlite3.connect(Path(temporary) / 'library.sqlite3')
            database.execute('PRAGMA user_version=6')
            database.close()
            result = self.run_server('--data-dir', temporary)
        self.assertEqual('This library was created by a newer application. Its data was not changed.', self.handshake_error(result))

    def test_damaged_database_is_reported_in_the_handshake(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'library.sqlite3').write_bytes(b'not a database ' * 400)
            result = self.run_server('--data-dir', temporary)
        error = self.handshake_error(result)
        self.assertTrue(error.startswith('DatabaseError: '), error)
        self.assertIn('not a database', error)


class LockFreeOperations(Served):
    def test_read_only_and_control_operations_skip_the_mutation_lock(self):
        self.assertLessEqual({'visual_brief', 'illustration_history', 'select_pack', 'export', 'cancel_export'}, LOCK_FREE)
        self.assertFalse({'update_settings', 'bookmark', 'delete_book', 'generate', 'repair_audio', 'revise'} & LOCK_FREE)

    def test_visual_brief_answers_while_a_mutation_holds_the_lock(self):
        book = self.app.store.import_data(FB2, 'fixture.fb2')
        block = book['chapters'][0]['blocks'][-1]
        with self.app.mutations:
            started = time.monotonic()
            brief = self.call('visual_brief', {'book_id': book['id'], 'source_id': block['id']}, 5)
            self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(block['text'], brief['passage'])

    def test_unknown_and_malformed_operations_still_fail_cleanly(self):
        self.assertEqual('Unknown application operation.', self.call('no_such_operation')['error'])
        self.assertEqual('Unknown application operation.', self.call(['visual_brief'])['error'])


class ExportCancellation(Served):
    def test_cancel_reaches_a_running_export_outside_the_mutation_lock(self):
        entered, seen = threading.Event(), []

        def export(store, gid, format, chapters, binaries, stop=None, guard=None, music=None):
            seen.append(stop)
            entered.set()
            deadline = time.monotonic() + 10
            while not stop.is_set():
                if time.monotonic() > deadline:
                    return {'id': 'not-cancelled'}
                time.sleep(.02)
            raise AppError(CANCELLED, 'export_cancelled')
        with patch.object(runtime_server, 'build_export', export):
            thread, results = self.background('export', {'id': 'generation', 'format': 'm4b'})
            self.assertTrue(entered.wait(5))
            self.assertTrue(lock_free(self.app.mutations))
            started = time.monotonic()
            self.assertEqual({'requested': True}, self.call('cancel_export', {}, 5))
            self.assertLess(time.monotonic() - started, 4)
            thread.join(10)
        self.assertEqual([{'error': CANCELLED, 'code': 'export_cancelled', 'details': {}}], results)
        self.assertTrue(lock_free(self.app.mutations))

    def test_export_stop_combines_closing_with_a_cancel_cleared_per_export(self):
        self.app.export_cancel.set()
        with patch.object(runtime_server, 'build_export', lambda *arguments, **kwargs: arguments[5]):
            stop = self.app.dispatch('export', {'id': 'generation', 'format': 'vtt'})
        self.assertFalse(stop.is_set())
        self.app.dispatch('cancel_export', {})
        self.assertTrue(stop.is_set())
        self.app.export_cancel.clear()
        self.assertFalse(stop.is_set())
        self.app.closing.set()
        self.assertTrue(stop.is_set())


class PackSelectionLocking(Served):
    def setUp(self):
        super().setUp()
        self.pack = self.root / 'pack'
        pack_fixture(self.pack)
        self.entered, self.release = threading.Event(), threading.Event()
        real = packs.file_digest

        def blocking(path, stop=None):
            self.entered.set()
            self.release.wait(10)
            return real(path, stop)
        patcher = patch.object(packs, 'file_digest', blocking)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)

    def test_verification_runs_outside_the_mutation_lock(self):
        thread, results = self.background('select_pack', {'path': str(self.pack)})
        self.assertTrue(self.entered.wait(5))
        self.assertTrue(lock_free(self.app.mutations))
        self.assertEqual('dark', self.call('update_settings', {'theme': 'dark'}, 5)['theme'])
        self.release.set()
        thread.join(10)
        self.assertEqual(str(self.pack.resolve()), results[0]['settings']['pack_path'])
        self.assertEqual('dark', results[0]['settings']['theme'])
        self.assertEqual(str(self.pack.resolve()), self.app.store.settings()['pack_path'])

    def test_closing_interrupts_verification_without_saving_the_selection(self):
        before = self.app.store.settings()
        results = []

        def select():
            try:
                results.append(self.app.dispatch('select_pack', {'path': str(self.pack)}))
            except AppError as error:
                results.append(str(error))
        thread = threading.Thread(target=select, daemon=True)
        thread.start()
        self.assertTrue(self.entered.wait(5))
        self.app.closing.set()
        self.release.set()
        thread.join(10)
        self.assertEqual(['The local application is closing.'], results)
        self.assertEqual(before, self.app.store.settings())

    def test_waiting_health_verifier_honours_closing(self):
        self.release.set()
        self.app.store.update_settings({'pack_path': str(self.pack)})
        waiting = threading.Event()
        real = packs.pack_signature

        def signature(pack):
            waiting.set()
            return real(pack)
        results = []
        with patch.object(packs, 'pack_signature', signature), packs.VERIFYING:
            thread = threading.Thread(target=lambda: results.append(self.app.dispatch('health', {})), daemon=True)
            thread.start()
            self.assertTrue(waiting.wait(5))
            self.app.closing.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual('Preparation paused.', results[0]['pack_error'])


class BundledPackRelocation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.resource = self.root / 'resources'
        shutil.copytree(ROOT / 'resources' / 'branding', self.resource / 'branding')
        self.library = self.root / 'library'
        self.stale = str(self.root / 'old-mount' / 'resources' / 'packs' / 'demo')
        environment = patch.dict(os.environ, {'RIMSKY_RESOURCE_DIR': str(self.resource)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.temporary.cleanup)

    def bundle(self, name, title=None):
        path = self.resource / 'packs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        pack_fixture(path)
        if title:
            manifest = json.loads((path / 'pack.json').read_text('utf-8'))
            write_json(path / 'pack.json', {**manifest, 'name': title})
        return load_pack(str(path))

    def prepare(self, pack_path, fingerprint=None):
        store = Store(self.library)
        try:
            store.update_settings({'pack_path': pack_path})
            if fingerprint:
                book = store.import_data(FB2, 'fixture.fb2')
                store.create_generation(book['id'], {'pack_path': pack_path, 'pack_fingerprint': fingerprint, 'pack_name': 'Old', 'device': 'cpu', 'align_words': False})
        finally:
            store.close()

    def selected(self):
        app = Application(self.library, self.root / 'bin')
        try:
            if app.relocation:
                app.relocation.join(10)
            return app.store.settings()['pack_path']
        finally:
            app.close()

    def held(self):
        entered, release = threading.Event(), threading.Event()
        real = runtime_server.relocated_pack
        def slow(*arguments):
            entered.set()
            release.wait(10)
            return real(*arguments)
        self.addCleanup(release.set)
        return patch.object(runtime_server, 'relocated_pack', slow), entered, release

    def test_missing_selection_moves_to_the_sole_bundled_pack(self):
        bundled = self.bundle('demo')
        self.prepare(self.stale)
        self.assertEqual(bundled['_root'], self.selected())

    def test_bundle_location_follows_the_bundled_pack_of_the_same_folder(self):
        demo = self.bundle('demo', 'Demo pack')
        self.bundle('other', 'Other pack')
        self.prepare(self.stale, 'older-bundled-revision')
        self.assertEqual(demo['_root'], self.selected())

    def test_missing_custom_pack_is_kept_and_used_again_when_it_returns(self):
        self.bundle('demo')
        custom = self.root / 'usb-drive' / 'my-pack'
        custom.parent.mkdir()
        pack_fixture(custom)
        manifest = json.loads((custom / 'pack.json').read_text('utf-8'))
        write_json(custom / 'pack.json', {**manifest, 'name': 'My voices'})
        mine = load_pack(str(custom))
        self.prepare(mine['_root'], mine['_fingerprint'])
        (self.root / 'usb-drive').rename(self.root / 'unplugged')
        self.assertEqual(mine['_root'], self.selected())
        (self.root / 'unplugged').rename(self.root / 'usb-drive')
        self.assertEqual(mine['_root'], self.selected())
        self.prepare(str(self.root / 'never-mounted' / 'pack'))
        self.assertEqual(str(self.root / 'never-mounted' / 'pack'), self.selected())

    def test_custom_folder_with_a_bundled_fingerprint_moves_to_that_bundled_pack(self):
        self.bundle('first', 'First pack')
        second = self.bundle('second', 'Second pack')
        copy = str(self.root / 'usb-drive' / 'copy')
        self.prepare(copy, second['_fingerprint'])
        self.assertEqual(second['_root'], self.selected())

    def test_startup_does_not_wait_for_the_stored_pack_folder(self):
        bundled = self.bundle('demo')
        self.prepare(self.stale)
        patched, entered, release = self.held()
        with patched:
            app = Application(self.library, self.root / 'bin')
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.stale, app.store.settings()['pack_path'])
                self.assertEqual(self.stale, app.dispatch('settings', {})['pack_path'])
                release.set()
                app.relocation.join(10)
                self.assertFalse(app.relocation.is_alive())
                self.assertEqual(bundled['_root'], app.store.settings()['pack_path'])
            finally:
                app.close()

    def test_a_selection_saved_while_relocation_runs_is_kept(self):
        self.bundle('demo')
        self.prepare(self.stale)
        chosen = str(self.root / 'chosen')
        patched, entered, release = self.held()
        with patched:
            app = Application(self.library, self.root / 'bin')
            try:
                self.assertTrue(entered.wait(5))
                with app.mutations:
                    app.dispatch('update_settings', {'pack_path': chosen})
                release.set()
                app.relocation.join(10)
                self.assertEqual(chosen, app.store.settings()['pack_path'])
            finally:
                app.close()

    def test_fingerprint_of_the_old_folder_chooses_among_several_bundled_packs(self):
        self.bundle('first', 'First pack')
        second = self.bundle('second', 'Second pack')
        self.prepare(self.stale, second['_fingerprint'])
        self.assertEqual(second['_root'], self.selected())

    def test_ambiguous_bundled_packs_keep_the_selection(self):
        self.bundle('first', 'First pack')
        self.bundle('second', 'Second pack')
        self.prepare(self.stale, 'unknown-fingerprint')
        self.assertEqual(self.stale, self.selected())

    def test_existing_broken_pack_folder_is_not_replaced(self):
        self.bundle('demo')
        broken = self.root / 'broken'
        broken.mkdir()
        (broken / 'pack.json').write_text('{', 'utf-8')
        self.prepare(str(broken))
        self.assertEqual(str(broken), self.selected())

    def test_new_library_still_selects_the_sole_bundled_pack(self):
        self.bundle('demo')
        self.assertEqual(str(self.resource / 'packs' / 'demo'), self.selected())


class ReleaseBuildIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.packager = script('package_runtime')
        self.release = script('check_release')
        (self.root / 'runtime' / 'rimsky').mkdir(parents=True)
        (self.root / 'runtime' / 'server.py').write_text('print(1)\n', 'utf-8')
        (self.root / 'runtime' / 'rimsky' / '__init__.py').write_text(f'__version__ = "{__version__}"\n', 'utf-8')
        (self.root / 'runtime' / 'rimsky' / 'analysis.py').write_text('SCHEMA = 1\n', 'utf-8')
        self.runtime = self.root / 'resources' / 'runtime'

    def record(self, folder, digest):
        (self.runtime / folder).mkdir(parents=True, exist_ok=True)
        if digest is not None:
            write_json(self.runtime / folder / 'build-info.json', {'component': folder, 'version': __version__, 'source_digest': digest})

    def test_source_digest_follows_runtime_sources_only(self):
        digest = self.packager.source_digest(self.root)
        analysis = self.root / 'runtime' / 'rimsky' / 'analysis.py'
        analysis.write_bytes(b'SCHEMA = 1\r\n')
        self.assertEqual(digest, self.packager.source_digest(self.root))
        (self.root / 'runtime' / 'rimsky' / 'nested').mkdir()
        (self.root / 'runtime' / 'rimsky' / 'nested' / 'ignored.py').write_text('x = 1\n', 'utf-8')
        (self.root / 'runtime' / 'notes.txt').write_text('ignored', 'utf-8')
        self.assertEqual(digest, self.packager.source_digest(self.root))
        analysis.write_text('SCHEMA = 2\n', 'utf-8')
        self.assertNotEqual(digest, self.packager.source_digest(self.root))
        analysis.write_text('SCHEMA = 1\n', 'utf-8')
        analysis.rename(analysis.with_name('contract.py'))
        self.assertNotEqual(digest, self.packager.source_digest(self.root))

    def test_build_info_records_version_and_digest_and_core_collects_certifi(self):
        commands = []
        with patch.object(self.packager.importlib.util, 'find_spec', return_value=object()), \
             patch.object(self.packager, 'source_digest', return_value='f' * 64), \
             patch.object(self.packager.subprocess, 'run', side_effect=lambda command, **_: commands.append(command)), \
             patch.object(self.packager.shutil, 'copytree', side_effect=lambda source, target: Path(target).mkdir(parents=True)):
            self.packager.build('core', self.root / 'out')
        info = json.loads((self.root / 'out' / 'core' / 'build-info.json').read_text('utf-8'))
        self.assertEqual(('core', __version__, 'f' * 64), (info['component'], info['version'], info['source_digest']))
        self.assertIn(['--collect-all', 'certifi'], [commands[0][index:index + 2] for index in range(len(commands[0]) - 1)])

    def test_release_rejects_missing_mixed_and_stale_component_builds(self):
        current = self.packager.source_digest(self.root)
        for folder in ('core', 'infer', 'align'):
            self.record(folder, current)
        self.assertEqual([], self.release.build_issues(self.root, self.runtime))
        self.record('infer', 'a' * 64)
        issues = self.release.build_issues(self.root, self.runtime)
        self.assertEqual(2, len(issues), issues)
        self.assertIn('built from different sources', issues[0])
        self.assertTrue(issues[1].endswith(': infer. Rebuild them.'), issues[1])
        (self.runtime / 'align' / 'build-info.json').unlink()
        self.assertIn('runtime/align/build-info.json does not record a source digest. Rebuild this component with scripts/package_runtime.py.',
                      self.release.build_issues(self.root, self.runtime))
        self.assertTrue(any('different sources' in issue for issue in self.release.release_issues(self.root, require_acceptance=False, execute=False)))

    def test_other_target_is_not_executed_and_full_check_says_so(self):
        target = next(item for item in self.release.TARGETS if item != self.release.host_target())
        with patch.object(self.release, 'run_check', side_effect=AssertionError('must not execute')):
            full = self.release.release_issues(self.root, target=target)
            build = self.release.release_issues(self.root, require_acceptance=False, target=target)
        self.assertIn(f'Packaged binaries were not started on this host; run the release check on {target}.', full)
        self.assertFalse(any('not started' in issue for issue in build))


@unittest.skipIf(os.name == 'nt', 'POSIX shell scripts stand in for packaged binaries')
class ReleaseStartChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / 'resources' / 'runtime'
        self.addCleanup(self.temporary.cleanup)
        self.release = script('check_release')
        self.target = self.release.host_target()

    def place(self, folder, name, body):
        path = self.runtime / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\n' + body + '\n', 'utf-8')
        path.chmod(0o755)

    def test_each_packaged_binary_is_started_with_closed_input(self):
        self.place('core', 'rimsky-runtime', f'[ "$1" = --version ] || exit 7\necho \'{json.dumps({"version": __version__})}\'')
        self.place('infer', 'rimsky-infer', 'read line && exit 9\nexit 0')
        self.place('align', 'rimsky-align', 'exit 3')
        self.place('media', 'ffmpeg', '[ "$1" = -version ] && echo ffmpeg version test')
        self.place('media', 'ffprobe', 'echo broken probe >&2\nexit 1')
        issues = self.release.start_issues(self.runtime, self.target)
        self.assertEqual(['runtime/align/rimsky-align failed its start check: exit status 3',
                          'runtime/media/ffprobe failed its start check: broken probe'], issues)
        self.place('core', 'rimsky-runtime', 'echo \'{"version": "0.0.0"}\'')
        self.assertIn(f"runtime/core/rimsky-runtime reports version '0.0.0', but the source declares {__version__}.",
                      self.release.start_issues(self.runtime, self.target))

    def test_missing_or_non_executable_binaries_are_not_started(self):
        self.place('infer', 'rimsky-infer', 'exit 0')
        (self.runtime / 'infer' / 'rimsky-infer').chmod(0o644)
        with patch.object(self.release, 'run_check', side_effect=AssertionError('must not execute')):
            self.assertEqual([], self.release.start_issues(self.runtime, self.target))


class ReleaseLinkageChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / 'resources' / 'runtime'
        self.addCleanup(self.temporary.cleanup)
        self.release = script('check_release')

    def place(self, relative):
        path = self.runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'binary')
        return path

    def inspect(self, target, outputs):
        def run(command, timeout=120):
            return 0, outputs.get((command[1], Path(command[2]).name) if command[0] == 'otool' else Path(command[1]).name, ''), ''
        with patch.object(self.release, 'run_check', side_effect=run), patch.object(self.release.shutil, 'which', return_value='/usr/bin/tool'):
            return self.release.linkage_issues(self.runtime, target)

    def test_macos_rejects_homebrew_load_paths_but_ignores_a_library_identity(self):
        ffmpeg = self.place('media/ffmpeg')
        library = self.place('core/_internal/libx.dylib')
        self.place('core/_internal/ok.so')
        outputs = {
            ('-L', 'ffmpeg'): f'{ffmpeg}:\n\t/opt/homebrew/Cellar/ffmpeg/8.0.1_4/lib/libavcodec.62.dylib (compatibility version 62.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n',
            ('-L', 'libx.dylib'): f'{library}:\n\t/opt/homebrew/opt/x/lib/libx.dylib (compatibility version 1.0.0)\n\t@rpath/liby.dylib (compatibility version 1.0.0)\n',
            ('-D', 'libx.dylib'): f'{library}:\n/opt/homebrew/opt/x/lib/libx.dylib\n',
            ('-L', 'ok.so'): '\t@loader_path/libz.dylib (compatibility version 1.0.0)\n',
        }
        self.assertEqual(['runtime/media/ffmpeg loads libraries from outside the package: /opt/homebrew/Cellar/ffmpeg/8.0.1_4/lib/libavcodec.62.dylib'],
                         self.inspect('macos', outputs))

    def test_linux_rejects_missing_and_system_ffmpeg_libraries(self):
        media = self.runtime / 'media'
        self.place('media/ffmpeg')
        self.place('media/ffprobe')
        self.place('media/libavutil.so.59')
        self.place('infer/_internal/libfoo.so.1')
        outputs = {
            'ffmpeg': '\tlinux-vdso.so.1 (0x00007ffd)\n\tlibavcodec.so.61 => /usr/lib/x86_64-linux-gnu/libavcodec.so.61 (0x00007f00)\n\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f01)\n',
            'ffprobe': f'\tlibavutil.so.59 => {media.resolve()}/libavutil.so.59 (0x00007f00)\n',
            'libfoo.so.1': '\tlibbar.so.2 => not found\n',
        }
        self.assertEqual(['runtime/infer/_internal/libfoo.so.1 loads libraries from outside the package: libbar.so.2',
                          'runtime/media/ffmpeg loads libraries from outside the package: libavcodec.so.61'],
                         self.inspect('linux', outputs))

    def test_missing_inspection_tool_is_reported(self):
        self.place('media/ffmpeg')
        with patch.object(self.release.shutil, 'which', return_value=None):
            self.assertEqual(['otool is not available, so packaged shared-library dependencies were not inspected.'],
                             self.release.linkage_issues(self.runtime, 'macos'))


if __name__ == '__main__':
    unittest.main()
