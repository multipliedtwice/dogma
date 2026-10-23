from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky.books import import_book
from rimsky.util import AppError
from server import Application, handler_for
from test_epub_preparation import make_epub

XHTML11 = '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
UNRECOVERABLE = {'EPUB/story.xhtml': b'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><body><p></p><div>'}


def story(body, doctype=''):
    return {'EPUB/story.xhtml': (f'<?xml version="1.0" encoding="utf-8"?>{doctype}<html xmlns="http://www.w3.org/1999/xhtml" '
                                 f'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>One</title></head><body>{body}</body></html>').encode()}


def fb2(body, notes=''):
    return ('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>prose</genre><author><first-name>A</first-name>'
            '<last-name>B</last-name></author><book-title>Notes</book-title><lang>ru</lang></title-info></description>'
            f'<body><section><title><p>Глава</p></title>{body}</section></body>{notes}</FictionBook>').encode()


class ImportCorrectness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data, name, self.root)

    def texts(self, book):
        return [block['text'] for chapter in book['chapters'] for block in chapter['blocks']]

    def test_xhtml_named_entities_import_as_characters(self):
        for doctype in (XHTML11, ''):
            book = self.read(make_epub(extra=story('<p>Hello&nbsp;world &mdash; &laquo;quoted&raquo; &hellip;</p>', doctype)))
            self.assertIn('Hello world — «quoted» …', self.texts(book))

    def test_predefined_entities_cdata_and_unknown_entities_keep_xml_rules(self):
        book = self.read(make_epub(extra=story('<p>R&amp;D &lt;tag&gt; <![CDATA[&nbsp;]]></p>', XHTML11)))
        self.assertIn('R&D <tag> &nbsp;', self.texts(book))
        with self.assertRaises(AppError):
            self.read(make_epub(extra=story('<p>&unknownentity;</p>', XHTML11)))

    def test_unclosed_cdata_is_scanned_once(self):
        import time
        from rimsky.books import numeric_entities
        self.assertEqual(b'&#160; <![CDATA[ &nbsp; & tail', numeric_entities(b'&nbsp; <![CDATA[ &nbsp; & tail'))
        data = (b'<p>&nbsp; <![CDATA[ unclosed ' + b'x' * 9000 + b'</p>\n') * 900
        started = time.monotonic()
        numeric_entities(data)
        self.assertLess(time.monotonic() - started, 5)

    def test_malformed_xml_reports_document_and_line_without_parser_internals(self):
        recovered = self.read(make_epub(extra=story('<p>AT&T builds phones.</p>')))
        self.assertIn('AT&T builds phones.', self.texts(recovered))
        self.assertTrue(any('EPUB/story.xhtml' in warning for warning in recovered['warnings']))
        with self.assertRaises(AppError) as caught:
            self.read(make_epub(extra=UNRECOVERABLE))
        error = caught.exception
        self.assertEqual('invalid_book_xml', error.code)
        self.assertEqual('EPUB/story.xhtml', error.details['document'])
        self.assertIsInstance(error.details['line'], int)
        self.assertEqual(f'Book document EPUB/story.xhtml is not well-formed XML at line {error.details["line"]}.', str(error))
        self.assertNotIn('EntityRef', str(error))
        with self.assertRaises(AppError) as caught:
            self.read(b'<?xml version="1.0"?><FictionBook', 'broken.fb2')
        self.assertEqual('invalid_book_xml', caught.exception.code)
        self.assertEqual('', caught.exception.details['document'])
        self.assertTrue(str(caught.exception).startswith('The book is not well-formed XML'))

    def test_fb2_inline_style_text_is_kept_for_reading_and_narration(self):
        book = self.read(fb2('<p>Он сказал: <style name="foreign">Bonjour</style>, и ушёл.</p>'), 'style.fb2')
        self.assertIn('Он сказал: Bonjour, и ушёл.', self.texts(book))
        self.assertIn('Bonjour', book['chapters'][0]['blocks'][1]['html'])

    def test_xhtml_style_elements_are_still_dropped(self):
        book = self.read(make_epub(extra=story('<style>p{color:red}</style><p>Visible text.</p>')))
        self.assertEqual(['Visible text.'], [text for text in self.texts(book) if text != 'One'])

    def test_fb2_note_markers_stay_linked_but_are_not_narrated_and_notes_form_one_chapter(self):
        notes = ('<body name="notes"><title><p>Примечания</p></title><section id="n1"><title><p>1</p></title><p>Первое.</p></section>'
                 '<section id="n2"><title><p>2</p></title><p>Второе.</p></section></body>')
        book = self.read(fb2('<p>Текст<a l:href="#n1" type="note">1</a> и ещё<a l:href="#n2" type="note">2</a>.</p>', notes), 'notes.fb2')
        story_chapter, notes_chapter = book['chapters']
        paragraph = story_chapter['blocks'][1]
        self.assertEqual('Текст и ещё.', paragraph['text'])
        self.assertEqual(['Примечания', 'Примечания'], [notes_chapter['title'], notes_chapter['blocks'][0]['text']])
        self.assertEqual(['1', 'Первое.', '2', 'Второе.'], [block['text'] for block in notes_chapter['blocks'][1:]])
        self.assertFalse(notes_chapter['default_narration'])
        first_note = notes_chapter['blocks'][1]['id']
        self.assertIn(f'data-source-link="{first_note}"', paragraph['html'])
        self.assertIn('data-note-ref=""', paragraph['html'])
        self.assertIn('>1</span>', paragraph['html'])

    def test_epub_noterefs_are_not_narrated(self):
        body = ('<p>Текст<sup><a epub:type="noteref" href="#n1">1</a></sup>.</p><p>Далее<a role="doc-noteref" href="#n1">*</a> идёт.</p>'
                '<aside epub:type="footnote" id="n1"><p>Сноска.</p></aside>')
        texts = self.texts(self.read(make_epub(extra=story(body))))
        self.assertIn('Текст.', texts)
        self.assertIn('Далее идёт.', texts)

    def test_import_endpoint_returns_structured_xml_error(self):
        app = Application(self.root / 'http-library', self.root)
        token = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, token, dev=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = make_epub(extra=UNRECOVERABLE)
            request = Request(f'http://127.0.0.1:{server.server_port}/import?name=broken.epub', data=data,
                              headers={'Authorization': 'Bearer ' + token})
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(400, rejected.exception.code)
            payload = json.load(rejected.exception)
            self.assertEqual('invalid_book_xml', payload['code'])
            self.assertEqual('EPUB/story.xhtml', payload['details']['document'])
            self.assertIsInstance(payload['details']['line'], int)
            self.assertNotIn('EntityRef', payload['error'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    unittest.main()
