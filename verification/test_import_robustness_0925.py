from __future__ import annotations

import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from rimsky import books
from rimsky.books import import_book
from rimsky.export import build_export
from rimsky.jobs import Jobs
from rimsky.preparation import default_chapters, narratable, speech_sources
from rimsky.store import Store
from rimsky.util import AppError
from test_core import FB2, complete_audio
from test_epub_preparation import make_epub

OBFUSCATED = ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
              '<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
              '<enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData></enc:EncryptedData></encryption>')


def fb2(body, notes='', lang='ru', binaries=''):
    return ('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><author><first-name>A</first-name><last-name>B</last-name>'
            f'</author><book-title>Книга</book-title><lang>{lang}</lang></title-info></description><body>{body}</body>{notes}{binaries}'
            '</FictionBook>').encode()


def photo(size=(640, 480), exif=True):
    image = Image.effect_noise(size, 40).convert('RGB')
    output = io.BytesIO()
    extra = {}
    if exif:
        data = Image.Exif()
        data[0x010F] = 'Synthetic camera'
        extra['exif'] = data.tobytes()
    image.save(output, 'JPEG', quality=85, **extra)
    return output.getvalue()


def png(size=(32, 24)):
    output = io.BytesIO()
    Image.new('RGBA', size, (10, 20, 30, 128)).save(output, 'PNG')
    return output.getvalue()


class ImportRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data, name='book.epub'):
        return import_book(data, name, self.root / 'books')

    def blocks(self, book):
        return [block for chapter in book['chapters'] for block in chapter['blocks']]

    def texts(self, book):
        return [block['text'] for block in self.blocks(book)]

    def test_duplicate_ids_are_accepted_and_first_occurrence_is_the_link_target(self):
        book = self.read(make_epub('<h1 id="a">Глава</h1><p id="a">Текст.</p><p>Назад <a href="#a">к началу</a>.</p>'))
        heading, text, link = self.blocks(book)
        self.assertEqual(['a'], heading['anchors'])
        self.assertEqual([], text['anchors'])
        self.assertIn(f'data-source-link="{heading["id"]}"', link['html'])
        self.assertEqual(1, sum('Repeated anchor IDs' in warning for warning in book['warnings']))
        named = self.read(make_epub('<h1><a name="x"/>Глава</h1><p id="x">Текст.</p><p><a name="x"/>Ещё.</p>'))
        self.assertEqual(['Глава', 'Текст.', 'Ещё.'], self.texts(named))
        self.assertEqual(['x'], self.blocks(named)[0]['anchors'])

    def test_bare_ampersand_document_is_recovered_with_a_warning(self):
        book = self.read(make_epub('<h1>Глава</h1><p>Tom & Jerry, AT&T &amp; R&D <script>alert(1)</script>&nbsp;дальше.</p>'))
        self.assertIn('Tom & Jerry, AT&T & R&D дальше.', self.texts(book))
        self.assertNotIn('<script', str(book['chapters']))
        self.assertNotIn('alert', str(book['chapters']))
        self.assertTrue(any('EPUB/story.xhtml' in warning and 'recovery' in warning for warning in book['warnings']))

    def test_malformed_document_without_recoverable_text_is_still_refused(self):
        for broken in (b'garbage & text', b'<html><body><p></p><div>', b'<<<<>>>>'):
            with self.subTest(broken=broken), self.assertRaises(AppError) as caught:
                self.read(make_epub(extra={'EPUB/story.xhtml': broken}))
            self.assertEqual('invalid_book_xml', caught.exception.code)
            self.assertEqual('EPUB/story.xhtml', caught.exception.details['document'])

    def test_unknown_entities_and_fb2_xml_errors_remain_strict(self):
        with self.assertRaises(AppError):
            self.read(make_epub('<p>&unknownentity;</p>'))
        with self.assertRaises(AppError) as caught:
            self.read(fb2('<section><p>Tom & Jerry</p></section>'), 'book.fb2')
        self.assertEqual('invalid_book_xml', caught.exception.code)

    def test_spine_entries_that_cannot_be_read_are_skipped(self):
        cases = {
            'missing id': ('', '<itemref idref="story"/><itemref idref="nope"/>'),
            'repeated id': ('', '<itemref idref="story"/><itemref idref="story"/>'),
            'missing file': ('<item id="gone" href="gone.xhtml" media-type="application/xhtml+xml"/>', '<itemref idref="gone"/><itemref idref="story"/>'),
            'same file twice': ('<item id="again" href="story.xhtml" media-type="application/xhtml+xml"/>', '<itemref idref="story"/><itemref idref="again"/>'),
        }
        for label, (manifest, spine) in cases.items():
            with self.subTest(label):
                book = self.read(make_epub(manifest=manifest, spine=spine))
                self.assertEqual(['Глава первая', '— Продолжим? — спросил Север.'], self.texts(book))
                self.assertTrue(any('skipped' in warning for warning in book['warnings']))
        with self.assertRaisesRegex(AppError, 'no readable documents'):
            self.read(make_epub(manifest='<item id="gone" href="gone.xhtml" media-type="application/xhtml+xml"/>',
                                spine='<itemref idref="nope"/><itemref idref="gone"/>'))

    def test_empty_encryption_metadata_means_no_encryption(self):
        for encryption in ('', '<?xml version="1.0"?>', '<encryption/>',
                           '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>'):
            with self.subTest(encryption=encryption):
                book = self.read(make_epub(encryption=encryption))
                self.assertEqual('Глава первая', book['chapters'][0]['title'])
                self.assertFalse(any('font' in warning for warning in book['warnings']))

    def test_obfuscated_fonts_of_any_font_type_are_skipped_with_a_warning(self):
        for media_type in ('font/opentype', 'font/sfnt', 'application/x-font-otf', 'application/x-font-truetype',
                           'application/font-woff2', 'application/vnd.ms-opentype', 'font/otf', 'application/octet-stream', None):
            with self.subTest(media_type=media_type):
                manifest = f'<item id="font" href="face.otf" media-type="{media_type}"/>' if media_type else ''
                book = self.read(make_epub(encryption=OBFUSCATED.format(uri='EPUB/face.otf'), manifest=manifest,
                                           extra={'EPUB/face.otf': b'synthetic obfuscated font bytes'}))
                self.assertTrue(book['chapters'])
                self.assertTrue(any('fonts were not loaded' in warning for warning in book['warnings']))

    def test_encrypted_content_and_images_are_still_refused(self):
        with self.assertRaisesRegex(AppError, 'Encrypted EPUB content'):
            self.read(make_epub(encryption=OBFUSCATED.format(uri='EPUB/story.xhtml')))
        with self.assertRaisesRegex(AppError, 'Encrypted EPUB content'):
            self.read(make_epub(encryption=OBFUSCATED.format(uri='EPUB/pic.png'),
                                manifest='<item id="pic" href="pic.png" media-type="image/png"/>', extra={'EPUB/pic.png': png()}))

    def test_jpeg_stays_jpeg_without_metadata_and_png_stays_png(self):
        source = photo()
        book = self.read(make_epub('<p>Текст.</p><p><img src="photo.jpg" alt="Фото"/></p><p><img src="mark.png" alt="Знак"/></p>',
                                   manifest='<item id="photo" href="photo.jpg" media-type="image/jpeg"/><item id="mark" href="mark.png" media-type="image/png"/>',
                                   extra={'EPUB/photo.jpg': source, 'EPUB/mark.png': png()}))
        assets = {asset['mime']: asset for asset in book['assets']}
        self.assertEqual({'image/jpeg', 'image/png'}, set(assets))
        self.assertTrue(assets['image/jpeg']['id'].endswith('.jpg'))
        self.assertTrue(assets['image/png']['id'].endswith('.png'))
        stored = self.root / 'books' / book['id'] / 'assets' / assets['image/jpeg']['id']
        with Image.open(stored) as image:
            self.assertEqual('JPEG', image.format)
            self.assertEqual((640, 480), image.size)
            self.assertFalse(image.getexif())
        converted = {}
        as_png = books.image_asset(source, self.root, converted)
        self.assertLess(stored.stat().st_size * 2, converted[as_png]['size'])
        self.assertIn(f'data-asset="{assets["image/jpeg"]["id"]}"', str(book['chapters']))

    def test_rotated_jpeg_is_stored_upright_without_orientation_metadata(self):
        raw = io.BytesIO()
        metadata = Image.Exif()
        metadata[274] = 6
        Image.new('RGB', (60, 30), 'red').save(raw, 'JPEG', exif=metadata)
        assets = {}
        key = books.image_asset(raw.getvalue(), self.root, assets, keep_jpeg=True)
        with Image.open(self.root / key) as saved:
            self.assertEqual(('JPEG', (30, 60)), (saved.format, saved.size))
            self.assertNotIn(274, saved.getexif())

    def test_visual_images_keep_png_encoding(self):
        assets = {}
        key = books.image_asset(photo(), self.root, assets)
        self.assertTrue(key.endswith('.png'))
        self.assertEqual('image/png', assets[key]['mime'])

    def test_fb2_images_keep_jpeg_and_failed_images_warn(self):
        good = base64.b64encode(photo((80, 60), exif=False)).decode()
        book = self.read(fb2('<section><title><p>Глава</p></title><p>Текст.</p><image l:href="#pic"/><image l:href="#bad"/></section>',
                             binaries=f'<binary id="pic" content-type="image/jpeg">{good}</binary>'
                                      '<binary id="bad" content-type="image/png">bm90IGFuIGltYWdl</binary>'), 'book.fb2')
        self.assertEqual(['image/jpeg'], [asset['mime'] for asset in book['assets']])
        self.assertEqual(1, sum('image could not be displayed' in warning for warning in book['warnings']))
        broken = self.read(fb2('<section><p>Текст.</p><image l:href="#x"/></section>',
                               binaries='<binary id="x" content-type="image/png">@@@</binary>'), 'broken.fb2')
        self.assertTrue(any('image could not be displayed' in warning for warning in broken['warnings']))

    def test_epub3_inline_footnotes_are_readable_notes_but_not_narrated(self):
        book = self.read(make_epub('<h1>Глава</h1><p>Текст<a epub:type="noteref" href="#fn1">1</a>.</p>'
                                   '<aside epub:type="footnote" id="fn1"><p>Сноска один.</p></aside>'
                                   '<aside epub:type="rearnote" id="fn2">Сноска два.</aside><p>Дальше.</p>'))
        chapter = book['chapters'][0]
        kinds = {block['text']: block['kind'] for block in chapter['blocks']}
        self.assertEqual('note', kinds['Сноска один.'])
        self.assertEqual('note', kinds['Сноска два.'])
        self.assertEqual('paragraph', kinds['Дальше.'])
        note = next(block for block in chapter['blocks'] if block['text'] == 'Сноска один.')
        self.assertIn(f'data-source-link="{note["id"]}"', chapter['blocks'][1]['html'])
        self.assertFalse(narratable(note))
        self.assertEqual([chapter['id']], default_chapters(book))
        spoken = [part['text'] for part in speech_sources(book, [chapter['id']])]
        self.assertEqual(['Глава', 'Текст.', 'Дальше.'], spoken)

    def test_epub3_endnotes_document_is_not_default_narration(self):
        notes = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>N</title></head><body>'
                 '<section epub:type="endnotes"><h1>Примечания</h1><aside epub:type="endnote" id="en1"><p>Примечание.</p></aside></section>'
                 '</body></html>').encode()
        book = self.read(make_epub('<h1>Глава</h1><p>Текст<a epub:type="noteref" href="notes.xhtml#en1">1</a>.</p>',
                                   manifest='<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>',
                                   spine='<itemref idref="story"/><itemref idref="notes"/>', extra={'EPUB/notes.xhtml': notes}))
        story, endnotes = book['chapters']
        self.assertTrue(story['default_narration'])
        self.assertFalse(endnotes['default_narration'])
        self.assertEqual([story['id']], default_chapters(book))
        self.assertIn('Примечание.', [part['text'] for part in speech_sources(book, [endnotes['id']])])
        inline = self.read(make_epub('<h1>Глава</h1><p>Текст.</p><section epub:type="footnotes"><aside epub:type="footnote" id="f"><p>Сноска.</p></aside></section>'))
        self.assertTrue(inline['chapters'][0]['default_narration'])

    def test_glued_note_markers_are_not_narrated(self):
        book = self.read(make_epub('<h1>Глава<a href="#n1"><sup>1</sup></a></h1><p>Текст<a href="#n1"><sup>1</sup></a> дальше.</p>'
                                   '<p>Ещё<a href="#n2">[2]</a> тут<a href="#n3">*</a>.</p><p>См. <a href="#n1">главу</a> и 2020 год.</p>'
                                   '<p id="n1">Сноска.</p><p id="n2">Вторая.</p><p id="n3">Третья.</p>'))
        texts = self.texts(book)
        self.assertEqual('Глава', book['chapters'][0]['title'])
        self.assertIn('Текст дальше.', texts)
        self.assertIn('Ещё тут.', texts)
        self.assertIn('См. главу и 2020 год.', texts)
        self.assertIn('<sup>1</sup>', self.blocks(book)[1]['html'])
        standalone = self.read(make_epub('<h2><a href="#c1">1</a></h2><p id="c1">Текст.</p>'))
        self.assertEqual('1', standalone['chapters'][0]['title'])
        notes = '<body name="notes"><section id="n1"><title><p>1</p></title><p>Первая.</p></section><section id="n2"><title><p>2</p></title><p>Вторая.</p></section></body>'
        fb = self.read(fb2('<section><title><p>Глава 1</p></title><p>Текст<a l:href="#n1" type="note">1</a> конец.</p><p>Ещё<a l:href="#n2">[2]</a> тут.</p></section>', notes), 'notes.fb2')
        self.assertEqual(['Текст конец.', 'Ещё тут.'], [block['text'] for block in fb['chapters'][0]['blocks'][1:]])

    def test_definition_lists_keep_terms_and_definitions_apart(self):
        book = self.read(make_epub('<dl><dt>Термин</dt><dd>Определение</dd><dt>Второй</dt><dd><p>Опр2</p><p>Ещё</p></dd></dl>'))
        self.assertEqual(['Термин', 'Определение', 'Второй', 'Опр2', 'Ещё'], self.texts(book))
        self.assertEqual('<p>Термин</p>', self.blocks(book)[0]['html'])

    def test_soft_hyphens_are_removed_from_text_but_kept_for_display(self):
        book = self.read(make_epub('<h1>Гла&shy;ва</h1><p>при&shy;мер тек&#173;ста</p>'))
        self.assertEqual(['Глава', 'пример текста'], self.texts(book))
        self.assertEqual('Глава', book['chapters'][0]['title'])
        self.assertIn('­', self.blocks(book)[1]['html'])
        fb = self.read(fb2('<section><title><p>Гла­ва</p></title><p>при­мер</p></section>'), 'shy.fb2')
        self.assertEqual(['Глава', 'пример'], self.texts(fb))

    def test_fb2_untitled_sections_get_localized_numbers_not_parent_titles(self):
        notes = '<body name="notes"><section id="n1"><title><p>1</p></title><p>Сноска.</p></section></body>'
        book = self.read(fb2('<title><p>Лев Толстой</p><p>Война и мир</p></title><section><p>Первая<a l:href="#n1" type="note">1</a>.</p></section>'
                             '<section><p>Вторая.</p></section><section><p>Третья.</p></section>', notes), 'war.fb2')
        self.assertEqual(['Глава 1', 'Глава 2', 'Глава 3', 'Примечания'], [chapter['title'] for chapter in book['chapters']])
        self.assertEqual(['Лев Толстой Война и мир', 'Первая.'], [block['text'] for block in book['chapters'][0]['blocks']])
        english = self.read(fb2('<section><p>One.</p></section><section><p>Two.</p></section>',
                                '<body name="notes"><section id="n1"><p>Note.</p></section></body>', lang='en'), 'en.fb2')
        self.assertEqual(['Chapter 1', 'Chapter 2', 'Notes'], [chapter['title'] for chapter in english['chapters']])

    def test_fb2_parts_do_not_become_heading_only_chapters(self):
        book = self.read(fb2('<section id="part1"><title><p>Часть первая</p></title><section><title><p>Глава 1</p></title><p>А.</p></section>'
                             '<section><p>Б.</p></section></section>'
                             '<section><title><p>Часть вторая</p></title><epigraph><p>Эпиграф.</p></epigraph><section><title><p>Глава 3</p></title><p>В.</p></section></section>'), 'parts.fb2')
        titles = [chapter['title'] for chapter in book['chapters']]
        self.assertEqual(['Глава 1', 'Часть первая (2)', 'Часть вторая', 'Глава 3'], titles)
        self.assertEqual(len(titles), len(set(titles)))
        for chapter in book['chapters']:
            self.assertTrue(any(block['kind'] != 'heading' for block in chapter['blocks']), chapter['title'])
        first = book['chapters'][0]['blocks']
        self.assertEqual(['Часть первая', 'Глава 1', 'А.'], [block['text'] for block in first])
        self.assertIn('part1', first[0]['anchors'])

    def test_fb2_note_markers_are_excluded_from_titles(self):
        notes = '<body name="notes"><section id="n1"><title><p>1</p></title><p>Сноска.</p></section></body>'
        book = self.read(fb2('<section><title><p>Глава 1<a l:href="#n1" type="note">1</a></p></title><p>Текст.</p></section>'
                             '<section><title><p>Глава 2<a l:href="#n1">[1]</a></p></title><p>Ещё.</p></section>', notes), 'titles.fb2')
        self.assertEqual(['Глава 1', 'Глава 2'], [chapter['title'] for chapter in book['chapters'][:2]])
        self.assertEqual(['Глава 1', 'Глава 2'], [chapter['blocks'][0]['text'] for chapter in book['chapters'][:2]])

    def test_empty_and_oversized_books_have_distinct_messages(self):
        with self.assertRaisesRegex(AppError, r'^Book has no readable text\.$'):
            self.read(fb2('<section><title><p></p></title></section>'), 'empty.fb2')
        with patch('rimsky.books.MAX_TEXT', 5), self.assertRaisesRegex(AppError, r'^Book text exceeds two million characters\.$'):
            self.read(fb2('<section><p>Достаточно длинный текст.</p></section>'), 'large.fb2')


class ReimportAndNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'library')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def changed_import(self, change):
        original = books.fb2
        def parse(data, asset_root):
            result = original(data, asset_root)
            change(result)
            return result
        with patch('rimsky.books.fb2', parse):
            return self.store.import_data(FB2, 'fixture.fb2')

    def edit(self, result):
        result['chapters'][0]['blocks'][1]['text'] += ' Исправлено.'

    def test_reimport_keeps_manifest_of_a_book_with_generations(self):
        book = self.store.import_data(FB2, 'fixture.fb2')
        generation = self.store.create_generation(book['id'], {'pack_path': 'unused', 'pack_fingerprint': 'fixture', 'pack_name': 'Verification', 'device': 'cpu'})
        complete_audio(self.store, generation)
        again = self.changed_import(self.edit)
        self.assertEqual(book['chapters'], again['chapters'])
        self.assertEqual(1, len(again['generations']))
        export = build_export(self.store, generation['id'], 'vtt', None, self.root)
        self.assertTrue((self.store.root / 'exports' / export['file']).read_text('utf-8').startswith('WEBVTT'))
        self.assertEqual('ready', Jobs(self.store, self.root).repair(generation['id'])['status'])

    def test_reimport_keeps_manifest_of_a_book_with_bookmarks_or_visuals(self):
        book = self.store.import_data(FB2, 'fixture.fb2')
        self.store.bookmark(book['id'], book['chapters'][0]['blocks'][1]['id'])
        self.assertEqual(book['chapters'], self.changed_import(self.edit)['chapters'])
        self.store.execute('DELETE FROM bookmarks')
        self.store.execute("INSERT INTO visuals(id,book_id,source_id,caption,file,kind,created) VALUES('v1',?,?,'', 'x.png','imported','now')",
                           (book['id'], book['chapters'][0]['blocks'][1]['id']))
        self.assertEqual(book['chapters'], self.changed_import(self.edit)['chapters'])

    def test_reimport_refreshes_an_unreferenced_book_and_drops_stale_positions(self):
        book = self.store.import_data(FB2, 'fixture.fb2')
        last = book['chapters'][1]
        self.store.save_reading_position(book['id'], {'chapter_id': last['id'], 'source_id': last['blocks'][-1]['id'], 'offset': 3})
        refreshed = self.changed_import(self.edit)
        self.assertTrue(refreshed['chapters'][0]['blocks'][1]['text'].endswith(' Исправлено.'))
        self.assertEqual(last['blocks'][-1]['id'], refreshed['reading_position']['source_id'])
        dropped = self.changed_import(lambda result: result['chapters'][1]['blocks'].pop())
        self.assertIsNone(dropped['reading_position'])
        self.assertEqual(1, len(self.store.library()))

    def test_default_generation_names_do_not_collide(self):
        book = self.store.import_data(FB2, 'fixture.fb2')
        config = {'pack_path': 'unused', 'pack_fingerprint': 'fixture', 'pack_name': 'Verification', 'device': 'cpu'}
        first = self.store.create_generation(book['id'], config)
        second = self.store.create_generation(book['id'], config)
        self.assertEqual(['Generation 1', 'Generation 2'], [first['name'], second['name']])
        self.store.delete_generation(first['id'])
        third = self.store.create_generation(book['id'], config)
        self.assertEqual('Generation 3', third['name'])
        self.store.create_generation(book['id'], config, 'Generation 4')
        self.assertEqual('Generation 5', self.store.create_generation(book['id'], config)['name'])


if __name__ == '__main__':
    unittest.main()
