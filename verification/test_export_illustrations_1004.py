from __future__ import annotations
import base64
import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from unittest.mock import patch
from PIL import Image
import PIL.EpsImagePlugin
sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
import test_paragraph_illustrations as fixture
from test_core import FB2, complete_audio, epub_fixture
from rimsky.export import ENCODER_CEILING, ENCODER_FLOOR, build_export, captions, concat_list, encoder_timeout, run_encoder, segment_captions
from rimsky.illustrations import SKIPPED_LONG, codex_directories, codex_environment, codex_executable, paragraph_plan, render_codex
from rimsky.integrity import validate_cues
from rimsky.store import Store
from rimsky.util import AppError
from rimsky.visuals import fetch_visual, https_context, image_preview, save_visual

DIALOGUE = 'Маша открыла книгу. — Читай, — сказал Иван.'
CANCELLED = 'Export cancelled. No incomplete output was published.'
CERTIFICATE = 'Secure connection to the image service failed (certificate check). No automatic paid retry was made.'
EPS = b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n(ATTACKER POSTSCRIPT) pop\nshowpage\n%%EOF\n'


def word_cues(spoken, words, step=.4, length=.35):
    return validate_cues([{'text': word, 'start': index * step, 'end': index * step + length} for index, word in enumerate(words)], spoken, len(words) * step)


def encoded(fmt):
    out = io.BytesIO()
    Image.new('RGB', (12, 8), 'purple').save(out, format=fmt)
    return out.getvalue()


class Flag:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value


class Captions(unittest.TestCase):
    def test_word_cues_become_punctuated_caption_lines(self):
        cues = word_cues(DIALOGUE, ['Маша', 'открыла', 'книгу', 'Читай', 'сказал', 'Иван'])
        self.assertEqual('1\n00:00:00,000 --> 00:00:01,150\nМаша открыла книгу.\n\n2\n00:00:01,200 --> 00:00:02,350\n— Читай, — сказал Иван.\n',
                         captions([{'duration': 3, 'spoken': DIALOGUE, 'cues': cues}], srt=True))

    def test_lines_stay_within_character_and_duration_limits(self):
        spoken = ' '.join(['слово'] * 30) + '.'
        lines = segment_captions({'duration': 12, 'spoken': spoken, 'cues': word_cues(spoken, ['слово'] * 30)})
        self.assertTrue(all(len(line['text']) <= 42 and line['end'] - line['start'] >= 1 for line in lines))
        self.assertEqual(spoken, ' '.join(line['text'] for line in lines))
        slow = ' '.join(['да'] * 12)
        lines = segment_captions({'duration': 18, 'spoken': slow, 'cues': word_cues(slow, ['да'] * 12, step=1.5, length=1.4)})
        self.assertTrue(all(line['end'] - line['start'] <= 7 for line in lines))
        self.assertEqual(slow, ' '.join(line['text'] for line in lines))

    def test_breaks_prefer_punctuation(self):
        spoken = 'Он открыл дверь, вошёл в тёмную комнату и долго стоял у окна'
        lines = segment_captions({'duration': 8, 'spoken': spoken, 'cues': word_cues(spoken, spoken.replace(',', '').split(), step=.5)})
        self.assertEqual('Он открыл дверь,', lines[0]['text'])
        self.assertEqual(spoken, ' '.join(line['text'] for line in lines))

    def texts(self, spoken):
        return [line['text'] for line in segment_captions({'duration': 6, 'spoken': spoken, 'cues': word_cues(spoken, re.findall(r'\w+', spoken))})]

    def test_initials_and_abbreviations_do_not_end_lines(self):
        for spoken, expected in (('Лев Николаевич Толстой и А. П. Чехов встретились в Ясной Поляне в 1900 году.', ['Лев Николаевич Толстой и А. П. Чехов', 'встретились в Ясной Поляне в 1900 году.']),
                                 ('Мы купили хлеб, молоко, сыр и т. д., а потом пошли домой через парк.', ['Мы купили хлеб, молоко, сыр и т. д.,', 'а потом пошли домой через парк.']),
                                 ('Вчера вечером к нам заходил писатель А. П. Чехов и долго рассказывал о Ялте.', ['Вчера вечером к нам заходил писатель', 'А. П. Чехов и долго рассказывал о Ялте.']),
                                 ('Вчера вечером мы гуляли по набережной и т. д. и долго смотрели на море.', ['Вчера вечером мы гуляли по набережной и', 'т. д. и долго смотрели на море.']),
                                 ('Да. Мы видели писателя и драматурга А. П. Чехова.', ['Да. Мы видели писателя и драматурга', 'А. П. Чехова.']),
                                 ('Mr. Smith met Dr. Watson on Baker Street and they talked for hours.', ['Mr. Smith met Dr. Watson on Baker Street', 'and they talked for hours.']),
                                 ('Это сказал я. Потом мы долго молчали и смотрели на тёмную воду реки.', ['Это сказал я.', 'Потом мы долго молчали и смотрели на', 'тёмную воду реки.'])):
            with self.subTest(spoken=spoken):
                self.assertEqual(expected, self.texts(spoken))

    def test_lines_under_a_second_join_a_neighbour_within_the_limits(self):
        for spoken, expected in (('В 1837 г. Пушкин погиб на дуэли с Дантесом под Петербургом.', ['В 1837 г. Пушкин погиб на дуэли с', 'Дантесом под Петербургом.']),
                                 ('Да. Он долго молчал и смотрел в окно, потом встал и вышел из комнаты.', ['Да. Он долго молчал и смотрел в окно,', 'потом встал и вышел из комнаты.']),
                                 ('Великолепный Константин А. Б. Достопримечательный человек.', ['Великолепный Константин', 'А. Б. Достопримечательный человек.'])):
            with self.subTest(spoken=spoken):
                self.assertEqual(expected, self.texts(spoken))
        fast = segment_captions({'duration': 1, 'spoken': DIALOGUE, 'cues': word_cues(DIALOGUE, ['Маша', 'открыла', 'книгу', 'Читай', 'сказал', 'Иван'], .1, .09)})
        self.assertEqual(['Маша открыла книгу.', '— Читай, — сказал Иван.'], [line['text'] for line in fast])

    def test_aligner_text_without_punctuation_keeps_spoken_punctuation(self):
        spoken = 'Кто-то постучал: «Откройте!»'
        self.assertEqual([{'start': 0.0, 'end': 1.15, 'text': spoken}],
                         segment_captions({'duration': 2, 'spoken': spoken, 'cues': word_cues(spoken, ['Ктото', 'постучал', 'Откройте'])}))

    def test_decomposed_letters_map_back_to_the_spoken_text(self):
        spoken = 'Мой до́м — йод.'
        self.assertEqual([{'start': 0.0, 'end': 1.15, 'text': spoken}],
                         segment_captions({'duration': 2, 'spoken': spoken, 'cues': word_cues(spoken, ['Мой', 'дом', 'йод'])}))

    def test_unmatched_cues_are_grouped_from_their_own_text(self):
        cues = [{'text': 'Hello', 'start': 0, 'end': .5}, {'text': 'world', 'start': .5, 'end': 1}]
        self.assertEqual([{'start': 0, 'end': 1, 'text': 'Hello world'}], segment_captions({'duration': 1, 'spoken': 'Other text', 'cues': cues}))

    def test_passage_captions_are_unchanged(self):
        segments = [{'duration': 1, 'spoken': 'Passage one.', 'cues': []}, {'duration': 2.5, 'spoken': 'Passage two.', 'cues': []}]
        self.assertEqual('WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nPassage one.\n\n2\n00:00:01.000 --> 00:00:03.500\nPassage two.\n', captions(segments))

    def test_word_lines_keep_offsets_and_numbering_after_passage_captions(self):
        segments = [{'duration': 1, 'spoken': 'Passage one.', 'cues': []},
                    {'duration': 3, 'spoken': DIALOGUE, 'cues': word_cues(DIALOGUE, ['Маша', 'открыла', 'книгу', 'Читай', 'сказал', 'Иван'])}]
        text = captions(segments)
        self.assertIn('\n2\n00:00:01.000 --> 00:00:02.150\nМаша открыла книгу.\n\n3\n00:00:02.200 --> 00:00:03.350\n— Читай, — сказал Иван.\n', text)


class StoredExports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        book = self.store.import_data(FB2, 'fixture.fb2')
        generation = self.store.create_generation(book['id'], {'device': 'cpu', 'pack_name': 'Verification'})
        # This suite protects historical export/caption/encoder behavior. New
        # musical-opening timelines are covered in test_opening_exports.py.
        legacy = {key: value for key, value in generation['config'].items() if key != 'opening'}
        self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(legacy), generation['id']))
        complete_audio(self.store, generation)
        self.generation = self.store.generation(generation['id'])
        self.gid = generation['id']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def assert_nothing_published(self):
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def align_first(self):
        words = ['Маша', 'открыла', 'книгу', 'Читай', 'сказал', 'Иван']
        cues = [{'text': word, 'start': index * .015, 'end': index * .015 + .012} for index, word in enumerate(words)]
        self.store.execute('UPDATE segments SET spoken=?,cues=? WHERE generation_id=? AND id=?',
                           (DIALOGUE, json.dumps(cues), self.gid, self.generation['segments'][0]['id']))

    def test_subtitle_export_groups_stored_word_cues(self):
        self.align_first()
        exported = build_export(self.store, self.gid, 'srt', None, self.root)
        text = (self.store.root / 'exports' / exported['file']).read_text('utf-8')
        self.assertTrue(text.startswith('1\n00:00:00,000 --> 00:00:00,042\nМаша открыла книгу.\n\n2\n00:00:00,045 --> 00:00:00,087\n— Читай, — сказал Иван.\n'), text[:200])
        self.assertNotIn('\nИван\n', text)

    def test_chapter_archive_subtitles_group_stored_word_cues(self):
        self.align_first()
        exported = build_export(self.store, self.gid, 'chapters', None, self.root)
        with zipfile.ZipFile(self.store.root / 'exports' / exported['file']) as archive:
            subtitles = [archive.read(name).decode() for name in archive.namelist() if name.endswith('.vtt')]
        self.assertTrue(any('\nМаша открыла книгу.\n' in text and '\n— Читай, — сказал Иван.\n' in text for text in subtitles))

    def test_concat_list_checks_the_stop_object_between_files(self):
        segments = self.generation['segments']
        self.assertGreater(len(segments), 1)
        flag, calls = Flag(), []
        from rimsky import export
        original = export.file_digest
        def digest(path, *args):
            calls.append(path)
            flag.value = True
            return original(path, *args)
        listing = self.root / 'audio.txt'
        with patch('rimsky.export.file_digest', side_effect=digest), self.assertRaises(AppError) as raised:
            concat_list(listing, segments, self.store.root, flag)
        self.assertEqual((1, 'export_cancelled', CANCELLED), (len(calls), raised.exception.code, str(raised.exception)))
        self.assertFalse(listing.exists())

    def test_cancel_during_hashing_publishes_nothing_and_starts_no_encoder(self):
        flag = Flag()
        from rimsky import export
        original = export.file_digest
        def digest(path, *args):
            flag.value = True
            return original(path, *args)
        with patch('rimsky.export.file_digest', side_effect=digest), \
             patch('rimsky.export.run_encoder', side_effect=AssertionError('encoder must not start')), \
             self.assertRaises(AppError) as raised:
            build_export(self.store, self.gid, 'm4b', None, self.root, flag)
        self.assertEqual(('export_cancelled', CANCELLED), (raised.exception.code, str(raised.exception)))
        self.assert_nothing_published()

    def test_cancel_before_start_uses_the_neutral_message(self):
        flag = Flag()
        flag.value = True
        with self.assertRaises(AppError) as raised:
            build_export(self.store, self.gid, 'srt', None, self.root, flag)
        self.assertEqual(('export_cancelled', CANCELLED), (raised.exception.code, str(raised.exception)))
        self.assert_nothing_published()

    def test_encoder_receives_a_duration_scaled_timeout_and_probe_keeps_its_default(self):
        seen = []
        def encoder(command, stop=None, **options):
            seen.append(options.get('timeout'))
            return run_encoder(command, stop, **options)
        with patch('rimsky.export.run_encoder', side_effect=encoder), patch('rimsky.export.encoder_timeout', return_value=1234.5) as timeout:
            exported = build_export(self.store, self.gid, 'm4b', None, self.root, threading.Event())
        self.assertGreater(exported['size'], 128)
        self.assertEqual([1234.5, None], seen)
        self.assertEqual([segment['id'] for segment in self.generation['segments']], [segment['id'] for segment in timeout.call_args.args[0]])


class EncoderTimeouts(unittest.TestCase):
    def test_timeout_scales_with_expected_audio_within_bounds(self):
        hour = 3600 * 24000
        self.assertEqual(ENCODER_FLOOR, encoder_timeout([{'samples': hour, 'sample_rate': 24000}]))
        self.assertEqual(600 + 37 * 360, encoder_timeout([{'samples': 37 * hour, 'sample_rate': 24000}]))
        self.assertEqual(ENCODER_CEILING, encoder_timeout([{'samples': 100 * hour, 'sample_rate': 24000}]))

    def test_run_encoder_deadline_follows_the_given_timeout(self):
        command = [sys.executable, '-c', 'import time; time.sleep(20)']
        for stop in (threading.Event(), None):
            with self.subTest(stop=stop):
                started = time.monotonic()
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_encoder(command, stop, timeout=.3)
                self.assertLess(time.monotonic() - started, 10)


class IllustrationResumePlan(unittest.TestCase):
    setUp = fixture.ParagraphIllustrations.setUp
    start = fixture.ParagraphIllustrations.start
    finished = fixture.ParagraphIllustrations.finished

    def plan(self, run=None):
        history = self.app.dispatch('illustration_history', {'book_id': self.book['id']})
        return next(item for item in history if run is None or item['id'] == run['id'])['resume']

    def visual_file(self, run, index):
        return self.store.root / 'visuals' / self.store.rows('SELECT file FROM visuals WHERE id=?', (run['items'][index]['visual_id'],))[0]['file']

    def test_missing_saved_file_is_counted_and_matches_what_resume_sends(self):
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            if brief['source_id'] == self.sources[1]:
                raise AppError('controlled provider failure')
            return fixture.png()
        self.app.illustrations.renderer = render
        run = self.finished(self.start(self.sources[:3]))
        self.assertEqual(['ready', 'failed', 'pending'], [item['status'] for item in run['items']])
        self.visual_file(run, 0).unlink()
        plan = {'remaining': 3, 'retry_failed': True, 'local_only': False}
        self.assertEqual(plan, self.plan(run))
        self.assertEqual(plan, self.app.dispatch('illustration_status', {'id': run['id']})['resume'])
        self.assertEqual(['ready', 'failed', 'pending'], [item['status'] for item in self.app.illustrations.snapshot(run['id'])['items']])
        self.calls.clear()
        self.app.illustrations.renderer = lambda brief, style, stop, root: self.calls.append(brief['source_id']) or fixture.png()
        resumed = self.app.dispatch('illustration_resume', {'id': run['id'], 'consent': True, 'retry_failed': plan['retry_failed']})
        self.assertEqual('ready', self.finished(resumed)['status'])
        self.assertEqual(self.sources[:3], self.calls)

    def test_plan_is_local_only_when_every_paragraph_has_a_saved_image(self):
        run = self.finished(self.start(self.sources[:1]))
        self.app.dispatch('delete_visual', {'id': run['items'][0]['visual_id']})
        self.assertEqual({'remaining': 1, 'retry_failed': False, 'local_only': False}, self.plan(run))
        save_visual(self.store, self.book['id'], self.sources[0], fixture.png('green'))
        self.assertEqual({'remaining': 0, 'retry_failed': False, 'local_only': True}, self.plan(run))

    def test_plan_is_absent_for_ready_and_active_runs(self):
        ready = self.finished(self.start(self.sources[:1]))
        self.assertIsNone(self.plan(ready))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def render(brief, style, stop, root):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Provider fixture was not released')
            return fixture.png('blue')
        self.app.illustrations.renderer = render
        active = self.start(self.sources[1:3])
        self.assertTrue(entered.wait(2))
        self.assertIsNone(self.app.dispatch('illustration_status', {'id': active['id']})['resume'])
        self.assertIsNone(self.plan(active))
        release.set()
        self.assertEqual('ready', self.finished(active)['status'])

    def test_saved_files_are_hashed_again_only_after_they_change(self):
        colors = ['red', 'green', 'blue']
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id'])
            if len(self.calls) == 2:
                stop.set()
            return fixture.png(colors[len(self.calls) - 1])
        self.app.illustrations.renderer = render
        run = self.finished(self.start(self.sources[:3]))
        self.assertEqual(['ready', 'ready', 'pending'], [item['status'] for item in run['items']])
        from rimsky import illustrations
        original, hashed = illustrations.file_digest, []
        def digest(path, *args):
            hashed.append(path)
            return original(path, *args)
        with patch('rimsky.illustrations.file_digest', side_effect=digest):
            self.assertEqual({'remaining': 1, 'retry_failed': False, 'local_only': False}, self.plan(run))
            first = len(hashed)
            self.assertGreater(first, 0)
            self.assertEqual({'remaining': 1, 'retry_failed': False, 'local_only': False}, self.plan(run))
            self.assertEqual(first, len(hashed))
            self.visual_file(run, 0).write_bytes(b'corrupt image')
            self.assertEqual({'remaining': 2, 'retry_failed': False, 'local_only': False}, self.plan(run))
            self.assertEqual(first + 1, len(hashed))


class OversizedParagraphs(unittest.TestCase):
    setUp = fixture.ParagraphIllustrations.setUp
    finished = fixture.ParagraphIllustrations.finished

    def long_book(self):
        self.long_text = ('Слово ' * 3000).strip() + '.'
        book = self.store.import_data(epub_fixture({'OEBPS/a.xhtml': ('<html><body><h1>Глава</h1><p>Первый абзац.</p><p>' + self.long_text + '</p><p>Третий абзац.</p></body></html>').encode()}), 'long.epub')
        chapter = book['chapters'][0]
        return book, chapter, [block['id'] for block in chapter['blocks'] if block['kind'] == 'paragraph']

    def start(self, book, sources, **options):
        return self.app.dispatch('illustration_start', {'book_id': book['id'], 'source_ids': sources, 'style': 'ink', 'consent': True, **options})

    def test_chapter_run_skips_an_oversized_paragraph_with_a_recorded_reason(self):
        book, chapter, sources = self.long_book()
        run = self.finished(self.start(book, sources))
        excluded = [{'source_id': sources[1], 'chapter_id': chapter['id'], 'chapter': 'Глава', 'opening': ' '.join(['Слово'] * 8),
                     'characters': len(self.long_text), 'reason': SKIPPED_LONG}]
        self.assertEqual('ready', run['status'])
        self.assertEqual([sources[0], sources[2]], self.calls)
        self.assertEqual([sources[0], sources[2]], [item['source_id'] for item in run['items']])
        self.assertEqual(excluded, run['excluded'])
        self.assertEqual(excluded, self.app.dispatch('illustration_history', {'book_id': book['id']})[0]['excluded'])
        self.assertEqual('This paragraph exceeds the 16,000-character illustration limit and was skipped. No text was truncated.', SKIPPED_LONG)

    def test_selection_of_only_an_oversized_paragraph_is_refused_by_name(self):
        book, chapter, sources = self.long_book()
        with self.assertRaises(AppError) as raised:
            self.start(book, sources[1:2])
        opening = ' '.join(['Слово'] * 8)
        self.assertEqual(('illustration_paragraph_too_long', f'The paragraph “{opening}…” in “Глава” exceeds the 16,000-character illustration limit. No text was truncated.'),
                         (raised.exception.code, str(raised.exception)))
        self.assertEqual({'source_id': sources[1], 'chapter_id': chapter['id'], 'chapter': 'Глава', 'opening': opening,
                          'characters': len(self.long_text), 'limit': 16000}, raised.exception.details)
        self.assertEqual([], self.store.rows('SELECT id FROM illustration_runs WHERE book_id=?', (book['id'],)))
        self.assertEqual([], self.calls)

    def test_new_version_still_needs_exactly_one_plannable_paragraph(self):
        book, chapter, sources = self.long_book()
        with self.assertRaisesRegex(AppError, 'one paragraph'):
            self.start(book, sources[:2], replace_existing=True)
        self.assertEqual([], self.calls)

    def test_plan_helper_names_the_paragraph_instead_of_skipping(self):
        book, chapter, sources = self.long_book()
        with self.assertRaises(AppError) as raised:
            paragraph_plan(book, sources)
        self.assertEqual(('illustration_paragraph_too_long', sources[1]), (raised.exception.code, raised.exception.details['source_id']))
        self.assertEqual([sources[0], sources[2]], [item['source_id'] for item in paragraph_plan(book, [sources[0], sources[2]])])


class CodexDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(os.path.realpath(self.temp.name))
        self.home = self.root / 'home'
        self.brew = self.root / 'brew'
        self.empty = self.root / 'empty'
        for folder in (self.home, self.brew, self.empty):
            folder.mkdir()
        environment = patch.dict(os.environ, {'PATH': str(self.empty)})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('APPDATA', 'LOCALAPPDATA'):
            os.environ.pop(name, None)
        for patcher in (patch.object(Path, 'home', return_value=self.home), patch('rimsky.illustrations.CODEX_SYSTEM_DIRS', (str(self.brew),))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def codex(self, folder, body='#!/bin/sh\nexit 0\n', name='codex'):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(body)
        path.chmod(0o755)
        return str(path)

    def test_path_is_searched_before_install_folders(self):
        on_path = self.codex(self.root / 'path-bin')
        self.codex(self.home / '.local/bin')
        os.environ['PATH'] = str(self.root / 'path-bin')
        self.assertEqual(on_path, codex_executable())

    @unittest.skipIf(os.name == 'nt', 'POSIX install-folder order')
    def test_gui_path_finds_common_install_folders_in_order(self):
        local = self.codex(self.home / '.local/bin')
        self.assertEqual(local, codex_executable())
        self.assertEqual(self.codex(self.brew), codex_executable())
        for name in ('.npm-global/bin', '.volta/bin', '.bun/bin'):
            with self.subTest(folder=name):
                other = tempfile.TemporaryDirectory()
                self.addCleanup(other.cleanup)
                home = Path(os.path.realpath(other.name))
                with patch.object(Path, 'home', return_value=home), patch('rimsky.illustrations.CODEX_SYSTEM_DIRS', ()):
                    self.assertEqual(self.codex(home / name), codex_executable())

    @unittest.skipIf(os.name == 'nt', 'POSIX nvm layout')
    def test_newest_nvm_node_is_chosen_by_version_number(self):
        self.codex(self.home / '.nvm/versions/node/v9.11.2/bin')
        newest = self.codex(self.home / '.nvm/versions/node/v20.3.0/bin')
        self.assertEqual(newest, codex_executable())

    def test_windows_npm_folder_is_searched(self):
        os.environ['APPDATA'] = str(self.root / 'AppData')
        self.assertEqual(self.codex(self.root / 'AppData' / 'npm'), codex_executable())

    def test_windows_searches_only_absolute_user_folders(self):
        self.codex(self.brew)
        nvm = Path(self.codex(self.home / '.nvm/versions/node/v20.3.0/bin')).parent
        local = self.root / 'Local'
        os.environ.update({'APPDATA': 'Roaming', 'LOCALAPPDATA': str(local)})
        profile = [self.home / name for name in ('.local/bin', '.npm-global/bin', '.volta/bin', '.bun/bin')]
        appdata = [local / 'pnpm', local / 'Volta' / 'bin']
        self.assertEqual([*profile, *appdata], codex_directories(windows=True))
        self.assertEqual([self.brew, *profile, nvm, *appdata], codex_directories(windows=False))
        with patch('rimsky.illustrations.codex_directories', lambda: codex_directories(windows=True)), self.assertRaises(AppError):
            codex_executable()
        with patch.object(Path, 'home', return_value=Path('home')):
            self.assertEqual(appdata, codex_directories(windows=True))

    def test_windows_folders_without_a_drive_are_never_searched(self):
        class WindowsPath(PureWindowsPath):
            profile = '\\Users\\victim'

            @classmethod
            def home(cls):
                return cls(cls.profile)
        os.environ.update({'APPDATA': 'C:\\Users\\victim\\AppData\\Roaming', 'LOCALAPPDATA': '\\Users\\victim\\AppData\\Local'})
        with patch('rimsky.illustrations.Path', WindowsPath):
            self.assertEqual(['C:\\Users\\victim\\AppData\\Roaming\\npm'], [str(path) for path in codex_directories(windows=True)])
            WindowsPath.profile = 'C:\\Users\\victim'
            self.assertEqual(['C:\\Users\\victim\\.local\\bin', 'C:\\Users\\victim\\.npm-global\\bin', 'C:\\Users\\victim\\.volta\\bin',
                              'C:\\Users\\victim\\.bun\\bin', 'C:\\Users\\victim\\AppData\\Roaming\\npm'], [str(path) for path in codex_directories(windows=True)])

    def test_missing_codex_keeps_the_existing_error(self):
        with self.assertRaises(AppError) as raised:
            codex_executable()
        self.assertEqual('Codex CLI is not installed or is not on PATH. No automatic installation or API fallback is used.', str(raised.exception))

    @unittest.skipIf(os.name == 'nt', 'POSIX PATH syntax and install-folder order')
    def test_child_environment_appends_install_folders_and_drops_api_keys(self):
        executable = self.codex(self.home / '.nvm/versions/node/v20.3.0/bin')
        os.environ.update({'PATH': '/usr/bin:/bin', 'OPENAI_API_KEY': 'secret', 'CODEX_API_KEY': 'secret', 'AZURE_OPENAI_API_KEY': 'secret'})
        environment = codex_environment(executable)
        entries = environment['PATH'].split(os.pathsep)
        self.assertEqual(['/usr/bin', '/bin', str(Path(executable).parent), str(self.brew), str(self.home / '.local/bin')], entries[:5])
        self.assertEqual(len(entries), len(set(entries)))
        self.assertFalse({'OPENAI_API_KEY', 'CODEX_API_KEY', 'AZURE_OPENAI_API_KEY'} & set(environment))

    @unittest.skipIf(os.name == 'nt', 'POSIX npm launcher layout')
    def test_npm_launcher_finds_node_beside_codex_under_a_gui_path(self):
        image = base64.b64encode(fixture.png()).decode()
        folder = self.home / '.nvm/versions/node/v20.3.0/bin'
        self.codex(folder, f'''#!{sys.executable}
import base64,json,pathlib
brief=json.loads(pathlib.Path('paragraph.json').read_text())
pathlib.Path('result.png').write_bytes(base64.b64decode({image!r}))
pathlib.Path('result.json').write_text(json.dumps({{'source_id':brief['source_id'],'status':'generated','file':'result.png','reason':''}}))
''', 'node')
        self.codex(folder, '#!/usr/bin/env node\n')
        os.environ['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        output = render_codex({'source_id': 'paragraph', 'passage': 'Synthetic verification only.'}, 'ink', threading.Event(), self.root, executable=codex_executable())
        self.assertEqual(fixture.png(), output)


class ImageFormats(unittest.TestCase):
    setUp = fixture.ParagraphIllustrations.setUp

    def ghostscript(self):
        calls = []
        def blocked(*args, **kwargs):
            calls.append(args)
            raise OSError('Ghostscript blocked by the test')
        patcher = patch.object(PIL.EpsImagePlugin, 'Ghostscript', side_effect=blocked)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_postscript_and_tiff_uploads_are_refused_before_decoding(self):
        calls = self.ghostscript()
        for data in (EPS, encoded('TIFF')):
            with self.subTest(size=len(data)), self.assertRaisesRegex(AppError, 'Choose a valid PNG, JPEG, or WebP image'):
                save_visual(self.store, self.book['id'], self.sources[0], data)
        self.assertEqual([], calls)
        self.assertEqual([], self.store.visuals(self.book['id']))
        self.assertEqual([], [path for path in (self.store.root / 'visuals').iterdir() if path.is_file()])

    def test_allowed_raster_uploads_are_saved_as_png(self):
        for fmt in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP'):
            with self.subTest(fmt=fmt):
                visual = save_visual(self.store, self.book['id'], self.sources[0], encoded(fmt))
                with Image.open(self.store.root / 'visuals' / visual['file']) as saved:
                    self.assertEqual(('PNG', (12, 8)), (saved.format, saved.size))

    def test_preview_refuses_a_non_raster_file(self):
        calls = self.ghostscript()
        path = self.store.root / 'visuals' / 'postscript.png'
        path.write_bytes(EPS)
        with self.assertRaisesRegex(AppError, 'Saved illustration cannot be decoded.'):
            image_preview(path, self.root / 'previews')
        self.assertEqual([], calls)
        bitmap = self.store.root / 'visuals' / 'bitmap.gif'
        bitmap.write_bytes(encoded('GIF'))
        with Image.open(image_preview(bitmap, self.root / 'previews')) as preview:
            self.assertEqual(('PNG', (12, 8)), (preview.format, preview.size))


class ImagesApiTls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('OpenSSL CLI is unavailable')
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True, timeout=60)
        class Provider(BaseHTTPRequestHandler):
            status = 200
            def log_message(self, *args):
                return
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                raw = json.dumps({'data': [{'b64_json': base64.b64encode(b'image-bytes').decode()}]}).encode()
                self.send_response(type(self).status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        cls.provider = Provider
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.cert), str(key))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.temp.cleanup()

    def setUp(self):
        self.provider.status = 200

    def request(self, port=None):
        return urllib.request.Request(f'https://127.0.0.1:{port or self.server.server_port}/v1/images/generations', data=b'{}', method='POST')

    def certifi(self, path):
        return patch.dict(sys.modules, {'certifi': types.SimpleNamespace(where=lambda: str(path))})

    def test_untrusted_certificate_reports_a_secure_connection_failure(self):
        with patch.dict(sys.modules, {'certifi': None}), self.assertRaises(AppError) as raised:
            fetch_visual(self.request())
        self.assertEqual(CERTIFICATE, str(raised.exception))

    def test_certifi_bundle_is_trusted_for_image_requests(self):
        with self.certifi(self.cert):
            self.assertEqual(b'image-bytes', fetch_visual(self.request()))

    def test_provider_http_error_keeps_its_message(self):
        self.provider.status = 500
        with self.certifi(self.cert), self.assertRaises(AppError) as raised:
            fetch_visual(self.request())
        self.assertEqual('Image provider returned HTTP 500. The request is not retried automatically.', str(raised.exception))

    def test_unreadable_certifi_bundle_falls_back_to_the_default_store(self):
        with self.certifi(Path(self.temp.name) / 'missing.pem'):
            context = https_context()
            self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
            with self.assertRaises(AppError) as raised:
                fetch_visual(self.request())
        self.assertEqual(CERTIFICATE, str(raised.exception))

    def test_refused_connection_keeps_the_generic_message(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with self.certifi(self.cert), self.assertRaises(AppError) as raised:
            fetch_visual(self.request(port))
        self.assertEqual('Image preparation failed. No automatic paid retry was made.', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
