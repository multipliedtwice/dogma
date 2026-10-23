from __future__ import annotations

import io
from pathlib import Path
import random
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky import books, epub
from rimsky.books import import_book, sentence_spans, split_text
from rimsky.util import AppError
from test_epub_preparation import make_epub
from test_import_fixes_1004 import fb2

HEAD = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Документ</title>'
        '<link rel="stylesheet" href="book.css"/></head><body>')
CSS_ITEM = '<item id="css" href="book.css" media-type="text/css"/>'


def styled(body, css=b'.strong {font-weight: 700} .emphasis {font-style: italic}'):
    return make_epub(manifest=CSS_ITEM, extra={'EPUB/story.xhtml': (HEAD + body + '</body></html>').encode(), 'EPUB/book.css': css})


def stored(data):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item))
    return out.getvalue()


def damaged(data, name, offset):
    data = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        item = archive.getinfo(name)
    data[item.header_offset + 30 + len(item.filename.encode()) + len(item.extra) + offset] ^= 0xFF
    return bytes(data)


def covered(text, spans):
    return not text and not spans or spans[0][0] == 0 and spans[-1][1] == len(text) and all(
        a < b for a, b in spans) and all(left[1] == right[0] for left, right in zip(spans, spans[1:]))


class MergedImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data if isinstance(data, bytes) else data.encode(), name, self.root / 'books')

    def blocks(self, book):
        return [block for chapter in book['chapters'] for block in chapter['blocks']]

    def test_sentence_spans_fold_initials_and_abbreviations_without_cutting_sentences(self):
        cases = {
            'Это А. С. Пушкин. Потом он ушёл.': ['Это А. С. Пушкин. ', 'Потом он ушёл.'],
            'Дом им. Горького стоит. Рядом см. Рис. 2.': ['Дом им. Горького стоит. ', 'Рядом см. Рис. 2.'],
            'Visit Mr. Smith and Dr. Who. Then St. Ives.': ['Visit Mr. Smith and Dr. Who. ', 'Then St. Ives.'],
            'Это я. Потом он ушёл.': ['Это я. ', 'Потом он ушёл.'],
            'Он ушёл; и вот он тут.': ['Он ушёл; и вот он тут.'],
            'Как вдруг… я снова тут. Конец.': ['Как вдруг… я снова тут. ', 'Конец.'],
            'Это А... Потом.': ['Это А... ', 'Потом.'],
            '— Да, мы видели, — усмехнулся мужчина. Потом он ушёл.': ['— Да, мы видели, — усмехнулся мужчина. ', 'Потом он ушёл.'],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, [text[a:b] for a, b in sentence_spans(text)])

    def test_sentence_spans_cover_every_character_once_and_terminate(self):
        random.seed(1005)
        alphabet = 'абАБ я.!?…»”")] \n\t«“(—–-;,ЯСMrsd5'
        for _ in range(2000):
            text = ''.join(random.choice(alphabet) for _ in range(random.randint(0, 400)))
            self.assertTrue(covered(text, sentence_spans(text)), repr(text))
            parts = split_text(text, random.randint(1, 60))
            self.assertEqual(text, ''.join(part for _, _, part in parts))
            self.assertTrue(covered(text, [(a, b) for a, b, _ in parts]))
        for text in ('', ' ', '.', '. ', '...   ', 'А. ' * 50, '\n' * 30):
            self.assertTrue(covered(text, sentence_spans(text)))

    def test_split_text_groups_whole_sentences_and_never_cuts_a_long_sentence(self):
        long = 'Очень ' * 120 + 'длинное предложение без точки внутри, с запятыми, и всё равно целое.'
        short = 'Короткая фраза. '
        text = short * 3 + long + ' ' + short * 40
        parts = split_text(text)
        spans = sentence_spans(text)
        edges = {edge for span in spans for edge in span}
        self.assertEqual(text, ''.join(part for _, _, part in parts))
        self.assertTrue(all(a in edges and b in edges for a, b, _ in parts))
        self.assertIn(long + ' ', [part for _, _, part in parts])
        self.assertTrue(all(len(part) <= 420 for _, _, part in parts if part != long + ' '))
        self.assertEqual(len(parts), len({a for a, _, _ in parts}))

    def test_sentence_spans_and_split_text_are_linear_on_megabyte_blocks(self):
        for text in ('А. ' * 300_000, 'Слово. Ещё. ' * 90_000, 'я' * 1_000_000, '. ' * 500_000):
            started = time.monotonic()
            spans = sentence_spans(text)
            parts = split_text(text)
            self.assertLess(time.monotonic() - started, 15)
            self.assertTrue(covered(text, spans))
            self.assertEqual(text, ''.join(part for _, _, part in parts))

    def test_css_rule_scan_matches_previous_rule_grammar(self):
        previous = re.compile(r'([^{}]+)\{([^{}]*)\}')
        random.seed(5)
        pieces = ['{', '}', '.a', ' ', 'b', ';', 'font-weight:bold', ',', '@media x', '\n']
        for _ in range(3000):
            text = ''.join(random.choice(pieces) for _ in range(random.randint(0, 30)))
            self.assertEqual(previous.findall(text), list(epub.css_rules(text)), repr(text))

    def test_css_comments_are_removed_linearly_and_unclosed_comment_runs_to_end(self):
        self.assertEqual({'b': ('strong',)}, epub.stylesheet_class_formats(b'/* .a {font-weight:bold} */ .b {font-weight:bold}'))
        self.assertEqual({}, epub.stylesheet_class_formats(b'/* .a {font-weight:bold}'))
        self.assertEqual({'a': ('strong', 'emphasis'), 'x': ('strong', 'emphasis')}, epub.stylesheet_class_formats(
            b'\xef\xbb\xbfp.a:first-child, .x:hover {font-weight: 600; font-style: italic} .a {font-weight: bold} div > .y {font-weight: bold}'))

    def test_megabyte_brace_free_and_hostile_css_completes_quickly(self):
        hostile = (b'a' * 2_000_000, b' ' * 2_000_000, b'/*' * 1_000_000, b'a{' * 1_000_000, b'}' * 2_000_000,
                   b'.a' + b' ' * 1_000_000 + b'x{font-weight:bold}', b'.a, ' * 400_000 + b'{font-weight:bold}')
        for css in hostile:
            started = time.monotonic()
            epub.stylesheet_class_formats(css)
            self.assertLess(time.monotonic() - started, 10, css[:20])
        started = time.monotonic()
        book = self.read(styled('<p>Текст.</p>', b'.strong' + bytes(random.Random(1).choices(b'abcdefghij .,:;', k=1_500_000))))
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(['Текст.'], [block['text'] for block in self.blocks(book)])

    def test_damaged_stylesheet_member_is_a_damaged_entry_error(self):
        data = stored(styled('<p><span class="strong">Карта</span></p>', b'.strong {font-weight: 700}\n' * 200))
        self.assertIn('<strong>Карта</strong>', self.blocks(self.read(data))[0]['html'])
        with self.assertRaises(AppError) as caught:
            self.read(damaged(data, 'EPUB/book.css', 300))
        self.assertEqual(('damaged_book_entry', 'EPUB/book.css'), (caught.exception.code, caught.exception.details['document']))

    def test_stylesheet_budget_skips_class_formatting_but_keeps_text(self):
        data = styled('<p><span class="bold1">Карта</span> и <span class="cite">текст</span>.</p>', b'.bold1 {font-weight: 700}')
        self.assertIn('<strong>Карта</strong>', self.blocks(self.read(data))[0]['html'])
        with patch.object(epub, 'MAX_STYLESHEETS', 10):
            book = self.read(data)
        block = self.blocks(book)[0]
        self.assertEqual('Карта и текст.', block['text'])
        self.assertIn(epub.STYLESHEET_WARNING, book['warnings'])
        self.assertNotIn('<strong>', block['html'])
        self.assertEqual(['cite'], block['formatting']['classes']['inline'])

    def test_class_formatting_goes_through_sanitizer_and_class_hints_are_bounded(self):
        names = [f'c{index}' for index in range(200)]
        css = ' '.join(f'.{name} {{font-weight: bold}}' for name in names).encode()
        body = (f'<div class="poem {" ".join(names)}"><p class="stanza" onclick="x()" style="color:red">'
                '<span class="emphasis">Строка</span> стиха.</p></div>')
        block = self.blocks(self.read(styled(body, css + b' .emphasis {font-style: italic}')))[0]
        self.assertEqual('Строка стиха.', block['text'])
        self.assertNotRegex(block['html'], r'class=|onclick|style=')
        self.assertIn('<em>Строка</em>', block['html'])
        self.assertLessEqual(len(block['formatting']['classes']['enclosing']), books.MAX_CLASS_HINTS)
        self.assertEqual(['poem', 'c0'], block['formatting']['classes']['enclosing'][:2])
        self.assertIn('stanza', block['formatting']['classes']['enclosing'])
        self.assertEqual(['emphasis'], block['formatting']['classes']['inline'])

    def test_recovered_html_document_keeps_classes_for_formatting_and_semantics(self):
        body = '<p class="cite">Цитата документа<br></p><p><span class="strong">Карта</span> системы</p>'
        book = self.read(styled(body))
        self.assertTrue(any('recovery mode and all of its text was kept' in warning for warning in book['warnings']))
        blocks = self.blocks(book)
        self.assertEqual(['Цитата документа', 'Карта системы'], [block['text'] for block in blocks])
        self.assertEqual(['cite'], blocks[0]['formatting']['classes']['enclosing'])
        self.assertIn('<strong>Карта</strong>', blocks[1]['html'])
        self.assertEqual(['strong'], blocks[1]['formatting']['classes']['inline'])

    def test_heading_and_paragraph_star_rows_remain_separators(self):
        book = self.read(make_epub('<h1>Глава</h1><p>До.</p><h5>* * *</h5><p>Между.</p><p>⁂</p><p>***</p><p>После.</p>'))
        self.assertEqual([('heading', 'Глава'), ('paragraph', 'До.'), ('separator', '* * *'), ('paragraph', 'Между.'),
                          ('separator', '⁂'), ('separator', '***'), ('paragraph', 'После.')],
                         [(block['kind'], block['text']) for block in self.blocks(book)])

    FB2_BODY = ('<section><title><p>Глава</p></title>'
                '<epigraph><p>Эпиграф строка.</p><text-author>Автор эпиграфа</text-author></epigraph>'
                '<p>Обычный текст.</p>'
                '<poem><stanza><v>Первая строка,</v><v>Вторая строка.</v></stanza><text-author>Поэт</text-author></poem>'
                '<cite><p>Цитата документа.</p><text-author>Источник</text-author></cite>'
                '<subtitle>Подзаголовок</subtitle><subtitle>* * *</subtitle><p>Конец.</p></section>')

    def test_fb2_native_structure_becomes_bounded_class_hints(self):
        blocks = self.blocks(self.read(fb2(self.FB2_BODY), 'book.fb2'))
        found = {block['text']: (block['kind'], block.get('formatting', {}).get('classes', {}).get('enclosing', [])) for block in blocks}
        self.assertEqual({
            'Глава': ('heading', []),
            'Эпиграф строка.': ('paragraph', ['epigraph']),
            'Автор эпиграфа': ('paragraph', ['epigraph', 'text-author']),
            'Обычный текст.': ('paragraph', []),
            'Первая строка,': ('paragraph', ['stanza', 'poem']),
            'Вторая строка.': ('paragraph', ['stanza', 'poem']),
            'Поэт': ('paragraph', ['poem', 'text-author']),
            'Цитата документа.': ('paragraph', ['cite']),
            'Источник': ('paragraph', ['cite', 'text-author']),
            'Подзаголовок': ('heading', ['subtitle']),
            '* * *': ('separator', ['subtitle']),
            'Конец.': ('paragraph', []),
        }, found)
        self.assertFalse(any('location' in block or 'class=' in block['html'] for block in blocks))
        self.assertTrue(all(set(block['formatting']) >= {'enclosing', 'inline'} for block in blocks if 'formatting' in block))

    def test_fb2_class_hints_do_not_change_text_ids_kinds_or_markup(self):
        hinted = self.blocks(self.read(fb2(self.FB2_BODY), 'book.fb2'))
        with patch.dict(books.FB2_CLASSES, clear=True), tempfile.TemporaryDirectory() as other:
            plain = [block for chapter in import_book(fb2(self.FB2_BODY).encode(), 'book.fb2', Path(other))['chapters'] for block in chapter['blocks']]
        keys = ('id', 'kind', 'text', 'html', 'source', 'anchors', 'assets')
        self.assertEqual([[block.get(key) for key in keys] for block in plain], [[block.get(key) for key in keys] for block in hinted])
        self.assertFalse(any('classes' in block.get('formatting', {}) for block in plain))

    def test_fb2_existing_class_attribute_is_kept_with_native_hint(self):
        root = books.etree.fromstring('<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body><poem class="x strong"><stanza><v>Строка.</v></stanza></poem></body></FictionBook>')
        books.fb2_structure([root[0]])
        self.assertEqual('x strong poem', root[0][0].get('class'))
        self.assertEqual('poem', root[0][0][0][0].get('class'))


if __name__ == '__main__':
    unittest.main()
