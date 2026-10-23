from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from rimsky.books import import_book
from rimsky.preparation import default_chapters, speech_language, speech_sources
from rimsky.util import AppError
from test_epub_preparation import make_epub
from test_import_fixes_1004 import documents, fb2, page

NAV = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{toc}</ol></nav>'
       '<nav epub:type="landmarks"><ol>{landmarks}</ol></nav></body></html>')
RUSSIAN = 'Мама мыла раму, а папа читал газету и всё было хорошо. Вечером они долго гуляли по тихому парку.'


def opf(language, documents='<item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>', spine='<itemref idref="story"/>'):
    return ('<package version="3.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Т</dc:title>'
            f'<dc:language>{language}</dc:language></metadata><manifest>{documents}</manifest><spine>{spine}</spine></package>').encode()


class Fix3Import(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data if isinstance(data, bytes) else data.encode(), name, self.root / 'books')

    def texts(self, book):
        return [block['text'] for chapter in book['chapters'] for block in chapter['blocks']]

    def roles(self, book):
        return [(chapter['title'], chapter['role'], chapter['default_narration']) for chapter in book['chapters']]

    def test_one_letter_lines_join_the_next_paragraph_only_with_a_drop_cap_hint(self):
        verse = self.read(fb2('<section><poem><stanza><v>Я</v><v>волком бы</v><v>выгрыз</v></stanza></poem></section>'), 'verse.fb2')
        self.assertEqual(['Я', 'волком бы', 'выгрыз'], self.texts(verse))
        self.assertEqual('<p>Я</p>', verse['chapters'][0]['blocks'][0]['html'])
        for body in ('<p>Я</p><p>не знаю.</p>', '<p>I</p><p>am the one.</p>', '<p>О</p><p>как ты прекрасна, ночь.</p>',
                     '<blockquote><p>Ж</p></blockquote><p>изнь.</p>', '<p class="verse">А</p><p>б) второе.</p>'):
            with self.subTest(body=body):
                self.assertEqual(2, len(self.texts(self.read(make_epub(body)))))
        for body in ('<p class="initial">Т</p><p>ак началось утро.</p>', '<div><span class="drop-cap">Т</span></div><p>ак началось утро.</p>',
                     '<div class="lettrine"><p>Т</p></div><p>ак началось утро.</p>', '<p style="float: left; font-size: 3em">Т</p><p>ак началось утро.</p>'):
            with self.subTest(body=body):
                book = self.read(make_epub(body))
                self.assertEqual(['Так началось утро.'], self.texts(book))
                self.assertEqual('<p>Так началось утро.</p>', book['chapters'][0]['blocks'][0]['html'])
        tail = self.read(make_epub('<p class="dropcap">Т</p><p>ак началось утро.</p><p>и тогда всё началось.</p>'))
        self.assertEqual(['Так началось утро.', 'и тогда всё началось.'], self.texts(tail))

    def test_landmark_and_guide_entries_with_a_fragment_keep_the_document_role(self):
        body = ('<nav id="toc"><h2>Содержание</h2><ol><li><a href="#c1">Глава 1</a></li><li><a href="#c2">Глава 2</a></li></ol></nav>'
                '<h1 id="c1">Глава 1</h1><p>— Привет, — сказал он.</p><h1 id="c2">Глава 2</h1><p>Конец.</p>')
        toc = '<li><a href="story.xhtml#c1">Глава 1</a></li><li><a href="story.xhtml#c2">Глава 2</a></li>'
        cases = {'landmark toc': {'nav': '', 'extra': {'EPUB/nav.xhtml': NAV.format(toc=toc, landmarks='<li><a epub:type="toc" href="story.xhtml#toc">Содержание</a></li>').encode()}},
                 'landmark cover': {'nav': '', 'extra': {'EPUB/nav.xhtml': NAV.format(toc=toc, landmarks='<li><a epub:type="cover" href="story.xhtml#c1">Обложка</a></li>').encode()}},
                 'guide toc': {'guide': '<guide><reference type="toc" href="story.xhtml#toc" title="TOC"/></guide>'},
                 'guide title page': {'guide': '<guide><reference type="title-page" href="story.xhtml#toc"/></guide>'}}
        for label, options in cases.items():
            with self.subTest(label):
                book = self.read(make_epub(body, **options))
                self.assertEqual([('body', True)] * 3, [(role, narrated) for _, role, narrated in self.roles(book)])
                self.assertEqual(3, len(default_chapters(book)))
        manifest, spine = documents('contents', 'cover')
        whole = self.read(make_epub('<h1>Глава 1</h1><p>Текст.</p>', nav='', manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                    extra={'EPUB/nav.xhtml': NAV.format(toc='<li><a href="story.xhtml">Глава 1</a></li>',
                                                                        landmarks='<li><a epub:type="toc" href="contents.xhtml">Содержание</a></li>'
                                                                                  '<li><a epub:type="cover" href="cover.xhtml">Обложка</a></li>').encode(),
                                           'EPUB/contents.xhtml': page('<p>Глава 1 ... 3</p>'), 'EPUB/cover.xhtml': page('<p>Книга</p>')}))
        self.assertEqual(['navigation', 'cover', 'body'], [role for _, role, _ in self.roles(whole)])
        alone = self.read(make_epub(body, nav='', extra={'EPUB/nav.xhtml': NAV.format(toc=toc, landmarks='<li><a epub:type="toc" href="story.xhtml">Содержание</a></li>').encode()}))
        self.assertEqual([('body', True)] * 3, [(role, narrated) for _, role, narrated in self.roles(alone)])

    def test_restore_guard_ignores_chapters_without_narratable_text(self):
        image = io.BytesIO()
        Image.new('RGB', (40, 60), 'red').save(image, 'PNG')
        manifest, spine = documents('pic')
        book = self.read(make_epub('<h1>Книга</h1><p>Автор</p><h2>Глава 1</h2><p>— Привет, — сказал он.</p><h2>Глава 2</h2><p>Конец.</p>',
                                   manifest=manifest, spine=spine + '<itemref idref="story"/>', guide='<guide><reference type="title-page" href="story.xhtml"/></guide>',
                                   extra={'EPUB/pic.xhtml': page('<div><img src="pic.png" alt=""/></div>'), 'EPUB/pic.png': image.getvalue()}))
        self.assertEqual(['body'] * 4, [role for _, role, _ in self.roles(book)])
        self.assertEqual([chapter['id'] for chapter in book['chapters'][1:]], default_chapters(book))
        kept = self.read(make_epub('<h1>Глава 1</h1><p>Текст.</p>', manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                   guide='<guide><reference type="title-page" href="pic.xhtml"/></guide>',
                                   extra={'EPUB/pic.xhtml': page('<p>Автор</p><p>Название</p>')}))
        self.assertEqual([('auxiliary', False), ('body', True)], [(role, narrated) for _, role, narrated in self.roles(kept)])

    def test_fb2_without_encoding_declaration_imports_only_plausible_cyrillic(self):
        def document(text, info=None):
            return fb2(f'<section><p>{text}</p></section>', declaration='', info=info)
        accepted = {'windows-1251': (RUSSIAN + ' «Правда» — №1.', 'cp1251'), 'koi8-r': (RUSSIAN, 'koi8-r'),
                    'ukrainian windows-1251': ('Ще не вмерла України і слава, і воля, ще нам, браття молодії, усміхнеться доля.', 'cp1251')}
        for label, (text, encoding) in accepted.items():
            with self.subTest(label):
                self.assertEqual(text, self.texts(self.read(document(text).encode(encoding), 'legacy.fb2'))[0])
        latin = '<author><first-name>Jean</first-name></author><book-title>Livre</book-title>'
        rejected = {'windows-1251 with an undefined byte': document(RUSSIAN).encode('cp1251').replace('раму'.encode('cp1251'), 'раму'.encode('cp1251') + b'\x98'),
                    'french windows-1252': document("L'élève a été très heureux à Noël, où il a reçu un cadeau.", latin).encode('cp1252'),
                    'german windows-1252': document('Größere Übungen für Äpfel und Öl, schön müde.', latin).encode('cp1252'),
                    'mac cyrillic': document(RUSSIAN).encode('mac_cyrillic'), 'cp866': document(RUSSIAN).encode('cp866'),
                    'iso-8859-5': document(RUSSIAN).encode('iso8859_5')}
        for label, data in rejected.items():
            with self.subTest(label), self.assertRaises(AppError) as caught:
                self.read(data, 'legacy.fb2')
            self.assertEqual(('invalid_book_encoding', 'The book text encoding could not be read.'), (caught.exception.code, str(caught.exception)))

    def test_endnote_list_numbers_stay_visible(self):
        notes = page('<section epub:type="endnotes" role="doc-endnotes"><h2>Endnotes</h2><ol>'
                     '<li id="note-1" epub:type="endnote" role="doc-endnote"><p>First note text. <a href="story.xhtml#r1" epub:type="backlink" role="doc-backlink">↩</a></p></li>'
                     '<li id="note-2" epub:type="endnote" role="doc-endnote"><p>Second note text.</p></li></ol></section>')
        manifest, spine = documents('endnotes')
        book = self.read(make_epub('<p>He said it.<a href="endnotes.xhtml#note-1" id="r1" epub:type="noteref">1</a></p>', manifest=manifest,
                                   spine='<itemref idref="story"/>' + spine, extra={'EPUB/endnotes.xhtml': notes}))
        blocks = book['chapters'][1]['blocks']
        self.assertEqual(['Endnotes', '1. First note text.', '2. Second note text.'], [block['text'] for block in blocks])
        self.assertTrue(blocks[1]['html'].startswith('<p>1. First note text. '))
        self.assertEqual(['He said it.'], [source['text'] for source in speech_sources(book, default_chapters(book))])

    def test_typed_note_links_drop_short_markers_but_keep_words(self):
        markers = ['****', 'a', '[a]', 'ii', '[iv]', 'IV', '§', '¶', '‖', '[1*]', '1.', '[ 1 ]', '*1', '1*', '¹', '(2)']
        body = ''.join(f'<p>Слово{index}<a epub:type="noteref" href="#n{index}">{marker}</a> дальше.</p>' for index, marker in enumerate(markers))
        body += '<p>См. <a epub:type="noteref" href="#n0">прим. 1</a> дальше.</p><p>Слово<a href="#n0">ii</a> дальше.</p>'
        notes = ''.join(f'<aside epub:type="footnote" id="n{index}"><p>Сноска {index}.</p></aside>' for index in range(len(markers)))
        texts = [text for text in self.texts(self.read(make_epub(body + notes))) if not text.startswith('Сноска')]
        self.assertEqual([f'Слово{index} дальше.' for index in range(len(markers))] + ['См. прим. 1 дальше.', 'Словоii дальше.'], texts)

    def test_language_names_map_and_unknown_values_use_script_detection(self):
        def book(language, text):
            return {'language': language, 'chapters': [{'id': 'c1', 'default_narration': True, 'blocks': [{'kind': 'paragraph', 'text': text}]}]}
        latin = 'Mary opened the book and read it aloud.'
        cases = [('Russian', RUSSIAN, 'Russian'), ('russian', RUSSIAN, 'Russian'), ('русский', RUSSIAN, 'Russian'), ('English', latin, 'English'),
                 ('English (US)', latin, 'English'), ('en_US', latin, 'English'), ('x-unknown', RUSSIAN, 'Russian'), ('x-unknown', latin, 'English'),
                 (None, RUSSIAN, 'Russian'), ('mul', RUSSIAN, 'Russian'), ('de', latin, 'Auto'), ('uk', RUSSIAN, 'Auto'), ('Russian', latin, 'English')]
        for language, text, expected in cases:
            with self.subTest(language=language, text=text[:10]):
                self.assertEqual(expected, speech_language(book(language, text)))
        for language, title in (('russian', 'Глава 1'), ('Russian', 'Глава 1'), ('x-unknown', 'Глава 1'), ('English', 'Chapter 1'), ('de', 'Chapter 1')):
            with self.subTest(fb2=language):
                self.assertEqual(title, self.read(fb2('<section><p>Текст без заголовка.</p></section>', lang=f'<lang>{language}</lang>'), 'lang.fb2')['chapters'][0]['title'])
        for language, title in (('Russian', 'Раздел 1'), ('russian', 'Раздел 1'), ('English', 'Section 1')):
            with self.subTest(epub=language):
                book = self.read(make_epub(extra={'EPUB/story.xhtml': page('<p>Текст.</p>', title=''), 'EPUB/book.opf': opf(language)}))
                self.assertEqual(title, book['chapters'][0]['title'])

    def test_fb2_parent_pieces_count_only_their_own_parts_and_separator_titles_use_first_words(self):
        book = self.read(fb2('<section><title><p>Часть 1</p></title><p>Вступление.</p>'
                             '<section><title><p>Глава 1</p></title><p>Текст 1.</p></section>'
                             '<section><title><p>Глава 2</p></title><p>Текст 2.</p></section><p>Послесловие.</p></section>'
                             '<section><title><p>Часть 2</p></title><section><title><p>Глава 3</p></title><p>Текст 3.</p></section>'
                             '<section><p>Без названия.</p></section></section>'
                             '<section><title><p>* * *</p></title><poem><stanza><v>Я помню чудное мгновенье:</v></stanza></poem></section>'
                             '<section><title><p>* * *</p></title><p>Один два три четыре пять шесть семь восемь.</p></section>'
                             '<section><p>Без заголовка.</p></section>'), 'names.fb2')
        self.assertEqual(['Часть 1', 'Глава 1', 'Глава 2', 'Часть 1 (2)', 'Глава 3', 'Часть 2 (2)', '* * * Я помню чудное мгновенье:',
                          '* * * Один два три четыре пять шесть…', 'Глава 9'], [chapter['title'] for chapter in book['chapters']])

    def test_fb2_single_empty_lines_follow_the_spacing_style_of_the_whole_book(self):
        short = '<p>Сцена один.</p><empty-line/><p>Сцена два.</p><empty-line/><p>Сцена три.</p><empty-line/><p>Сцена четыре.</p>'
        dense = ''.join(f'<p>Абзац {index}.</p><empty-line/>' for index in range(40)) + '<p>Конец.</p>'
        def separators(book):
            return [[block['kind'] for block in chapter['blocks']].count('separator') for chapter in book['chapters']]
        alone = self.read(fb2(f'<section>{short}</section>'), 'short.fb2')
        self.assertEqual([3], separators(alone))
        self.assertEqual(4, len({source['scene_id'] for source in speech_sources(alone, default_chapters(alone))}))
        self.assertEqual([0, 0], separators(self.read(fb2(f'<section>{dense}</section><section>{short}</section>'), 'mixed.fb2')))
        self.assertEqual([3] * 9, separators(self.read(fb2(f'<section>{short}</section>' * 9), 'few.fb2')))
        self.assertEqual([0] * 12, separators(self.read(fb2(f'<section>{short}</section>' * 12), 'many.fb2')))
        self.assertEqual([40], separators(self.read(fb2(f'<section>{dense}</section><section><p>А.</p><p>Б.</p>{"<p>В.</p>" * 10}</section>'), 'sparse.fb2'))[:1])


if __name__ == '__main__':
    unittest.main()
