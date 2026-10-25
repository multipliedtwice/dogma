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

from .book_identity import hints, parse_title, UNKNOWN_AUTHOR, UNTITLED
from .name_stress import ACUTE, VOWELS, build, name_spans,aligned_stress_index
from .respelling import substitute, validate_respelling
from .util import digest, now, file_digest
from .listener_order import ordered


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
    author_keys=_author_keys(authors)
    # Keep existing single-author release identities, but order is not part of
    # coauthor identity. A multiset retains repeated metadata authors.
    key = {'author': author_keys if len(author_keys)>1 else (author_keys[0]if author_keys else ''),
           'series': normalized(series)}
    # Missing bibliographic identity is not evidence that two files belong to
    # one series. Retain same-source corrections without leaking name families
    # between unrelated anonymous/untitled books.
    if not author_keys or key['series']in ('',normalized(UNTITLED),'unknown series'):
        key['source']=book.get('source_hash')or book.get('id')or digest(book)
    return {'id': digest(key)[:32], 'name': series, 'author': author,
            'volume': found.get('volume'), 'key': key,'author_keys':author_keys}


def _author_keys(authors):
    return sorted(value for author in authors if (value:=normalized(author))and value!=normalized(UNKNOWN_AUTHOR))


def series_ids(store,series):
    """Canonical ID and verified read-only aliases of legacy ordered coauthors.

    Only persisted bibliographic metadata may establish a former order. Never
    infer aliases for anonymous/source-scoped books or arbitrary journal IDs.
    """
    canonical=series['id'];keys=series.get('author_keys',[])
    if len(keys)<2 or 'source'in series.get('key',{}):return (canonical,)
    aliases=set()
    metadata=[{'series_id':digest({'author':normalized(series['author']),
                                  'series':normalized(series['name'])})[:32],
               'series_name':series['name'],'author':series['author']}]
    metadata.extend(store.rows('SELECT DISTINCT series_id,series_name,author FROM series_character_state'))
    for row in metadata:
        if (normalized(row['series_name'])!=normalized(series['name'])
                or _author_keys(row['author'].split(','))!=keys):continue
        legacy=digest({'author':normalized(row['author']),'series':normalized(row['series_name'])})[:32]
        if row['series_id']==legacy:aliases.add(legacy)
    return (canonical,*sorted(aliases-{canonical}))


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
    ids=series_ids(store,series)
    rows=store.rows('SELECT * FROM series_character_state WHERE series_id IN ('+','.join('?'for _ in ids)+') ORDER BY created,character_key',ids)
    for raw in sorted(rows,key=lambda row:row['series_id']==series['id']):
        row = _decoded(raw)
        previous = merged.get(row['character_key'], {})
        merged[row['character_key']] = {**previous, **row,
            'state': {**(previous.get('state') or {}), **(row.get('state') or {})}}
    return series, list(merged.values())


def _analysis_cast(store, generation: dict) -> dict[str, dict]:
    """Read an explicitly source-bound display snapshot, never nearby checkpoints."""
    preview = generation.get('analysis', {}).get('external_preview') or {}
    snapshot=preview.get('cast_snapshot')
    if not isinstance(snapshot,dict)or not isinstance(snapshot.get('characters'),list):return {}
    if (snapshot.get('generation_id')!=generation.get('id')or snapshot.get('book_id')!=generation.get('book_id')
        or snapshot.get('sha256')!=digest({key:value for key,value in snapshot.items()if key!='sha256'})):return {}
    if snapshot.get('source_hash')!=store.book(generation['book_id']).get('source_hash'):return {}
    rows={}
    for row in snapshot['characters']:
        if (not isinstance(row,dict)or not isinstance(row.get('id'),str)or not row['id']
            or not isinstance(row.get('name'),str)or not row['name']or row['id']in rows
            or not isinstance(row.get('aliases',[]),list)or any(not isinstance(alias,str)for alias in row.get('aliases',[]))):return {}
        rows[row['id']]=row
    return rows


def generation_characters(store, generation: dict) -> list[dict]:
    external = _analysis_cast(store, generation)
    found = []
    for raw in generation.get('cast', []):
        if raw.get('id') == 'narrator':
            continue
        display = external.get(raw.get('id'),raw)if not raw.get('name')or raw.get('name')==raw.get('id')else raw
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
    ids=series_ids(store,series)
    existing = store.rows('SELECT * FROM series_character_state WHERE series_id IN ('+','.join('?'for _ in ids)+') AND character_key=? ORDER BY updated DESC',
                          (*ids,key))
    existing.sort(key=lambda row:row['series_id']!=series['id'])
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


def unique_name_bindings(bindings):
    """Case-only spellings are one identity; stress and spoken letters still differ."""
    result=[];seen=set()
    for binding in bindings:
        if binding is None:continue
        key=(binding[0],binding[1].casefold())
        if key not in seen:seen.add(key);result.append(binding)
    return result


def _base_match(character: dict, selected: str, intended: str) -> tuple[str, str] | None:
    selected_key = normalized(selected)
    plain_selected = selected.replace(ACUTE, '')
    position = _stress_position(plain_selected, intended)
    if position is None:
        return None
    stress_index, respelled = position
    matches = []
    for base in _candidate_words(character):
        # A selected lexical component binds that component, never the whole
        # compound as a second competing identity through edit alignment.
        if any(char in base for char in '-‐‑')and not any(char in selected for char in '-‐‑'):continue
        vowels = [i for i, letter in enumerate(base) if letter in VOWELS]
        if not vowels:
            continue
        probe = _marked(base, vowels[0])
        if selected_key not in build({'probe': probe or base}, implicit_single=True):
            continue
        # A listener respelling (Мо́зэс) carries into the base only inside the stem shared with the selected form.
        mapped=aligned_stress_index(plain_selected,base,stress_index)
        if mapped is None:continue
        substitutions=[]
        for letter in respelled:
            target=aligned_stress_index(plain_selected,base,letter['index'])
            if target is not None:substitutions.append((target,letter['spoken']))
        spoken = substitute(base,substitutions)
        if spoken[mapped] in 'ёЁ':
            matches.append((normalized(base), spoken))
            continue
        stressed = _marked(spoken, mapped)
        if stressed:
            matches.append((normalized(base), stressed))
    unique = unique_name_bindings(matches)
    return unique[0] if len(unique) == 1 else None


def _canonical_family_binding(store,book,report):
    """Resolve identity from reviewed series families; keep this report's mark."""
    from .listener_stress import _journal,_ordinary_context,_proper_family,_legacy_family
    if _ordinary_context(report):return None
    series=identity(store,book)
    ids=series_ids(store,series)
    with _journal(store) as db:
        rows=db.execute('SELECT rowid AS journal_order,created,report_id,raw,family FROM corrections WHERE series_id IN ('+','.join('?'for _ in ids)+') AND family IS NOT NULL ORDER BY created,rowid',
            ids).fetchall()
    families={};witnesses={}
    for row in ordered(rows):
        raw=json.loads(row['raw']);family=json.loads(row['family'])
        if raw.get('kind')=='legacy_family':
            validated,_ordinary=_legacy_family(store,book,raw)
            if validated!=family:continue
        elif raw.get('kind')=='stress' and not _ordinary_context(raw):
            selected=raw.get('selected_text','');offset=raw.get('text_offset');passage=raw.get('passage_text','')
            review=raw.get('review_request') or {}
            is_review=(raw.get('feedback_source')=='cast_name_stress' and review.get('operation')=='cast_name_stress'
                and review.get('book_id')==raw.get('book_id') and review.get('generation_id')==raw.get('generation_id'))
            if not is_review and (type(offset) is not int or passage[offset:offset+len(selected)]!=selected):continue
            rebound=[_base_match({'canonical_name':stressed.replace(ACUTE,'')},selected,raw.get('intended_stress',''))
                for stressed in family.values()]
            rebound=unique_name_bindings(rebound)
            if len(rebound)!=1 or {rebound[0][0]:rebound[0][1]}!=family:continue
        else:continue
        for base,stressed in family.items():
            families[base]=stressed;witnesses[base]=row['report_id']
    selected=report['selected_text'].replace(ACUTE,'')
    position=_stress_position(selected,report['intended_stress'])
    if position is None:return None
    stress_index,_=position
    lexical=[]
    for base,stressed in families.items():
        plain=stressed.replace(ACUTE,'')
        vowels=[index for index,letter in enumerate(plain) if letter in VOWELS]
        probe=_marked(plain,vowels[0]) if vowels else None
        if probe and normalized(selected) in build({'probe':probe},implicit_single=True):
            lexical.append((base,plain,witnesses[base]))
    # Stress in an ending cannot eliminate a rival identity: it may mean that
    # the correction is occurrence-only for that identity, not a different name.
    if len({base for base,_plain,_witness in lexical})!=1:return None
    matches=[]
    for base,plain,witness in lexical:
        left=selected.casefold().replace('ё','е');right=plain.casefold().replace('ё','е')
        shared=next((i for i,(a,b) in enumerate(zip(left,right)) if a!=b),min(len(left),len(right)))
        if stress_index>=shared:continue
        found=_base_match({'canonical_name':plain},selected,report['intended_stress'])
        if found:matches.append((found,witness))
    identities=unique_name_bindings(found for found,_witness in matches)
    if len(identities)!=1:return None
    base,stressed=identities[0]
    # Positive evidence for another identity in this book outranks an old
    # family's mere spelling compatibility. Let the local resolver handle it.
    local=_proper_family(book,report)
    if local and base not in local:return None
    family={base:stressed};encoded=json.dumps(family,ensure_ascii=False)
    resolver_sha=file_digest(Path(__file__))
    with _journal(store) as db:
        # An append-only derived binding proposal, not a new human receipt.
        # The subsequent core record adds its accepted resolution separately.
        if not db.execute('SELECT 1 FROM correction_resolutions WHERE report_id=? AND family=? AND resolver_sha256=?',
                (report['id'],encoded,resolver_sha)).fetchone():
            db.execute('INSERT INTO correction_resolutions(report_id,created,family,occurrence,resolver_sha256) VALUES(?,?,?,?,?)',
                (report['id'],now(),encoded,None,resolver_sha))
    # The core binds both canonical_name and pronunciation keys. Keep the
    # same source case here so they deduplicate as one identity rather than
    # yielding two case-only stressed candidates in its historical contract.
    return {'status':'updated','canonical_name':stressed.replace(ACUTE,''),
        'pronunciations':{stressed.replace(ACUTE,''):stressed},
        'series_id':series['id'],'series_name':series['name'],
        'family_resolution':{'method':'canonical-series-family-v1','resolver_sha256':resolver_sha,
            'witness_report_ids':sorted({witness for found,witness in matches if found[0]==base and found[1].casefold()==stressed.casefold()})}}


def record_listener_stress(store, report: dict) -> dict:
    """Promote a stress report only when it names exactly one known character."""
    selected = report['selected_text'].replace(ACUTE, '')
    if not selected[:1].isupper():
        return {'status': 'not_character', 'reason': 'ordinary_word_case', 'matches': 0}
    offset, passage = report.get('text_offset'), report.get('passage_text', '')
    if type(offset) is int and not re.search(r'[А-Яа-яЁё]', re.split(r'[.!?…]', passage[:offset])[-1]):
        try:
            from .proper_noun_stress import _morph
            morph = _morph()
            names = any({'Name', 'Surn', 'Patr', 'Geox'} & set(p.tag.grammemes)
                        for p in morph.parse(selected.casefold()))
            if morph.word_is_known(selected.casefold()) and not names:
                return {'status': 'not_character', 'reason': 'sentence_initial_ordinary_word', 'matches': 0}
        except ImportError:
            return {'status': 'not_character', 'reason': 'family_resolution_unavailable', 'matches': 0}
    generation = store.generation(report['generation_id'])
    book = store.book(generation['book_id'])
    series, records = sync_generation(store, book, generation)
    # Retained series projections must pass the canonical journal resolver;
    # matching their spelling here would bypass its rival-family/source checks.
    local_keys={_key(character)for character in generation_characters(store,generation)}
    local=[]
    for row in records:
        if row['character_key']not in local_keys:continue
        for base in _candidate_words(row):
            vowel=next((i for i,letter in enumerate(base)if letter in VOWELS),None)
            probe=_marked(base,vowel)if vowel is not None else None
            if probe and normalized(selected)in build({'probe':probe},implicit_single=True):
                local.append(row);break
    # Which stress survives an ending is not evidence for character identity.
    if len(local)>1:
        return {'status':'ambiguous','series_id':series['id'],'series_name':series['name'],'matches':len(local)}
    matched = [(row, _base_match(row, report['selected_text'], report['intended_stress'])) for row in local]
    matched = [(row, base) for row, base in matched if base]
    if len(matched) != 1:
        if not matched:
            canonical=_canonical_family_binding(store,book,report)
            if canonical:return canonical
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
    """Overlay canonical human families; old series values are projections only."""
    sync_generation(store, book, {'cast': cast, 'analysis': {}}, pronunciation)
    from .listener_stress import snapshot
    frozen=snapshot(store,book,cast)
    authority=frozen['names']
    names={key:row for key,row in (pronunciation.get('names') or {}).items() if row.get('source')!='series_listener'}
    holds=[]
    for character in cast:
        if character.get('id') == 'narrator':
            continue
        primary, _ = split_display_name(character.get('name', ''))
        spans=name_spans(primary,authority,implicit_single=True)
        if spans:
            stressed=primary
            for start,end,marked in reversed(spans):stressed=stressed[:start]+marked+stressed[end:]
            previous = names.get(character['id'], {})
            names[character['id']] = {**previous, 'name': primary, 'stressed': stressed,
                                      'source': 'series_listener', 'series_id': identity(store,book)['id'],
                                      'series_character_key':normalized(primary)}
        elif ((pronunciation.get('names') or {}).get(character['id']) or {}).get('source')=='series_listener':
            holds.append({'character_id':character['id'],'reason':'No validated canonical human family backs the retained series projection.'})
    from .spoken_terms import terms_for_book
    terms = terms_for_book(store, book)
    return {**{key:value for key,value in pronunciation.items() if key!='listener_projection_holds'}, 'names': names,
            **({'listener_projection_holds':holds} if holds else {}), **({'terms': terms} if terms else {})}


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
