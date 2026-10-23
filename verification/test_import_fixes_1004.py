from __future__ import annotations

import io
from pathlib import Path
import random
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from rimsky import books
from rimsky.books import import_book, split_text
from rimsky.integrity import validate_coverage
from rimsky.preparation import audible, default_chapters, speech_language, speech_sources
from rimsky.util import AppError, display_text
from test_epub_preparation import make_epub

XHTML = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{title}</title></head>'
         '<body{attrs}>{body}</body></html>')
FB2_NS = 'http://www.gribuser.ru/xml/fictionbook/2.0'


def page(body, title='Документ', attrs=''):
    return XHTML.format(title=title, attrs=attrs, body=body).encode()


def fb2(body, notes='', lang='<lang>ru</lang>', ns=FB2_NS, info=None, declaration='<?xml version="1.0" encoding="utf-8"?>'):
    info = info if info is not None else f'<author><first-name>A</first-name><last-name>B</last-name></author><book-title>Книга</book-title>{lang}'
    xmlns = f' xmlns="{ns}"' if ns else ''
    return (f'{declaration}<FictionBook{xmlns} xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>{info}</title-info></description>'
            f'<body>{body}</body>{notes}</FictionBook>')


def documents(*names):
    manifest = ''.join(f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>' for name in names)
    spine = ''.join(f'<itemref idref="{name}"/>' for name in names)
    return manifest, spine


class ImportFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data if isinstance(data, bytes) else data.encode(), name, self.root / 'books')

    def blocks(self, book):
        return [(block['kind'], block['text']) for chapter in book['chapters'] for block in chapter['blocks']]

    def spoken(self, book):
        return list(speech_sources(book, default_chapters(book)))

    def test_star_headings_are_scene_separators_not_narrated_headings(self):
        book = self.read(make_epub('<h1>Глава</h1><p>До.</p><h5 class="subtitle">* * *</h5><p>После.</p>'))
        self.assertEqual([('heading', 'Глава'), ('paragraph', 'До.'), ('separator', '* * *'), ('paragraph', 'После.')], self.blocks(book))
        sources = self.spoken(book)
        self.assertEqual(['Глава', 'До.', 'После.'], [source['text'] for source in sources])
        self.assertEqual(2, len({source['scene_id'] for source in sources}))
        unlisted = self.read(make_epub('<h1>Т</h1><p>А.</p><h5>* * *</h5><p>Б.</p><h5>* * *</h5><p>В.</p>'))
        self.assertEqual(['Т'], [chapter['title'] for chapter in unlisted['chapters']])
        manifest, spine = documents('story', 'more')
        joined = self.read(make_epub('<h1 id="a">Глава 1</h1><p>Начало.</p>', nav='<li><a href="story.xhtml#a">Глава 1</a></li>',
                                     manifest=manifest.replace('<item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>', ''),
                                     spine=spine, extra={'EPUB/more.xhtml': page('<h5>* * *</h5><p>Продолжение.</p>')}))
        self.assertEqual(['Глава 1'], [chapter['title'] for chapter in joined['chapters']])

    def test_glyph_rows_are_separators_but_short_rows_and_roman_numerals_are_text(self):
        for row in ('— — —', '–––', '∗ ∗ ∗', '⁎ ⁎ ⁎', '• • •', '◆ ◆ ◆', '~ ~ ~', 'x x x', 'х х х', '***', '* * * * *', '⁂'):
            with self.subTest(row=row):
                self.assertEqual(('separator', row), self.blocks(self.read(make_epub(f'<p>До.</p><p>{row}</p><p>После.</p>')))[1])
        for row in ('*', '* *', 'XXX', 'ХХХ', '...'):
            with self.subTest(row=row):
                self.assertEqual(('paragraph', row), self.blocks(self.read(make_epub(f'<p>До.</p><p>{row}</p><p>После.</p>')))[1])
        self.assertEqual([('paragraph', 'До.'), ('paragraph', 'После.')], self.blocks(self.read(make_epub('<p>До.</p><p>&#160;</p><p class="empty-line"/><p>После.</p>'))))

    def test_fb2_subtitles_are_headings_or_separators(self):
        book = self.read(fb2('<section><p>— Пойдём, — сказал Иван.</p><subtitle>II</subtitle><p>— Нет, — ответила Маша.</p>'
                             '<subtitle>— — —</subtitle><p>Конец.</p></section>'), 'book.fb2')
        self.assertEqual(['paragraph', 'heading', 'paragraph', 'separator', 'paragraph'], [kind for kind, _ in self.blocks(book)])
        self.assertEqual(['— Пойдём, — сказал Иван.', 'II', '— Нет, — ответила Маша.', 'Конец.'], [source['text'] for source in self.spoken(book)])

    def test_fb2_empty_line_runs_used_as_paragraph_spacing_are_not_scene_breaks(self):
        spaced = ''.join(f'<p>Абзац {index}.</p><empty-line/>' for index in range(40)) + '<p>Конец.</p>'
        book = self.read(fb2(f'<section>{spaced}</section>'), 'spacing.fb2')
        self.assertNotIn('separator', [kind for kind, _ in self.blocks(book)])
        scene = self.read(fb2('<section>' + spaced.replace('<empty-line/><p>Абзац 3.', '<empty-line/><empty-line/><p>Абзац 3.') + '</section>'), 'scene.fb2')
        self.assertEqual(1, [kind for kind, _ in self.blocks(scene)].count('separator'))
        breaks = self.read(fb2('<section><p>А.</p><p>Б.</p><empty-line/><p>В.</p><p>Г.</p><p>Д.</p><empty-line/><p>Е.</p></section>'), 'breaks.fb2')
        self.assertEqual(2, [kind for kind, _ in self.blocks(breaks)].count('separator'))

    def test_note_link_text_is_dropped_only_when_it_is_a_marker(self):
        notes = '<body name="notes"><section id="n1"><title><p>1</p></title><p>Сноска.</p></section></body>'
        book = self.read(fb2('<section><p>Он взял <a l:href="#n1" type="note">кирку</a> и ушёл.</p>'
                             '<p>Второй<a l:href="#n1" type="note">{3}</a> пример.</p><p>Третий<a l:href="#n1">³</a> пример.</p>'
                             '<p>Четвёртый<a l:href="#n1">(2)</a> и<a l:href="#n1">†</a> пример.</p></section>', notes), 'notes.fb2')
        texts = [block['text'] for block in book['chapters'][0]['blocks']]
        self.assertEqual(['Он взял кирку и ушёл.', 'Второй пример.', 'Третий пример.', 'Четвёртый и пример.'], texts)
        first = book['chapters'][0]['blocks'][0]['html']
        self.assertIn('кирку', first)
        self.assertIn('data-source-link=', first)
        self.assertNotIn('data-note-ref', first)
        epub = self.read(make_epub('<p>Он заказал <a epub:type="noteref" href="#n1">абсент</a> и сел<a role="doc-noteref" href="#n1">[1]</a>.</p>'
                                   '<aside epub:type="footnote" id="n1"><p>Напиток.</p></aside>'))
        self.assertEqual(('paragraph', 'Он заказал абсент и сел.'), self.blocks(epub)[0])

    def test_invisible_format_characters_are_removed_from_prepared_text(self):
        self.assertEqual('abcdefg h', display_text('a\u200bb\u200cc\u200dd\u2060e\ufefff\u200e\u200f\u00adg \u202ah\u202c'))
        book = self.read(make_epub('<p>При\u200bвет,\u2060 мир\ufeff.</p>'))
        self.assertEqual([('paragraph', 'Привет, мир.')], self.blocks(book))
        self.assertIn('\u200b', book['chapters'][0]['blocks'][0]['html'])

    def test_sibling_drop_cap_letter_joins_its_paragraph(self):
        book = self.read(make_epub('<div class="dropcap" id="d">Т</div><p id="p">ак началось утро.</p><p>Дальше.</p>'))
        blocks = book['chapters'][0]['blocks']
        self.assertEqual([('paragraph', 'Так началось утро.'), ('paragraph', 'Дальше.')], self.blocks(book))
        self.assertEqual('<p>Так началось утро.</p>', blocks[0]['html'])
        self.assertEqual(['d', 'p'], blocks[0]['anchors'])
        self.assertEqual([('paragraph', 'А'), ('paragraph', 'Б тоже.')], self.blocks(self.read(make_epub('<p>А</p><p>Б тоже.</p>'))))
        self.assertEqual([('paragraph', 'а'), ('paragraph', 'б.')], self.blocks(self.read(make_epub('<p>а</p><p>б.</p>'))))
        self.assertEqual([('paragraph', 'Б'), ('paragraph', 'бабочка — насекомое.')], self.blocks(self.read(make_epub('<p>Б</p><p>бабочка — насекомое.</p>'))))

    def test_semantic_front_and_back_matter_is_not_default_narration(self):
        manifest, spine = documents('title', 'copy', 'notes', 'colophon')
        guide = ('<guide><reference type="title-page" href="title.xhtml"/><reference type="copyright-page" href="copy.xhtml"/>'
                 '<reference type="notes" href="notes.xhtml"/><reference type="text" href="story.xhtml"/></guide>')
        book = self.read(make_epub('<h1>Глава 1</h1><p>Текст главы.</p>', manifest=manifest, guide=guide,
                                   spine=spine.replace('<itemref idref="notes"/>', '<itemref idref="story"/><itemref idref="notes"/>'),
                                   extra={'EPUB/title.xhtml': page('<p>Автор</p><p>Название</p>'),
                                          'EPUB/copy.xhtml': page('<p>ISBN 978-5-00000-000-0</p>'),
                                          'EPUB/notes.xhtml': page('<p>Первая сноска.</p>'),
                                          'EPUB/colophon.xhtml': page('<section epub:type="colophon"><p>Отпечатано.</p></section>')}))
        roles = {chapter['source']: (chapter['role'], chapter['default_narration']) for chapter in book['chapters']}
        self.assertEqual({'EPUB/title.xhtml': ('auxiliary', False), 'EPUB/copy.xhtml': ('auxiliary', False), 'EPUB/story.xhtml': ('body', True),
                          'EPUB/notes.xhtml': ('auxiliary', False), 'EPUB/colophon.xhtml': ('auxiliary', False)}, roles)
        self.assertEqual(['Глава 1', 'Текст главы.'], [source['text'] for source in self.spoken(book)])
        fragment = self.read(make_epub('<h1>Глава</h1><p>Текст.</p>', guide='<guide><reference type="copyright-page" href="story.xhtml#c"/></guide>',
                                       manifest=documents('copy')[0], spine='<itemref idref="copy"/><itemref idref="story"/>',
                                       extra={'EPUB/copy.xhtml': page('<p>Копирайт.</p>')}))
        self.assertTrue(all(chapter['default_narration'] for chapter in fragment['chapters']))

    def test_landmarks_and_body_types_mark_auxiliary_documents(self):
        nav = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
               '<nav epub:type="toc"><ol><li><a href="story.xhtml">Глава</a></li></ol></nav>'
               '<nav epub:type="landmarks"><ol><li><a epub:type="copyright-page" href="copy.xhtml">Права</a></li>'
               '<li><a epub:type="bodymatter" href="story.xhtml">Начало</a></li></ol></nav></body></html>').encode()
        manifest, spine = documents('copy', 'front')
        book = self.read(make_epub('<p>Текст.</p>', manifest=manifest + '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>',
                                   spine=spine + '<itemref idref="story"/>',
                                   extra={'EPUB/nav.xhtml': nav, 'EPUB/copy.xhtml': page('<p>© Издатель.</p>'),
                                          'EPUB/front.xhtml': page('<p>Титул.</p>', attrs=' epub:type="frontmatter titlepage"')}))
        roles = {chapter['source']: chapter['role'] for chapter in book['chapters']}
        self.assertEqual({'EPUB/copy.xhtml': 'auxiliary', 'EPUB/front.xhtml': 'auxiliary', 'EPUB/story.xhtml': 'body'}, roles)
        alone = self.read(make_epub('<p>Единственный текст.</p>', guide='<guide><reference type="title-page" href="story.xhtml"/></guide>'))
        self.assertEqual([('body', True)], [(chapter['role'], chapter['default_narration']) for chapter in alone['chapters']])

    def test_endnote_sections_are_notes_even_with_an_outside_heading(self):
        manifest, spine = documents('notes')
        book = self.read(make_epub('<h1>Глава</h1><p>Текст.</p>', manifest=manifest, spine='<itemref idref="story"/>' + spine,
                                   extra={'EPUB/notes.xhtml': page('<h1>Примечания</h1><section epub:type="endnotes"><ol>'
                                                                   '<li epub:type="endnote" id="n1"><p>Сноска один.</p></li></ol></section>')}))
        story, notes = book['chapters']
        self.assertEqual(('auxiliary', False, 'Примечания'), (notes['role'], notes['default_narration'], notes['title']))
        self.assertEqual([story['id']], default_chapters(book))
        self.assertIn('1. Сноска один.', [source['text'] for source in speech_sources(book, [notes['id']])])
        asides = self.read(make_epub('<h1>Глава</h1><p>Текст.</p>', manifest=manifest, spine='<itemref idref="story"/>' + spine,
                                     extra={'EPUB/notes.xhtml': page('<h2>Сноски</h2><aside epub:type="footnote" id="f1"><p>Первая.</p></aside>'
                                                                     '<aside epub:type="footnote" id="f2"><p>Вторая.</p></aside>')}))
        self.assertEqual(('auxiliary', ['Сноски', 'Первая.', 'Вторая.']),
                         (asides['chapters'][1]['role'], [source['text'] for source in speech_sources(asides, [asides['chapters'][1]['id']])]))
        pandoc = self.read(make_epub('<section><h1>Глава 1</h1><p>Текст<a href="#fn1" role="doc-noteref"><sup>1</sup></a> дальше.</p></section>'
                                     '<section id="footnotes" role="doc-endnotes"><hr/><ol><li id="fn1" role="doc-endnote"><p>Сноска.'
                                     '<a href="#fnref1" role="doc-backlink">↩\ufe0e</a></p></li></ol></section>'))
        self.assertEqual([('heading', 'Глава 1'), ('paragraph', 'Текст дальше.'), ('separator', ''), ('note', '1. Сноска.')], self.blocks(pandoc))
        self.assertEqual(['Глава 1', 'Текст дальше.'], [source['text'] for source in self.spoken(pandoc)])

    def test_navigation_label_after_vignette_and_cover_fallback_title(self):
        png = io.BytesIO()
        Image.new('RGB', (6, 4), 'black').save(png, 'PNG')
        book = self.read(make_epub('<div><img src="v.png" alt=""/></div><div class="h1"><p id="t">Глава первая</p></div><p>Текст.</p>',
                                   ncx='<navPoint><navLabel><text>Глава первая</text></navLabel><content src="story.xhtml"/></navPoint>',
                                   extra={'EPUB/v.png': png.getvalue()}))
        blocks = book['chapters'][0]['blocks']
        self.assertEqual(['figure', 'heading', 'paragraph'], [block['kind'] for block in blocks])
        self.assertEqual('navigation-label', blocks[1]['heading_source'])
        cover = self.read(make_epub('<p>Текст.</p>', metadata='<meta name="cover" content="art"/>',
                                    manifest='<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="art" href="v.png" media-type="image/png"/>',
                                    spine='<itemref idref="cover"/><itemref idref="story"/>',
                                    extra={'EPUB/v.png': png.getvalue(), 'EPUB/cover.xhtml': page('<img src="v.png" alt=""/>', title='fb2converter')}))
        self.assertEqual(('cover', 'Обложка'), (cover['chapters'][0]['role'], cover['chapters'][0]['title']))

    def test_split_text_avoids_abbreviation_initial_and_ellipsis_cuts(self):
        lead = ('Он шёл по длинной улице, разглядывая витрины, вывески, прохожих, машины и деревья вдоль дороги, '
                'и вспоминал старые времена, когда всё было иначе, и люди были другими, и дома стояли другие, и даже воздух пах иначе, ')
        english = ('He walked down the long street, looking at the shop windows and the signs and the people and the cars and the trees along the road, '
                   'and he remembered the old days when everything was different and the houses were different and even the air smelled different, ')
        cases = {
            'initials': lead + 'и никто не спешил, и каждый вечер он сидел у окна и читал толстую книгу, на обложке которой значилось: А. С. Пушкин, собрание сочинений в трёх томах, издание старое, потрёпанное и пожелтевшее от времени, но любимое.',
            'abbreviation': lead + 'и наконец остановился у самого входа в старый дворец культуры, который все называли домом им. Горького до сих пор, хотя его давно переименовали, и все в городе по привычке называли его по-старому, как и сорок лет назад.',
            'ellipsis': 'Он шёл по длинной улице, разглядывая витрины, вывески, прохожих, машины и деревья вдоль дороги, и долго не мог понять, что именно его тревожит, пока не заметил странную тень на стене дома напротив, и тогда он остановился, посмотрел вверх, прислушался к шуму ветра и далёким голосам. Как вдруг… я снова оказался на той самой площади, где всё началось, среди тех же людей, тех же звуков и запахов, будто время повернуло назад и решило показать мне всё заново, с самого начала, без пропусков.',
            'title': english + 'and he stopped at the door of the old house that had belonged to the family of Mr. Smith for a hundred years and more, and he knocked twice and waited for someone to answer.',
            'latin': english + 'and he kept many small things in a box, e.g. letters and stamps and old coins that his grandfather had collected over many years, and nobody in the family ever asked about them.',
        }
        for label, text in cases.items():
            with self.subTest(label):
                parts = split_text(text)
                self.assertEqual(text, ''.join(part[2] for part in parts))
                self.assertEqual([0] + [end for _, end, _ in parts[:-1]], [start for start, _, _ in parts])
                self.assertEqual(2 if label == 'ellipsis' else 1, len(parts))
                for start, end, part in parts[:-1]:
                    self.assertFalse(part.rstrip().endswith(('А.', 'С.', 'им.', 'вдруг…', 'Mr.', 'e.g.', 'St.')), part[-30:])
                    self.assertFalse(text[end:end + 1].islower() and part.rstrip()[-1:] in '.!?…', part[-30:])
        self.assertTrue(split_text(cases['ellipsis'])[0][2].endswith('голосам. '))
        self.assertEqual([(0, 7, 'Это я. '), (7, 21, 'Потом он ушёл.')], split_text('Это я. Потом он ушёл.', 14))
        self.assertEqual([(0, 21, 'Это А. Потом он ушёл.')], split_text('Это А. Потом он ушёл.', 14))
        self.assertEqual([(0, 22, 'Он ушёл; и вот он тут.')], split_text('Он ушёл; и вот он тут.', 14))

    def test_split_text_keeps_whole_sentences_graphemes_and_exact_offsets(self):
        sentence = ('Он думал о том, что ' + 'и снова о том, что ' * 30 + 'всё будет хорошо').strip() + '.'
        self.assertGreater(len(sentence), 420)
        self.assertEqual([(0, len(sentence), sentence)], split_text(sentence))
        stressed = 'x' + 'за\u0301' * 300
        self.assertEqual([(0, len(stressed), stressed)], split_text(stressed))
        random.seed(11)
        alphabet = 'абвгд ежз. ,!?…»;:—\u0301АСЯя\n'
        for _ in range(500):
            text = ''.join(random.choice(alphabet) for _ in range(random.randint(1, 2500)))
            parts = split_text(text)
            spans = books.sentence_spans(text)
            edges = {edge for span in spans for edge in span}
            self.assertEqual(text, ''.join(part[2] for part in parts))
            self.assertEqual([0] + [end for _, end, _ in parts[:-1]], [start for start, _, _ in parts])
            self.assertTrue(all(0 < end - start and text[start:end] == part and start in edges and end in edges for start, end, part in parts))
            self.assertTrue(all(end - start <= 420 or sum(start <= a and b <= end for a, b in spans) == 1 for start, end, _ in parts))
            self.assertTrue(all(b - parts[index][0] > 420 for index, (start, end, _) in enumerate(parts[:-1])
                                for a, b in spans if a == end))

    def test_existing_segment_boundaries_remain_valid_coverage(self):
        text = ('Первая фраза т. е. продолжение, ' * 20).strip()
        book = self.read(make_epub(f'<p>{text}</p>'))
        chapter = book['chapters'][0]
        source = chapter['blocks'][0]
        legacy = [(0, 300), (300, 360), (360, len(text))]
        segments = [{'source_id': source['id'], 'chapter_id': chapter['id'], 'start': start, 'end': end, 'text': text[start:end], 'spoken': text[start:end]}
                    for start, end in legacy]
        validate_coverage(book, [chapter['id']], segments)

    def test_fb2_language_defaults_to_undetermined_and_titles_follow_script(self):
        russian = self.read(fb2('<section><p>Текст.</p></section>', lang=''), 'ru.fb2')
        self.assertEqual(('und', 'Глава 1', 'Russian'), (russian['language'], russian['chapters'][0]['title'], speech_language(russian)))
        empty = self.read(fb2('<section><p>Hello there.</p></section>', lang='<lang/>'), 'en.fb2')
        self.assertEqual(('und', 'Chapter 1', 'English'), (empty['language'], empty['chapters'][0]['title'], speech_language(empty)))
        def opf(language):
            return ('<package version="3.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Т</dc:title>'
                    f'<dc:language>{language}</dc:language></metadata><manifest><item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="story"/></spine></package>').encode()
        for language, title in (('RU', 'Раздел 1'), ('ru-RU', 'Раздел 1'), ('rum', 'Section 1'), ('en', 'Section 1')):
            with self.subTest(language=language):
                book = self.read(make_epub(extra={'EPUB/story.xhtml': page('<p>Текст.</p>', title=''), 'EPUB/book.opf': opf(language)}))
                self.assertEqual((language, title), (book['language'], book['chapters'][0]['title']))

    def test_speech_language_and_audible_helpers(self):
        def book(language, text, default=True):
            return {'language': language, 'chapters': [{'id': 'c1', 'default_narration': default, 'blocks': [{'kind': 'paragraph', 'text': text}]}]}
        cases = [('ru', 'Привет, мир.', 'Russian'), ('ru-RU', 'Привет.', 'Russian'), ('RUS', 'Привет.', 'Russian'), ('en', 'Hello.', 'English'),
                 ('en-GB', 'Hello.', 'English'), ('eng', 'Hello.', 'English'), ('en', 'Это русский текст.', 'Russian'), ('ru', 'Plain English text here.', 'English'),
                 ('und', 'Это русский текст.', 'Russian'), ('', 'Hello.', 'English'), ('und', '— 42 —', 'Auto'), ('de', 'Hallo Welt.', 'Auto'),
                 ('uk', 'Привіт.', 'Auto'), ('rum', 'Привет.', 'Auto'), ('ru', 'Mixed текст here и там', 'Russian')]
        for language, text, expected in cases:
            with self.subTest(language=language, text=text):
                self.assertEqual(expected, speech_language(book(language, text)))
        self.assertEqual('Russian', speech_language({'language': 'und', 'chapters': [{'id': 'c1', 'default_narration': False, 'blocks': [{'kind': 'paragraph', 'text': 'Только примечания.'}]}]}))
        for text, expected in (('Привет', True), ('42', True), ('...', False), ('— — —', False), ('\u0301', False), ('', False), ('« »', False)):
            with self.subTest(text=text):
                self.assertEqual(expected, audible(text))

    def test_fb2_nested_untitled_sections_are_named_after_their_parent(self):
        book = self.read(fb2('<section><title><p>Глава 1</p></title><section><p>Сцена один.</p></section><section><p>Сцена два.</p></section></section>'
                             '<section><title><p>Глава 2</p></title><p>Текст главы два.</p></section>'
                             '<section><title><p>Глава 3</p></title><p>Начало.</p><section><p>Вложенная.</p></section><p>Хвост.</p></section>'
                             '<section><title><p>* * *</p></title><p>Без названия.</p></section>'), 'nested.fb2')
        self.assertEqual(['Глава 1', 'Глава 1 (2)', 'Глава 2', 'Глава 3', 'Глава 3 (2)', 'Глава 3 (3)', '* * * Без названия.'],
                         [chapter['title'] for chapter in book['chapters']])
        self.assertEqual(['Глава 1', 'Сцена один.'], [block['text'] for block in book['chapters'][0]['blocks']])
        self.assertEqual('separator', book['chapters'][-1]['blocks'][0]['kind'])

    def test_fb2_author_and_date_lines_are_separate_passages(self):
        book = self.read(fb2('<section><poem><stanza><v>Я помню чудное мгновенье</v></stanza><text-author>А. Пушкин</text-author><date>1825</date></poem>'
                             '<epigraph><p>Цитата.</p><text-author>А. Пушкин</text-author><text-author>Евгений Онегин</text-author></epigraph>'
                             '<p>Текст<empty-line/>внутри.</p></section>'), 'poem.fb2')
        self.assertEqual(['Я помню чудное мгновенье', 'А. Пушкин', '1825', 'Цитата.', 'А. Пушкин', 'Евгений Онегин', 'Текст внутри.'],
                         [text for _, text in self.blocks(book)])
        hr = self.read(make_epub('<p>Текст<hr/>внутри.</p>'))
        self.assertEqual('Текст внутри.', self.blocks(hr)[0][1])

    def test_fb2_inline_style_text_is_kept_for_any_fictionbook_namespace(self):
        for ns in (FB2_NS, '', 'http://www.gribuser.ru/xml/fictionbook/2.1'):
            with self.subTest(ns=ns):
                book = self.read(fb2('<section><p>Он сказал: <style name="foreign">Bonjour</style>, и ушёл.</p></section>', ns=ns), 'style.fb2')
                self.assertEqual('Он сказал: Bonjour, и ушёл.', self.blocks(book)[0][1])
                self.assertIn('Bonjour', book['chapters'][0]['blocks'][0]['html'])

    def test_fb2_nickname_authors_and_empty_titles(self):
        info = ('<author><nickname>Вова Сталкер</nickname></author><author><first-name>Анна</first-name><middle-name></middle-name><last-name>Иванова</last-name></author>'
                '<author><first-name>Анна</first-name><last-name>Иванова</last-name></author><book-title/><lang>ru</lang>')
        book = self.read(fb2('<section><p>Текст.</p></section>', info=info), 'meta.fb2')
        self.assertEqual(('Untitled book', 'Вова Сталкер, Анна Иванова'), (book['title'], book['author']))
        sole = self.read(fb2('<section><p>Текст.</p></section>', info='<author><nickname>Ник</nickname></author><book-title>Т</book-title>'), 'sole.fb2')
        self.assertEqual('Ник', sole['author'])

    def test_fb2_legacy_encodings_and_encoding_errors(self):
        body = fb2('<section><p>Привет, мир. Съешь же ещё этих мягких булок.</p></section>', declaration='')
        declared = lambda encoding: f'<?xml version="1.0" encoding="{encoding}"?>'
        accepted = {
            'cp1251 without declaration': body.encode('cp1251'),
            'koi8-r without declaration': body.encode('koi8-r'),
            'cp1251 with version-only declaration': ('<?xml version="1.0"?>' + body).encode('cp1251'),
            'newline before declaration': ('\n ' + declared('utf-8') + body).encode(),
            'BOM and newline before declaration': b'\xef\xbb\xbf' + ('\n' + declared('utf-8') + body).encode(),
            'declared koi8-r': (declared('koi8-r') + body).encode('koi8-r'),
        }
        for label, data in accepted.items():
            with self.subTest(label):
                self.assertEqual('Привет, мир. Съешь же ещё этих мягких булок.', self.blocks(self.read(data, 'enc.fb2'))[0][1])
        rejected = {
            'declared utf-8 with cp1251 bytes': (declared('utf-8') + body).encode('cp1251'),
            'declared cp1251 with undefined byte': (declared('windows-1251') + body).encode('cp1251').replace('мир'.encode('cp1251'), 'мир'.encode('cp1251') + b'\x98'),
            'mostly utf-8 with a stray byte': body.encode().replace('мир'.encode(), 'мир'.encode() + b'\xff'),
            'unknown declared encoding': (declared('x-unknown-cyrillic') + body).encode(),
        }
        for label, data in rejected.items():
            with self.subTest(label), self.assertRaises(AppError) as caught:
                self.read(data, 'enc.fb2')
            self.assertEqual(('invalid_book_encoding', 'The book text encoding could not be read.'), (caught.exception.code, str(caught.exception)))
        with self.assertRaises(AppError) as caught:
            self.read((declared('utf-8') + body.replace('</p>', '')).encode(), 'broken.fb2')
        self.assertEqual('invalid_book_xml', caught.exception.code)

    def test_image_decoding_is_limited_to_raster_book_formats(self):
        assets = {}
        for format in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP'):
            with self.subTest(format=format):
                raw = io.BytesIO()
                Image.new('RGB', (5, 3), 'red').save(raw, format)
                self.assertIsNotNone(books.image_asset(raw.getvalue(), self.root, assets))
        eps = b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n0 0 moveto 10 10 lineto stroke\nshowpage\n%%EOF\n'
        tiff = io.BytesIO()
        Image.new('RGB', (5, 3), 'red').save(tiff, 'TIFF')
        with patch('subprocess.check_call', side_effect=AssertionError('external decoder started')) as call:
            self.assertIsNone(books.image_asset(eps, self.root, assets))
            self.assertIsNone(books.image_asset(tiff.getvalue(), self.root, assets))
        call.assert_not_called()

    def test_oversized_vector_drawings_are_omitted_with_a_warning(self):
        small = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0 L10 10"/></svg>'
        huge = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0 {"L1 1 " * 7000}"/></svg>'
        book = self.read(make_epub(f'<p>До.</p><div>{small}</div><p>Середина {huge}текст.</p><p>После.</p>'))
        self.assertEqual(['До.', '', 'Середина текст.', 'После.'], [text for _, text in self.blocks(book)])
        self.assertIn('<path', str(book['chapters']))
        self.assertNotIn('L1 1', str(book['chapters']))
        self.assertIn('Some vector drawings were too large and were omitted.', book['warnings'])
        self.assertNotIn('Some vector drawings were too large and were omitted.', self.read(make_epub(f'<div>{small}</div><p>Текст.</p>'))['warnings'])
        with patch('rimsky.books.MAX_SVG_GEOMETRY', 30):
            capped = self.read(make_epub(f'<div>{small}</div><div>{small}</div><p>Текст.</p>'))
        self.assertEqual(1, str(capped['chapters']).count('<path'))
        self.assertIn('Some vector drawings were too large and were omitted.', capped['warnings'])

    def test_zip_names_without_utf8_flag_are_read_as_utf8(self):
        name = 'EPUB/глава.xhtml'
        placeholder = 'EPUB/' + 'x' * (len(name.encode()) - len('EPUB/.xhtml')) + '.xhtml'
        raw = make_epub('<p>Пролог.</p>', manifest='<item id="ch" href="%D0%B3%D0%BB%D0%B0%D0%B2%D0%B0.xhtml" media-type="application/xhtml+xml"/>',
                        spine='<itemref idref="story"/><itemref idref="ch"/>', extra={placeholder: page('<p>Текст главы.</p>')})
        data = raw.replace(placeholder.encode(), name.encode())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertFalse(archive.getinfo(name.encode().decode('cp437')).flag_bits & 0x800)
        book = self.read(data)
        self.assertEqual(['Пролог.', 'Текст главы.'], [text for _, text in self.blocks(book)])
        self.assertEqual('EPUB/глава.xhtml', book['chapters'][1]['source'])


if __name__ == '__main__':
    unittest.main()
