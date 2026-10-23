from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

SPEC = importlib.util.spec_from_file_location('source_packager', Path(__file__).resolve().parents[1] / 'scripts/package_source.py')
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


class SourcePackagingChecks(unittest.TestCase):
    def test_only_the_approved_branded_audio_pack_is_included(self):
        for name in ('manifest.json', 'ru-speech.wav', 'ru-music.wav', 'en-speech.wav', 'en-music.wav'):
            self.assertTrue(PACKAGER.included(PurePosixPath('resources/branding/dogma-opening-v1') / name))
        for name in ('resources/branding/dogma-opening-v1/extra.wav', 'resources/branding/private/reference.wav',
                     'resources/branding/dogma-opening-v2/ru-music.wav', 'training-work/ident.wav'):
            self.assertFalse(PACKAGER.included(PurePosixPath(name)))

    def test_policy_keeps_current_sources_and_excludes_generated_material(self):
        for name in ['src/App.tsx', 'runtime/server.py', 'verification/test_recheck.py', 'docs/TRAINING.md', 'docs/LABELING.md', 'src-tauri/icons/icon.png', 'src-tauri/Cargo.lock', 'resources/runtime/.gitkeep', 'packs/pack.example.json', 'training/recipes/corpus.example.json', 'package-lock.json', 'LICENSE']:
            with self.subTest(name=name):
                self.assertTrue(PACKAGER.included(PurePosixPath(name)))
        for name in ['docs/AUDIT-0.1.16.md', 'docs/audit-0.1.16/results.json', 'docs/recheck-python.txt', 'docs/preview.png', 'runtime/__pycache__/server.pyc', 'src-tauri/target/debug/app', 'node_modules/react/index.js', 'resources/runtime/core/app.exe', 'resources/packs/reader/model.safetensors', 'training/work/clip.wav', 'training/recipes/book.epub', '.local-library/library.sqlite', 'public/font.woff2', 'SOURCE_MANIFEST.sha256', 'manual-labeling/review.json', 'manual-labeling/book/metadata/candidates.jsonl', 'manual-labeling/book/audio/reference.wav', 'training-work/dataset/manifest.json', 'corpora/labels.jsonl', 'content/source.json']:
            with self.subTest(name=name):
                self.assertFalse(PACKAGER.included(PurePosixPath(name)))

    def test_archive_manifest_and_reproducible_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            (root / 'package.json').write_text('{}')
            (root / 'src').mkdir()
            (root / 'src' / 'main.ts').write_text('export const value = 1;')
            (root / 'docs').mkdir()
            (root / 'docs' / 'AUDIT-0.1.16.md').write_text('old output')
            (root / 'SOURCE_MANIFEST.sha256').write_text('stale')
            one, two = Path(temporary) / 'one.zip', Path(temporary) / 'two.zip'
            PACKAGER.package_source(root, one)
            PACKAGER.package_source(root, two)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with ZipFile(one) as archive:
                self.assertEqual(len(archive.namelist()), 3)
                self.assertIsNone(archive.testzip())
                manifest = archive.read('rimskifikator/SOURCE_MANIFEST.sha256').decode()
                for line in manifest.splitlines():
                    digest, name = line.split('  ', 1)
                    self.assertEqual(digest, hashlib.sha256(archive.read('rimskifikator/' + name)).hexdigest())
            self.assertEqual((root / 'SOURCE_MANIFEST.sha256').read_text(), 'stale')

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'package.json').write_text('{}')
            output = root / 'source.zip'
            output.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                PACKAGER.package_source(root, output)
            self.assertEqual(output.read_bytes(), b'existing')

    def test_failed_write_removes_only_new_partial_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'package.json').write_text('{}')
            output = root / 'source.zip'
            with patch.object(PACKAGER, 'add_file', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    PACKAGER.package_source(root, output)
            self.assertFalse(output.exists())

    def test_source_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'package.json').write_text('{}')
            (root / 'src').mkdir()
            (root / 'outside').write_text('private')
            try:
                (root / 'src' / 'main.ts').symlink_to(root / 'outside')
            except OSError as error:
                if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege is unavailable')
                raise
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                PACKAGER.package_source(root, root / 'source.zip')
            self.assertFalse((root / 'source.zip').exists())

    def test_e2e_sources_are_kept_but_outputs_and_binary_fixtures_are_not(self):
        kept = ['playwright.config.ts', 'docs/E2E.md', 'e2e/fixtures.ts', 'e2e/guard.mjs',
                'e2e/guard.test.mjs', 'e2e/tsconfig.json', 'e2e/runtime/serve_fixture.py',
                'e2e/runtime/make_fixtures.py', 'e2e/runtime/unpack_export.py', 'e2e/specs/reading.spec.ts']
        omitted = ['test-results/run/trace.zip', 'playwright-report/index.html', 'blob-report/report.zip',
                   'e2e/test-results/run/results.json', 'e2e/playwright-report/index.html',
                   'e2e/blob-report/data.json', 'e2e/results.json', 'e2e/runtime/pack.json',
                   'e2e/runtime/__pycache__/serve_fixture.pyc', 'e2e/inputs/harbor.epub',
                   'e2e/inputs/forest.fb2', 'e2e/inputs/paragraph.png', 'e2e/specs/reading.spec.ts-snapshots/page.png']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            (root / 'package.json').write_text('{}')
            for name in kept + omitted:
                with self.subTest(name=name):
                    self.assertEqual(PACKAGER.included(PurePosixPath(name)), name in kept)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture content')
            output = Path(temporary) / 'source.zip'
            PACKAGER.package_source(root, output)
            with ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {'rimskifikator/' + name for name in kept + ['package.json', 'SOURCE_MANIFEST.sha256']})
                self.assertIsNone(archive.testzip())

    def test_every_maintained_e2e_source_is_selected(self):
        root = Path(__file__).resolve().parents[1]
        sources = [path for path in (root / 'e2e').rglob('*')
                   if path.is_file() and path.suffix in {'.py', '.ts', '.mjs'}
                   and not any(part in PACKAGER.OMIT_DIRS or part.startswith('.') for part in path.relative_to(root).parts)]
        self.assertGreater(len(sources), 10)
        for path in sources:
            with self.subTest(path=path.relative_to(root)):
                self.assertTrue(PACKAGER.included(PurePosixPath(path.relative_to(root).as_posix())))
