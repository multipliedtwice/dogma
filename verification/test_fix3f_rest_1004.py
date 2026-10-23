from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server as runtime_server
from rimsky import books, epub
from rimsky.books import import_book
from rimsky.preparation import default_chapters, speech_sources
from rimsky.store import Store
from rimsky.util import write_json
from server import Application, pinned_packs
from test_core import FB2
from test_epub_preparation import make_epub
from test_fix3e_pipeline_1004 import RelocationCase
from test_import_fixes_1004 import documents, page

NAV = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{toc}</ol></nav>'
       '<nav epub:type="landmarks"><ol>{landmarks}</ol></nav></body></html>')
CHAPTER = '<navPoint><navLabel><text>Глава 1</text></navLabel><content src="story.xhtml"/></navPoint>'


class PinTimeRelocation(RelocationCase):
    def opened(self):
        app = Application(self.library, self.root / 'bin')
        try:
            if app.relocation:
                app.relocation.join(10)
            return app.store.settings()['pack_path'], app.dispatch('health', {})['pack']
        finally:
            app.close()

    def rerooted(self, original):
        store = Store(self.library)
        try:
            revision = store.revise(original['id'], [{'id': original['segments'][0]['id'], 'spoken': 'Другое чтение.'}])
            store.execute('UPDATE generations SET created=? WHERE id=?', ('2026-01-09T00:00:00Z', revision['id']))
            store.delete_generation(original['id'])
            revision = store.generation(revision['id'])
        finally:
            store.close()
        self.assertIsNone(revision['parent_id'])
        return revision

    def test_correction_that_outlives_its_original_keeps_the_newest_custom_pin(self):
        app = Application(self.library, self.root / 'bin')
        try:
            book = app.store.import_data(FB2, 'fixture.fb2')
            app.store.update_settings({'pack_path': str(self.custom)})
            manifest = json.loads((self.custom / 'pack.json').read_text('utf-8'))
            real = runtime_server.health

            def generate(name):
                write_json(self.custom / 'pack.json', {**manifest, 'name': name})
                with patch.object(runtime_server, 'health', lambda *arguments: {**real(*arguments), 'ready': True, 'missing': []}), \
                        patch.object(app.jobs, 'start', app.store.generation), app.mutations:
                    made = app.dispatch('generate', {'book_id': book['id']})
                app.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({'complete': True}), made['id']))
                return made
            older, newer = generate(self.copy['name']), generate('My edited voices')
            self.assertEqual([self.bundled['_fingerprint'], self.edited['_fingerprint']], [older['config']['pack_fingerprint'], newer['config']['pack_fingerprint']])
            self.assertRegex(older['config']['pinned_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$')
            self.assertLess(older['config']['pinned_at'], newer['config']['pinned_at'])
            with app.mutations:
                revision = app.dispatch('revise', {'id': older['id'], 'changes': [{'id': older['segments'][0]['id'], 'spoken': 'Другое чтение.'}]})
                app.dispatch('delete_generation', {'id': older['id']})
            revision = app.store.generation(revision['id'])
            self.assertEqual((None, older['config']['pinned_at']), (revision['parent_id'], revision['config']['pinned_at']))
            self.assertLessEqual(newer['created'], revision['created'])
            self.assertEqual([newer['config']['pinned_at'], older['config']['pinned_at']], sorted((pin for _, _, pin in pinned_packs(app.store)), reverse=True))
        finally:
            app.close()
        self.unplug()
        self.assertEqual(str(self.custom), self.selected())
        (self.root / 'unplugged').rename(self.root / 'usb-drive')
        path, pack = self.opened()
        self.assertEqual((str(self.custom), 'My edited voices'), (path, pack['name']))

    def test_newest_bundled_pin_relocates_despite_a_later_correction_of_an_older_custom_generation(self):
        older, _ = self.pinned(self.edited['_fingerprint'], self.copy['_fingerprint'], stamped=True)
        self.assertEqual('2026-01-01T00:00:00.000000+00:00', self.rerooted(older)['config']['pinned_at'])
        self.unplug()
        self.assertEqual(self.bundled['_root'], self.selected())

    def test_generations_without_a_pin_time_keep_a_folder_pinned_to_several_packs(self):
        older, _ = self.pinned(self.copy['_fingerprint'], self.edited['_fingerprint'])
        self.assertNotIn('pinned_at', self.rerooted(older)['config'])
        self.unplug()
        self.assertEqual(str(self.custom), self.selected())
        (self.root / 'unplugged').rename(self.root / 'usb-drive')
        path, pack = self.opened()
        self.assertEqual((str(self.custom), 'My edited voices'), (path, pack['name']))

    def test_generations_without_a_pin_time_and_a_newer_bundled_copy_keep_the_folder(self):
        self.pinned(self.edited['_fingerprint'], self.copy['_fingerprint'])
        self.unplug()
        self.assertEqual(str(self.custom), self.selected())

    def test_generations_without_a_pin_time_and_one_bundled_fingerprint_still_relocate(self):
        older, _ = self.pinned(self.copy['_fingerprint'], self.copy['_fingerprint'])
        self.rerooted(older)
        self.unplug()
        self.assertEqual(self.bundled['_root'], self.selected())


class Fix3fImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data, name, self.root / 'books')

    def roles(self, book):
        return [(chapter['title'], chapter['role'], chapter['default_narration']) for chapter in book['chapters']]

    def paged(self, name, body, reference, ncx=CHAPTER):
        manifest, spine = documents(name)
        if reference.startswith('<li>'):
            return self.read(make_epub('<h1>Глава 1</h1><p>— Привет, — сказал он.</p>', nav='', manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                       extra={'EPUB/nav.xhtml': NAV.format(toc='<li><a href="story.xhtml">Глава 1</a></li>', landmarks=reference).encode(),
                                              f'EPUB/{name}.xhtml': page(body)}))
        return self.read(make_epub('<h1>Глава 1</h1><p>— Привет, — сказал он.</p>', ncx=ncx, manifest=manifest, spine=spine + '<itemref idref="story"/>',
                                   guide=f'<guide>{reference}</guide>', extra={f'EPUB/{name}.xhtml': page(body)}))

    def test_toc_and_cover_fragments_at_the_start_of_a_page_outside_the_toc_set_its_role(self):
        links = '<p><a href="story.xhtml">Глава 1</a></p><p><a href="story.xhtml">Глава 2</a></p>'
        for label, body in (('heading id', f'<h1 id="toc">Содержание</h1>{links}'), ('empty top anchor', f'<a id="toc"/><h1>Содержание</h1>{links}'),
                            ('first paragraph', f'<p id="toc">Содержание</p>{links}'), ('anchor opening the heading', f'<h1><a id="toc"/>Содержание</h1>{links}'),
                            ('anchor closing the heading', f'<h1>Содержание<a id="toc"/></h1>{links}')):
            for reference in ('<reference type="toc" title="Содержание" href="toc.xhtml#toc"/>', '<li><a epub:type="toc" href="toc.xhtml#toc">Содержание</a></li>'):
                with self.subTest(label, reference=reference):
                    book = self.paged('toc', body, reference)
                    self.assertEqual(['navigation', 'body'], [role for _, role, _ in self.roles(book)])
                    self.assertEqual([book['chapters'][1]['id']], default_chapters(book))
        for reference in ('<reference type="cover" href="cover.xhtml#cover"/>', '<li><a epub:type="cover" href="cover.xhtml#cover">Обложка</a></li>'):
            with self.subTest(reference=reference):
                book = self.paged('cover', '<h1 id="cover">Книга</h1><h2>Автор Авторович</h2>', reference)
                self.assertEqual([('cover', False), ('body', True)], [(role, narrated) for _, role, narrated in self.roles(book)])

    def test_start_fragments_keep_the_role_of_toc_targets_later_anchors_and_other_types(self):
        toc = '<reference type="toc" title="Содержание" href="toc.xhtml#toc"/>'
        listed = CHAPTER + '<navPoint><navLabel><text>Содержание</text></navLabel><content src="toc.xhtml"/></navPoint>'
        links = ''.join(f'<p><a href="story.xhtml">Глава {n}</a></p>' for n in range(1, 5))
        for label, name, body, reference, ncx in (
                ('anchor after text', 'toc', f'<p>Вступление к книге.</p><h1 id="toc">Содержание</h1>{links}', toc, CHAPTER),
                ('anchor in a heading after text', 'toc', f'<p>Вступление к книге.</p><h1><a id="toc"/>Содержание</h1>{links}', toc, CHAPTER),
                ('anchor after a block in a container', 'toc', f'<div>Вступление.<p>Абзац.</p><a id="toc"/></div>{links}', toc, CHAPTER),
                ('page listed in the toc', 'toc', f'<h1 id="toc">Содержание</h1>{links}', toc, listed),
                ('title page type', 'title', '<h1 id="t">Книга</h1><p>Автор Авторович</p>', '<reference type="title-page" href="title.xhtml#t"/>', CHAPTER)):
            with self.subTest(label):
                book = self.paged(name, body, reference, ncx)
                self.assertTrue(all(role == 'body' and narrated for _, role, narrated in self.roles(book)), self.roles(book))

    def test_landmark_fragments_share_one_id_lookup_per_document(self):
        root = books.xml(page(''.join(f'<p id="p{index}">Абзац номер {index}.</p>' for index in range(400))))
        body = root.xpath('.//*[local-name()="body"]')[0]
        blocks = books.extract_blocks(body, 'd0001', lambda source: None, track_source=True, resolve_link=lambda source: None)
        marks = {('toc', f'missing{index}') for index in range(300)} | {('title-page', f'p{index}') for index in range(300)} | {('preface', f'p{index}') for index in range(300)}
        elements = sum(isinstance(node.tag, str) for node in body.iter())
        linked = lambda block: True
        with patch('rimsky.books.anchors_of', side_effect=books.anchors_of) as looked_up:
            self.assertEqual(set(), epub.semantic_types(marks, body, blocks, False, linked))
            self.assertEqual({'toc'}, epub.semantic_types(marks | {('toc', 'p0')}, body, blocks, False, linked))
            self.assertEqual(set(), epub.semantic_types(marks | {('toc', 'p0')}, body, blocks, True, linked))
        self.assertEqual(3 * elements, looked_up.call_count)
        with patch('rimsky.books.anchors_of', side_effect=books.anchors_of) as looked_up:
            self.assertEqual({'toc'}, epub.semantic_types({('toc', ''), ('preface', 'p1')}, body, blocks, True, linked))
        self.assertEqual(0, looked_up.call_count)

    def test_drop_cap_merge_treats_line_break_whitespace_as_markup(self):
        for body, text, markup in (('<p class="dropcap">Т</p>\n<p>\n    ак началось утро.</p>', 'Так началось утро.', '<p>Так началось утро.</p>'),
                                   ('<p class="dropcap">Т</p><p>\n\t\t\t<em>ак</em> началось утро.</p>', 'Так началось утро.', '<p>Т<em>ак</em> началось утро.</p>'),
                                   ('<p class="dropcap">Т</p><p><span>\n    ак</span> началось утро.</p>', 'Так началось утро.', '<p>Т<span>ак</span> началось утро.</p>'),
                                   ('<blockquote><p class="dropcap">Т</p>\n<p>\n  ак началось.</p></blockquote>', 'Так началось.', '<blockquote><p>Так началось.</p></blockquote>'),
                                   ('<p class="dropcap">В</p><p>\n    тот вечер мы молчали.</p>', 'Втот вечер мы молчали.', '<p>Втот вечер мы молчали.</p>'),
                                   ('<p class="dropcap">В</p><p> тот вечер.</p>', 'В тот вечер.', '<p>В тот вечер.</p>'),
                                   ('<p class="dropcap">В</p><p>\n&#160;тот вечер.</p>', 'В тот вечер.', '<p>В\xa0тот вечер.</p>')):
            with self.subTest(body=body):
                book = self.read(make_epub(body))
                self.assertEqual([(text, markup)], [(block['text'], block['html']) for chapter in book['chapters'] for block in chapter['blocks']])
                self.assertEqual([text], [source['text'] for source in speech_sources(book, default_chapters(book))])


if __name__ == '__main__':
    unittest.main()
