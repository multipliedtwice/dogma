from __future__ import annotations

import functools
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky import books
from rimsky.books import character_data, import_book
from rimsky.util import AppError
from server import Application, handler_for
from test_epub_preparation import make_epub
from test_import_fixes_1004 import documents, fb2, page
from test_import_robustness_0925 import png

HEAD = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Документ</title></head><body>'


def story(body, declaration='', encoding='utf-8'):
    return {'EPUB/story.xhtml': (declaration + HEAD + body + '</body></html>').encode(encoding)}


def repacked(data, compression=zipfile.ZIP_STORED):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w', compression) as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item))
    return out.getvalue()


def central_records(data):
    position = data.find(b'PK\x01\x02')
    while position >= 0:
        size = struct.unpack('<H', data[position + 28:position + 30])[0]
        yield position, data[position + 46:position + 46 + size].decode()
        position = data.find(b'PK\x01\x02', position + 46 + size)


def damaged(data, name, offset=60):
    data = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        item = archive.getinfo(name)
    start = item.header_offset + 30 + len(item.filename.encode()) + len(item.extra)
    data[start + offset] ^= 0xFF
    return bytes(data)


def unsupported(data, name, method=97):
    data = bytearray(data)
    for position, filename in central_records(bytes(data)):
        if filename == name:
            data[position + 10:position + 12] = struct.pack('<H', method)
    return bytes(data)


class ImportSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data if isinstance(data, bytes) else data.encode(), name, self.root / 'books')

    def rejected(self, data, name='book.epub'):
        with self.assertRaises(AppError) as caught:
            self.read(data, name)
        return caught.exception

    def blocks(self, book):
        return [(block['kind'], block['text']) for chapter in book['chapters'] for block in chapter['blocks']]

    def recovered(self, body):
        book = self.read(make_epub(extra=story(body)))
        self.assertTrue(any('EPUB/story.xhtml' in warning and 'recovery mode and all of its text was kept' in warning for warning in book['warnings']))
        return self.blocks(book)

    def test_fb2_inline_node_flood_is_rejected_during_parsing_before_extraction(self):
        data = fb2('<section><title><p>Глава</p></title><p>' + '<emphasis>а</emphasis>' * 2_000 + '</p></section>')
        with patch.dict(books.XML_LIMITS, {'elements': 500}), patch.object(books, 'extract_blocks', side_effect=AssertionError('extracted')):
            error = self.rejected(data, 'flood.fb2')
        self.assertEqual(('book_markup_limit', books.XML_LIMIT_MESSAGE), (error.code, str(error)))

    def test_default_markup_limits_are_locked(self):
        self.assertEqual({'elements': 200_000, 'attributes': 400_000, 'texts': 400_000}, books.XML_LIMITS)

    def test_extraction_state_is_released_when_extraction_fails(self):
        caches, original = [], functools.cache
        class Recorded:
            def __init__(self, function):
                self.function, self.cleared = original(function), False
                caches.append(self)
            def __call__(self):
                return self.function()
            def cache_clear(self):
                self.cleared = True
                self.function.cache_clear()
        cache = Recorded
        root = books.xml(page('<p>До <img src="x.png"/> после</p>'))
        def broken(src):
            raise AppError('image failed')
        with patch.object(books.functools, 'cache', cache), self.assertRaisesRegex(AppError, 'image failed'):
            books.extract_blocks(root, 'c1', broken)
        self.assertEqual([True, True], [item.cleared for item in caches])

    def test_epub_node_budget_is_summed_across_content_documents(self):
        names = [f'part{index}' for index in range(4)]
        manifest, spine = documents(*names)
        extra = {f'EPUB/{name}.xhtml': page('<p>' + '<span>а</span>' * 100 + '</p>') for name in names}
        data = repacked(make_epub(manifest=manifest, spine=spine, extra=extra))
        with patch.dict(books.XML_LIMITS, {'elements': 380}):
            self.assertEqual('book_markup_limit', self.rejected(data).code)
        with patch.dict(books.XML_LIMITS, {'elements': 600}):
            self.assertEqual(4, len(self.read(data)['chapters']))

    def test_each_markup_counter_is_enforced(self):
        markup = '<section><p>' + '<emphasis id="x" name="y">а</emphasis>б' * 80 + '</p></section>'
        for key, limit in (('elements', 60), ('attributes', 60), ('texts', 60)):
            with self.subTest(key=key), patch.dict(books.XML_LIMITS, {key: limit}):
                self.assertEqual('book_markup_limit', self.rejected(fb2(markup), 'small.fb2').code)
        self.assertEqual('Книга', self.read(fb2(markup), 'small.fb2')['title'])

    def test_declared_entity_flood_and_deep_nesting_are_rejected_before_tree_use(self):
        data = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE FictionBook [<!ENTITY e "а">]>' + fb2('<section><p>' + '&e;' * 5_000 + '</p></section>', declaration='')).encode()
        with patch.object(books, 'bounded_parse', side_effect=AssertionError('parsed')):
            self.assertEqual('Entity references are not accepted in books.', str(self.rejected(data, 'entities.fb2')))
        deep = fb2('<section>' + '<cite>' * 300 + '<p>а</p>' + '</cite>' * 300 + '</section>')
        self.assertEqual('Book XML nesting exceeds the safety limit.', str(self.rejected(deep, 'deep.fb2')))

    def test_unclosed_paragraphs_and_line_breaks_keep_text_and_paragraphs(self):
        self.assertEqual([('paragraph', 'Один'), ('paragraph', 'Два Три'), ('paragraph', 'Четыре')],
                         self.recovered('<p>Один<p>Два<br>Три<p>Четыре'))

    def test_void_image_keeps_following_text(self):
        self.assertEqual([('paragraph', 'До'), ('paragraph', 'После')], self.recovered('<p>До</p><img src="x.jpg"><p>После</p>'))

    def test_void_rule_keeps_following_text_and_separator(self):
        self.assertEqual([('paragraph', 'Сцена 1'), ('separator', ''), ('paragraph', 'Сцена 2')],
                         self.recovered('<p>Сцена 1</p><hr><p>Сцена 2</p>'))

    def test_unclosed_dialogue_paragraphs_stay_separate(self):
        self.assertEqual([('paragraph', '— Один, — сказал он.'), ('paragraph', '— Два!'), ('paragraph', 'Три.')],
                         self.recovered('<p>— Один, — сказал он.<p>— Два!<p>Три.'))

    def test_recovered_line_break_and_mismatched_inline_tags_lose_nothing(self):
        self.assertEqual([('paragraph', 'Строка — Реплика'), ('paragraph', 'Абзац два')], self.recovered('<p>Строка<br>— Реплика</p><p>Абзац два</p>'))
        self.assertEqual([('paragraph', 'Один курсив'), ('paragraph', 'Два')], self.recovered('<p>Один <i>курсив</p><p>Два</p>'))
        self.assertEqual([('paragraph', 'Том & Джерри'), ('paragraph', 'a < b и дальше')], self.recovered('<p>Том & Джерри</p><p>a < b и дальше</p>'))

    def test_recovered_document_keeps_note_and_svg_semantics_without_crashing(self):
        blocks = self.recovered('<p>Tom & Jerry<a epub:type="noteref" href="#n1">1</a>.</p><aside epub:type="footnote" id="n1"><p>Сноска.</p></aside>'
                                '<svg:svg xmlns:svg="http://www.w3.org/2000/svg" viewBox="0 0 2 2"><svg:path d="M0 0L1 1"/></svg:svg>'
                                '<p>Странный <a=b>тег</a=b> конец.</p>')
        self.assertEqual([('paragraph', 'Tom & Jerry.'), ('note', 'Сноска.'), ('figure', ''), ('paragraph', 'Странный тег конец.')], blocks)
        book = self.read(make_epub(extra=story('<p>A & B</p><svg viewBox="0 0 2 2"><path d="M0 0"/></svg>')))
        self.assertIn('<svg viewBox="0 0 2 2" xmlns="http://www.w3.org/2000/svg">', book['chapters'][0]['blocks'][1]['html'])

    def test_lossy_recovery_is_rejected(self):
        for body in ('<p>A & B</p><p><![CDATA[скрыто]]></p>', '<p>A & B</p></body></html><p>Хвост</p>', '<p>A & B &unknownentity;</p>',
                     '<p>A & B</p><title>а <b>б</b></title>'):
            with self.subTest(body=body):
                error = self.rejected(make_epub(extra=story(body)))
                self.assertEqual(('invalid_book_xml', 'EPUB/story.xhtml'), (error.code, error.details['document']))

    def test_undeclared_cp1251_xhtml_is_rejected_without_replacement_characters(self):
        meta = '<meta http-equiv="Content-Type" content="text/html; charset=windows-1251"/>'
        for data in (story('<p>— Привет, — сказал он.</p>', encoding='cp1251'),
                     {'EPUB/story.xhtml': HEAD.replace('<title>', meta + '<title>').encode('cp1251') + '<p>Привет</p></body></html>'.encode('cp1251')},
                     story('<p>Привет & пока</p>', encoding='cp1251'),
                     {'EPUB/story.xhtml': HEAD.encode() + b'<p>Caf\xe9</p></body></html>'}):
            with self.subTest(data=data):
                error = self.rejected(make_epub(extra=data))
                self.assertEqual(('invalid_book_encoding', 'EPUB/story.xhtml'), (error.code, error.details['document']))
                self.assertEqual('The text encoding of book document EPUB/story.xhtml could not be read.', str(error))

    def test_declared_cp1251_xhtml_still_imports_strictly_and_in_recovery(self):
        declaration = '<?xml version="1.0" encoding="windows-1251"?>'
        strict = self.read(make_epub(extra=story('<p>Привет.</p>', declaration, 'cp1251')))
        self.assertEqual([('paragraph', 'Привет.')], self.blocks(strict))
        self.assertFalse(any('recovery' in warning for warning in strict['warnings']))
        self.assertEqual([('paragraph', 'Привет & пока'), ('paragraph', 'Второй')],
                         self.blocks(self.read(make_epub(extra=story('<p>Привет & пока<p>Второй', declaration, 'cp1251')))))

    def test_damaged_zip_entries_are_import_errors(self):
        body = '<h1>Глава</h1>' + '<p>Текст абзаца номер один, достаточно длинный.</p>' * 200
        deflated = make_epub(body)
        stored = repacked(deflated)
        for label, data in (('deflate stream', damaged(deflated, 'EPUB/story.xhtml', 8)), ('crc', damaged(stored, 'EPUB/story.xhtml', 300)),
                            ('compression method', unsupported(deflated, 'EPUB/story.xhtml'))):
            with self.subTest(label=label):
                error = self.rejected(data)
                self.assertEqual(('damaged_book_entry', 'EPUB/story.xhtml'), (error.code, error.details['document']))
                self.assertEqual('EPUB entry EPUB/story.xhtml is damaged and cannot be read.', str(error))
        for name in ('EPUB/book.opf', 'META-INF/container.xml'):
            with self.subTest(name=name):
                self.assertEqual('damaged_book_entry', self.rejected(unsupported(deflated, name)).code)

    def test_damaged_image_entry_is_an_import_error(self):
        data = make_epub('<p>Текст.</p><img src="pic.png" alt=""/>', manifest='<item id="pic" href="pic.png" media-type="image/png"/>',
                         extra={'EPUB/pic.png': png((64, 64))})
        self.assertEqual(['Текст.', ''], [text for _, text in self.blocks(self.read(data))])
        error = self.rejected(damaged(repacked(data), 'EPUB/pic.png', 40))
        self.assertEqual(('damaged_book_entry', 'EPUB/pic.png'), (error.code, error.details['document']))

    def test_navigation_errors_that_bound_the_import_are_not_downgraded_to_warnings(self):
        nav = ''.join(f'<li><a href="story.xhtml">Раздел {index}</a></li>' for index in range(60))
        data = make_epub(nav=nav)
        self.assertEqual(1, len(self.read(data)['chapters']))
        with patch.dict(books.XML_LIMITS, {'elements': 100}):
            self.assertEqual('book_markup_limit', self.rejected(data).code)
        error = self.rejected(unsupported(data, 'EPUB/nav.xhtml'))
        self.assertEqual(('damaged_book_entry', 'EPUB/nav.xhtml'), (error.code, error.details['document']))

    def test_damaged_entry_is_a_structured_http_400(self):
        app = Application(self.root / 'http-library', self.root)
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, token, dev=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f'http://127.0.0.1:{server.server_port}/import?name=broken.epub', headers={'Authorization': 'Bearer ' + token},
                              data=damaged(repacked(make_epub('<p>' + 'Текст. ' * 400 + '</p>')), 'EPUB/story.xhtml', 300))
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=10)
            self.assertEqual(400, rejected.exception.code)
            payload = json.load(rejected.exception)
            self.assertEqual(('damaged_book_entry', 'EPUB/story.xhtml'), (payload['code'], payload['details']['document']))
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_valid_books_import_unchanged(self):
        body = ('<h1 id="c1">Глава первая</h1><p>— Продолжим? — спросил <em>Север</em>.<a epub:type="noteref" href="#n1">1</a></p>'
                '<ol><li>Один</li><li>Два</li></ol><hr/><p>R&amp;D &laquo;кавычки&raquo;</p><aside epub:type="footnote" id="n1"><p>Сноска.</p></aside>')
        book = self.read(make_epub(body))
        self.assertEqual([('heading', 'Глава первая'), ('paragraph', '— Продолжим? — спросил Север.'), ('paragraph', '1. Один'), ('paragraph', '2. Два'),
                          ('separator', ''), ('paragraph', 'R&D «кавычки»'), ('note', 'Сноска.')], self.blocks(book))
        self.assertEqual('<p>— Продолжим? — спросил <em>Север</em>.<span data-source-link="c0001-p00007" data-note-ref="">1</span></p>',
                         book['chapters'][0]['blocks'][1]['html'])
        self.assertEqual({'xpath': '/*/*[2]/*[2]', 'position': 2, 'href': 'EPUB/story.xhtml'}, book['chapters'][0]['blocks'][1]['location'])
        self.assertFalse(any('recovery' in warning for warning in book['warnings']))
        novel = self.read(fb2('<section><title><p>Глава</p></title><p>Он <emphasis>сказал</emphasis>.</p><empty-line/><p>Ещё.</p></section>'), 'novel.fb2')
        self.assertEqual([('heading', 'Глава'), ('paragraph', 'Он сказал.'), ('separator', ''), ('paragraph', 'Ещё.')], self.blocks(novel))
        self.assertEqual([], novel['warnings'])

    def test_filename_alt_of_an_image_that_cannot_be_shown_is_not_narrated(self):
        body = ('<p>Текст.</p><p class="illus"><img src="img/missing.jpg" alt="img_0001.jpg"/></p><p><img src="img/plan.jpg" alt="plan"/></p>'
                '<p><img src="img/x.tiff" alt="Image 3"/></p><p><img src="img/map.jpg" alt="Карта острова"/></p><p>Конец.</p>')
        self.assertEqual([('paragraph', 'Текст.'), ('paragraph', 'Карта острова'), ('paragraph', 'Конец.')],
                         [block for block in self.blocks(self.read(make_epub(body))) if block[0] == 'paragraph'])

    def test_source_paths_match_lxml_for_namespaced_and_repeated_siblings(self):
        from lxml import etree
        root = etree.fromstring(b'<a xmlns:s="urn:s" xmlns:t="urn:s"><s:b/><t:b/><b/><s:b/><?pi x?><!--c--><c xmlns="urn:d"><b/><b/><e/></c><b/></a>')
        tree = root.getroottree()
        paths = books.element_paths(tree)
        self.assertEqual([tree.getpath(node) for node in root.iter(etree.Element)], [paths[node] for node in root.iter(etree.Element)])

    def test_epub_locations_use_source_paths_and_fb2_builds_none(self):
        book = self.read(make_epub('<p>Один.</p><p>Два.</p>'))
        located = [block['location'] for chapter in book['chapters'] for block in chapter['blocks']]
        self.assertTrue(located and all(item['xpath'].startswith('/*/') for item in located))
        with patch.object(books, 'element_paths', side_effect=AssertionError('paths built for FB2')):
            novel = self.read(fb2('<section><title><p>Глава</p></title>' + '<p>Абзац.</p>' * 50 + '</section>'), 'novel.fb2')
        self.assertFalse(any('location' in block for chapter in novel['chapters'] for block in chapter['blocks']))

    def test_source_scan_handles_unterminated_markup(self):
        for text in ('<a "' * 2_000, '<a b' * 2_000, '<script ' * 2_000, '<![CDATA[' * 2_000, '<!--' * 2_000):
            with self.subTest(text=text[:9]):
                self.assertIsInstance(character_data(text)[0], str)


if __name__ == '__main__':
    unittest.main()
