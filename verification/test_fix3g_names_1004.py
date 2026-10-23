from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_core import SyntheticEngine, narration
from test_epub_preparation import make_epub
from test_fix3_analysis_1004 import Library, character, fb2
from rimsky.analysis import common_words, merge_character, preferred_name
from rimsky.books import import_book
from rimsky.jobs import merge_aliases
from rimsky.preparation import default_chapters, speech_sources

CAPTAIN = ['The captain looked at the grey sea. "We sail at dawn," the captain said.',
           'Nobody answered the captain. Later the captain went below, and the captain slept until noon.',
           'The men respected the captain; even the cook feared the captain and his temper.',
           '"Ready?" asked Captain Hale. The crew nodded to Captain Hale, and then Captain Hale smiled.']
VERSE = 'Over the hills and far away, The wind is blowing cold, The sea is grey and the sky is grey, The night is growing old.'
PROSE = 'The old man walked to the edge of the pier and looked at the boats, the nets and the gulls over the water of the bay.'
TIMES = 'He read The Times at the club, the doorman read The Times at the door, and the cook read The Times in the kitchen.'
SAMPLE = ['Роман писал роман третий год. Этот роман о море, и роман почти готов.', '— Рома, иди обедать! — крикнула мать.',
          'Вера кивнула. Его вера была крепка, а вера отца — ещё крепче.', '— Верочка, подожди! — сказал он.',
          'Will said he will come. I will wait, and you will see.', '— Willy, come here! — she said.']


def labels(cast):
    return [(item['name'], item['aliases']) for item in cast if item['id'] != 'narrator']


def merged(old, new, common):
    return labels(merge_aliases([character('a', old), character('b', new)], {'b': 'a'}, common))


class Naming(SyntheticEngine):
    def __init__(self, introduce, rename):
        super().__init__()
        self.introduce, self.rename = introduce, rename

    def call(self, method, payload, stop):
        if method != 'analyze':
            return super().call(method, payload, stop)
        known, characters = {item['name']: item['id'] for item in payload['cast']}, []
        for item in payload['segments']:
            characters += [character(f'new_{len(characters) + 1}', name, evidence=[item['id']]) for cue, name in self.introduce.items() if cue in item['text']]
            characters += [character(known[old], name, evidence=[item['id']]) for cue, (old, name) in self.rename.items() if cue in item['text'] and old in known]
        return {'characters': characters, 'passages': [narration(item) for item in payload['segments']]}


class Titles(unittest.TestCase):
    def test_articles_and_a_capital_before_another_capital_are_not_name_evidence(self):
        for texts, word, pairs in ((CAPTAIN, 'captain', (('Captain', 'Hale'), ('The captain', 'Hale'))),
                                   ([VERSE, PROSE], 'the', (('The old man', 'Santiago'), ('The captain', 'Hale'))),
                                   ([TIMES], 'the', (('The old man', 'Santiago'),))):
            common = common_words(texts)
            self.assertIn(word, common)
            for old, new in pairs:
                with self.subTest(old=old, new=new):
                    self.assertEqual((new, [(new, [old])]), (preferred_name(old, new, common), merged(old, new, common)))

    def test_only_whitespace_before_the_next_capital_cancels_the_name_evidence(self):
        def vocabulary(after):
            return common_words([' '.join(f'и старик и Старик{after}' for _ in range(3)) + ' и старик и старик'])
        for after in (' Петров', '\xa0Петров', '  Петров'):
            with self.subTest(adjacent=after):
                self.assertIn('старик', vocabulary(after))
        for after in ('. Потом', ', Иван', ' — Иван', '! Иван', ' и'):
            with self.subTest(separated=after):
                self.assertNotIn('старик', vocabulary(after))

    def test_epub_verse_lines_and_two_line_headings_keep_the_article_in_the_vocabulary(self):
        poem = f'<p class="verse">{VERSE.replace(", The", ",<br/>The")}</p><p>{PROSE}</p>'
        heads = ''.join(f'<h1>Chapter {number}<br/>The {title}</h1><p>{PROSE}</p>' for number, title in ((1, 'Storm'), (2, 'Harbour'), (3, 'Wreck')))
        for body in (poem, heads):
            with self.subTest(body=body[:40]), tempfile.TemporaryDirectory() as folder:
                book = import_book(make_epub(body), 'book.epub', Path(folder))
                common = common_words(source['text'] for source in speech_sources(book, default_chapters(book)))
                self.assertIn('the', common)
                self.assertEqual('Santiago', preferred_name('The old man', 'Santiago', common))


class Stems(unittest.TestCase):
    def test_a_single_word_label_extending_the_name_stem_becomes_an_alias(self):
        common = common_words(SAMPLE)
        self.assertTrue({'вера', 'will'} <= common and 'роман' not in common)
        for old, new in (('Вера', 'Верочка'), ('Will', 'Willy'), ('Роман', 'Рома'), ('Вера', 'Веру')):
            with self.subTest(old=old, new=new):
                record = merge_character(character('a', old), character('b', new), common)
                self.assertEqual((old, old, [new], [(old, [new])]), (preferred_name(old, new, common), record['name'], record['aliases'], merged(old, new, common)))

    def test_other_labels_follow_the_proper_name_rule(self):
        common = common_words([*SAMPLE, 'Вошёл незнакомец, и незнакомец сел. Дед кивнул, а дед ушёл, и дед вернулся.'])
        self.assertTrue({'вера', 'незнакомец', 'дед'} <= common)
        for old, new, expected in (('Вера', 'Вера Павловна', 'Вера Павловна'), ('Вера', 'Павловна', 'Павловна'), ('Незнакомец', 'Кирилл', 'Кирилл'),
                                   ('Дед', 'Денис', 'Денис'), ('Верочка', 'Вера', 'Верочка')):
            with self.subTest(old=old, new=new):
                self.assertEqual(expected, preferred_name(old, new, common))


class JobsNames(Library):
    def test_jobs_rename_descriptors_despite_verse_and_honorific_capitals(self):
        done = self.run_book(fb2(('One', [VERSE, '"Sit down," the old man said to the boy.', *CAPTAIN]),
                                 ('Two', ['"My name is Santiago," the old man said.', '"Call me Hale," the captain said.'])),
                             lambda _: Naming({'"Sit down,"': 'The old man', '"We sail': 'Captain'},
                                              {'Santiago': ('The old man', 'Santiago'), 'Call me Hale': ('Captain', 'Hale')}))
        self.assertEqual('review', done['status'], done['error'])
        self.assertEqual([('Hale', ['Captain']), ('Santiago', ['The old man'])], sorted(labels(done['cast'])))

    def test_jobs_keep_a_name_written_only_at_sentence_starts_when_its_diminutive_arrives(self):
        done = self.run_book(fb2(('Первая', ['Его вера была крепка, и вера отца тоже.', 'Вера кивнула и вышла.']), ('Вторая', ['— Верочка, подожди! — сказал он.'])),
                             lambda _: Naming({'Вера кивнула': 'Вера'}, {'Верочка': ('Вера', 'Верочка')}))
        self.assertEqual('review', done['status'], done['error'])
        self.assertEqual([('Вера', ['Верочка'])], labels(done['cast']))


if __name__ == '__main__':
    unittest.main()
