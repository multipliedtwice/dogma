"""Series-scoped spoken respellings of non-character terms (listener-authoritative).

The listener decides how a coined term sounds (тек-купол read as тэк-купол,
like English "tech"). The decision belongs to the series, not to one
generation: it lives in resources/series-terms.json (release data, per series
id or series name) with an optional local overlay `series-terms.json` beside
the library, and is frozen into each generation's pronunciation spec under
`terms` so it enters the synthesis text and key. The printed text never changes.

A prefix term ('hyphen-prefix', printed 'тек-') respells the first part of every hyphenated
compound that begins with it (тек-купол, тек-щит, any future тек-X), whatever follows.

A term is a hyphenated compound. Its first parts are invariant; the last part
may take any dictionary inflection (тек-куполом, тек-купола), so unrelated
words (текст, ректор, a bare тек) never match.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path

from .respelling import RULE as RESPELLING_RULE, validate_respelling, substitute
from .util import digest

RULE = 'spoken-term-respelling-v1'
MATCHES = ('hyphenated-compound-inflected', 'hyphen-prefix')
_COMPOUND = re.compile(r'(?<![А-Яа-яЁё́\-‐‑])[А-Яа-яЁё]+(?:[-‐‑][А-Яа-яЁё]+)+(?![А-Яа-яЁё́\-‐‑])')
_HYPHEN = re.compile(r'[-‐‑]')


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def _fold(word: str) -> str:
    return word.casefold().replace('ё', 'е')


def validate_term(term: dict) -> dict:
    """The term's respelling must be a pure е->э/ё respelling of a hyphenated compound (or of its first part)."""
    printed, spoken = term.get('printed', ''), term.get('spoken', '')
    if not term.get('id') or not term.get('source') or term.get('match') not in MATCHES:
        raise ValueError(f'Incomplete spoken term: {term!r}')
    if term['match'] == 'hyphen-prefix':
        if not re.fullmatch(r'[А-Яа-яЁё]+[-‐‑]', printed) or not re.fullmatch(r'[А-Яа-яЁё]+[-‐‑]', spoken):
            raise ValueError(f'A prefix term is one word and a hyphen: {printed!r}')
    elif len(_HYPHEN.split(printed)) < 2 or len(_HYPHEN.split(spoken)) != len(_HYPHEN.split(printed)):
        raise ValueError(f'A spoken term must be a hyphenated compound: {printed!r}')
    if not validate_respelling(printed, spoken):
        raise ValueError(f'Spoken term {printed!r} does not respell anything')
    return term


def _read(path: Path) -> list[dict]:
    try:
        document = json.loads(path.read_text('utf8'))
    except (OSError, ValueError, TypeError):
        return []
    if document.get('schema') != 1 or not isinstance(document.get('series'), list):
        return []
    return [row for row in document['series'] if isinstance(row, dict)]


def series_terms(series: dict, resources: Path | None = None, overlay: Path | None = None) -> dict | None:
    """The frozen term lexicon of a series ({'rule','entries','sha256'}) or None.

    `series` is series_characters.identity(...). A group matches by id or by
    the normalized series name; an overlay entry replaces a bundled one of the same id.
    """
    from .series_characters import normalized
    resources = resources or Path(__file__).resolve().parents[2] / 'resources/series-terms.json'
    name = normalized(series.get('name', ''))
    entries: dict[str, dict] = {}
    for path in (resources, overlay):
        for group in _read(path) if path else []:
            if group.get('id') == series.get('id') or name and name in [normalized(k) for k in group.get('series_keys', [])]:
                for term in group.get('terms', []):
                    entries[term['id']] = validate_term(dict(term))
    if not entries:
        return None
    rows = [entries[k] for k in sorted(entries)]
    return {'rule': RULE, 'entries': rows, 'sha256': digest(rows)}


def terms_for_book(store, book: dict) -> dict | None:
    """Frozen terms for the book's series (bundled seeds plus the library's local overlay)."""
    from .series_characters import identity, _resource_path
    resources = _resource_path(store).with_name('series-terms.json')
    return series_terms(identity(store, book), resources if resources.exists() else None,
                        Path(store.root) / 'series-terms.json')


def _lexeme(candidate: str, head: str) -> bool:
    folded = _fold(candidate)
    if folded == head:
        return True
    try:
        return any(_fold(p.normal_form) == head for p in _morph().parse(folded))
    except ImportError:
        from .name_stress import ENDINGS
        return any(folded == head + e for e in ENDINGS)


def apply_terms(text: str, terms: dict) -> tuple[str, list[dict]]:
    """text with every term respelled in place, and one record per occurrence."""
    if not terms or terms.get('rule') != RULE:
        raise ValueError('Unknown spoken term lexicon')
    parsed = []
    for term in terms.get('entries', []):
        validate_term(term)
        printed = _HYPHEN.split(term['printed']); spoken = _HYPHEN.split(term['spoken'])
        if term['match'] == 'hyphen-prefix':printed, spoken = printed[:1], spoken[:1]
        parsed.append((term, [_fold(p) for p in printed],
                       [tuple((s['index'], s['spoken']) for s in validate_respelling(a, b)) for a, b in zip(printed, spoken)]))
    pieces = []; cursor = 0; records = []
    for match in _COMPOUND.finditer(text):
        parts = _HYPHEN.split(match[0])
        for term, folded, letters in parsed:
            if term['match'] == 'hyphen-prefix':
                if _fold(parts[0]) != folded[0]:continue
                spoken = substitute(parts[0], letters[0]) + match[0][len(parts[0]):]
                if spoken != match[0]:
                    pieces += [text[cursor:match.start()], spoken]; cursor = match.end()
                    records.append({'rule': RESPELLING_RULE, 'kind': 'term', 'term_id': term['id'], 'term_rule': RULE,
                                    'printed': match[0], 'spoken': spoken, 'start': match.start(), 'end': match.end()})
                break
            if len(parts) != len(folded) or any(_fold(a) != b for a, b in zip(parts[:-1], folded[:-1])):
                continue
            if not _lexeme(parts[-1], folded[-1]):
                continue
            # The last part inflects: a letter counts only inside the stem it shares with the printed head.
            shared = next((i for i, (a, b) in enumerate(zip(_fold(parts[-1]), folded[-1])) if a != b), min(len(parts[-1]), len(folded[-1])))
            out = [substitute(p, l if i < len(parts) - 1 else tuple(x for x in l if x[0] < shared))
                   for i, (p, l) in enumerate(zip(parts, letters))]
            separators = _HYPHEN.findall(match[0])
            spoken = out[0] + ''.join(s + p for s, p in zip(separators, out[1:]))
            if spoken != match[0]:
                pieces += [text[cursor:match.start()], spoken]; cursor = match.end()
                records.append({'rule': RESPELLING_RULE, 'kind': 'term', 'term_id': term['id'], 'term_rule': RULE,
                                'printed': match[0], 'spoken': spoken, 'start': match.start(), 'end': match.end()})
            break
    return (''.join(pieces) + text[cursor:]) if pieces else text, records
