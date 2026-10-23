from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from rimsky import books, epub
from rimsky.books import import_book
from rimsky.preparation import default_chapters
from test_epub_preparation import make_epub
from test_fix3_import_1004 import NAV
from test_import_fixes_1004 import documents, page

CHAPTERS = ''.join(f'<h1 id="c{n}">Глава {n}</h1>' + ''.join(f'<p>— Реплика {n}.{k}, — сказал Иван.</p>' for k in range(1, 4)) for n in range(1, 4))
INLINE = '<h2 id="toc">Содержание</h2>' + ''.join(f'<p><a href="#c{n}">Глава {n}</a></p>' for n in range(1, 4))
NCX = ''.join(f'<navPoint><navLabel><text>{label}</text></navLabel><content src="{name}.xhtml"/></navPoint>' for name, label in (('title', 'Титул'), ('ch1', 'Глава 9')))
TOC = '<li><a epub:type="toc" href="story.xhtml#toc">Содержание</a></li>', '<reference type="toc" title="Содержание" href="story.xhtml#toc"/>'
COVER = '<li><a epub:type="cover" href="story.xhtml#cover">Обложка</a></li>', '<reference type="cover" title="Обложка" href="story.xhtml#cover"/>'


class Fix3gEpub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, story, reference, extra=None):
        manifest, _ = documents('title', 'ch1', 'notes')
        files = {'EPUB/title.xhtml': page('<h1>Книга</h1><p>Автор</p>'), 'EPUB/ch1.xhtml': page('<h1>Глава 9</h1><p>— Конец, — сказал Иван.</p>'),
                 'EPUB/notes.xhtml': page('<aside epub:type="footnote" id="n1"><p>Сноска.</p></aside>'), **(extra or {})}
        options = {'manifest': manifest, 'spine': '<itemref idref="title"/><itemref idref="story"/><itemref idref="ch1"/>', 'extra': files}
        if reference.startswith('<li>'):
            files['EPUB/nav.xhtml'] = NAV.format(toc='<li><a href="title.xhtml">Титул</a></li><li><a href="ch1.xhtml">Глава 9</a></li>', landmarks=reference).encode()
            return import_book(make_epub(story, nav='', **options), 'book.epub', self.root / 'books')
        return import_book(make_epub(story, ncx=NCX, guide=f'<guide>{reference}</guide>', **options), 'book.epub', self.root / 'books')

    def roles(self, book):
        return [(chapter['role'], chapter['default_narration']) for chapter in book['chapters'] if chapter['source'] == 'EPUB/story.xhtml']

    def linear(self, book):
        return [chapter['id'] for chapter in book['chapters'] if chapter['linear']]

    def test_start_fragments_keep_documents_with_body_text_primary(self):
        noted = '<h1 id="toc">Глава 1</h1>' + ''.join(f'<p>Абзац {n}<a href="notes.xhtml#n1">{n}</a>.</p>' for n in range(1, 4))
        minority = '<h1 id="toc">Содержание</h1><p><a href="ch1.xhtml">Глава 9</a></p>' + CHAPTERS
        inside = INLINE + ''.join(f'<p><a href="#c1">Глава {n}</a></p>' for n in range(4, 9)) + '<h1 id="c1">Глава 1</h1><p>— Реплика, — сказал Иван.</p>'
        for label, story, references, count in (('inline table of contents', INLINE + CHAPTERS, TOC, 4), ('links to other documents are a minority', minority, TOC, 4),
                                                ('links within the document are a majority', inside, TOC, 2),
                                                ('note references to another document', noted, TOC, 1),
                                                ('first chapter heading', CHAPTERS.replace('id="c1"', 'id="cover"'), COVER, 3),
                                                ('wrapper at the top', '<div id="cover"><p>Книга</p></div>' + CHAPTERS, COVER, 4),
                                                ('title with an author line', '<h1 id="cover">Книга</h1><p>Автор Авторович</p>', COVER, 1)):
            for reference in references:
                with self.subTest(label, reference=reference):
                    book = self.book(story, reference)
                    self.assertEqual([('body', True)] * count, self.roles(book))
                    self.assertEqual(self.linear(book), default_chapters(book))

    def test_start_fragments_still_mark_table_of_contents_and_cover_pages(self):
        image = io.BytesIO()
        Image.new('RGB', (40, 60), 'red').save(image, 'PNG')
        for label, story, references, role in (
                ('list items', '<h1 id="toc">Содержание</h1><ol><li><a href="title.xhtml">Титул</a></li><li><a href="ch1.xhtml">Глава 9</a></li></ol>', TOC, 'navigation'),
                ('links with page numbers', '<a id="toc"/><h1>Содержание</h1><p><a href="title.xhtml">Титул</a> … 1</p><p><a href="ch1.xhtml">Глава 9</a> … 5</p>', TOC, 'navigation'),
                ('number-only fragment links', '<h1 id="toc">Содержание</h1><p><a href="title.xhtml#t">1</a></p><p><a href="ch1.xhtml#c">2</a></p>', TOC, 'navigation'),
                ('page-number fragment links', '<h1 id="toc">Содержание</h1><p>Титул … <a href="title.xhtml#t">1</a></p><p>Глава 9 … <a href="ch1.xhtml#c">7</a></p>', TOC, 'navigation'),
                ('list of number-only fragment links', '<h1 id="toc">Содержание</h1><ol><li><a href="title.xhtml#t">1</a></li><li><a href="ch1.xhtml#c">2</a></li></ol>', TOC, 'navigation'),
                ('heading and figure', '<h1 id="cover">Книга</h1><div><img src="cover.png" alt=""/></div>', COVER, 'cover'),
                ('figure and headings', '<a id="cover"/><div><img src="cover.png" alt=""/></div><h1>Книга</h1><h2>Автор Авторович</h2>', COVER, 'cover')):
            for reference in references:
                with self.subTest(label, reference=reference):
                    book = self.book(story, reference, {'EPUB/cover.png': image.getvalue()})
                    self.assertEqual({(role, False)}, set(self.roles(book)))
                    self.assertEqual([chapter['id'] for chapter in book['chapters'] if chapter['source'] != 'EPUB/story.xhtml'], default_chapters(book))

    def test_table_of_contents_shape_needs_more_than_half_of_the_passages_to_link_out(self):
        for markup, linked, expected in (('<h1 id="toc">Содержание</h1><p>Глава 1</p><p>Глава 2</p>', {'Глава 1', 'Глава 2'}, {'toc'}),
                                         ('<h1 id="toc">Содержание</h1><p>Глава 1</p><p>Глава 2</p>', {'Глава 1'}, set()),
                                         ('<h1 id="toc">Содержание</h1><p>Глава 1</p>', {'Глава 1'}, set()),
                                         ('<h1 id="toc">Содержание</h1><div><img src="a.png"/></div><p>Глава 1</p><p>Глава 2</p>', {'Глава 1', 'Глава 2'}, {'toc'})):
            with self.subTest(markup, linked=linked):
                body = books.xml(page(markup)).xpath('.//*[local-name()="body"]')[0]
                blocks = books.extract_blocks(body, 'd0001', lambda source: 'a', track_source=True, resolve_link=lambda source: None)
                self.assertEqual(expected, epub.semantic_types({('toc', 'toc')}, body, blocks, False, lambda block: block['text'] in linked))
                self.assertEqual(set(), epub.semantic_types({('toc', 'toc')}, body, blocks, True, lambda block: True))


if __name__ == '__main__':
    unittest.main()
