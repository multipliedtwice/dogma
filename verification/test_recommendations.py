import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime')]
from rimsky import book_identity, recommendations
from rimsky.store import DEFAULTS, Store
from rimsky.util import AppError


def similar(work_id, name, author, rating='8.00', voters=1000, kind='novel', image=None):
    return {'id': work_id, 'name': name, 'name_orig': '', 'name_type': 'роман', 'name_type_icon': kind, 'year': 2020,
            'creators': {'authors': [{'id': 1, 'name': author}]}, 'stat': {'rating': rating, 'voters': voters, 'responses': 3},
            'image_preview': image, 'description': '[b]Описание[/b]   с   пробелами', 'saga': {'name': 'Цикл'}}


SEARCH = {
    'атаманов искажающие реальность обратный отсчет': [],
    'атаманов искажающие реальность обратный': [],
    'атаманов искажающие реальность': [
        {'work_id': 999544, 'name_eng': 'cycle', 'rusname': 'Искажающие реальность', 'name': '', 'altname': '', 'autor_rusname': 'Михаил Атаманов', 'markcount': 845},
        {'work_id': 904309, 'name_eng': 'novel', 'rusname': 'Искажающие реальность. Обратный отсчëт', 'name': '', 'altname': '', 'autor_rusname': 'Михаил Атаманов', 'markcount': 1131},
        {'work_id': 5, 'name_eng': 'review', 'rusname': 'Искажающие реальность. Обратный отсчёт', 'name': '', 'altname': '', 'autor_rusname': 'Михаил Атаманов', 'markcount': 9999},
    ],
}
SIMILARS = {
    904309: [similar(6414, 'Диптаун', 'Сергей Лукьяненко', '8.64', 6273, 'cycle'),
             similar(7, 'Рецензия', 'Кто-то', kind='review'),
             similar(904309, 'Искажающие реальность. Обратный отсчёт', 'Михаил Атаманов'),
             similar(42, 'Малоизвестная', 'Автор', '9.90', 4)],
    4325: [similar(6414, 'Диптаун', 'Сергей Лукьяненко', '8.64', 6273, 'cycle'),
           similar(1, 'Гиперион', 'Дэн Симмонс', '8.64', 10565, image='/images/editions/small/184')],
}


def hint(title, authors=(), series='', volume=None, isbns=()):
    return {'title': title, 'authors': list(authors), 'series': series, 'volume': volume, 'isbns': list(isbns), 'alternate_titles': []}


def query(path):
    return parse_qs(urlsplit(path).query).get('q', [''])[0]


class FakeFantLab:
    def __init__(self):
        self.paths = []

    def __call__(self, store, path):
        self.paths.append(path)
        url = urlsplit(path)
        if url.path == '/search-works':
            return SEARCH.get(parse_qs(url.query)['q'][0], [])
        if url.path.endswith('/similars'):
            return SIMILARS.get(int(url.path.split('/')[2]), [])
        if url.path.endswith('/extended'):
            return {'children': []}
        if url.path == '/search-autors':
            return []
        raise AssertionError(path)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name))

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_online_lookups_are_opt_in(self):
        self.assertFalse(self.store.settings()['online_recommendations'])
        with self.assertRaises(AppError) as raised:
            recommendations.recommend(self.store, [], ['litrpg'], fetch=FakeFantLab())
        self.assertEqual(raised.exception.code, 'recommendations_off')
        with self.assertRaises(AppError):
            recommendations.cover(self.store, '184', download=lambda *_args, **_kw: self.fail('downloaded while off'))

    def test_genre_settings_are_validated(self):
        saved = self.store.update_settings({'recommendation_genres': ['litrpg', 'horror']})
        self.assertEqual(saved['recommendation_genres'], ['litrpg', 'horror'])
        for invalid in (['unknown'], ['litrpg', 'litrpg'], 'litrpg'):
            with self.assertRaises(AppError):
                self.store.update_settings({'recommendation_genres': invalid})

    def test_library_match_broadens_the_search_and_requires_the_author(self):
        fake = FakeFantLab()
        found = recommendations.match_work(self.store, hint('Искажающие реальность. Обратный отсчёт', ['Михаил Атаманов']), fake)
        self.assertEqual((found['work_id'], found['method']), (904309, 'title'))
        self.assertEqual([query(path) for path in fake.paths], ['атаманов искажающие реальность обратный отсчет', 'атаманов искажающие реальность'])
        # Another author with a title that is not an exact, unique fit is not a match.
        self.assertIsNone(recommendations.match_work(self.store, hint('Искажающие реальность. Обратный', ['Другой Писатель']), FakeFantLab()))

    def test_pen_names_match_only_an_exact_unique_title(self):
        works = [{'work_id': 1730009, 'name_eng': 'novel', 'rusname': 'Элирм IV', 'autor_rusname': 'Владимир Готлейб', 'markcount': 50},
                 {'work_id': 1730012, 'name_eng': 'novel', 'rusname': 'Элирм V', 'autor_rusname': 'Владимир Готлейб', 'markcount': 40},
                 {'work_id': 1729998, 'name_eng': 'cycle', 'rusname': 'Элирм', 'autor_rusname': 'Владимир Готлейб', 'markcount': 90}]
        fetch = lambda store, path: [] if 'посмыгаев' in query(path) else works  # noqa: E731
        found = recommendations.match_work(self.store, hint('Элирм IV', ['Владимир Посмыгаев'], 'Элирм', 4), fetch)
        self.assertEqual((found['work_id'], found['method']), (1730009, 'title_only'))
        # A series named after its first book: the novel, not the cycle with the same name.
        works.append({'work_id': 1729990, 'name_eng': 'cycle', 'rusname': 'Элирм IV', 'autor_rusname': 'Владимир Готлейб', 'markcount': 1})
        self.assertEqual(recommendations.match_work(self.store, hint('Элирм IV', ['Владимир Посмыгаев']), fetch)['work_id'], 1730009)
        # A shared first name is not author evidence, and an inexact title is not enough on its own.
        self.assertIsNone(recommendations.match_work(self.store, hint('Элирм', ['Владимир Посмыгаев'], 'Элирм', 2), fetch))

    def test_volume_numbers_never_match_a_neighbour_or_the_cycle(self):
        works = [{'work_id': 2, 'name_eng': 'novel', 'rusname': 'Искажающие реальность-2. Внешняя угроза', 'autor_rusname': 'Михаил Атаманов', 'markcount': 900},
                 {'work_id': 9, 'name_eng': 'cycle', 'rusname': 'Искажающие реальность', 'autor_rusname': 'Михаил Атаманов', 'markcount': 999}]
        fetch = lambda store, path: works  # noqa: E731
        self.assertIsNone(recommendations.match_work(self.store, hint('Искажающие реальность. Книга 8', ['Михаил Атаманов']), fetch))
        self.assertEqual(recommendations.match_work(self.store, hint('(ир 02) Искажающие реальность-2', ['Атаманов Михаил Александрович']), fetch)['work_id'], 2)
        # A title equal to both a cycle and a novel's alternate name resolves to the novel: a file is one book.
        works.append({'work_id': 1, 'name_eng': 'novel', 'rusname': 'Искажающие реальность. Обратный отсчёт', 'altname': 'Искажающие реальность', 'autor_rusname': 'Михаил Атаманов', 'markcount': 1})
        self.assertEqual(recommendations.match_work(self.store, hint('Искажающие реальность', ['Михаил Атаманов']), fetch)['work_id'], 1)

    def test_volume_penalty_applies_only_to_numbered_titles(self):
        score = recommendations._title_score
        # Volume 3 from series metadata and a title naming no number: an unnumbered FantLab title is still a match,
        # but a candidate carrying the number wins ("Искажающие реальность" with series index 5).
        self.assertGreaterEqual(score(['кровь', 'эльфов'], 3, ['Кровь эльфов'], 'novel'), recommendations.ACCEPT)
        self.assertGreater(score(['искажающие', 'реальность'], 5, ['Искажающие реальность-5. Прыжок в неизвестность'], 'novel'),
                           score(['искажающие', 'реальность'], 5, ['Искажающие реальность'], 'novel'))
        # "Книга 8" against an unnumbered title is weak; against another volume number it is rejected.
        self.assertLess(score(['искажающие', 'реальность', '8'], 8, ['Искажающие реальность'], 'novel'), recommendations.ACCEPT)
        self.assertEqual(score(['искажающие', 'реальность', '8'], 8, ['Искажающие реальность-2. Внешняя угроза'], 'novel'), 0)
        self.assertEqual(score(['искажающие', 'реальность', '8'], 8, ['Искажающие реальность'], 'cycle'), 0)
        # A number the book names but the title lacks: a side story "2.5" is not the series' first book.
        self.assertLess(score(['искажающие', 'реальность', '2_5'], None, ['Искажающие реальность'], 'novel'), recommendations.ACCEPT)
        # One shared word in a long title is not a fit, even for the same author.
        self.assertEqual(score(['сектор'], None, ['Защита Периметра. Восьмой сектор'], 'novel'), 0)
        self.assertGreater(score(['обратный', 'отсчет'], None, ['Искажающие реальность. Обратный отсчëт'], 'novel'), recommendations.ACCEPT)

    def test_queries_drop_shelf_prefixes_and_patronymics(self):
        fake = FakeFantLab()
        recommendations.match_work(self.store, hint('(ир 02) Искажающие реальность-2', ['Атаманов Михаил Александрович']), fake)
        self.assertEqual(query(fake.paths[0]), 'атаманов искажающие реальность 2')

    def test_series_volume_is_found_inside_the_cycle(self):
        cycle = {'work_id': 50, 'name_eng': 'cycle', 'rusname': 'Ведьмак Геральт', 'autor_rusname': 'Анджей Сапковский', 'markcount': 9000}
        children = [{'work_id': 51, 'work_name': 'Последнее желание', 'work_type_icon': 'collection'},
                    {'work_id': 52, 'work_name': 'Кровь эльфов', 'work_type_icon': 'novel'},
                    {'work_id': 53, 'work_name': 'Час Презрения', 'work_type_icon': 'novel'}]

        def fetch(store, path):
            return {'children': children} if path.endswith('/extended') else [cycle]
        found = recommendations.match_work(self.store, hint('Час презрения', ['Анджей Сапковский'], 'Ведьмак', 4), fetch)
        self.assertEqual((found['work_id'], found['method'], found['cycle_id']), (53, 'series', 50))
        # Reading order is not trusted when the cycle mixes collections and novels.
        self.assertIsNone(recommendations.match_work(self.store, hint('Ведьмак. Книга 3', ['Анджей Сапковский']), fetch))
        children[0]['plus'] = 1
        found = recommendations.match_work(self.store, hint('Ведьмак. Книга 2', ['Анджей Сапковский']), fetch)
        self.assertEqual((found['work_id'], found['method']), (53, 'series_position'))
        # Without an author, nobody's cycle is trusted for reading order.
        self.assertIsNone(recommendations.match_work(self.store, hint('Ведьмак. Книга 2'), fetch))

    def test_isbn_needs_a_single_work_edition(self):
        def fetch(store, path):
            if path.startswith('/search-editions'):
                return [{'edition_id': 8300 if '585374190X' in path else 8301}]
            return {'edition_name': 'Дюна', 'content': ['Фрэнк Херберт. <a href="/work4325">Дюна</a> (роман)', ' Фрэнк Херберт. Книга первая. Дюна']} if '8300' in path else                 {'content': ['А. <a href="/work1">Один</a>', 'Б. <a href="/work2">Два</a>']}
        found = recommendations.match_work(self.store, hint('Дюна', isbns=['585374190X']), fetch)
        self.assertEqual((found['work_id'], found['method']), (4325, 'isbn'))
        # An ISBN whose edition shares neither the author nor a title word is a catalog error, not this book.
        self.assertIsNone(recommendations._by_isbn(self.store, ['585374190X'], fetch, ['Иван Другой'], [['совсем', 'иное']]))
        self.assertIsNone(recommendations._by_isbn(self.store, ['9780306406157'], fetch))

    def test_author_spelling_variants_resolve_through_fantlab_author_search(self):
        dune = {'work_id': 4325, 'name_eng': 'novel', 'rusname': 'Дюна', 'autor_id': 146, 'autor1_id': 146, 'autor_rusname': 'Фрэнк Герберт', 'markcount': 9000}
        other = {'work_id': 9, 'name_eng': 'novel', 'rusname': 'Дюна', 'autor_id': 7, 'autor1_id': 7, 'autor_rusname': 'Кто-то Другой', 'markcount': 5}
        paths = []

        def fetch(store, path):
            paths.append(query(path) or path)
            if path.startswith('/search-autors'):
                return [{'autor_id': 146, 'rusname': 'Фрэнк Герберт', 'name': 'Frank Herbert'}, {'autor_id': 1184, 'rusname': 'Джеймс Герберт', 'name': 'James Herbert'}]
            return [dune, other] if 'херберт' not in query(path) else []
        found = recommendations.match_work(self.store, hint('Дюна', ['Фрэнк Херберт']), fetch)
        self.assertEqual(found['work_id'], 4325)
        self.assertIn('герберт дюна', paths)

    def test_omnibus_resolves_to_the_cycle(self):
        works = [{'work_id': 1, 'name_eng': 'novel', 'rusname': 'Искажающие реальность. Обратный отсчёт', 'altname': 'Искажающие реальность',
                  'autor_rusname': 'Михаил Атаманов', 'markcount': 900},
                 {'work_id': 9, 'name_eng': 'cycle', 'rusname': 'Искажающие реальность', 'autor_rusname': 'Михаил Атаманов', 'markcount': 800}]
        found = recommendations.match_work(self.store, hint('Искажающие реальность. Книги 1-3', ['Михаил Атаманов']) | {'omnibus': True}, lambda *_: works)
        self.assertEqual(found['work_id'], 9)

    def test_two_editions_of_one_book_count_once(self):
        self.store.update_settings({'online_recommendations': True})
        library = [{'id': 'a' * 32, 'title': 'Искажающие реальность. Обратный отсчёт', 'author': 'Михаил Атаманов'},
                   {'id': 'b' * 32, 'title': 'Искажающие реальность. Обратный отсчёт', 'author': 'Михаил Атаманов'}]
        once = recommendations.recommend(self.store, library[:1], [], fetch=FakeFantLab(), workers=1)
        twice = recommendations.recommend(self.store, library, [], fetch=FakeFantLab(), workers=1)
        self.assertEqual([(item['work_id'], item['score']) for item in once['items']], [(item['work_id'], item['score']) for item in twice['items']])

    def test_import_placeholders_are_not_metadata(self):
        book = {'id': 'b' * 32, 'title': 'Untitled book', 'author': 'Unknown author', 'format': 'epub'}
        found = book_identity.hints(self.store, book)
        self.assertEqual((found['title'], found['authors']), ('', []))
        self.assertIsNone(recommendations.match_work(self.store, found, lambda *_: self.fail('searched without a title')))

    def test_recommendations_rank_library_matches_and_genres(self):
        self.store.update_settings({'online_recommendations': True})
        library = [{'id': 'a' * 32, 'title': 'Искажающие реальность. Обратный отсчёт', 'author': 'Михаил Атаманов'}]
        genres = tuple(dict(genre, anchors=(4325,)) if genre['id'] == 'space_opera' else genre for genre in recommendations.GENRES)
        with patch.object(recommendations, 'GENRES', genres):
            result = recommendations.recommend(self.store, library, ['space_opera'], fetch=FakeFantLab(), workers=1)
        self.assertEqual(result['seeds'][0]['work_id'], 904309)
        ids = [item['work_id'] for item in result['items']]
        self.assertEqual(ids[0], 6414)
        self.assertNotIn(7, ids)
        self.assertNotIn(904309, ids)
        top = result['items'][0]
        self.assertEqual([reason['kind'] for reason in top['reasons']], ['book', 'genre'])
        self.assertEqual(top['description'], 'Описание с пробелами')
        self.assertEqual(top['url'], 'https://fantlab.ru/work6414')
        self.assertEqual(next(item for item in result['items'] if item['work_id'] == 1)['cover'], '184')
        self.assertEqual(result['failures'], 0)
        with self.assertRaises(AppError):
            recommendations.recommend(self.store, library, ['not-a-genre'], fetch=FakeFantLab())

    def test_stale_cache_survives_an_outage(self):
        calls = []

        def download(url, accept='application/json'):
            calls.append(url)
            if len(calls) > 1:
                raise AppError('FantLab could not be reached.', 'fantlab_unreachable')
            return json.dumps([{'id': 1}]).encode(), 'application/json'

        self.assertEqual(recommendations.fetch_json(self.store, '/work/1/similars', download), [{'id': 1}])
        cached = recommendations._cache_path(self.store, 'api', '/work/1/similars')
        record = json.loads(cached.read_text(encoding='utf-8'))
        record['at'] = time.time() - recommendations.TTL - 1
        cached.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(recommendations.fetch_json(self.store, '/work/1/similars', download), [{'id': 1}])
        self.assertEqual(len(calls), 2)
        with self.assertRaises(AppError):
            recommendations.fetch_json(self.store, '/work/2/similars', download)

    def test_few_voters_are_shrunk_toward_the_prior(self):
        def card(rating, voters):
            return {'rating': rating, 'voters': voters}
        self.assertLess(recommendations._quality(card(9.9, 4)), recommendations._quality(card(8.4, 5000)))
        self.assertGreater(recommendations._quality(card(None, 0)), recommendations._quality(card(5.0, 5000)))

    def test_retired_genres_do_not_block_settings(self):
        self.store.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps({**self.store.settings(), 'recommendation_genres': ['litrpg', 'retired', 'litrpg']}),))
        self.assertEqual(self.store.settings()['recommendation_genres'], ['litrpg'])
        self.assertEqual(self.store.update_settings({'theme': 'dark'})['theme'], 'dark')
        self.store.settings()['recommendation_genres'].append('litrpg')
        self.store.update_settings({'recommendation_genres': []})
        self.store.settings()['recommendation_genres'].append('horror')
        self.assertEqual(DEFAULTS['recommendation_genres'], [])

    def test_unreadable_answers_fall_back_to_stale_cache(self):
        with self.assertRaises(AppError) as raised:
            recommendations.fetch_json(self.store, '/work/3/similars', lambda url, accept='': (b'<html>busy</html>', 'text/html'))
        self.assertEqual(raised.exception.code, 'fantlab_error')
        recommendations.fetch_json(self.store, '/work/3/similars', lambda url, accept='': (b'[1]', 'application/json'))
        cached = recommendations._cache_path(self.store, 'api', '/work/3/similars')
        record = json.loads(cached.read_text(encoding='utf-8'))
        record['at'] = 0
        cached.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(recommendations.fetch_json(self.store, '/work/3/similars', lambda url, accept='': (b'<html>', 'text/html')), [1])

    def test_breaker_stops_contacting_an_unreachable_fantlab(self):
        calls = []

        def download(url, accept='application/json'):
            calls.append(url)
            raise AppError('FantLab could not be reached.', 'fantlab_unreachable')

        guarded = recommendations._breaker(download, time.monotonic() + 60)
        for _ in range(3):
            with self.assertRaises(AppError):
                guarded('https://api.fantlab.ru/work/1/similars')
        self.assertEqual(len(calls), 1)
        missing = []

        def not_found(url, accept='application/json'):
            missing.append(url)
            raise AppError('FantLab answered 404.', 'fantlab_error')

        guarded = recommendations._breaker(not_found, time.monotonic() + 60)
        for _ in range(2):
            with self.assertRaises(AppError):
                guarded('https://api.fantlab.ru/work/0/similars')
        # An unknown work is not an outage.
        self.assertEqual(len(missing), 2)
        expired = recommendations._breaker(lambda url, accept='': self.fail('contacted after the budget'), time.monotonic() - 1)
        with self.assertRaises(AppError):
            expired('https://api.fantlab.ru/work/1/similars')
        closing = threading.Event()
        closing.set()
        stopped = recommendations._breaker(lambda url, accept='': self.fail('contacted while closing'), time.monotonic() + 60, closing)
        with self.assertRaises(AppError):
            stopped('https://api.fantlab.ru/work/1/similars')

    def test_recommend_survives_an_outage_and_malformed_items(self):
        self.store.update_settings({'online_recommendations': True})
        broken = [{'id': 'x', 'name_type_icon': 'novel'}, {'id': '²', 'name_type_icon': 'novel'}, 'junk', similar(77, 'Странная', 'Автор', 'n/a', 'много'),
                  similar(78, 'Обратный отсчёт', 'Михаил Атаманов')]

        def fetch(store, path):
            if path.startswith('/search-works'):
                raise AppError('FantLab could not be reached.', 'fantlab_unreachable')
            return broken

        library = [{'id': 'a' * 32, 'title': 'Обратный отсчёт', 'author': 'Атаманов Михаил'}]
        result = recommendations.recommend(self.store, library, ['litrpg'], fetch=fetch, workers=2)
        ids = [item['work_id'] for item in result['items']]
        # The unmatched library book is still recognised on the shelf by title and author.
        self.assertEqual(ids, [77])
        self.assertEqual((result['items'][0]['rating'], result['items'][0]['voters']), (None, 0))
        self.assertEqual(result['failures'], 1)
        self.assertIsNone(result['seeds'][0]['work_id'])

    def test_covers_accept_only_numeric_images(self):
        self.store.update_settings({'online_recommendations': True})
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 16
        path, mime = recommendations.cover(self.store, '184', download=lambda url, accept: (png, 'image/png'))
        self.assertEqual((path.read_bytes(), mime), (png, 'image/png'))
        self.assertEqual(recommendations.cover(self.store, '184', download=lambda *_: self.fail('not cached'))[1], 'image/png')
        for bad in ('../x', '184.jpg', ''):
            with self.assertRaises(AppError):
                recommendations.cover(self.store, bad)
        with self.assertRaises(AppError):
            recommendations.cover(self.store, '185', download=lambda url, accept: (b'<html>', 'image/jpeg'))
        jpeg = b'\xff\xd8\xff\xe0' + b'0' * 16
        self.assertEqual(recommendations.cover(self.store, '186', download=lambda url, accept: (jpeg, 'application/octet-stream'))[1], 'image/jpeg')
        # A damaged cached file is fetched again instead of being served.
        recommendations._cache_path(self.store, 'covers', '186').write_bytes(b'garbage')
        self.assertEqual(recommendations.cover(self.store, '186', download=lambda url, accept: (png, 'image/png'))[1], 'image/png')


if __name__ == '__main__':
    unittest.main()
