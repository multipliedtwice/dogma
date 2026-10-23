"""Book suggestions from FantLab (https://api.fantlab.ru, documented at
github.com/FantLab/FantLab-API).

FantLab has no browse-by-genre endpoint, so each genre chip is anchored to a
few representative works and suggestions come from FantLab's reader-curated
"similar works" lists for those anchors and for the books in the library.
Library books are found on FantLab from hints read locally from their files
(see book_identity). Only titles, series names, author names, ISBNs and the
chosen genre ids leave the computer, and only while online suggestions are on.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .book_identity import UNKNOWN_AUTHOR, UNTITLED, hints, name_words, parse_title, query_words, same_name, surname, tokens
from .util import AppError, atomic_write

API = 'https://api.fantlab.ru'
SITE = 'https://fantlab.ru'
AGENT = 'Dogma (personal audiobook player)'
TTL = 7 * 24 * 3600
TIMEOUT = 20
BUDGET = 60
MAX_BYTES = 8 * 1024 * 1024
SEED_BOOKS = 12
LIMIT = 24
SEED_WEIGHT = 2.0
GENRE_WEIGHT = 1.0
# FantLab work types worth narrating; reviews, articles, scripts and comics are not.
NARRATABLE = frozenset({'novel', 'cycle', 'epic', 'story', 'short', 'collection'})
ACCEPT = .6  # title fit needed when the author matches too
TITLE_ONLY = .95  # exact title fit needed when no author name matches
SURE = .9  # title fit that ends the search without trying broader queries

GENRES = (
    {'id': 'litrpg', 'en': 'LitRPG', 'ru': 'ЛитРПГ', 'anchors': (434677, 904309, 417083, 274653)},
    {'id': 'space_opera', 'en': 'Space opera', 'ru': 'Космоопера', 'anchors': (4325, 1, 1243)},
    {'id': 'military_sf', 'en': 'Military SF', 'ru': 'Боевая фантастика', 'anchors': (116185, 2796, 15912, 29460)},
    {'id': 'fantasy', 'en': 'Fantasy', 'ru': 'Фэнтези', 'anchors': (80145, 3201, 1156, 2959)},
    {'id': 'urban_fantasy', 'en': 'Urban fantasy', 'ru': 'Городское фэнтези', 'anchors': (1255, 10134, 4348, 40658)},
    {'id': 'alt_history', 'en': 'Alternate history', 'ru': 'Попаданцы и альтернативная история', 'anchors': (125205, 207947, 854844)},
    {'id': 'post_apocalypse', 'en': 'Post-apocalyptic', 'ru': 'Постапокалипсис', 'anchors': (4351, 569, 293, 140298)},
    {'id': 'cyberpunk', 'en': 'Cyberpunk', 'ru': 'Киберпанк', 'anchors': (2654, 1242, 49065, 9765)},
    {'id': 'classic_sf', 'en': 'Classic SF', 'ru': 'Классическая фантастика', 'anchors': (552, 7, 3104, 5149)},
    {'id': 'horror', 'en': 'Horror', 'ru': 'Ужасы и мистика', 'anchors': (291, 31606, 355, 3003)},
    {'id': 'humor', 'en': 'Humorous', 'ru': 'Юмористическая фантастика', 'anchors': (1710, 3018, 2076)},
)
GENRE_IDS = frozenset(genre['id'] for genre in GENRES)

_fetch_lock = threading.Lock()
_in_flight: dict[str, threading.Event] = {}


def genres():
    return [{'id': genre['id'], 'en': genre['en'], 'ru': genre['ru']} for genre in GENRES]


def require_online(store):
    if not store.settings()['online_recommendations']:
        raise AppError('Online book suggestions are turned off.', 'recommendations_off')


def _cache_path(store, kind, key):
    return store.root / 'cache' / 'fantlab' / kind / (hashlib.sha256(key.encode()).hexdigest()[:32] + ('.json' if kind != 'covers' else ''))


def _download(url, accept='application/json'):
    from .visuals import https_context
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=https_context()) as response:
            return response.read(MAX_BYTES + 1), response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        # 404 means an unknown id; 429 and 5xx mean FantLab is struggling.
        raise AppError(f'FantLab answered {error.code}.', 'fantlab_error' if error.code < 500 and error.code != 429 else 'fantlab_unreachable') from error
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise AppError('FantLab could not be reached.', 'fantlab_unreachable') from error


def fetch_json(store, path, download=_download):
    """GET an API path with a week-long disk cache; a stale copy is used if FantLab fails."""
    cached = _cache_path(store, 'api', path)
    stale = None
    try:
        record = json.loads(cached.read_text(encoding='utf-8'))
        if time.time() - record['at'] < TTL:
            return record['data']
        stale = record['data']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        raw, _ = download(API + path)
        if len(raw) > MAX_BYTES:
            raise AppError('FantLab sent an oversized answer.', 'fantlab_error')
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise AppError('FantLab sent an unreadable answer.', 'fantlab_error') from error
    except AppError:
        if stale is not None:
            return stale
        raise
    atomic_write(cached, json.dumps({'at': time.time(), 'data': data}, ensure_ascii=False).encode())
    return data


def _breaker(download, deadline, stop=None):
    """Stop contacting FantLab for the rest of one request once it is unreachable, the time budget is spent or the app closes.

    Without it an outage costs one TIMEOUT per lookup, and a cold request makes dozens of lookups.
    Cached answers are still used after the breaker opens.
    """
    down = threading.Event()

    def guarded(url, accept='application/json'):
        if down.is_set() or time.monotonic() > deadline or stop is not None and stop.is_set():
            down.set()
            raise AppError('FantLab could not be reached.', 'fantlab_unreachable')
        try:
            return download(url, accept)
        except AppError as error:
            if error.code == 'fantlab_unreachable':
                down.set()
            raise
    return guarded


def _image_type(head):
    """Image type from magic bytes; FantLab's Content-Type header is not trusted."""
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _cached_cover(path):
    try:
        with path.open('rb') as handle:
            return _image_type(handle.read(12))
    except OSError:
        return None


def cover(store, edition_id, download=_download):
    """Local copy of a FantLab edition cover; the webview CSP only allows same-origin images."""
    require_online(store)
    if not isinstance(edition_id, str) or not re.fullmatch(r'\d{1,10}', edition_id):
        raise AppError('Unknown cover.')
    path = _cache_path(store, 'covers', edition_id)
    if mime := _cached_cover(path):
        return path, mime
    with _fetch_lock:
        waiting = _in_flight.get(edition_id)
        if waiting is None:
            _in_flight[edition_id] = threading.Event()
    if waiting is not None:
        waiting.wait(TIMEOUT + 5)
        if mime := _cached_cover(path):
            return path, mime
        raise AppError('Cover not available.')
    try:
        raw, _ = download(f'{SITE}/images/editions/big/{edition_id}', accept='image/*')
        mime = _image_type(raw[:12])
        if mime is None or len(raw) > MAX_BYTES:
            raise AppError('Cover not available.')
        atomic_write(path, raw)
        return path, mime
    finally:
        with _fetch_lock:
            _in_flight.pop(edition_id).set()


def _search(store, words, fetch):
    query = ' '.join(words[:6])
    result = fetch(store, f'/search-works?onlymatches=1&q={urllib.parse.quote_plus(query)}') if query else []
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def _fantlab_surnames(item):
    names = []
    for key in ('all_autor_rusname', 'all_autor_name', 'autor_rusname', *(f'autor{index}_rusname' for index in range(1, 6))):
        names += str(item.get(key) or '').split(',')
    return {words[-1] for words in (name_words(name) for name in names) if words}


def _author_matches(authors, surnames):
    """A shared family name; a shared first name alone ("Владимир") is not evidence."""
    return any(same_name(word, other) for author in authors for word in name_words(author) for other in surnames)


def _title_score(view, volume, names, kind):
    """How well one FantLab title fits a view of the book: 0 rejects, 1 is exact."""
    if kind == 'cycle' and volume:
        return 0.0  # a numbered volume is never the whole cycle
    book = set(view)
    best = 0.0
    for name in names:
        words = set(tokens(name))
        common = len(book & words)
        if not common or common / len(words) < .3:
            continue  # one shared word in a long title ("Сектор" / "Защита Периметра. Восьмой сектор") is not a fit
        numbers = {word for word in words if word.isdecimal()}
        if volume and numbers and str(volume) not in numbers:
            continue  # "Книга 8" is not "Искажающие реальность-2"
        score = .7 * common / len(book) + .3 * common / len(words)
        if volume and str(volume) in numbers:
            score = min(1.0, score + .1)
        elif volume and not numbers and volume > 1:
            # "Книга 8" against an unnumbered title is weak: FantLab often leaves only the first volume unnumbered.
            # A title naming no number ("Кровь эльфов", volume 3 from metadata) is still plausible, but loses to a
            # candidate that carries the number ("Искажающие реальность" with series index 5).
            score *= .6 if str(volume) in book else .75
        elif not volume and numbers - book:
            score *= .8
        if not volume and {word for word in book if word.isdecimal() or '_' in word} - words:
            score *= .75  # the book names a number ("Книга 2.5", "Метро 2034") this title does not carry
        best = max(best, score)
    return best


def _candidate(item):
    return {'work_id': int(item['work_id']), 'title': str(item.get('rusname') or item.get('name') or ''),
            'names': [str(item.get(key) or '') for key in ('rusname', 'name', 'altname')], 'kind': item.get('name_eng'),
            'popularity': _number(item.get('markcount')) or 0, 'surnames': _fantlab_surnames(item),
            'author_ids': {int(value) for key in ('autor_id', *(f'autor{index}_id' for index in range(1, 6)))
                           if str(value := item.get(key) or '').isdecimal() and int(value)}}


def _resolve_authors(store, authors, fetch):
    """FantLab authors for the book's author names, through FantLab's own author search, which knows spelling
    variants ("Херберт" finds Фрэнк Герберт). Returns their ids and FantLab's spelling of their surnames."""
    ids, surnames = set(), []
    for author in authors[:2]:
        words = name_words(author)
        family = surname(author)
        if not family:
            continue
        found = fetch(store, f'/search-autors?onlymatches=1&q={urllib.parse.quote_plus(family)}')
        for item in (found if isinstance(found, list) else [])[:5]:
            if not isinstance(item, dict) or not str(item.get('autor_id', '')).isdecimal():
                continue
            names = name_words(f"{item.get('rusname') or ''} {item.get('name') or ''} {item.get('pseudo_names') or ''}")
            given = [word for word in words if word != family]
            # With a first name in the metadata, FantLab's author must share it ("Фрэнк", not James Herbert).
            if given and not any(same_name(word, other) or word[:4] == other[:4] for word in given for other in names):
                continue
            ids.add(int(item['autor_id']))
            spelled = name_words(str(item.get('rusname') or ''))
            if spelled and spelled[-1] not in surnames:
                surnames.append(spelled[-1])
    return ids, surnames


def _pick(candidates, views, strict_views, volume, authors, method, author_ids=frozenset(), omnibus=False):
    """Best candidate: the author must match, or the title must be an exact, unique fit (pen names FantLab does not list)."""
    scored = []
    for candidate in candidates:
        author = candidate.get('author_ok') or bool(authors) and _author_matches(authors, candidate['surnames']) \
            or bool(author_ids & candidate.get('author_ids', set()))
        score = max((_title_score(view, volume, candidate['names'], candidate['kind']) for view in views), default=0)
        strict = max((_title_score(view, volume, candidate['names'], candidate['kind']) for view in strict_views), default=0)
        scored.append((author, score, strict, candidate))
    matched = sorted((entry for entry in scored if entry[0] and entry[1] >= ACCEPT),
                     key=lambda entry: (entry[1], entry[3]['kind'] != 'cycle', entry[3]['popularity']), reverse=True)
    if omnibus:
        # A file holding several volumes is the cycle; fall back to single works only when no cycle fits.
        matched = [entry for entry in matched if entry[3]['kind'] == 'cycle'] or matched
    if matched:
        _, score, _, best = matched[0]
        return {'work_id': best['work_id'], 'title': best['title'], 'method': method, 'score': round(score, 3)}
    exact = {entry[3]['work_id']: entry for entry in scored if entry[2] >= TITLE_ONLY}
    books = {work: entry for work, entry in exact.items() if (entry[3]['kind'] == 'cycle') == omnibus}
    if len(books) == 1:
        exact = books  # a series named after its first book: a file is the book, not the cycle
    if len(exact) == 1:
        _, _, score, best = next(iter(exact.values()))
        return {'work_id': best['work_id'], 'title': best['title'], 'method': 'title_only', 'score': round(score, 3)}
    return None


def _by_isbn(store, isbns, fetch, authors=(), views=()):
    """Work for an ISBN: the edition's table of contents must hold exactly one top-level work (not an omnibus),
    and its line must share the book's author or a title word, so a wrong ISBN in either catalog is not trusted."""
    for isbn in isbns[:2]:
        editions = fetch(store, f'/search-editions?onlymatches=1&q={isbn}')
        for edition in (editions if isinstance(editions, list) else [])[:1]:
            if not isinstance(edition, dict) or not str(edition.get('edition_id', '')).isdecimal():
                continue
            details = fetch(store, f"/edition/{int(edition['edition_id'])}?content=1")
            lines = [line for line in (details.get('content') or [] if isinstance(details, dict) else []) if isinstance(line, str) and '/work' in line]
            if not lines:
                continue
            depth = min(len(line) - len(line.lstrip()) for line in lines)
            top = [line for line in lines if len(line) - len(line.lstrip()) == depth]
            works = {int(found) for line in top for found in re.findall(r'/work(\d+)', line)}
            text = re.sub(r'<[^>]+>', ' ', ' '.join(top))
            agrees = not authors and not views or _author_matches(authors, set(name_words(text))) \
                or any(set(view) & set(tokens(text)) for view in views)
            if len(works) == 1 and agrees:
                return {'work_id': works.pop(), 'title': str(details.get('edition_name') or ''), 'method': 'isbn'}
    return None


def _volume_in_cycle(store, cycle, views, volume, fetch):
    """The numbered volume inside a FantLab cycle, by title first and by reading order second."""
    data = fetch(store, f"/work/{cycle['work_id']}/extended")
    children = [child for child in (data.get('children') or [] if isinstance(data, dict) else [])
                if isinstance(child, dict) and str(child.get('work_id', '')).isdecimal()]
    candidates = [{'work_id': int(child['work_id']), 'title': str(child.get('work_name') or ''),
                   'names': [str(child.get(key) or '') for key in ('work_name', 'work_name_alt', 'work_name_orig')],
                   'kind': child.get('work_type_icon'), 'popularity': _number(child.get('val_voters')) or 0, 'surnames': set(),
                   'author_ok': True}  # the cycle itself already matched the author
                  for child in children if child.get('work_type_icon') in NARRATABLE]
    if found := _pick(candidates, views, [], volume, [], 'series'):
        return {**found, 'cycle_id': cycle['work_id']}
    # Reading order only when the cycle is a plain run of novels; side stories are flagged "plus".
    main = [child for child in children if not child.get('plus') and child.get('deep', 1) == 1]
    if volume and main and all(child.get('work_type_icon') == 'novel' for child in main) and len(main) >= volume:
        child = main[volume - 1]
        return {'work_id': int(child['work_id']), 'title': str(child.get('work_name') or ''), 'method': 'series_position', 'cycle_id': cycle['work_id']}
    return None


def match_work(store, hint, fetch=fetch_json):
    """FantLab work for a library book's hints (see book_identity.hints), or None."""
    parsed = parse_title(hint.get('title', ''))
    volume = hint.get('volume') or parsed['volume']
    series = hint.get('series') or parsed['series']
    authors = hint.get('authors') or []
    omnibus = bool(hint.get('omnibus', parsed.get('omnibus')))
    if omnibus:
        volume = None
    # An omnibus ("Книги 1-3") is compared by its series name: the range is not a volume the cycle's title carries.
    main = parsed['series'] if omnibus and parsed['series'] else parsed['title']
    whole = [tokens(main), *(tokens(title) for title in hint.get('alternate_titles', []))]
    numbered = tokens(series) + [str(volume)] if series and volume else []
    views = [view for view in [*whole, numbered, tokens(parsed['subtitle'])] if view]
    strict_views = [view for view in [*whole, numbered] if view]
    if not views:
        return None
    if hint.get('isbns') and (found := _by_isbn(store, hint['isbns'], fetch, authors, strict_views)):
        return found
    searches = []
    for words in (query_words(parsed['title']), query_words(series) + ([str(volume)] if series and volume else []),
                  query_words(parsed['subtitle']), query_words(parsed['title'])[:2], *(query_words(title) for title in hint.get('alternate_titles', []))):
        if words and words[:6] not in searches:
            searches.append(words[:6])
    surnames = list(dict.fromkeys(name for name in (surname(author) for author in authors) if name))[:2]
    pool, found, author_ids = {}, None, frozenset()

    def search(name, plan):
        nonlocal found
        for words in plan:
            for item in _search(store, [name, *words] if name else words, fetch):
                if item.get('name_eng') in NARRATABLE and str(item.get('work_id', '')).isdecimal():
                    pool.setdefault(int(item['work_id']), _candidate(item))
            found = _pick(pool.values(), views, strict_views, volume, authors, 'title', author_ids, omnibus) if pool else None
            if found and found['score'] >= SURE:
                return True
        return bool(found)

    # Up to three searches per author surname as written. If none fits, FantLab's author search may know the
    # name under another spelling ("Херберт" / "Герберт"): one search under that spelling. Then two title-only
    # searches for pen names FantLab files under another name. A near-exact fit ends the search early.
    for name in surnames:
        if search(name, searches[:3]):
            return found
    if surnames:
        resolved, spellings = _resolve_authors(store, authors, fetch)
        author_ids = frozenset(resolved)
        for name in [spelling for spelling in spellings if spelling not in surnames][:2]:
            if search(name, searches[:1]):
                return found
    if search('', searches[:2]):
        return found
    if series and volume and authors and not omnibus:
        # Publisher series names differ from FantLab cycle names ("Ведьмак" / "Ведьмак Геральт"), so the
        # cycle must be the author's: reading order inside a stranger's cycle would pick an unrelated book.
        cycles = [candidate for candidate in pool.values() if candidate['kind'] == 'cycle'
                  and _title_score(tokens(series), None, candidate['names'], 'novel') >= .8
                  and _author_matches(authors, candidate['surnames'])]
        for cycle in sorted(cycles, key=lambda candidate: candidate['popularity'], reverse=True)[:1]:
            if found := _volume_in_cycle(store, cycle, views, volume, fetch):
                return found
    return None


def _plain(text, limit=280):
    text = re.sub(r'\[/?[a-zA-Z*]+(?:=[^\]]*)?\]', '', text or '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '…'


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _card(item):
    stat = item.get('stat') if isinstance(item.get('stat'), dict) else {}
    edition = re.search(r'/editions/(?:small|big)/(\d+)$', str(item.get('image_preview') or item.get('image') or ''))
    creators = item.get('creators') if isinstance(item.get('creators'), dict) else {}
    work_id = int(item['id'])
    return {
        'work_id': work_id,
        'title': str(item.get('name') or item.get('name_orig') or ''),
        'original_title': str(item.get('name_orig') or ''),
        'authors': [str(author.get('name') or author.get('name_orig') or '') for author in creators.get('authors') or [] if isinstance(author, dict)],
        'year': int(_number(item.get('year')) or 0) or None,
        'kind': str(item.get('name_type_icon') or ''),
        'kind_label': str(item.get('name_type') or ''),
        'series': str((item.get('saga') or {}).get('name') or '') if isinstance(item.get('saga'), dict) else '',
        'rating': _number(stat.get('rating')),
        'voters': int(_number(stat.get('voters')) or 0),
        'responses': int(_number(stat.get('responses')) or 0),
        'cover': edition.group(1) if edition else None,
        'description': _plain(str(item.get('description') or '')),
        'url': f'{SITE}/work{work_id}',
    }


def _quality(card):
    # Bayesian average so a 9.0 from five voters does not outrank a well-read 8.4.
    rating, voters = card['rating'] or 0.0, card['voters']
    weighted = (rating * voters + 7.0 * 200) / (voters + 200)
    return max(.2, min(1.2, (weighted - 5) / 3))


def recommend(store, library, chosen, fetch=None, workers=4, stop=None):
    require_online(store)
    if not isinstance(chosen, list) or any(not isinstance(genre, str) or genre not in GENRE_IDS for genre in chosen):
        raise AppError('Invalid recommendation genres.')
    if fetch is None:
        download = _breaker(_download, time.monotonic() + BUDGET, stop)
        fetch = lambda store, path: fetch_json(store, path, download)  # noqa: E731
    chosen = [genre for genre in GENRES if genre['id'] in set(chosen)]
    books = library[:SEED_BOOKS]

    def seed(book):
        try:
            return match_work(store, hints(store, book), fetch), False
        except AppError:
            return None, True

    def similar(work_id):
        try:
            result = fetch(store, f'/work/{work_id}/similars')
            return result if isinstance(result, list) else [], False
        except AppError:
            return [], True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        matched = list(pool.map(seed, books))
        seeds = [{'book_id': book['id'], 'title': book['title'], 'work_id': work and work['work_id'], 'work_title': work and work['title'],
                  'method': work and work['method']} for book, (work, _) in zip(books, matched)]
        # Two editions of one book are one example, not twice the weight.
        first = {}
        for entry in seeds:
            if entry['work_id']:
                first.setdefault(entry['work_id'], entry)
        sources = [('book', entry['book_id'], entry['title'], SEED_WEIGHT, work) for work, entry in first.items()]
        sources += [('genre', genre['id'], genre['ru'], GENRE_WEIGHT, anchor) for genre in chosen for anchor in genre['anchors']]
        works = list(dict.fromkeys(source[4] for source in sources))
        lists = dict(zip(works, pool.map(similar, works)))
    failures = sum(failed for _, failed in matched) + sum(failed for _, failed in lists.values())

    # Owned works, and the cycles they were found in: suggesting the whole series of a book you have is noise.
    owned = {entry['work_id'] for entry in seeds if entry['work_id']} | {work['cycle_id'] for work, _ in matched if work and work.get('cycle_id')}
    # Books beyond the matched seeds are recognised by title and author.
    shelf = [(frozenset(tokens(parse_title(book['title'])['title'])), [] if book['author'] == UNKNOWN_AUTHOR else [book['author']])
             for book in library if book['title'] != UNTITLED]

    def on_shelf(card):
        words, names = frozenset(tokens(card['title'])), {words[-1] for words in map(name_words, card['authors']) if words}
        return any(words and words == title and (not author or _author_matches(author, names)) for title, author in shelf)

    found = {}
    for kind, ident, label, weight, work in sources:
        for rank, item in enumerate(lists[work][0]):
            if not isinstance(item, dict) or item.get('name_type_icon') not in NARRATABLE or not str(item.get('id', '')).isdecimal() or int(item['id']) in owned:
                continue
            card = found.get(int(item['id']))
            if card is None:
                try:
                    card = found[int(item['id'])] = {**_card(item), 'score': 0.0, 'reasons': []}
                except (TypeError, ValueError, AttributeError):
                    continue
            card['score'] += weight / (1 + .15 * rank)
            if not any(reason['kind'] == kind and reason['id'] == ident for reason in card['reasons']):
                card['reasons'].append({'kind': kind, 'id': ident, 'title': label})
    cards = [card for card in found.values() if not on_shelf(card)]
    for card in cards:
        card['score'] = round(card['score'] * _quality(card), 4)
    cards.sort(key=lambda card: (-card['score'], -card['voters'], card['work_id']))
    return {'items': cards[:LIMIT], 'seeds': seeds, 'genres': [genre['id'] for genre in chosen], 'failures': failures}
