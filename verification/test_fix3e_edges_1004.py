from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky import books
from rimsky.books import import_book
from rimsky.illustrations import codex_path
from rimsky.preparation import default_chapters, speech_sources
from test_epub_preparation import make_epub, ncx_point
from test_import_fixes_1004 import documents, fb2, page

NAV = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{toc}</ol></nav>'
       '<nav epub:type="landmarks"><ol>{landmarks}</ol></nav></body></html>')
RUSSIAN = 'Мама мыла раму, а папа читал газету и всё было хорошо. Вечером они долго гуляли по тихому парку.'
UKRAINIAN = 'Ще не вмерла України і слава, і воля, ще нам, браття молодії, усміхнеться доля. Згинуть наші вороженьки, як роса на сонці.'


class Fix3eEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data if isinstance(data, bytes) else data.encode(), name, self.root / 'books')

    def texts(self, book):
        return [block['text'] for chapter in book['chapters'] for block in chapter['blocks']]

    def roles(self, book):
        return [(chapter['title'], chapter['role'], chapter['default_narration']) for chapter in book['chapters']]

    def test_drop_cap_wrapper_texts_are_computed_once_per_document(self):
        letters = ''.join(f'<p>{"ЯАИОУВСК"[index % 8]}</p><p>Обычный абзац номер {index}.</p>' for index in range(60))
        with patch('rimsky.books.letter_texts', side_effect=books.letter_texts) as computed:
            book = self.read(make_epub(f'<div><div>{letters}</div></div>'))
        self.assertEqual(1, computed.call_count)
        self.assertEqual(120, len(self.texts(book)))
        with patch('rimsky.books.letter_texts', side_effect=books.letter_texts) as computed:
            hinted = self.read(make_epub(''.join(f'<p class="dropcap">Т</p><p>ак началось утро {index}.</p>' for index in range(30))))
        self.assertEqual(0, computed.call_count)
        self.assertEqual([f'Так началось утро {index}.' for index in range(30)], self.texts(hinted))
        for body in ('<div class="dropcap"><p>Т</p><p> </p></div><p>ак началось утро.</p>', '<div class="drop"><a id="start"/><p>Т</p></div><p>ак началось утро.</p>',
                     '<section class="lettrine"><div><p>Т</p></div></section><p>ак началось утро.</p>'):
            with self.subTest(body=body):
                self.assertEqual(['Так началось утро.'], self.texts(self.read(make_epub(body))))
        self.assertEqual(['Т', 'Другое.', 'ак началось утро.'], self.texts(self.read(make_epub('<div class="dropcap"><p>Т</p><p>Другое.</p></div><p>ак началось утро.</p>'))))

    def test_drop_cap_merge_keeps_the_source_space_and_ignores_css_keywords(self):
        for body, text in (('<div class="dropcap">В</div><p> тот вечер мы долго молчали.</p>', 'В тот вечер мы долго молчали.'),
                           ('<p class="first-letter">Я</p><p> помню <em>чудное</em> мгновенье.</p>', 'Я помню чудное мгновенье.'),
                           ('<p class="dropcap">В</p><p><span> тот</span> вечер.</p>', 'В тот вечер.'),
                           ('<p class="dropcap">Т</p><p>ак началось утро.</p>', 'Так началось утро.'),
                           ('<p style="initial-letter: 3">Т</p><p>ак началось утро.</p>', 'Так началось утро.'),
                           ('<p class="initial-letter">Т</p><p>ак началось утро.</p>', 'Так началось утро.')):
            with self.subTest(body=body):
                book = self.read(make_epub(body))
                self.assertEqual([text], self.texts(book))
                self.assertEqual([text], [source['text'] for source in speech_sources(book, default_chapters(book))])
        self.assertEqual('<p>В тот вечер мы долго молчали.</p>', self.read(make_epub('<div class="dropcap">В</div><p> тот вечер мы долго молчали.</p>'))['chapters'][0]['blocks'][0]['html'])
        for body in ('<p style="text-indent: initial">Я</p><p>помню чудное мгновенье.</p>', '<p class="para-initial">А</p><p>потом пришла зима.</p>',
                     '<p style="filter: drop-shadow(1px 1px 2px)">Я</p><p>помню чудное мгновенье.</p>', '<p class="initially">А</p><p>потом.</p>'):
            with self.subTest(body=body):
                self.assertEqual(2, len(self.texts(self.read(make_epub(body)))))

    def test_short_typed_note_markers_are_dropped_and_typed_words_stay(self):
        markers = ['&lt;1&gt;', '1.1', '12a', '[12a]', '1,2', '1–3', 'a.', 'ii.', 'n1', '(i)', 'Ⅳ', '*a', '1)', '¹⁾', 'xiv', '[viii]', 'note1', '※']
        body = ''.join(f'<p>Слово{index}<a epub:type="noteref" href="#n{index}">{marker}</a> дальше.</p>' for index, marker in enumerate(markers))
        body += ''.join(f'<p>Он взял <a epub:type="noteref" href="#n0">{word}</a> дальше.</p>' for word in ('кирку', 'mild', 'прим. 1', '[прим. 1]', 'Примечание'))
        body += '<p>Слово<a href="#n0">12a</a> дальше.</p>'
        notes = ''.join(f'<aside epub:type="footnote" id="n{index}"><p>Сноска {index}.</p></aside>' for index in range(len(markers)))
        texts = [text for text in self.texts(self.read(make_epub(body + notes))) if not text.startswith('Сноска')]
        self.assertEqual([f'Слово{index} дальше.' for index in range(len(markers))]
                         + [f'Он взял {word} дальше.' for word in ('кирку', 'mild', 'прим. 1', '[прим. 1]', 'Примечание')] + ['Слово12a дальше.'], texts)
        cyrillic = ['а', '[б]', 'в)', 'α', '1a', '2б', 'Ⅻ']
        fbody = ''.join(f'<p>Слово{index}<a l:href="#n{index}" type="note">{marker}</a> дальше.</p>' for index, marker in enumerate(cyrillic))
        fbody += '<p>Он взял <a l:href="#n0" type="note">кирку</a> и ушёл.</p>'
        fnotes = '<body name="notes">' + ''.join(f'<section id="n{index}"><p>Сноска {index}.</p></section>' for index in range(len(cyrillic))) + '</body>'
        book = self.read(fb2(f'<section>{fbody}</section>', fnotes), 'notes.fb2')
        self.assertEqual([f'Слово{index} дальше.' for index in range(len(cyrillic))] + ['Он взял кирку и ушёл.'],
                         [block['text'] for block in book['chapters'][0]['blocks']])

    def test_landmark_fragment_that_wraps_the_whole_document_applies_to_it(self):
        ncx = ncx_point('Глава 1', 'c1').replace('story.xhtml#c1', 'story.xhtml')
        manifest, spine = documents('toc')
        for label, toc_page in (('wrapper div', page('<div id="toc"><h1>Содержание</h1><p><a href="story.xhtml">Глава 1</a></p><p><a href="story.xhtml">Глава 2</a></p></div>')),
                                ('body id', page('<h1>Содержание</h1><p><a href="story.xhtml">Глава 1</a></p>', attrs=' id="toc"')),
                                ('whole nav', page('<nav id="toc"><h1>Содержание</h1><ol><li><a href="story.xhtml">Глава 1</a></li></ol></nav>'))):
            with self.subTest(label):
                book = self.read(make_epub('<h1>Глава 1</h1><p>— Привет, — сказал он.</p>', ncx=ncx, manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                           guide='<guide><reference type="toc" title="Содержание" href="toc.xhtml#toc"/></guide>', extra={'EPUB/toc.xhtml': toc_page}))
                self.assertEqual(['navigation', 'body'], [role for _, role, _ in self.roles(book)])
                self.assertEqual([book['chapters'][1]['id']], default_chapters(book))
        manifest, spine = documents('cover')
        covered = self.read(make_epub('<h1>Глава 1</h1><p>Текст.</p>', nav='', manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                      extra={'EPUB/nav.xhtml': NAV.format(toc='<li><a href="story.xhtml">Глава 1</a></li>',
                                                                          landmarks='<li><a epub:type="cover" href="cover.xhtml#cover">Обложка</a></li>').encode(),
                                             'EPUB/cover.xhtml': page('<div id="cover"><p>Книга</p></div>')}))
        self.assertEqual(['cover', 'body'], [role for _, role, _ in self.roles(covered)])
        manifest, spine = documents('title')
        inline = ('<nav id="toc"><h2>Содержание</h2><ol><li><a href="#c1">Глава 1</a></li></ol></nav>'
                  '<div id="top"><h1 id="c1">Глава 1</h1><p>— Привет, — сказал он.</p></div><h1 id="c2">Глава 2</h1><p>Конец.</p>')
        for landmark in ('<li><a epub:type="toc" href="story.xhtml#toc">Содержание</a></li>', '<li><a epub:type="cover" href="story.xhtml#c1">Обложка</a></li>',
                         '<li><a epub:type="cover" href="story.xhtml#top">Обложка</a></li>'):
            with self.subTest(landmark=landmark):
                book = self.read(make_epub(inline, nav='', manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                           extra={'EPUB/title.xhtml': page('<p>Автор</p><p>Название</p>'),
                                                  'EPUB/nav.xhtml': NAV.format(toc='<li><a href="story.xhtml#c1">Глава 1</a></li><li><a href="story.xhtml#c2">Глава 2</a></li>',
                                                                               landmarks=landmark).encode()}))
                self.assertTrue(all(role == 'body' and narrated for _, role, narrated in self.roles(book)))
        manifest, spine = documents('ch1', 'ch2')
        top = self.read(make_epub(nav='', manifest=manifest, spine=spine,
                                  extra={'EPUB/ch1.xhtml': page('<h1 id="top">Глава 1</h1><p>— Привет, — сказал он.</p>'), 'EPUB/ch2.xhtml': page('<h1>Глава 2</h1><p>Конец.</p>'),
                                         'EPUB/nav.xhtml': NAV.format(toc='<li><a href="ch1.xhtml">Глава 1</a></li><li><a href="ch2.xhtml">Глава 2</a></li>',
                                                                      landmarks='<li><a epub:type="cover" href="ch1.xhtml#top">Cover</a></li>').encode()}))
        self.assertEqual([('Глава 1', 'body', True), ('Глава 2', 'body', True)], self.roles(top))

    def test_undeclared_koi8u_ukrainian_fb2_is_read_without_mojibake(self):
        def document(text, title='Книга', language='ru'):
            return fb2(f'<section><title><p>{title}</p></title><p>{text}</p></section>', declaration='',
                       info=f'<author><first-name>Павло</first-name></author><book-title>{title}</book-title><lang>{language}</lang>')
        book = self.read(document(UKRAINIAN, 'Гімн', 'uk').encode('koi8_u'), 'anthem.fb2')
        self.assertEqual(('Гімн', ['Гімн', UKRAINIAN]), (book['title'], self.texts(book)))
        for label, (data, text) in {'koi8-r russian': (document(RUSSIAN).encode('koi8_r'), RUSSIAN),
                                    'koi8-u russian': (document(RUSSIAN).encode('koi8_u'), RUSSIAN),
                                    'windows-1251 ukrainian': (document(UKRAINIAN).encode('cp1251'), UKRAINIAN),
                                    'windows-1251 soft hyphen': (document(RUSSIAN + ' Пере\u00adход.').encode('cp1251'), RUSSIAN + ' Переход.')}.items():
            with self.subTest(label):
                self.assertEqual(['Книга', text], self.texts(self.read(data, 'legacy.fb2')))

    def test_fb2_short_chapters_in_a_spacing_book_have_no_scene_breaks(self):
        sections = ''.join(f'<section><title><p>Глава {chapter + 1}</p></title>'
                           + '<empty-line/>'.join(f'<p>Абзац {chapter}-{index} с обычным текстом.</p>' for index in range(8)) + '</section>' for chapter in range(60))
        book = self.read(fb2(sections), 'short.fb2')
        self.assertEqual(0, sum(block['kind'] == 'separator' for chapter in book['chapters'] for block in chapter['blocks']))
        self.assertEqual(60, len({source['scene_id'] for source in speech_sources(book, default_chapters(book))}))
        mixed = self.read(fb2(sections + '<section><p>А.</p><p>Б.</p><empty-line/><p>В.</p><p>Г.</p></section>'), 'mixed.fb2')
        self.assertEqual([0] * 60 + [1], [[block['kind'] for block in chapter['blocks']].count('separator') for chapter in mixed['chapters']])
        doubled = self.read(fb2(sections.replace('<empty-line/><p>Абзац 3-4', '<empty-line/><empty-line/><p>Абзац 3-4')), 'scene.fb2')
        self.assertEqual(['c0004'], [chapter['id'] for chapter in doubled['chapters'] if any(block['kind'] == 'separator' for block in chapter['blocks'])])


class WindowsCodexLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        environment = patch.dict(os.environ, {'PATH': ''})
        environment.start()
        self.addCleanup(environment.stop)
        directories = patch('rimsky.illustrations.codex_directories', lambda: [])
        directories.start()
        self.addCleanup(directories.stop)
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)

    def codex(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'codex'
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return str(path)

    def test_windows_lookup_names_an_absolute_folder_in_every_search(self):
        planted = self.root / 'Downloads'
        self.codex(planted)
        self.codex(planted / 'bin')
        tools = self.codex(self.root / 'Tools')
        os.chdir(planted)
        os.environ['PATH'] = os.pathsep.join(['', '.', 'bin', str(self.root / 'Tools')])
        real = shutil.which
        with patch('shutil.which', side_effect=lambda command, *args, **kwargs: real(command, *args, **kwargs)) as which:
            self.assertEqual(tools, codex_path(windows=True))
            os.environ['PATH'] = os.pathsep.join(['', '.', 'bin'])
            self.assertIsNone(codex_path(windows=True))
        commands = [call.args[0] for call in which.call_args_list]
        self.assertEqual([str(self.root / 'Tools' / 'codex')], commands)
        self.assertTrue(all(os.path.isabs(os.path.dirname(command)) and not call.args[1:] and not call.kwargs for command, call in zip(commands, which.call_args_list)))

    def test_windows_drive_relative_path_entries_are_skipped(self):
        class WindowsPath(PureWindowsPath):
            pass
        searched = []
        with patch('rimsky.illustrations.Path', WindowsPath), patch.object(os, 'pathsep', ';'), \
                patch('shutil.which', side_effect=lambda command, *args, **kwargs: searched.append(command)):
            os.environ['PATH'] = ';'.join(['C:\\Tools', '\\opt\\bin', 'C:rel', '.', '', '\\\\server\\share\\bin', 'D:\\Node'])
            self.assertIsNone(codex_path(windows=True))
        self.assertEqual(['C:\\Tools\\codex', '\\\\server\\share\\bin\\codex', 'D:\\Node\\codex'], searched)

    def test_other_platforms_keep_the_path_lookup(self):
        tools = self.codex(self.root / 'Tools')
        os.environ['PATH'] = str(self.root / 'Tools')
        real = shutil.which
        with patch('shutil.which', side_effect=lambda command, *args, **kwargs: real(command, *args, **kwargs)) as which:
            self.assertEqual(tools, codex_path(windows=False))
        self.assertEqual('codex', which.call_args_list[0].args[0])


if __name__ == '__main__':
    unittest.main()
