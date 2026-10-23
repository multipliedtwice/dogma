from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.books import import_book, split_text
from rimsky.integrity import validate_coverage
from rimsky.store import Store
from rimsky.util import AppError


def make_epub(body='<h1>Глава первая</h1><p>— Продолжим? — спросил Север.</p>', *, nav=None, ncx=None,
              extra=None, manifest='', spine='<itemref idref="story"/>', guide='', package_path='EPUB/book.opf',
              metadata='', encryption=None):
    folder = str(Path(package_path).parent)
    if folder == '.':
        folder = ''
    def key(name):
        return f'{folder}/{name}' if folder else name
    entries = {'mimetype': b'application/epub+zip',
               'META-INF/container.xml': f'<container><rootfiles><rootfile full-path="{package_path}"/></rootfiles></container>'.encode(),
               key('story.xhtml'): f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Документ</title></head><body>{body}</body></html>'.encode()}
    if nav is not None:
        entries[key('nav.xhtml')] = f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{nav}</ol></nav></body></html>'.encode()
        manifest += '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
    if ncx is not None:
        entries[key('toc.ncx')] = f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{ncx}</navMap></ncx>'.encode()
        manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    entries[package_path] = f'<package version="3.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Искусственная книга для проверки</dc:title><dc:creator>Verification Fixture</dc:creator><dc:language>ru</dc:language>{metadata}</metadata><manifest><item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>{manifest}</manifest><spine toc="ncx">{spine}</spine>{guide}</package>'.encode()
    if encryption is not None:
        entries['META-INF/encryption.xml'] = encryption.encode()
    entries.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def ncx_point(label, fragment, nested=''):
    return f'<navPoint><navLabel><text>{label}</text></navLabel><content src="story.xhtml#{fragment}"/>{nested}</navPoint>'


class EpubPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data):
        return import_book(data, 'fixture.epub', self.root / 'books')

    def test_ncx_splits_several_chapters_in_one_document(self):
        book = self.read(make_epub('<p id="one">Первый раздел.</p><p id="two">Второй раздел.</p>',
                                  ncx=ncx_point('Первая глава', 'one') + ncx_point('Вторая глава', 'two')))
        self.assertEqual(['Первая глава', 'Вторая глава'], [c['title'] for c in book['chapters']])
        self.assertEqual(['Первый раздел.', 'Второй раздел.'], [c['blocks'][0]['text'] for c in book['chapters']])

    def test_epub3_fragment_navigation_precedes_ncx(self):
        book = self.read(make_epub('<p id="a">Один.</p><p id="b">Два.</p>',
                                  nav='<li><a href="story.xhtml#a">Начало</a></li><li><a href="story.xhtml#b">Продолжение</a></li>',
                                  ncx=ncx_point('Устаревшее название', 'a')))
        self.assertEqual(['Начало', 'Продолжение'], [c['title'] for c in book['chapters']])
        self.assertEqual('epub3-nav', book['epub']['navigation'])

    def test_parent_section_anchor_maps_to_first_passage(self):
        book = self.read(make_epub('<section id="one"><p>А.</p></section><section id="two"><p>Б.</p></section>',
                                  nav='<li><a href="story.xhtml#one">Один</a></li><li><a href="story.xhtml#two">Два</a></li>'))
        self.assertEqual(['Один', 'Два'], [c['title'] for c in book['chapters']])
        self.assertEqual(2, len({x['source_id'] for x in book['navigation']}))

    def test_empty_anchor_before_text_is_preserved(self):
        book = self.read(make_epub('<a id="one"/><p>А.</p><a id="two"/><p>Б.</p>',
                                  ncx=ncx_point('Один', 'one') + ncx_point('Два', 'two')))
        self.assertEqual(['Один', 'Два'], [c['title'] for c in book['chapters']])

    def test_navigation_does_not_reorder_source(self):
        book = self.read(make_epub('<p id="a">А.</p><p id="b">Б.</p>',
                                  nav='<li><a href="story.xhtml#b">Вторая</a></li><li><a href="story.xhtml#a">Первая</a></li>'))
        self.assertEqual(['А.', 'Б.'], [b['text'] for c in book['chapters'] for b in c['blocks']])
        self.assertEqual(['Первая', 'Вторая'], [c['title'] for c in book['chapters']])

    def test_spine_order_remains_authoritative(self):
        raw = make_epub('<p>Первая.</p>', manifest='<item id="second" href="z.xhtml"/>',
                        spine='<itemref idref="second"/><itemref idref="story"/>',
                        extra={'EPUB/z.xhtml': b'<html><body><p>Second in manifest, first in spine.</p></body></html>'})
        book = self.read(raw)
        self.assertEqual('EPUB/z.xhtml', book['chapters'][0]['source'])

    def test_headings_split_a_document_without_navigation(self):
        book = self.read(make_epub('<h1>Один</h1><p>А.</p><h1>Два</h1><p>Б.</p>'))
        self.assertEqual(['Один', 'Два'], [c['title'] for c in book['chapters']])

    def test_part_title_does_not_swallow_repeated_chapter_headings(self):
        book = self.read(make_epub('<h1>Часть</h1><h2>Один</h2><p>А.</p><h2>Два</h2><p>Б.</p>'))
        self.assertEqual(['Часть', 'Один', 'Два'], [c['title'] for c in book['chapters']])

    def test_broken_fragment_does_not_drop_introduction(self):
        book = self.read(make_epub('<p>Вступление.</p><h1 id="real">Основная глава</h1><p>Текст.</p>',
                                  nav='<li><a href="story.xhtml#missing">Нет</a></li><li><a href="story.xhtml#real">Глава</a></li>'))
        self.assertEqual('Вступление.', book['chapters'][0]['blocks'][0]['text'])
        self.assertTrue(any('fragment' in w for w in book['warnings']))

    def test_nested_navigation_does_not_duplicate_text(self):
        book = self.read(make_epub('<section id="part"><h1 id="a">Глава</h1><p>Текст.</p></section>',
                                  nav='<li><a href="story.xhtml#part">Часть</a><ol><li><a href="story.xhtml#a">Глава</a></li></ol></li>'))
        self.assertEqual(1, len(book['chapters']))
        self.assertEqual(1, book['chapters'][0]['navigation_depth'])
        self.assertEqual(2, len(book['navigation']))
        self.assertEqual('Глава Текст.', ' '.join(b['text'] for b in book['chapters'][0]['blocks']))

    def test_nonlinear_notes_are_readable_but_not_default_audio(self):
        raw = make_epub('<p>История.</p>', manifest='<item id="notes" href="notes.xhtml"/>',
                        spine='<itemref idref="story"/><itemref idref="notes" linear="no"/>',
                        extra={'EPUB/notes.xhtml': '<html><body><h1>Примечания</h1><p id="note">Сноска.</p></body></html>'.encode()})
        store = Store(self.root / 'library')
        try:
            book = store.import_data(raw, 'book.epub')
            self.assertEqual(2, len(book['chapters']))
            self.assertFalse(book['chapters'][1]['default_narration'])
            generation = store.create_generation(book['id'], {})
            self.assertEqual([book['chapters'][0]['id']], generation['config']['chapters'])
            explicit = store.create_generation(book['id'], {}, chapters=[book['chapters'][1]['id']])
            self.assertTrue(any(s['text'] == 'Сноска.' for s in explicit['segments']))
        finally:
            store.close()

    def test_toc_document_is_retained_without_becoming_default_narration(self):
        book = self.read(make_epub(nav='<li><a href="story.xhtml">Глава</a></li>',
                                  spine='<itemref idref="nav"/><itemref idref="story"/>'))
        self.assertEqual('navigation', book['chapters'][0]['role'])
        self.assertFalse(book['chapters'][0]['default_narration'])
        self.assertEqual([book['chapters'][1]['id']], book['preparation']['default_chapters'])

    def test_declared_cover_uses_epub2_meta(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (3, 3), 'white').save(stream, 'PNG')
        book = self.read(make_epub(manifest='<item id="cover" href="cover.png" media-type="image/png"/>',
                                  metadata='<meta name="cover" content="cover"/>', extra={'EPUB/cover.png': stream.getvalue()}))
        self.assertIsNotNone(book['cover'])
        self.assertTrue((self.root / 'books' / book['id'] / 'assets' / book['cover']).is_file())

    def test_multiple_authors_are_retained(self):
        book = self.read(make_epub(metadata='<dc:creator>Second Fixture Author</dc:creator>'))
        self.assertEqual('Verification Fixture, Second Fixture Author', book['author'])

    def test_percent_encoded_utf8_resource_and_fragment(self):
        raw = make_epub('<p id="глава">Текст.</p>',
                        nav='<li><a href="story.xhtml#%D0%B3%D0%BB%D0%B0%D0%B2%D0%B0">Глава</a></li>')
        book = self.read(raw)
        self.assertEqual(book['chapters'][0]['blocks'][0]['id'], book['navigation'][0]['source_id'])

    def test_root_package_is_supported(self):
        book = self.read(make_epub(package_path='book.opf'))
        self.assertEqual('story.xhtml', book['chapters'][0]['source'])

    def test_duplicate_manifest_id_is_rejected(self):
        with self.assertRaises(AppError):
            self.read(make_epub(manifest='<item id="story" href="story.xhtml"/>'))

    def test_duplicate_spine_reference_is_skipped(self):
        book = self.read(make_epub(spine='<itemref idref="story"/><itemref idref="story"/>'))
        self.assertEqual(1, len(book['chapters']))
        self.assertTrue(any('skipped' in w for w in book['warnings']))

    def test_duplicate_content_id_keeps_first_target(self):
        book = self.read(make_epub('<p id="same">Один.</p><p id="same">Два.</p>'))
        self.assertEqual([['same'], []], [b['anchors'] for b in book['chapters'][0]['blocks']])
        self.assertTrue(any('Repeated anchor IDs' in w for w in book['warnings']))

    def test_external_navigation_remains_non_executable(self):
        book = self.read(make_epub(nav='<li><a href="https://example.invalid/book">Удалённое</a></li>'))
        self.assertEqual([], book['navigation'])
        self.assertTrue(any('invalid' in w for w in book['warnings']))

    def test_unsupported_spine_media_without_fallback_fails(self):
        with self.assertRaises(AppError):
            self.read(make_epub(manifest='<item id="video" href="film.bin" media-type="video/mp4"/>',
                                spine='<itemref idref="video"/>', extra={'EPUB/film.bin': b'not video'}))

    def test_supported_declared_spine_fallback(self):
        book = self.read(make_epub(manifest='<item id="video" href="film.bin" media-type="video/mp4" fallback="story"/>',
                                  spine='<itemref idref="video"/>', extra={'EPUB/film.bin': b'not video'}))
        self.assertEqual('EPUB/story.xhtml', book['chapters'][0]['source'])

    def test_font_only_obfuscation_does_not_block_book_text(self):
        encryption = '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><CipherData><CipherReference URI="EPUB/font.otf"/></CipherData></EncryptedData></encryption>'
        book = self.read(make_epub(encryption=encryption, manifest='<item id="font" href="font.otf" media-type="font/otf"/>',
                                  extra={'EPUB/font.otf': b'synthetic non-font obfuscation fixture'}))
        self.assertTrue(book['chapters'])
        self.assertTrue(any('fonts' in w for w in book['warnings']))
        self.assertFalse(any(p.suffix == '.otf' for p in (self.root / 'books').rglob('*')))

    def test_content_encryption_remains_rejected(self):
        encryption = '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/><CipherData><CipherReference URI="EPUB/story.xhtml"/></CipherData></EncryptedData></encryption>'
        with self.assertRaises(AppError):
            self.read(make_epub(encryption=encryption))

    def test_blockquote_keeps_dialogue_paragraphs_separate(self):
        book = self.read(make_epub('<blockquote><p>— Привет.</p><p>— Здравствуй.</p></blockquote>'))
        self.assertEqual(['— Привет.', '— Здравствуй.'], [b['text'] for c in book['chapters'] for b in c['blocks']])

    def test_scene_breaks_are_visible_but_not_spoken(self):
        raw = make_epub('<p>До.</p><p>* * *</p><hr/><p>После.</p>')
        store = Store(self.root / 'library')
        try:
            book = store.import_data(raw, 'book.epub')
            self.assertEqual(2, sum(b['kind'] == 'separator' for c in book['chapters'] for b in c['blocks']))
            generation = store.create_generation(book['id'], {})
            self.assertEqual('До.После.', ''.join(s['text'] for s in generation['segments']))
            validate_coverage(book, generation['config']['chapters'], generation['segments'])
        finally:
            store.close()

    def test_blank_spine_documents_are_skipped_and_heading_scene_break_remains(self):
        raw = make_epub('<h1>Глава</h1><p>До.</p><h5>* * *</h5><p>После.</p>',
                        manifest='<item id="blank" href="blank.xhtml" media-type="application/xhtml+xml"/>',
                        spine='<itemref idref="blank"/><itemref idref="story"/>',
                        extra={'EPUB/blank.xhtml': b'<html><body><p>  </p><hr/></body></html>'})
        store = Store(self.root / 'blank-library')
        try:
            book = store.import_data(raw, 'blank.epub')
            self.assertEqual(1, len(book['chapters']))
            self.assertEqual(['Глава', 'До.', '* * *', 'После.'],
                             [block['text'] for block in book['chapters'][0]['blocks']])
            self.assertEqual('separator', book['chapters'][0]['blocks'][2]['kind'])
            self.assertEqual(1, store.library()[0]['chapter_count'])
        finally:
            store.close()

    def test_image_only_svg_spine_section_is_not_treated_as_blank(self):
        raw = make_epub('<p>Текст.</p>',
                        manifest='<item id="art" href="art.svg" media-type="image/svg+xml"/>',
                        spine='<itemref idref="art"/><itemref idref="story"/>',
                        extra={'EPUB/art.svg': b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100"/></svg>'})
        store = Store(self.root / 'svg-library')
        try:
            book = store.import_data(raw, 'svg.epub')
            self.assertEqual(2, len(book['chapters']))
            self.assertEqual('figure', book['chapters'][0]['blocks'][0]['kind'])
            self.assertEqual(2, store.library()[0]['chapter_count'])
        finally:
            store.close()

    def test_opening_legacy_manifest_hides_empty_section_and_dead_navigation(self):
        store = Store(self.root / 'legacy-blank-library')
        try:
            book = store.import_data(make_epub('<p>Visible.</p>'), 'legacy.epub')
            manifest = store._book_manifest(book['id'])
            manifest['chapters'].append({'id': 'empty-legacy', 'title': 'Blank', 'blocks': []})
            manifest.setdefault('navigation', []).append({'title': 'Blank', 'source_id': 'empty-legacy'})
            store.execute('UPDATE books SET manifest=? WHERE id=?', (json.dumps(manifest), book['id']))
            store.execute('INSERT INTO reading_positions VALUES(?,?)',
                          (book['id'], json.dumps({'chapter_id': 'empty-legacy', 'offset': 0})))
            reopened = store.book(book['id'])
            self.assertEqual(1, len(reopened['chapters']))
            self.assertFalse(any(item.get('source_id') == 'empty-legacy' for item in reopened['navigation']))
            self.assertNotIn('empty-legacy', reopened['preparation']['default_chapters'])
            self.assertIsNone(reopened['reading_position'])
            self.assertEqual(1, store.library()[0]['chapter_count'])
        finally:
            store.close()

    def test_long_cyrillic_passage_has_exact_contiguous_source_ranges(self):
        from rimsky.preparation import speech_sources
        text = ('— Ты всё проверил? — спросил Север. — Да, числа 3,14 и 2026 сохранены. ' * 40).strip()
        book = self.read(make_epub(f'<p>{text}</p>'))
        sources = list(speech_sources(book, book['preparation']['default_chapters']))
        self.assertEqual(text, ''.join(source['text'] for source in sources))
        self.assertTrue(all(source['text'] == text[source['start']:source['end']] for source in sources))
        self.assertTrue(all(len(source['text']) <= 420 for source in sources))
        self.assertEqual(len(sources), book['preparation']['base_segments'])

    def test_source_bytes_and_preparation_digest_are_stable(self):
        raw = make_epub()
        first = self.read(raw)
        second = self.read(raw)
        self.assertEqual(first, second)
        self.assertEqual(raw, (self.root / 'books' / first['id'] / 'source.epub').read_bytes())

    def test_source_location_contains_file_and_dom_path(self):
        book = self.read(make_epub('<p id="stable">Текст.</p>'))
        block = book['chapters'][0]['blocks'][0]
        self.assertEqual('EPUB/story.xhtml', block['location']['href'])
        self.assertTrue(block['location']['xpath'])
        self.assertEqual(['stable'], block['anchors'])

    def test_internal_links_resolve_to_known_source_blocks(self):
        book = self.read(make_epub('<p><a href="#note">Сноска</a></p><p id="note">Текст примечания.</p>'))
        blocks = book['chapters'][0]['blocks']
        self.assertIn(f'data-source-link="{blocks[1]["id"]}"', blocks[0]['html'])
        self.assertNotIn('href=', blocks[0]['html'])

    def test_cross_file_notes_link_is_preserved(self):
        book = self.read(make_epub('<p>Текст <a href="notes.xhtml#note">1</a>.</p>',
                                  manifest='<item id="notes" href="notes.xhtml"/>',
                                  spine='<itemref idref="story"/><itemref idref="notes" linear="no"/>',
                                  extra={'EPUB/notes.xhtml': '<html><body><p id="note">Сноска.</p></body></html>'.encode()}))
        target = book['chapters'][1]['blocks'][0]['id']
        self.assertIn(f'data-source-link="{target}"', book['chapters'][0]['blocks'][0]['html'])

    def test_broken_links_keep_their_visible_text(self):
        book = self.read(make_epub('<p><a href="#missing">ссылка</a> и <a href="javascript:alert(1)">текст</a>.</p>'))
        block = book['chapters'][0]['blocks'][0]
        self.assertEqual('ссылка и текст.', block['text'])
        self.assertNotIn('data-source-link', block['html'])
        self.assertNotIn('javascript', block['html'])

    def test_legacy_manifest_and_frozen_generation_are_not_rewritten(self):
        store = Store(self.root / 'library')
        try:
            book = store.import_data(make_epub(), 'book.epub')
            original = json.loads(store.rows('SELECT manifest FROM books WHERE id=?', (book['id'],))[0]['manifest'])
            original.pop('preparation')
            store.execute('UPDATE books SET manifest=? WHERE id=?', (json.dumps(original), book['id']))
            generation = store.create_generation(book['id'], {})
            result = store.book(book['id'])
            self.assertIn('preparation', result)
            stored = json.loads(store.rows('SELECT manifest FROM books WHERE id=?', (book['id'],))[0]['manifest'])
            self.assertNotIn('preparation', stored)
            store.import_data(make_epub(), 'renamed.epub')
            self.assertEqual(generation, store.generation(generation['id']))
        finally:
            store.close()

    def test_cli_prepares_without_inference_or_network(self):
        source = self.root / 'book.epub'
        source.write_bytes(make_epub())
        destination = self.root / 'prepared'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/prepare_book.py'),
                                 str(source), '--output', str(destination)], capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['requires_model'])
        self.assertTrue((destination / 'book.json').is_file())
        inputs = [json.loads(line) for line in (destination / 'speech-inputs.jsonl').read_text('utf-8').splitlines()]
        self.assertEqual(report['base_segments'], len(inputs))
        self.assertEqual(source.read_bytes(), (destination / 'books' / report['book']['id'] / 'source.epub').read_bytes())
        refused = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/prepare_book.py'),
                                  str(source), '--output', str(destination)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn('nothing was overwritten', refused.stderr)

    def test_cli_failure_leaves_no_prepared_directory(self):
        source = self.root / 'broken.epub'
        source.write_bytes(b'not an epub')
        destination = self.root / 'prepared'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/prepare_book.py'),
                                 str(source), '--output', str(destination)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(destination.exists())

    def test_document_level_navigation_is_not_split_at_subheadings(self):
        book = self.read(make_epub('<h1>Глава</h1><h2>Подраздел</h2><p>А.</p><h2>Ещё</h2><p>Б.</p>',
                                  nav='<li><a href="story.xhtml">Единая глава</a></li>'))
        self.assertEqual(['Единая глава'], [chapter['title'] for chapter in book['chapters']])
        self.assertEqual(5, len(book['chapters'][0]['blocks']))

    def test_linked_offspine_notes_are_retained_without_changing_primary_order(self):
        book = self.read(make_epub('<p id="source">Текст <a href="notes.xhtml#note">1</a>.</p>',
                                  manifest='<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>',
                                  extra={'EPUB/notes.xhtml': '<html><body><p id="note">Примечание <a href="story.xhtml#source">назад</a>.</p></body></html>'.encode()}))
        self.assertEqual(2, len(book['chapters']))
        story, notes = book['chapters']
        self.assertEqual(['body', 'auxiliary'], [story['role'], notes['role']])
        self.assertEqual([story['id']], book['preparation']['default_chapters'])
        self.assertIn(f'data-source-link="{notes["blocks"][0]["id"]}"', story['blocks'][0]['html'])
        self.assertIn(f'data-source-link="{story["blocks"][0]["id"]}"', notes['blocks'][0]['html'])
        self.assertIsNone(notes['spine_index'])

    def test_unlinked_manifest_documents_are_not_added_to_narration(self):
        book = self.read(make_epub(manifest='<item id="unlinked" href="other.xhtml" media-type="application/xhtml+xml"/>',
                                  extra={'EPUB/other.xhtml': b'<html><body><p>Not in declared reading order.</p></body></html>'}))
        self.assertEqual(1, len(book['chapters']))

    def test_linked_undeclared_content_is_not_parsed(self):
        book = self.read(make_epub('<p><a href="undeclared.xhtml">Текст</a></p>',
                                  extra={'EPUB/undeclared.xhtml': b'not valid XML'}))
        self.assertEqual(1, len(book['chapters']))
        self.assertNotIn('data-source-link', book['chapters'][0]['blocks'][0]['html'])
        self.assertTrue(any('no displayable target' in warning for warning in book['warnings']))

    def test_missing_navigation_file_falls_back_without_losing_chapters(self):
        book = self.read(make_epub('<h1>Один</h1><p>А.</p><h1>Два</h1><p>Б.</p>',
                                  manifest='<item id="badnav" href="absent.xhtml" properties="nav"/>'))
        self.assertEqual(['Один', 'Два'], [chapter['title'] for chapter in book['chapters']])
        self.assertTrue(any('navigation document is missing' in warning for warning in book['warnings']))

    def test_cyclic_note_links_do_not_repeat_or_loop(self):
        book = self.read(make_epub('<p><a href="a.xhtml#a">А</a></p>',
                                  manifest='<item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/>',
                                  extra={'EPUB/a.xhtml': b'<html><body><p id="a">A <a href="b.xhtml#b">B</a></p></body></html>',
                                         'EPUB/b.xhtml': b'<html><body><p id="b">B <a href="a.xhtml#a">A</a></p></body></html>'}))
        self.assertEqual(3, len(book['chapters']))
        self.assertEqual(3, len({chapter['source'] for chapter in book['chapters']}))

    def test_scene_separator_survives_analysis_without_being_spoken(self):
        from rimsky.analysis import validate_analysis
        from rimsky.casting import casting_inputs, dialogue_contexts
        from rimsky.preparation import speech_sources
        book = self.read(make_epub('<p>— Один.</p><p>— Два.</p><hr/><p>— Три.</p><p>— Четыре.</p>'))
        targets = list(speech_sources(book, book['preparation']['default_chapters']))
        cast = [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}, {'id': 'd'}]
        result = {'characters': [], 'passages': [
            {'id': target['id'], 'parts': [{'first': 0, 'last': 0, 'speaker': character['id'],
                                          'delivery': 'neutral', 'evidence': [target['id']],
                                          'context': {'mood': 'neutral', 'channel': 'direct', 'content_register': 'prose',
                                                      'confidence': 'low', 'evidence': []}}]}
            for character, target in zip(cast, targets)]}
        parts, _ = validate_analysis(result, targets, cast)
        contexts = dialogue_contexts(casting_inputs(parts))
        self.assertEqual([['a', 'b'], ['c', 'd']], [context['speakers'] for context in contexts])
        self.assertEqual(4, len(parts))

    def test_navigation_to_middle_of_paragraph_keeps_whole_source_passage(self):
        book = self.read(make_epub('<p>До <span id="middle">якоря</span> и после.</p>',
                                  nav='<li><a href="story.xhtml#middle">Раздел</a></li>'))
        self.assertEqual('До якоря и после.', book['chapters'][0]['blocks'][0]['text'])
        self.assertEqual(1, len(book['chapters']))

    def test_large_cyrillic_book_preserves_all_paragraphs_in_order(self):
        paragraphs = [f'— Проверка {index}: число {index * 17}, ёж, «кавычки» и слово сохранены.' for index in range(8000)]
        body = ''.join(f'<h1>Раздел {start // 400 + 1}</h1>' + ''.join(f'<p>{text}</p>' for text in paragraphs[start:start+400])
                       for start in range(0, len(paragraphs), 400))
        book = self.read(make_epub(body))
        actual = [block['text'] for chapter in book['chapters'] for block in chapter['blocks'] if block['kind'] == 'paragraph']
        self.assertEqual(paragraphs, actual)
        self.assertEqual(20, len(book['chapters']))
        self.assertEqual(8020, book['preparation']['passages'])
        self.assertEqual(8020, book['preparation']['base_segments'])

    def test_nav_target_outside_spine_is_optional_not_silently_main_content(self):
        book = self.read(make_epub(nav='<li><a href="story.xhtml">Основной текст</a></li><li><a href="extra.xhtml">Приложение</a></li>',
                                  manifest='<item id="extra" href="extra.xhtml"/>',
                                  extra={'EPUB/extra.xhtml': '<html><body><p>Дополнение.</p></body></html>'.encode()}))
        self.assertEqual(['Основной текст', 'Приложение'], [c['title'] for c in book['chapters']])
        self.assertFalse(book['chapters'][1]['default_narration'])
        self.assertEqual(2, len(book['navigation']))

    def test_primary_spine_is_required_even_when_manifest_has_text(self):
        with self.assertRaises(AppError):
            self.read(make_epub(spine='', nav='<li><a href="story.xhtml">Раздел</a></li>'))

    def test_manifest_without_href_cannot_read_the_package_as_prose(self):
        with self.assertRaises(AppError):
            self.read(make_epub(manifest='<item id="missing" media-type="application/xhtml+xml"/>',
                                spine='<itemref idref="missing"/>'))

    def test_non_content_root_is_not_imported_as_narrative(self):
        with self.assertRaises(AppError):
            self.read(make_epub(extra={'EPUB/story.xhtml': b'<package><metadata><title>Not book prose.</title></metadata></package>'}))

    def test_real_http_preparation_and_restart_do_not_require_model_pack(self):
        from test_lifecycle import runtime, request
        from urllib.request import Request, urlopen
        data = make_epub('<p id="a">Первый текст.</p><p id="b">Второй текст.</p>',
                         ncx=ncx_point('Один', 'a') + ncx_point('Два', 'b'))
        directory = self.root / 'http-library'
        with runtime(directory) as (_, session):
            headers = {'Authorization': 'Bearer ' + session['token'], 'Content-Type': 'application/octet-stream',
                       'Origin': 'http://127.0.0.1:1420'}
            with urlopen(Request(f'http://127.0.0.1:{session["port"]}/import?name=fixture.epub', data=data, headers=headers), timeout=5) as response:
                book = json.load(response)
            self.assertFalse(request(session, 'health')['ready'])
            self.assertEqual(2, len(book['chapters']))
            self.assertEqual(2, book['preparation']['base_segments'])
            self.assertEqual([], book['generations'])
            self.assertEqual(book['preparation'], request(session, 'book', {'id': book['id']})['preparation'])
        with runtime(directory) as (_, second):
            reopened = request(second, 'book', {'id': book['id']})
            self.assertEqual(book['chapters'], reopened['chapters'])
            self.assertEqual(book['preparation'], reopened['preparation'])
            self.assertEqual([], reopened['generations'])

    def test_toc_chapter_can_span_multiple_heading_free_spine_documents(self):
        book = self.read(make_epub('<h1 id="a">Глава</h1><p>Начало.</p>',
                                  nav='<li><a href="story.xhtml#a">Единая глава</a></li><li><a href="last.xhtml#last">Вторая глава</a></li>',
                                  manifest='<item id="continued" href="continued.xhtml"/><item id="last" href="last.xhtml"/>',
                                  spine='<itemref idref="story"/><itemref idref="continued"/><itemref idref="last"/>',
                                  extra={'EPUB/continued.xhtml': '<html><body><p id="continued">Продолжение.</p></body></html>'.encode(),
                                         'EPUB/last.xhtml': '<html><body><h1 id="last">Новая глава</h1><p>Конец.</p></body></html>'.encode()}))
        self.assertEqual(['Единая глава', 'Вторая глава'], [chapter['title'] for chapter in book['chapters']])
        first = book['chapters'][0]
        self.assertEqual(['EPUB/story.xhtml', 'EPUB/continued.xhtml'], first['source_files'])
        self.assertEqual('Продолжение.', first['blocks'][-1]['text'])
        self.assertEqual('EPUB/continued.xhtml', first['blocks'][-1]['location']['href'])
        self.assertEqual(5, len({block['id'] for chapter in book['chapters'] for block in chapter['blocks']}))

    def test_a_heading_in_a_continuation_still_creates_a_visible_section(self):
        book = self.read(make_epub(nav='<li><a href="story.xhtml">Первая</a></li>',
                                  manifest='<item id="second" href="second.xhtml"/>',
                                  spine='<itemref idref="story"/><itemref idref="second"/>',
                                  extra={'EPUB/second.xhtml': '<html><body><h1>Вторая</h1><p>Текст.</p></body></html>'.encode()}))
        self.assertEqual(['Первая', 'Вторая'], [chapter['title'] for chapter in book['chapters']])

    def test_continuation_link_targets_keep_their_final_source_ids(self):
        book = self.read(make_epub('<p id="first">Текст <a href="more.xhtml#more">далее</a>.</p>',
                                  nav='<li><a href="story.xhtml#first">Глава</a></li>',
                                  manifest='<item id="more" href="more.xhtml"/>',
                                  spine='<itemref idref="story"/><itemref idref="more"/>',
                                  extra={'EPUB/more.xhtml': '<html><body><p id="more">Продолжение <a href="story.xhtml#first">назад</a>.</p></body></html>'.encode()}))
        self.assertEqual(1, len(book['chapters']))
        blocks = book['chapters'][0]['blocks']
        self.assertIn(f'data-source-link="{blocks[1]["id"]}"', blocks[0]['html'])
        self.assertIn(f'data-source-link="{blocks[0]["id"]}"', blocks[1]['html'])

    def check_prepared_job(self, include_note):
        from test_core import pack_fixture, SyntheticEngine
        from rimsky.jobs import Jobs
        raw = make_epub('<h1 id="a">Один</h1><p>А.</p><h1 id="b">Два</h1><p>Б.</p>',
                        nav='<li><a href="story.xhtml#a">Один</a></li><li><a href="story.xhtml#b">Два</a></li>',
                        manifest='<item id="notes" href="notes.xhtml"/>',
                        spine='<itemref idref="nav"/><itemref idref="story"/><itemref idref="notes" linear="no"/>',
                        extra={'EPUB/notes.xhtml': '<html><body><p>Примечание.</p></body></html>'.encode()})
        store = Store(self.root / 'job-library')
        store.update_settings({'cast_review': False})
        engine = SyntheticEngine()
        jobs = Jobs(store, self.root, engine_factory=lambda _: engine)
        try:
            book = store.import_data(raw, 'job.epub')
            main = book['preparation']['default_chapters']
            note = book['chapters'][-1]['id']
            chosen = note if include_note else main[0]
            pack = pack_fixture(self.root / 'pack')
            generation = store.create_generation(book['id'], {
                'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                'pack_name': pack['name'], 'device': 'cpu', 'align_words': False}, chapters=[chosen])
            jobs.start(generation['id'])
            jobs.thread.join(timeout=5)
            self.assertFalse(jobs.thread.is_alive())
            ready = store.generation(generation['id'])
            self.assertEqual('ready', ready['status'], ready['error'])
            self.assertEqual(set(main) | ({note} if include_note else set()), engine.analysis_chapters)
            self.assertEqual({chosen}, {part['chapter_id'] for part in ready['segments']})
            validate_coverage(book, ready['config']['chapters'], ready['segments'])
        finally:
            jobs.shutdown()
            store.close()

    def test_selected_main_chapter_analyses_primary_book_but_not_unselected_notes(self):
        self.check_prepared_job(False)

    def test_explicit_note_generation_analyses_note_and_primary_book(self):
        self.check_prepared_job(True)

    def test_invalid_segment_limit_is_rejected(self):
        for limit in (0, -1, 1.5, True):
            with self.assertRaises(AppError):
                split_text('Текст', limit)


if __name__ == '__main__':
    unittest.main()
