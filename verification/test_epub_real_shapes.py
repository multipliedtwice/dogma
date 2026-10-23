from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lxml import etree
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.books import import_book, sanitized, xml
from rimsky.preparation import speech_sources
from rimsky.store import Store
from test_epub_preparation import make_epub, ncx_point


def png():
    output = io.BytesIO()
    Image.new('RGB', (4, 8), (30, 90, 160)).save(output, 'PNG')
    return output.getvalue()


def with_cover():
    return make_epub('<p>Title page.</p>', ncx=ncx_point('Title', ''),
        metadata='<meta name="cover" content="art"/>',
        manifest='<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="art" href="cover.png" media-type="image/png"/>',
        spine='<itemref idref="cover"/><itemref idref="story"/>',
        extra={'EPUB/cover.png': png(), 'EPUB/cover.xhtml': b'<html><body><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 800"><image xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="cover.png" width="500" height="800"/></svg></body></html>'})


class ConvertedEpubShapes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, raw):
        return import_book(raw, 'converted.epub', self.root / 'books')

    def test_cover_retains_svg_image_namespace_and_geometry(self):
        book = self.read(with_cover())
        block = book['chapters'][0]['blocks'][0]
        node = etree.fromstring(block['html'].encode())
        images = node.xpath('.//*[local-name()="image"]')
        self.assertEqual(1, len(images))
        self.assertEqual('http://www.w3.org/2000/svg', etree.QName(images[0]).namespace)
        self.assertEqual(('500', '800'), (images[0].get('width'), images[0].get('height')))
        self.assertEqual(book['cover'], images[0].get('data-asset'))
        self.assertNotIn('href=', block['html'])
        self.assertNotIn('<img', block['html'])

    def test_metadata_cover_is_named_without_using_class_heuristics(self):
        book = self.read(with_cover())
        cover = book['chapters'][0]
        self.assertEqual('cover', cover['role'])
        self.assertEqual('Обложка', cover['title'])
        self.assertFalse(cover['default_narration'])

    def test_no_declared_cover_does_not_relabel_an_ordinary_illustration(self):
        book = self.read(make_epub('<img src="picture.png"/><p>Story.</p>',
            extra={'EPUB/picture.png': png()}))
        self.assertEqual('body', book['chapters'][0]['role'])
        self.assertTrue(book['chapters'][0]['default_narration'])

    def test_cover_image_inside_prose_does_not_exclude_the_story(self):
        book = self.read(make_epub('<img src="picture.png"/><p>Story.</p>',
            metadata='<meta name="cover" content="art"/>',
            manifest='<item id="art" href="picture.png" media-type="image/png"/>',
            extra={'EPUB/picture.png': png()}))
        self.assertTrue(book['chapters'][0]['default_narration'])
        self.assertEqual('body', book['chapters'][0]['role'])

    def test_svg_source_urls_and_event_handlers_are_not_retained(self):
        node = xml(b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://outside.invalid/a.png" onload="alert(1)" width="20" height="30"/></svg>')
        markup = sanitized(node, lambda _: None)
        self.assertNotIn('outside.invalid', markup)
        self.assertNotIn('onload', markup)
        node[0].set('href', 'local.png')
        node[0].set('transform', 'url(https://outside.invalid/)')
        markup = sanitized(node, lambda _: 'a' * 64 + '.png')
        self.assertNotIn('transform=', markup)
        self.assertNotIn('href=', markup)
        self.assertIn('width="20"', markup)

    def test_ncx_title_paragraph_becomes_heading_without_changing_prose(self):
        book = self.read(make_epub('<span id="a"><div><p>Начало</p></div><p>— Пойдём.</p></span>',
            ncx=ncx_point('Начало', 'a')))
        first, second = book['chapters'][0]['blocks']
        self.assertEqual('heading', first['kind'])
        self.assertEqual('navigation-label', first['heading_source'])
        self.assertIn('<h2>', first['html'])
        self.assertEqual('Начало', first['text'])
        self.assertEqual('paragraph', second['kind'])
        self.assertEqual('— Пойдём.', second['text'])
        self.assertEqual(first['id'], book['navigation'][0]['source_id'])

    def test_navigation_label_does_not_promote_a_different_paragraph(self):
        book = self.read(make_epub('<p id="a">— Начало? — переспросили.</p>', ncx=ncx_point('Начало', 'a')))
        self.assertEqual('paragraph', book['chapters'][0]['blocks'][0]['kind'])

    def test_title_page_fallback_uses_existing_book_title_without_dropping_author(self):
        book = self.read(make_epub(extra={'EPUB/story.xhtml': '<html><head><title/></head><body><p>Автор</p><p>Искусственная книга для проверки</p></body></html>'.encode()}))
        self.assertEqual('Искусственная книга для проверки', book['chapters'][0]['title'])
        self.assertTrue(book['chapters'][0]['default_narration'])
        self.assertEqual(['Автор', 'Искусственная книга для проверки'],
                         [block['text'] for block in book['chapters'][0]['blocks']])

    def test_code_containers_preserve_paragraphs_formatting_and_spoken_text(self):
        book = self.read(make_epub('<div><code><p>Уровень: 7</p><p>Энергия: <strong>40</strong></p></code></div><p>— Дальше.</p>'))
        blocks = book['chapters'][0]['blocks']
        self.assertEqual(['Уровень: 7', 'Энергия: 40', '— Дальше.'], [b['text'] for b in blocks])
        for block in blocks[:2]:
            self.assertIn('<code>', block['html'])
            self.assertEqual(['code'], block['formatting']['enclosing'])
            self.assertEqual('paragraph', block['kind'])
        self.assertNotIn('formatting', blocks[-1])
        sources = list(speech_sources(book, book['preparation']['default_chapters']))
        self.assertEqual(['Уровень: 7', 'Энергия: 40', '— Дальше.'], [s['text'] for s in sources])
        self.assertEqual(['code'], sources[0]['formatting']['enclosing'])
        self.assertNotIn('speaker', sources[0])

    def test_nested_formatting_does_not_lose_source_anchor_or_inline_text(self):
        book = self.read(make_epub('<strong><code><p id="a">А <em>Б</em> В.</p></code></strong>',
            ncx=ncx_point('Другой заголовок', 'a')))
        block = book['chapters'][0]['blocks'][0]
        self.assertEqual('А Б В.', block['text'])
        self.assertEqual(['code', 'strong'], block['formatting']['enclosing'])
        self.assertEqual(['emphasis'], block['formatting']['inline'])
        self.assertIn('a', block['anchors'])
        self.assertIn('<em>Б</em>', block['html'])
        self.assertEqual('EPUB/story.xhtml', block['location']['href'])

    def test_quote_context_retains_separate_dialogue_paragraphs(self):
        book = self.read(make_epub('<blockquote><p>— Первый.</p><p>— Второй.</p></blockquote>'))
        blocks = book['chapters'][0]['blocks']
        self.assertEqual(2, len(blocks))
        for block in blocks:
            self.assertEqual(['quotation'], block['formatting']['enclosing'])
            self.assertIn('<blockquote>', block['html'])

    def test_inline_markup_is_not_claimed_as_enclosing_formatting(self):
        block = self.read(make_epub('<p>Надпись <code>А</code>, не вся строка.</p>'))['chapters'][0]['blocks'][0]
        self.assertEqual({'enclosing': [], 'inline': ['code']}, block['formatting'])
        self.assertEqual('Надпись А, не вся строка.', block['text'])

    def test_fb2_css_classes_preserve_display_and_semantic_structure(self):
        story = (b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                 b'<link rel="stylesheet" href="book.css"/></head><body>'
                 b'<p><span class="strong">SYSTEM CARD</span></p>'
                 b'<p><span class="emphasis">Place name</span></p>'
                 b'<p class="cite">Quoted document</p>'
                 b'<p class="text-author">Archive author</p>'
                 b'</body></html>')
        raw = make_epub(manifest='<item id="css" href="book.css" media-type="text/css"/>',
                        extra={'EPUB/story.xhtml': story,
                               'EPUB/book.css': (b'.strong {font-weight: 700} '
                                                 b'.emphasis {font-style: italic}')})
        blocks = self.read(raw)['chapters'][0]['blocks']
        self.assertIn('<strong>SYSTEM CARD</strong>', blocks[0]['html'])
        self.assertIn('<em>Place name</em>', blocks[1]['html'])
        self.assertEqual(['strong'], blocks[0]['formatting']['inline'])
        self.assertEqual(['strong'], blocks[0]['formatting']['full'])
        self.assertEqual(['strong'], blocks[0]['formatting']['classes']['inline'])
        self.assertEqual(['emphasis'], blocks[1]['formatting']['inline'])
        self.assertEqual(['cite'], blocks[2]['formatting']['classes']['enclosing'])
        self.assertEqual(['text-author'], blocks[3]['formatting']['classes']['enclosing'])

        from rimsky.analysis import analysis_target
        target = analysis_target({'id': 'card', 'text': blocks[0]['text'],
                                  'kind': 'paragraph',
                                  'formatting': blocks[0]['formatting']})
        self.assertEqual(['strong'], target['formatting']['classes']['inline'])
        self.assertEqual(['strong'], target['formatting']['full'])

    def test_analysis_receives_bounded_styling_not_raw_markup(self):
        from rimsky.analysis import analysis_target
        target = analysis_target({'id': 'p1', 'text': 'Текст', 'kind': 'paragraph',
            'html': '<script>unsafe</script>', 'formatting': {'enclosing': ['code', 'run-command', 'code'],
            'inline': ['strong', {}, 4], 'other': 'unused'}})
        self.assertEqual({'id': 'p1', 'units': [{'n': 0, 'text': 'Текст'}], 'kind': 'paragraph',
            'formatting': {'enclosing': ['code'], 'inline': ['strong']}}, target)
        self.assertEqual({'id': 'p1', 'units': [{'n': 0, 'text': 'Текст'}]}, analysis_target({'id': 'p1', 'text': 'Текст'}))

    def test_worker_passes_formatting_to_the_model_request(self):
        import worker
        from unittest.mock import Mock
        model = Mock()
        model.tokenize.return_value = [1]
        model.create_chat_completion.return_value = {'choices': [{'message': {'content': '{"characters": [], "passages": []}'}, 'finish_reason': 'stop'}]}
        source = {'id': 'p1', 'text': 'Уровень: 7', 'kind': 'paragraph', 'formatting': {'enclosing': ['code'], 'inline': []}}
        with patch.object(worker, 'load_text', return_value=model):
            worker.analyze({'pack': {'text_model': {'context': 8192, 'analysis_mode': 'full'}}, 'device': 'cpu', 'segments': [source], 'cast': []})
        content = json.loads(model.create_chat_completion.call_args.kwargs['messages'][1]['content'])
        self.assertEqual({'id': 'p1', 'units': [{'n': 0, 'text': 'Уровень: 7'}], 'kind': 'paragraph', 'formatting': source['formatting']}, content['targets'][0])

    def test_saved_manifest_survives_restart_without_changing_source_ids(self):
        store = Store(self.root / 'library')
        book = store.import_data(make_epub('<code><p>Параметр: 3</p></code>'), 'save.epub')
        store.close()
        reopened = Store(self.root / 'library')
        try:
            saved = reopened.book(book['id'])
            self.assertEqual(book['chapters'], saved['chapters'])
            self.assertEqual(book['preparation'], saved['preparation'])
        finally:
            reopened.close()


if __name__ == '__main__':
    unittest.main()
