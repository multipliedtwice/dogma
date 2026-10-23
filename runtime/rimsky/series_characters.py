"""Durable, cumulative character identity and pronunciation state per book series.

Bundled seeds are read-only release data.  The SQLite table is a local overlay:
it survives deleting individual books and always wins over a bundled field.
"""
from __future__ import annotations

import contextlib
import json
import re
import unicodedata
from pathlib import Path

from .book_identity import hints, parse_title
from .name_stress import ACUTE, VOWELS, build, name_spans
from .respelling import substitute, validate_respelling
from .util import digest, now


WORD = re.compile(r'[А-Яа-яЁё]+(?:[-‐‑][А-Яа-яЁё]+)*')
PARENTHETICAL = re.compile(r'^(.*?)\s*\((.*?)\)\s*$')


def normalized(value: str) -> str:
    value = unicodedata.normalize('NFKC', value or '').replace(ACUTE, '').casefold().replace('ё', 'е')
    return ' '.join(re.findall(r'[а-яa-z0-9]+', value))


def split_display_name(value: str) -> tuple[str, list[str]]:
    value = ' '.join((value or '').split()).strip()
    match = PARENTHETICAL.match(value)
    primary = (match.group(1) if match else value).strip()
    aliases = [part.strip() for part in re.split(r'[,;/]', match.group(2)) if part.strip()] if match else []
    return primary, aliases


def identity(store, book: dict) -> dict:
    """Stable author+series identity, preferring source metadata over title inference."""
    try:
        found = hints(store, book)
    except Exception:
        parsed = parse_title(book.get('title', ''))
        found = {'series': parsed.get('series') or '', 'volume': parsed.get('volume'),
                 'authors': [book.get('author', '')]}
    series = (found.get('series') or '').strip()
    if not series:
        parsed = parse_title(book.get('title', ''))
        series = (parsed.get('series') or book.get('title') or 'Unknown series').strip()
    authors = [part for part in (found.get('authors') or [book.get('author', '')]) if part]
    author = ', '.join(authors) or book.get('author', '') or 'Unknown author'
    key = {'author': normalized(author), 'series': normalized(series)}
    return {'id': digest(key)[:32], 'name': series, 'author': author,
            'volume': found.get('volume'), 'key': key}


def _resource_path(store) -> Path:
    resource = getattr(store, 'resources', None)
    return Path(resource) / 'series-characters.json' if resource else Path(__file__).resolve().parents[2] / 'resources/series-characters.json'


def _bundled(store, series_id: str) -> list[dict]:
    try:
        document = json.loads(_resource_path(store).read_text('utf8'))
    except (OSError, ValueError, TypeError):
        return []
    if document.get('schema') != 1 or not isinstance(document.get('series'), list):
        return []
    group = next((row for row in document['series'] if row.get('id') == series_id), None)
    return [dict(row) for row in (group or {}).get('characters', []) if isinstance(row, dict)]


def _decoded(row: dict) -> dict:
    result = dict(row)
    for field, fallback in (('aliases', []), ('state', {})):
        if isinstance(result.get(field), str):
            with contextlib.suppress(ValueError, TypeError):
                result[field] = json.loads(result[field])
        if not isinstance(result.get(field), type(fallback)):
            result[field] = fallback
    return result


def series_state(store, book: dict) -> tuple[dict, list[dict]]:
    series = identity(store, book)
    merged = {row.get('character_key'): row for row in _bundled(store, series['id']) if row.get('character_key')}
    for raw in store.rows('SELECT * FROM series_character_state WHERE series_id=? ORDER BY created,character_key', (series['id'],)):
        row = _decoded(raw)
        previous = merged.get(row['character_key'], {})
        merged[row['character_key']] = {**previous, **row,
            'state': {**(previous.get('state') or {}), **(row.get('state') or {})}}
    return series, list(merged.values())


def _analysis_cast(store, generation: dict) -> dict[str, dict]:
    """Recover display names retained by external-preview analysis checkpoints."""
    rows: dict[str, dict] = {}
    preview = generation.get('analysis', {}).get('external_preview') or {}
    if not preview.get('generation'):
        return rows
    analysis = store.root.parent / 'analysis'
    for path in sorted(analysis.glob('c*/cast.json')):
        with contextlib.suppress(OSError, ValueError, TypeError):
            for row in json.loads(path.read_text('utf8')):
                if isinstance(row, dict) and row.get('id') and row.get('name'):
                    rows.setdefault(row['id'], row)
    return rows


def generation_characters(store, generation: dict) -> list[dict]:
    external = _analysis_cast(store, generation)
    found = []
    for raw in generation.get('cast', []):
        if raw.get('id') == 'narrator':
            continue
        display = external.get(raw.get('id'), raw)
        primary, aliases = split_display_name(display.get('name') or raw.get('name') or '')
        aliases = list(dict.fromkeys([*aliases, *(display.get('aliases') or []), *(raw.get('aliases') or [])]))
        if not primary:
            continue
        found.append({**raw, 'id': raw.get('id') or display.get('id'), 'name': primary,
                      'aliases': [a for a in aliases if normalized(a) != normalized(primary)]})
    return found


def _key(character: dict) -> str:
    return normalized(character.get('name', '')) or normalized(character.get('id', ''))


def upsert_character(store, series: dict, character: dict, *, state: dict | None = None) -> dict:
    key = _key(character)
    if not key:
        raise ValueError('Character needs a stable name.')
    existing = store.rows('SELECT * FROM series_character_state WHERE series_id=? AND character_key=?',
                          (series['id'], key))
    bundled = next((row for row in _bundled(store, series['id']) if row.get('character_key') == key), {})
    prior = {**_decoded(bundled), **(_decoded(existing[0]) if existing else {})}
    aliases = list(dict.fromkeys([*(prior.get('aliases') or []), *(character.get('aliases') or [])]))
    prior_state = prior.get('state') or {}
    incoming = state or {}
    if character.get('id') and not incoming.get('character_id'):
        incoming['character_id'] = character['id']
    if character.get('description') and not incoming.get('description'):
        incoming['description'] = character['description']
    if character.get('kind') and not incoming.get('kind'):
        incoming['kind'] = character['kind']
    if prior_state.get('pronunciation_source') == 'listener' and incoming.get('pronunciation_source') != 'listener':
        incoming = {k: v for k, v in incoming.items()
                    if k not in ('pronunciations', 'pronunciation_source', 'pronunciation_evidence')}
    state = {**prior_state, **incoming}
    # Discovery may fill blanks but never turns an automatic assignment into a reviewed one.
    if character.get('voice_id') and not state.get('voice_id'):
        state['voice_id'] = character['voice_id']
        state['voice_source'] = 'generation_suggestion'
    timestamp = now()
    values = (series['id'], key, series['name'], series['author'], character.get('name', ''),
              json.dumps(aliases, ensure_ascii=False), json.dumps(state, ensure_ascii=False),
              prior.get('created') or timestamp, timestamp)
    store.execute('''INSERT INTO series_character_state
        (series_id,character_key,series_name,author,canonical_name,aliases,state,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(series_id,character_key) DO UPDATE SET
        series_name=excluded.series_name,author=excluded.author,canonical_name=excluded.canonical_name,
        aliases=excluded.aliases,state=excluded.state,updated=excluded.updated''', values)
    return {**character, 'character_key': key, 'state': state, 'series_id': series['id']}


def sync_generation(store, book: dict, generation: dict, pronunciation: dict | None = None) -> tuple[dict, list[dict]]:
    """Add newly discovered characters; characters missing from this book are retained."""
    series = identity(store, book)
    names = (pronunciation or {}).get('names', {})
    for character in generation_characters(store, generation):
        row = names.get(character.get('id')) or {}
        state = {}
        if row.get('stressed'):
            primary, _ = split_display_name(character['name'])
            state['pronunciations'] = {normalized(primary): row['stressed']}
            state['pronunciation_source'] = row.get('source', 'model')
        upsert_character(store, series, character, state=state)
    return series_state(store, book)


def seed_cast(store, book: dict) -> list[dict]:
    """Known identities for a later volume; callers prune those not encountered."""
    _, records = series_state(store, book)
    used = set()
    result = []
    for row in records:
        state = row.get('state') or {}
        base = re.sub(r'[^a-z0-9_]+', '_', str(state.get('character_id') or '').casefold()).strip('_')
        identifier = base or 'series_' + digest(row['character_key'])[:12]
        if identifier == 'narrator' or identifier in used:
            identifier = 'series_' + digest([row['character_key'], identifier])[:12]
        used.add(identifier)
        result.append({'id': identifier, 'name': row.get('canonical_name', ''),
                       'aliases': row.get('aliases') or [], 'description': state.get('description', ''),
                       'kind': state.get('kind', 'person'), 'evidence': [], '_series_seed': True,
                       **({'voice_id': state['voice_id']} if state.get('voice_source') == 'listener' and state.get('voice_id') else {})})
    return result


def _marked(word: str, index: int) -> str | None:
    return word[:index + 1] + ACUTE + word[index + 1:] if 0 <= index < len(word) and word[index] in VOWELS else None


def _candidate_words(character: dict) -> list[str]:
    values = [character.get('canonical_name', ''), *(character.get('aliases') or [])]
    candidates = []
    for value in values:
        for match in WORD.finditer(value):
            whole = match[0]
            candidates.append(whole)
            # The UI selection may contain only the lexical part of a compound
            # name (Ар-Хакону -> Хакону).  Keep the full name for identity, but
            # allow a substantial component to bind its inflected stress.
            candidates.extend(part for part in re.split(r'[-‐‑]', whole) if len(part) >= 3)
    return list(dict.fromkeys(candidates))


def _stress_position(selected: str, intended: str) -> tuple[int, list[dict]] | None:
    """(stressed letter index, respelled letters) of an intended form; ё is its own stress mark."""
    try:
        respelled = validate_respelling(selected, intended)
    except ValueError:
        return None
    if ACUTE in intended:
        return intended.index(ACUTE) - 1, respelled
    yo = [s['index'] for s in respelled if s['spoken'] in 'ёЁ']
    return (yo[0], respelled) if len(yo) == 1 else None


def _base_match(character: dict, selected: str, intended: str) -> tuple[str, str] | None:
    selected_key = normalized(selected)
    plain_selected = selected.replace(ACUTE, '')
    position = _stress_position(plain_selected, intended)
    if position is None:
        return None
    stress_index, respelled = position
    matches = []
    for base in _candidate_words(character):
        vowels = [i for i, letter in enumerate(base) if letter in VOWELS]
        if not vowels:
            continue
        probe = _marked(base, vowels[0])
        if selected_key not in build({'probe': probe or base}, implicit_single=True):
            continue
        # A listener respelling (Мо́зэс) carries into the base only inside the stem shared with the selected form.
        shared = next((i for i, (a, b) in enumerate(zip(plain_selected.casefold().replace('ё', 'е'), base.casefold().replace('ё', 'е'))) if a != b),
                      min(len(plain_selected), len(base)))
        spoken = substitute(base, [(s['index'], s['spoken']) for s in respelled if s['index'] < shared])
        if 0 <= stress_index < len(spoken) and spoken[stress_index] in 'ёЁ':
            matches.append((normalized(base), spoken))
            continue
        stressed = _marked(spoken, stress_index)
        if stressed:
            matches.append((normalized(base), stressed))
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def record_listener_stress(store, report: dict) -> dict:
    """Promote a stress report only when it names exactly one known character."""
    generation = store.generation(report['generation_id'])
    book = store.book(generation['book_id'])
    series, records = sync_generation(store, book, generation)
    matched = [(row, _base_match(row, report['selected_text'], report['intended_stress'])) for row in records]
    matched = [(row, base) for row, base in matched if base]
    if len(matched) != 1:
        return {'status': 'ambiguous' if matched else 'not_character', 'series_id': series['id'],
                'series_name': series['name'], 'matches': len(matched)}
    row, (base, stressed) = matched[0]
    state = dict(row.get('state') or {})
    pronunciations = {**(state.get('pronunciations') or {}), base: stressed}
    evidence_rows = [*(state.get('pronunciation_evidence') or []), {
        'report_id': report['id'], 'book_id': report['book_id'], 'generation_id': report['generation_id'],
        'selected_text': report['selected_text'], 'intended_stress': report['intended_stress'],
        'created': report['created'], 'source': 'listener'}]
    evidence_by_report = {}
    for item in evidence_rows:
        evidence_by_report.pop(item.get('report_id'), None)
        evidence_by_report[item.get('report_id')] = item
    evidence = list(evidence_by_report.values())[-50:]
    state.update(pronunciations=pronunciations, pronunciation_source='listener', pronunciation_evidence=evidence)
    character = {'name': row['canonical_name'], 'aliases': row.get('aliases') or []}
    saved = upsert_character(store, series, character, state=state)
    return {'status': 'updated', 'series_id': series['id'], 'series_name': series['name'],
            'character_key': saved['character_key'], 'canonical_name': row['canonical_name'],
            'pronunciations': pronunciations}


def apply_pronunciations(store, book: dict, cast: list[dict], pronunciation: dict) -> dict:
    """Overlay listener-reviewed series pronunciations onto a book's name analysis."""
    _, records = sync_generation(store, book, {'cast': cast, 'analysis': {}}, pronunciation)
    names = dict(pronunciation.get('names') or {})
    for character in cast:
        if character.get('id') == 'narrator':
            continue
        primary, _ = split_display_name(character.get('name', ''))
        candidates = [row for row in records if normalized(primary) in {
            normalized(row.get('canonical_name', '')), *(normalized(a) for a in row.get('aliases') or [])}]
        if len(candidates) != 1:
            continue
        state = candidates[0].get('state') or {}
        if state.get('pronunciation_source') != 'listener':
            continue
        stressed = (state.get('pronunciations') or {}).get(normalized(primary))
        if stressed:
            previous = names.get(character['id'], {})
            names[character['id']] = {**previous, 'name': primary, 'stressed': stressed,
                                      'source': 'series_listener', 'series_id': candidates[0]['series_id'],
                                      'series_character_key': candidates[0]['character_key']}
    from .spoken_terms import terms_for_book
    terms = terms_for_book(store, book)
    return {**pronunciation, 'names': names, **({'terms': terms} if terms else {})}


def propagation_targets(generation: dict, character_state: dict) -> list[dict]:
    """All ready segments containing this character, grouped into one correction per segment."""
    lexicon = character_state.get('pronunciations') or {}
    targets = []
    for segment in generation.get('segments', []):
        if segment.get('status') != 'ready' or not segment.get('audio_hash'):
            continue
        corrections = [{'selected_text': segment['text'][start:end], 'intended_stress': marked,
                        'text_offset': segment['start'] + start}
                       for start, end, marked in name_spans(segment.get('text', ''), lexicon, implicit_single=True)
                       if ACUTE in marked or marked != segment['text'][start:end]]
        if corrections:
            targets.append({'segment': segment, 'corrections': corrections})
    return targets
