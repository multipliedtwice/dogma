from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.analysis import relevant_characters
from rimsky.jobs import Jobs, alias_candidates, canonical_name, name_tokens


def record(cid, name, gender='unknown', *aliases):
    return {'id': cid, 'name': name, 'aliases': list(aliases), 'description': '', 'evidence': [f'p{cid}'],
            'gender': {'value': gender, 'evidence': [] if gender == 'unknown' else [f'p{cid}']}, 'age_band': {'value': 'unknown', 'evidence': []}}


def paired(first, second):
    return list(alias_candidates([record('a', first), record('b', second)]))


def forms(text):
    return canonical_name(name_tokens(text))


class Diminutives(unittest.TestCase):
    def test_common_nouns_are_not_read_as_diminutives(self):
        for first, second in (('Жена', 'Евгений'), ('Люди', 'Людмила'), ('Кола', 'Николай'), ('Вита', 'Виктор'), ('Лёни', 'Елена'),
                              ('Лень', 'Леонид'), ('Ром', 'Роман')):
            with self.subTest(first=first, second=second):
                self.assertEqual([], paired(first, second))

    def test_inflected_diminutives_join_full_names(self):
        for first, second in (('Сашей', 'Александр'), ('Коли', 'Николай'), ('Лёни', 'Леонид'), ('Женю', 'Евгения'), ('Стасом', 'Станислав'),
                              ('Паша', 'Павел'), ('Паша', 'Павла')):
            with self.subTest(first=first, second=second):
                self.assertEqual([('a', 'b')], paired(first, second))

    def test_only_capitalised_labels_get_diminutive_forms(self):
        self.assertEqual([], paired('слава', 'Ярослав'))
        self.assertEqual([('a', 'b')], paired('Слава', 'Ярослав'))
        self.assertEqual([('a', 'b')], list(alias_candidates([record('a', 'Ярослав'), record('b', 'гонец', 'unknown', 'Слава')])))


class GenderGuard(unittest.TestCase):
    def reconcile(self, characters, saved=None):
        asked, saves = [], []
        class Engine:
            def call(self, method, payload, stop):
                asked.append([character['id'] for character in payload['characters']])
                return {'same': True, 'evidence': [next(iter(passages)) for passages in payload['evidence'].values()]}
        jobs = Jobs(None, Path('.'))
        jobs.engine, jobs.stop = Engine(), threading.Event()
        texts = {pid: 'text' for character in characters for pid in character['evidence']}
        return asked, jobs._reconcile({}, characters, texts, saved=saved, save=saves.append), saves

    def test_known_opposite_genders_are_not_compared(self):
        characters = [record('d', 'Мария Петрова', 'female'), record('b', 'Петрова', 'female'), record('a', 'Петров', 'male'),
                      record('s', 'Александр', 'male'), record('t', 'Александра', 'female')]
        self.assertEqual({('d', 'b'), ('d', 'a'), ('b', 'a'), ('s', 't')}, set(alias_candidates(characters)))
        asked, (cast, merges, unmerged, skipped, state), saves = self.reconcile(characters)
        self.assertEqual([['d', 'b']], asked)
        self.assertEqual(({'b': 'd'}, 0, 0, []), (merges, unmerged, skipped, state['unmerged_pairs']))
        self.assertEqual({'stage': 'reconcile', 'done': 1, 'total': 1}, saves[-1]['progress'])

    def test_identical_names_and_unknown_genders_are_still_compared(self):
        characters = [record('a', 'Саша', 'male'), record('b', 'саша', 'female'), record('c', 'Петров', 'male'), record('d', 'Петрова')]
        asked, (cast, merges, *_), _ = self.reconcile(characters)
        self.assertEqual([['a', 'b'], ['c', 'd']], sorted(asked))
        self.assertEqual({'b': 'a', 'd': 'c'}, merges)

    def test_saved_merge_of_an_opposed_pair_is_not_replayed(self):
        characters = [record('a', 'Петров', 'male'), record('b', 'Петрова', 'female')]
        asked, (cast, merges, unmerged, skipped, state), _ = self.reconcile(characters, {'decisions': [['a', 'b', True]], 'compared': 1, 'skipped': 0})
        self.assertEqual(([], {}, 0, 0), (asked, merges, unmerged, skipped))
        self.assertEqual({'decisions': [], 'compared': 0, 'skipped': 0}, state['reconcile'])


class NameForms(unittest.TestCase):
    def test_case_forms_share_one_canonical_form(self):
        for paradigm in ('Николай Николая Николаю Николаем Николае', 'Павел Павла Павлу Павлом Павле', 'Лев Льва Льву Львом Льве',
                         'Андрей Андрея Андрею Андреем Андрее', 'Отец Отца Отцом', 'Боец Бойца Бойцом', 'Незнакомец Незнакомца',
                         'Санёк Санька Саньку', 'Сынок Сынка', 'Олежек Олежка', 'Аглая Аглаи Аглае Аглаю Аглаей',
                         'Пелагея Пелагеи Пелагее Пелагеей', 'Герой Героя Героем', 'Кузнец Кузнеца', 'Медведев Медведева Медведевым'):
            with self.subTest(paradigm=paradigm):
                self.assertEqual(1, len({forms(word) for word in paradigm.split()}))
        self.assertEqual(4, len({forms(word) for word in ('Марк', 'Марек', 'Танк', 'Танька')}))

    def test_fleeting_vowel_and_ai_forms_are_alias_candidates(self):
        for first, second in (('Павел', 'Павла'), ('Лев', 'Льва'), ('Николай', 'Николая'), ('Отец', 'Отца'), ('Санёк', 'Санька')):
            with self.subTest(first=first, second=second):
                self.assertEqual([('a', 'b')], paired(first, second))
        self.assertEqual([('a', 'b')], paired('Кузнец', 'Кузнецов'))

    def test_projection_finds_names_in_oblique_forms(self):
        cast = [record(f'x{index:02d}', f'Персонаж{index}') for index in range(45)] + [record('nik', 'Николай'), record('pav', 'Павел'), record('lev', 'Лев')]
        for text, cid in (('— Нет, — сказал брат Николая.', 'nik'), ('Он ждал Павла.', 'pav'), ('Все смотрели на Льва.', 'lev')):
            with self.subTest(text=text):
                self.assertIn(cid, [character['id'] for character in relevant_characters(cast, [text], names=forms)])


if __name__ == '__main__':
    unittest.main()
