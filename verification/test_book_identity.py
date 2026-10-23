import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime')]
from rimsky import book_identity
from rimsky.book_identity import parse_title, query_words, same_name, surname, tokens, valid_isbn
from rimsky.store import Store


def epub(metadata, package_attributes='version="2.0"'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr('OEBPS/content.opf', f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" {package_attributes}>'
                         f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">{metadata}</metadata>'
                         '<manifest/><spine/></package>')
    return buffer.getvalue()


FB2 = '''<?xml version="1.0" encoding="windows-1251"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description>
<title-info><genre>litrpg</genre><author><first-name>Владимир</first-name><last-name>Посмыгаев</last-name></author>
<book-title>Элирм IV</book-title><lang>ru</lang><sequence name="Элирм" number="4"/></title-info>
<src-title-info><author><first-name>Vladimir</first-name><last-name>Posmygaev</last-name></author><book-title>Elirm IV</book-title></src-title-info>
<publish-info><isbn>5-85374-190-X</isbn><sequence name="Издательская серия" number="77"/></publish-info>
</description><body><section><p>Текст.</p></section></body></FictionBook>'''.encode('cp1251')


class TitleParsing(unittest.TestCase):
    def test_volume_patterns(self):
        cases = {
            'Искажающие реальность. Книга 4. Паутина миров': ('Искажающие реальность', 4, 'Паутина миров'),
            '(ир 02) Искажающие реальность-2': ('Искажающие реальность', 2, ''),
            'Искажающие Реальность – 12': ('Искажающие Реальность', 12, ''),
            'Элирм IV': ('Элирм', 4, ''),
            'Ведьмак. Кн. 3. Кровь эльфов': ('Ведьмак', 3, 'Кровь эльфов'),
            'The Expanse #3': ('The Expanse', 3, ''),
            'Dune, Book 2: Dune Messiah': ('Dune', 2, 'Dune Messiah'),
            'Stalker Vol. II': ('Stalker', 2, ''),
        }
        for title, (series, volume, subtitle) in cases.items():
            parsed = parse_title(title)
            self.assertEqual((parsed['series'], parsed['volume'], parsed['subtitle']), (series, volume, subtitle), title)

    def test_numbers_that_are_not_volumes(self):
        for title in ('Метро 2033', 'Сталкер - 2033', '1984', 'Книга судеб', 'Звёзды — холодные игрушки', 'Гарри Поттер и узник Азкабана'):
            self.assertIsNone(parse_title(title)['volume'], title)
        # A shelf prefix alone still supplies a volume, and is removed from the title.
        self.assertEqual(parse_title('(ир 03) Ловец душ'), {'title': 'Ловец душ', 'series': '', 'volume': 3, 'subtitle': '', 'omnibus': False})
        # A year in the prefix is not a volume, and a fractional volume is a side story, not the volume before it.
        self.assertIsNone(parse_title('(2015) Путь домой')['volume'])
        self.assertIsNone(parse_title('Искажающие реальность. Книга 2.5')['volume'])
        self.assertIsNone(book_identity._volume('2.5'))
        self.assertEqual(book_identity._volume('3.0'), 3)

    def test_store_tags_and_leading_volume_words(self):
        self.assertEqual(parse_title('Искажающие реальность-7 (СИ) [Litres]'), {'title': 'Искажающие реальность-7', 'series': 'Искажающие реальность', 'volume': 7,
                                                                                 'subtitle': '', 'omnibus': False})
        self.assertEqual(parse_title('Цикл «Земляне» (Том 01)')['volume'], 1)  # a bracket holding the volume is not a tag
        self.assertEqual(parse_title('Книга 1. Начало'), {'title': 'Книга 1. Начало', 'series': '', 'volume': 1, 'subtitle': 'Начало', 'omnibus': False})

    def test_omnibus_files_have_no_single_volume(self):
        for title, series in (('Искажающие реальность. Книги 1-3', 'Искажающие реальность'), ('Искажающие реальность (тома 1–5)', 'Искажающие реальность'),
                              ('Ведьмак. Трилогия', '')):
            parsed = parse_title(title)
            self.assertEqual((parsed['series'], parsed['volume'], parsed['omnibus']), (series, None, True), title)
        self.assertEqual(tokens('Ведьмак. Трилогия'), ['ведьмак'])
        # A year range is not a set of volumes.
        self.assertFalse(parse_title('Война 1941-1945')['omnibus'])
        self.assertEqual(parse_title('(СИ)')['title'], '(СИ)')
        self.assertEqual(parse_title('Пикник на обочине (1972)')['title'], 'Пикник на обочине')
        self.assertEqual(parse_title('Сборник (Том 12)')['volume'], 12)

    def test_tokens_and_query_words(self):
        self.assertEqual(tokens('Элирм IV'), ['элирм', '4'])
        self.assertEqual(tokens('Искажающие реальность. Книга 04. Отсчëт'), ['искажающие', 'реальность', '4', 'отсчет'])
        self.assertEqual(tokens('I, Robot is mine'), ['1', 'robot', 'is', 'mine'])
        # FantLab indexes Roman numerals as written, so searches keep them.
        self.assertEqual(query_words('Элирм IV. Книга 02'), ['элирм', 'iv', '2'])
        # Unicode digits that int() rejects stay ordinary words instead of crashing.
        self.assertEqual(tokens('E=mc² 2'), ['mc²', '2'])
        self.assertEqual(tokens('Книга 2.5'), ['2_5'])
        self.assertEqual(query_words('Книга 2.5 Спасти'), ['спасти'])
        self.assertEqual(query_words('E=mc²'), ['mc²'])

    def test_names(self):
        self.assertEqual(surname('Атаманов Михаил Александрович'), 'атаманов')
        self.assertEqual(surname('Михаил Александрович Атаманов'), 'атаманов')
        self.assertEqual(surname('Дмитрий Шостакович'), 'шостакович')
        self.assertEqual(surname('Frank Herbert'), 'herbert')
        self.assertEqual(surname(''), '')
        self.assertTrue(same_name('стругацкий', 'стругацкие'))
        self.assertFalse(same_name('мазин', 'мазина'))
        self.assertFalse(same_name('владимир', 'готлейб'))
        # Transliteration across scripts, and the Г/Х rendering of H; unrelated short names stay distinct.
        self.assertTrue(same_name('atamanov', 'атаманов'))
        self.assertTrue(same_name('glukhovsky', 'глуховский'))
        self.assertTrue(same_name('lukyanenko', 'лукьяненко'))
        self.assertTrue(same_name('херберт', 'герберт'))
        self.assertFalse(same_name('гаров', 'харов'))
        self.assertFalse(same_name('ivanov', 'петров'))

    def test_isbn_checksums(self):
        self.assertEqual(valid_isbn('978-0-306-40615-7'), '9780306406157')
        self.assertEqual(valid_isbn('5-85374-190-x'), '585374190X')
        self.assertIsNone(valid_isbn('978-0-306-40615-8'))
        self.assertIsNone(valid_isbn('908aa89d-ba65-4781'))


class SourceMetadata(unittest.TestCase):
    def test_epub2_roles_series_and_isbn(self):
        found = book_identity.read_epub(epub(
            '<dc:title>Кровь эльфов</dc:title><dc:creator opf:role="aut">Анджей Сапковский</dc:creator>'
            '<dc:creator opf:role="trl">Евгений Вайсброт</dc:creator><dc:creator opf:role="AUT ">Соавтор Второй</dc:creator>'
            '<dc:identifier opf:scheme="ISBN">978-0-306-40615-7</dc:identifier>'
            '<dc:identifier opf:scheme="uuid">908aa89d-ba65-4781-afe6-2065521048fc</dc:identifier>'
            '<meta name="calibre:series" content="Ведьмак"/><meta name="calibre:series_index" content="3.0"/>'))
        self.assertEqual(found, {'titles': ['Кровь эльфов'], 'authors': ['Анджей Сапковский', 'Соавтор Второй'], 'series': 'Ведьмак', 'volume': 3,
                                 'isbns': ['9780306406157']})

    def test_epub3_refinements(self):
        found = book_identity.read_epub(epub(
            '<dc:title>Час презрения</dc:title><dc:creator id="a">Анджей Сапковский</dc:creator><dc:creator id="t">Переводчик Иванов</dc:creator>'
            '<meta refines="#t" property="role" scheme="marc:relators">trl</meta>'
            '<meta property="belongs-to-collection" id="c">Ведьмак</meta><meta refines="#c" property="collection-type">series</meta>'
            '<meta refines="#c" property="group-position">4</meta>', 'version="3.0"'))
        self.assertEqual((found['authors'], found['series'], found['volume']), (['Анджей Сапковский'], 'Ведьмак', 4))

    def test_fb2_sequence_original_title_and_isbn(self):
        # Only the header is parsed: a body that is not even well-formed does not matter.
        self.assertEqual(book_identity.read_fb2(FB2 + b'<broken'), book_identity.read_fb2(FB2))
        self.assertNotIn(b'<body>', book_identity._fb2_description(FB2))
        found = book_identity.read_fb2(FB2)
        self.assertEqual(found, {'titles': ['Элирм IV', 'Elirm IV'], 'authors': ['Владимир Посмыгаев', 'Vladimir Posmygaev'],
                                 'series': 'Элирм', 'volume': 4, 'isbns': ['585374190X']})


class Hints(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name))

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_hints_prefer_the_source_and_are_cached(self):
        book = {'id': 'c' * 32, 'title': 'Элирм IV', 'author': 'Владимир Посмыгаев, Vladimir Posmygaev', 'format': 'fb2'}
        (self.store.root / 'books' / book['id']).mkdir(parents=True)
        (self.store.root / 'books' / book['id'] / 'source.fb2').write_bytes(FB2)
        found = book_identity.hints(self.store, book)
        self.assertEqual(found, {'title': 'Элирм IV', 'alternate_titles': ['Elirm IV'], 'authors': ['Владимир Посмыгаев', 'Vladimir Posmygaev'],
                                 'series': 'Элирм', 'volume': 4, 'subtitle': '', 'omnibus': False, 'isbns': ['585374190X']})
        (self.store.root / 'books' / book['id'] / 'source.fb2').unlink()
        self.assertEqual(book_identity.hints(self.store, book), found)

    def test_unreadable_source_falls_back_to_the_library_entry(self):
        book = {'id': 'd' * 32, 'title': '(ир 03) Искажающие реальность-3', 'author': 'Атаманов Михаил Александрович, Другой Автор', 'format': 'epub'}
        (self.store.root / 'books' / book['id']).mkdir(parents=True)
        (self.store.root / 'books' / book['id'] / 'source.epub').write_bytes(b'not a zip')
        found = book_identity.hints(self.store, book)
        self.assertEqual((found['authors'], found['series'], found['volume']), (['Атаманов Михаил Александрович', 'Другой Автор'], 'Искажающие реальность', 3))

    def test_cache_version_is_respected(self):
        book = {'id': 'e' * 32, 'title': 'Дюна', 'author': 'Фрэнк Герберт', 'format': 'epub'}
        cache = self.store.root / 'cache' / 'book-hints' / f"{book['id']}.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({'version': book_identity.VERSION - 1, 'hints': {'title': 'stale'}}), encoding='utf-8')
        self.assertEqual(book_identity.hints(self.store, book)['title'], 'Дюна')


if __name__ == '__main__':
    unittest.main()
