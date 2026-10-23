from __future__ import annotations

import base64
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.books import MAX_ENTRY, import_book
from rimsky.preparation import default_chapters, speech_sources
from rimsky.util import AppError
from test_epub_preparation import make_epub

FB2_HEAD = '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><author><first-name>Synthetic</first-name><last-name>Fixture</last-name></author><book-title>Repairs</book-title><lang>ru</lang></title-info></description>'


def make_fb2(bodies, tail=''):
    return (FB2_HEAD + bodies + tail + '</FictionBook>').encode()


class ReviewRepairsImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def epub(self, body):
        return import_book(make_epub(body), 'fixture.epub', self.root / 'books')

    def fb2(self, data):
        return import_book(data, 'fixture.fb2', self.root / 'books')

    def texts(self, book):
        return [block['text'] for chapter in book['chapters'] for block in chapter['blocks']]

    def test_text_divs_are_separate_paragraphs(self):
        book = self.epub('<div class="p">Первый абзац.</div><div class="p">Второй <em>абзац</em>.</div>')
        self.assertEqual(['Первый абзац.', 'Второй абзац.'], self.texts(book))
        self.assertTrue(all(block['html'].startswith('<p>') for block in book['chapters'][0]['blocks']))

    def test_wrapper_divs_are_walked_as_containers(self):
        book = self.epub('<div class="chapter"><div class="p">Один.</div><div class="wrap"><div class="p">Два.</div><p>Три.</p></div></div>')
        self.assertEqual(['Один.', 'Два.', 'Три.'], self.texts(book))

    def test_inline_runs_stay_grouped_around_div_paragraphs(self):
        book = self.epub('<div class="p">Текст <span>со</span> <a href="#x">ссылкой</a> внутри.</div>Свободный <em>текст</em><div id="x" class="p">Цель.</div>')
        self.assertEqual(['Текст со ссылкой внутри.', 'Свободный текст', 'Цель.'], self.texts(book))
        blocks = book['chapters'][0]['blocks']
        self.assertIn(f'data-source-link="{blocks[2]["id"]}"', blocks[0]['html'])

    def test_named_secondary_fb2_bodies_are_not_default_narration(self):
        book = self.fb2(make_fb2('<body><section><title><p>Глава</p></title><p>Основной текст.</p></section></body>'
                                 '<body name="notes"><section id="n1"><title><p>1</p></title><p>Примечание.</p></section></body>'
                                 '<body name="comments"><section id="c1"><p>Комментарий.</p></section></body>'))
        roles = [(chapter['role'], chapter['default_narration'], chapter['linear']) for chapter in book['chapters']]
        self.assertEqual([('body', True, True), ('auxiliary', False, False), ('auxiliary', False, False)], roles)
        self.assertEqual([book['chapters'][0]['id']], default_chapters(book))
        self.assertEqual([book['chapters'][0]['id']], book['preparation']['default_chapters'])
        self.assertEqual(['Примечание.'], [block['text'] for block in book['chapters'][1]['blocks'] if block['kind'] == 'paragraph'])

    def test_fb2_empty_line_is_a_scene_separator(self):
        book = self.fb2(make_fb2('<body><section><p>— Первая сцена, — сказал он.</p><empty-line/><p>— Вторая сцена, — ответила она.</p></section></body>'))
        blocks = book['chapters'][0]['blocks']
        self.assertEqual(['paragraph', 'separator', 'paragraph'], [block['kind'] for block in blocks])
        self.assertEqual('<hr/>', blocks[1]['html'])
        scenes = [part['scene_id'] for part in speech_sources(book, [book['chapters'][0]['id']])]
        self.assertEqual(2, len(scenes))
        self.assertNotEqual(scenes[0], scenes[1])
        spacing = self.fb2(make_fb2('<body><section><empty-line/><p>Начало.</p><cite><p>Дорогой друг!</p><empty-line/><p>Пишу письмо.</p></cite>'
                                    '<epigraph><p>Строка.</p><empty-line/><p>Ещё строка.</p></epigraph><poem><stanza><v>Один</v><empty-line/><v>Два</v></stanza></poem>'
                                    '<p>Текст<empty-line/>внутри.</p><empty-line/></section></body>'))
        blocks = spacing['chapters'][0]['blocks']
        self.assertNotIn('separator', [block['kind'] for block in blocks])
        self.assertIn('Текст внутри.', [block['text'] for block in blocks])
        self.assertEqual(1, len({part['scene_id'] for part in speech_sources(spacing, [spacing['chapters'][0]['id']])}))

    def test_fb2_above_entry_limit_with_large_binary_imports(self):
        payload = base64.b64encode(b'\0' * (MAX_ENTRY // 4 * 3 + 1024 * 1024)).decode()
        data = make_fb2('<body><section><p>Текст книги.</p></section></body>', f'<binary id="big.png" content-type="image/png">{payload}</binary>')
        self.assertGreater(len(data), MAX_ENTRY)
        self.assertEqual(['Текст книги.'], self.texts(self.fb2(data)))

    def test_fb2_keeps_entity_and_depth_protections(self):
        with self.assertRaises(AppError):
            self.fb2(b'<!DOCTYPE FictionBook [<!ENTITY x SYSTEM "file:///etc/passwd">]><FictionBook><body><section><p>&x;</p></section></body></FictionBook>')
        deep = '<section>' * 300 + '<p>Глубоко.</p>' + '</section>' * 300
        with self.assertRaises(AppError):
            self.fb2(make_fb2(f'<body>{deep}</body>'))

    def test_fb2_note_links_resolve_to_prepared_sources(self):
        book = self.fb2(make_fb2('<body><section><p>Текст<a l:href="#n1" type="note">[1]</a> и <a l:href="#target">переход</a> и <a l:href="https://example.com">внешняя</a>.</p><p id="target">Цель.</p></section></body>'
                                 '<body name="notes"><section id="n1"><title><p>1</p></title><p>Примечание.</p></section></body>'))
        main, notes = book['chapters']
        html = main['blocks'][0]['html']
        self.assertIn(f'data-source-link="{notes["blocks"][0]["id"]}"', html)
        self.assertIn(f'data-source-link="{main["blocks"][1]["id"]}"', html)
        self.assertEqual(2, html.count('data-source-link='))
        self.assertNotIn('link0', html)
        self.assertNotIn('location', main['blocks'][0])

    def test_ordered_list_numbers_are_displayed_and_spoken(self):
        book = self.epub('<ol start="3"><li>Альфа.</li><li value="7">Бета.</li><li>Гамма.</li></ol>'
                         '<ol type="a"><li>Первый.</li><li><p>Второй.</p><p>Продолжение.</p></li></ol>'
                         '<ol reversed="reversed" type="I"><li>Два.</li><li>Один.</li></ol><ul><li>Пункт.</li></ul><p>После.</p>')
        blocks = book['chapters'][0]['blocks']
        self.assertEqual(['3. Альфа.', '7. Бета.', '8. Гамма.', 'a. Первый.', 'b. Второй.', 'Продолжение.', 'II. Два.', 'I. Один.', 'Пункт.', 'После.'],
                         [block['text'] for block in blocks])
        self.assertEqual('<p>3. Альфа.</p>', blocks[0]['html'])
        self.assertEqual('<li>Пункт.</li>', blocks[8]['html'])
        for part in speech_sources(book, [book['chapters'][0]['id']]):
            source = next(block for block in blocks if block['id'] == part['source_id'])
            self.assertEqual(source['text'][part['start']:part['end']], part['text'])


if __name__ == '__main__':
    unittest.main()
