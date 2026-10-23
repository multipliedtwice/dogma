"""Bibliographic hints for finding a library book in an online catalog.

The library keeps only a display title and a joined author string, so the hints
are read from the original file that import stores unchanged
(``books/<id>/source.epub`` or ``source.fb2``): author-role creators only, the
series name and volume, ISBNs and an original-language title. A title such as
"(ир 03) Искажающие реальность-3" or "Искажающие реальность. Книга 4. Паутина
миров" is also split into series, volume and subtitle when the file has no
series metadata. The source is keyed by its content hash, so hints are cached
per book and parsed once.
"""
from __future__ import annotations

import json
import re

from lxml import etree

from .books import MAX_UPLOAD, fb2_source, local, read_entry, safe_archive, text_of, xml
from .util import AppError, atomic_write

VERSION = 3  # bump whenever parsing changes: cached hints include parsed series and volume
UNTITLED = 'Untitled book'
UNKNOWN_AUTHOR = 'Unknown author'
# Words that mark a volume rather than name a book; ignored when comparing titles.
NOISE = frozenset({'книга', 'книги', 'кн', 'том', 'часть', 'роман', 'цикл', 'серия', 'сборник', 'повесть',
                   'тома', 'book', 'books', 'vol', 'volume', 'volumes', 'part', 'novel', 'series',
                   'дилогия', 'трилогия', 'тетралогия', 'пенталогия', 'гексалогия', 'омнибус', 'omnibus', 'trilogy', 'duology', 'tetralogy'})
ROMAN = re.compile(r'M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})')
VOLUME_WORD = r'(?:\b(?:книга|кн\.?|том|часть|book|vol\.?|volume|part)|[#№])'
MARKED = re.compile(rf'^(?P<series>.*?)[\s.,:;(\[–—-]*{VOLUME_WORD}\s*(?P<number>\d{{1,3}}|[IVXLC]{{1,7}})\b(?![.,]\d)[)\]]?(?P<rest>.*)$', re.I)
DASHED = re.compile(r'^(?P<series>.*?\w)\s*[-–—]\s*(?P<number>\d{1,3})(?!\d)(?![.,]\d)(?P<rest>.*)$')
TRAILING = re.compile(r'^(?P<series>.*?\w)\s+(?P<number>\d{1,2}|[IVXLC]{1,7})$')
PREFIX = re.compile(r'^\s*[(\[](?P<inner>[^)\]]*)[)\]]\s*')
# Several volumes in one file: "Книги 1-3", "Тома 1–5", "Books 1-3".
RANGE = re.compile(r'^(?P<series>.*?)[\s.,:;(\[–—-]*(?:\b(?:книги|тома|кн\.?|books|vols?\.?|volumes)\s*)?(?P<first>\d{1,3})\s*[-–—]\s*(?P<last>\d{1,3})(?!\d)[)\]]?(?P<rest>.*)$', re.I)
OMNIBUS = frozenset({'дилогия', 'трилогия', 'тетралогия', 'пенталогия', 'гексалогия', 'омнибус', 'omnibus', 'trilogy', 'duology', 'tetralogy'})
# Trailing store or format tags without a number: "(СИ)", "[Litres]", "(сборник)".
TAGS = re.compile(r'(?:\s*[(\[](?:[^)\]\d]*|(?:1[5-9]|20)\d\d)[)\]])+\s*$')  # also a trailing year: "(1972)"
PATRONYMIC = re.compile(r'(вич|вна|ична)$')
DECIMAL = re.compile(r'(\d)[.,](\d)')
CYRILLIC = re.compile(r'[а-яё]')
LATIN = str.maketrans({'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k',
                       'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
                       'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'})


def _roman(token):
    if not token or token != token.upper() or not ROMAN.fullmatch(token):
        return None
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    total = sum(-values[a] if values[a] < values[b] else values[a] for a, b in zip(token, token[1:])) + values[token[-1]]
    return total if 0 < total <= 60 else None


def number(token):
    """Volume number from "4", "04" or an upper-case Roman numeral such as "IV"."""
    if token.isdecimal():  # not isdigit(): "²" is a digit that int() rejects
        return int(token)
    return _roman(token)


def _decimals(text):
    # "2.5" stays one word: a side story between volumes 2 and 3, not "2" and "5".
    return DECIMAL.sub(r'\1_\2', text or '')


def tokens(text):
    """Comparable title words: lower case, ё folded, Roman numerals and zero-padded numbers as plain numbers."""
    result = []
    for raw in re.findall(r'\w+', _decimals(text).replace('ё', 'е').replace('Ё', 'Е').replace('ë', 'е').replace('Ë', 'Е')):
        value = number(raw) if raw.isdecimal() or raw.isupper() and len(raw) <= 7 else None
        word = str(value) if value is not None else raw.lower()
        if (len(word) > 1 or word.isdecimal()) and word not in NOISE:
            result.append(word)
    return result


def query_words(text):
    """Search words as FantLab indexes them: Roman numerals kept as written, "02" sent as "2", volume words dropped."""
    words = []
    for raw in re.findall(r'\w+', _decimals(text).lower().replace('ё', 'е').replace('ë', 'е')):
        word = str(int(raw)) if raw.isdecimal() else raw
        if (len(word) > 1 or word.isdecimal()) and word not in NOISE and word not in words and '_' not in word:
            words.append(word)
    return words


def name_words(name):
    return [word for word in re.findall(r'\w+', (name or '').lower().replace('ё', 'е').replace('ë', 'е')) if len(word) > 2]


def surname(name):
    """Likely family name of one author: drop a Russian patronymic, then take the longest word."""
    words = name_words(name)
    if len(words) >= 3:
        words = [word for word in words if not PATRONYMIC.search(word)] or words
    return max(words, key=len) if words else ''


def _skeleton(word):
    """Spelling-neutral key for one name across scripts: "Atamanov" / "Атаманов", "Glukhovsky" / "Глуховский"."""
    word = word.translate(LATIN)
    for old, new in (('shch', 's'), ('kh', 'h'), ('zh', 'z'), ('ch', 'c'), ('sh', 's'), ('ts', 'c'), ('tz', 'c'), ('yu', 'u'), ('iu', 'u'),
                     ('ya', 'a'), ('ia', 'a'), ('y', 'i'), ('j', 'i'), ('w', 'v'), ('ph', 'f'), ('ck', 'k'), ('x', 'ks'), ('g', 'h')):
        word = word.replace(old, new)
    return re.sub(r'(.)\1+', r'\1', word)


def _inflected(left, right):
    shortest = min(len(left), len(right))
    return shortest >= 6 and left[:max(5, shortest - 2)] == right[:max(5, shortest - 2)]


def same_name(left, right):
    """Equal family names, allowing Russian inflection ("Стругацкий" / "Стругацкие"), transliteration across
    scripts ("Atamanov" / "Атаманов") and the Г/Х rendering of H ("Херберт" / "Герберт")."""
    if left == right or _inflected(left, right):
        return True
    if bool(CYRILLIC.search(left)) != bool(CYRILLIC.search(right)):
        return _inflected(_skeleton(left), _skeleton(right))
    return len(left) == len(right) >= 6 and left[0] + right[0] in ('гх', 'хг') and left[1:] == right[1:]


def parse_title(title):
    """Split a display title into series, volume and subtitle when it names a numbered volume."""
    text = re.sub(r'\s+', ' ', title or '').strip()
    prefix_volume = None
    if (match := PREFIX.match(text)) and text[match.end():].strip():
        digits = re.findall(r'\b\d{1,3}\b', match.group('inner'))  # "(2015)" is a year, not volume 5
        prefix_volume = int(digits[-1]) if digits else None
        text = text[match.end():].strip()
    text = TAGS.sub('', text) or text
    match = RANGE.match(text)
    if match and int(match.group('first')) < int(match.group('last')) or set(re.findall(r'\w+', text.lower())) & OMNIBUS:
        series = match.group('series').strip(' .,:;–—-([') if match else ''
        rest = match.group('rest').strip(' .,:;–—-)]') if match else ''
        return {'title': text, 'series': series, 'volume': None, 'subtitle': rest, 'omnibus': True}
    for pattern in (MARKED, DASHED, TRAILING):
        match = pattern.match(text)
        if not match:
            continue
        volume = number(match.group('number').upper())
        series = match.group('series').strip(' .,:;–—-([')
        rest = (match.groupdict().get('rest') or '').strip(' .,:;–—-)]')
        # "Книга 1. Начало" names a volume and a subtitle without a series.
        if volume and (series or rest):
            return {'title': text, 'series': series, 'volume': volume, 'subtitle': rest, 'omnibus': False}
    return {'title': text, 'series': '', 'volume': prefix_volume, 'subtitle': '', 'omnibus': False}


def valid_isbn(value):
    digits = re.sub(r'[\s-]', '', value or '').upper()
    if re.fullmatch(r'\d{9}[\dX]', digits):
        total = sum((10 - index) * (10 if char == 'X' else int(char)) for index, char in enumerate(digits))
        return digits if total % 11 == 0 else None
    if re.fullmatch(r'97[89]\d{10}', digits):
        total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(digits))
        return digits if total % 10 == 0 else None
    return None


def _isbns(values):
    found = []
    for value in values:
        for candidate in re.findall(r'[\dXx][\dXx\s-]{8,20}[\dXx]', value or ''):
            if (isbn := valid_isbn(candidate)) and isbn not in found:
                found.append(isbn)
    return found


def _volume(value):
    """Whole volume number; a fractional calibre index ("2.5") marks a side story between volumes."""
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(result) if result.is_integer() and 0 < result < 1000 else None


def read_epub(data):
    with safe_archive(data) as archive:
        names = {entry.filename: entry for entry in archive.infolist() if not entry.is_dir()}
        if 'META-INF/container.xml' not in names:
            return {}
        container = xml(read_entry(archive, names['META-INF/container.xml']))
        paths = container.xpath('.//*[local-name()="rootfile"]/@full-path')
        if not paths or paths[0] not in names:
            return {}
        package = xml(read_entry(archive, names[paths[0]]))
    found = package.xpath('./*[local-name()="metadata"]')
    root = found[0] if found else package
    refines = {}
    for node in root.xpath('./*[local-name()="meta"][@refines]'):
        refines.setdefault(node.get('refines', '').lstrip('#'), {})[node.get('property', '')] = text_of(node)
    authors = []
    for node in root.xpath('./*[local-name()="creator"]'):
        role = next((value for key, value in node.attrib.items() if key.endswith('role')), '') or refines.get(node.get('id', ''), {}).get('role', '')
        if role.strip().lower() in ('', 'aut'):  # translators, illustrators and editors are not who FantLab files a book under
            authors.append(text_of(node))
    titles = [text_of(node) for node in root.xpath('./*[local-name()="title"]') if text_of(node)]
    series = volume = None
    for node in root.xpath('./*[local-name()="meta"]'):
        if node.get('name') == 'calibre:series' and node.get('content'):
            series = node.get('content').strip()
        elif node.get('name') == 'calibre:series_index':
            volume = _volume(node.get('content'))
        elif node.get('property') == 'belongs-to-collection' and not series:
            details = refines.get(node.get('id', ''), {})
            if details.get('collection-type', 'series') == 'series':
                series, volume = text_of(node), _volume(details.get('group-position'))
    identifiers = [f"{node.get('scheme', '')}:{text_of(node)}" for node in root.xpath('./*[local-name()="identifier"]')]
    isbn_like = [value for value in identifiers if 'isbn' in value.lower() or re.search(r'97[89][\d-]{10,}', value)]
    return {'titles': titles, 'authors': [name for name in authors if name], 'series': series or '', 'volume': volume, 'isbns': _isbns(isbn_like)}


def _fb2_description(data):
    """Only the <description> header: parsing a 50 MB book four at a time for its metadata would exhaust memory.
    Cutting bytes is safe in the ASCII-compatible encodings FB2 uses; UTF-16 files are parsed whole."""
    opening = re.search(rb'<((?:[\w.-]+:)?)FictionBook\b', data[:4096])
    closing = re.search(rb'</(?:[\w.-]+:)?description\s*>', data)
    if not opening or not closing or b'\x00' in data[:4]:
        return data
    return data[:closing.end()] + b'</' + opening.group(1) + b'FictionBook>'


def read_fb2(data):
    root = xml(fb2_source(_fb2_description(data)), MAX_UPLOAD + 64, huge=True, encoding_error=True)
    if local(root) != 'FictionBook':
        return {}

    def section(name):
        found = root.xpath(f'./*[local-name()="description"]/*[local-name()="{name}"]')
        return found[0] if found else None

    def child(node, name):
        found = node.xpath(f'./*[local-name()="{name}"]') if node is not None else []
        return text_of(found[0]) if found else ''

    def people(node):
        names = []
        for author in node.xpath('./*[local-name()="author"]') if node is not None else []:
            full = ' '.join(part for part in (child(author, 'first-name'), child(author, 'middle-name'), child(author, 'last-name')) if part)
            names.append(full or child(author, 'nickname'))
        return [name for name in names if name]

    info, source, publish = section('title-info'), section('src-title-info'), section('publish-info')
    sequence = info.xpath('./*[local-name()="sequence"][@name]') if info is not None else []
    titles = [title for title in (child(info, 'book-title'), child(source, 'book-title')) if title]
    authors = people(info)
    return {'titles': titles, 'authors': authors + [name for name in people(source) if name not in authors],
            'series': sequence[0].get('name', '').strip() if sequence else '', 'volume': _volume(sequence[0].get('number')) if sequence else None,
            'isbns': _isbns([child(publish, 'isbn')])}


def hints(store, book):
    """Matching hints for a library entry; falls back to its display title and author when the source cannot be read."""
    cache = store.root / 'cache' / 'book-hints' / f"{book['id']}.json"
    try:
        record = json.loads(cache.read_text(encoding='utf-8'))
        if record.get('version') == VERSION:
            return record['hints']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    source = {}
    path = store.root / 'books' / book['id'] / f"source.{book.get('format') or 'epub'}"
    try:
        data = path.read_bytes()
        source = read_fb2(data) if path.suffix == '.fb2' else read_epub(data)
    except (AppError, OSError, ValueError, LookupError, TypeError, etree.LxmlError):
        source = {}  # best effort: a damaged file still gets hints from its library entry
    title = book['title'][:500] if book['title'] != UNTITLED else ''
    author = book['author'] if book['author'] != UNKNOWN_AUTHOR else ''
    # Bounded like the importer's own title: file metadata is untrusted and feeds regular expressions and int().
    titles = list(dict.fromkeys(item[:500] for item in [title, *source.get('titles', [])] if item))
    parsed = parse_title(title)
    result = {
        'title': title,
        'alternate_titles': titles[1:4],
        'authors': [name[:200] for name in source.get('authors') or [name.strip() for name in author.split(',') if name.strip()]][:10],
        'series': (source.get('series') or parsed['series'])[:300],
        'volume': source.get('volume') or parsed['volume'],
        'subtitle': parsed['subtitle'],
        'omnibus': parsed['omnibus'],
        'isbns': source.get('isbns', []),
    }
    try:
        atomic_write(cache, json.dumps({'version': VERSION, 'hints': result}, ensure_ascii=False).encode())
    except OSError:
        pass
    return result
