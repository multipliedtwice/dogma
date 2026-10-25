"""Local identity projection of canonical human pronunciation families.

The shared journal owns human decisions and chronology. This projection adds
no speaker attribution, voice selection, kind, or listening approval.
"""
from __future__ import annotations

import json
from pathlib import Path

from .name_stress import ACUTE
from .util import digest, file_digest
from .listener_order import ordered

POLICY = 'listener-family-projection-v1'


def _validated_family(store, book, row, journal_db):
    from .listener_stress import _ordinary_context, _legacy_family
    from .respelling import validate_respelled_stress
    from .series_characters import _base_match,unique_name_bindings
    raw = json.loads(row['raw'])
    family = json.loads(row['family']) if row['family'] else None
    if not family:
        return None
    if raw.get('kind') == 'legacy_family':
        validated, _ = _legacy_family(store, book, raw, _journal_db=journal_db)
        return (raw, family) if validated == family else None
    if raw.get('kind') != 'stress' or _ordinary_context(raw):
        return None
    selected = raw.get('selected_text', '')
    intended = raw.get('intended_stress', '')
    try:
        validate_respelled_stress(selected, intended)
    except ValueError:
        return None
    review = raw.get('review_request') or {}
    is_review = (raw.get('feedback_source') == 'cast_name_stress'
        and review.get('operation') == 'cast_name_stress'
        and review.get('book_id') == raw.get('book_id')
        and review.get('generation_id') == raw.get('generation_id'))
    offset, passage = raw.get('text_offset'), raw.get('passage_text', '')
    if not is_review and (type(offset) is not int or passage[offset:offset + len(selected)] != selected):
        return None
    rebound = [_base_match({'canonical_name': value.replace(ACUTE, '')}, selected, intended)
        for value in family.values()]
    rebound = unique_name_bindings(rebound)
    return (raw, family) if len(rebound) == 1 and {rebound[0][0]: rebound[0][1]} == family else None


def project(store, book):
    # Read authority under the same library lock as the projection write, so
    # two report handlers cannot replay an earlier read after the newer write.
    with store.lock:
        if store.db.in_transaction:
            raise RuntimeError('Commit the local library transaction before projecting shared listener authority.')
        return _project_locked(store, book)


def _project_locked(store, book):
    """Rebuild accepted family fields without changing journal history.

    Replay order is the journal's human chronology, never the order in which
    libraries/backfills happen to call this function. Existing character IDs,
    voice choices and unrelated state are retained by upsert_character.
    """
    from .listener_stress import _journal
    from .series_characters import identity,series_ids
    series = identity(store, book)
    ids=series_ids(store,series)
    with _journal(store) as db:
        # One SQLite writer reservation serializes all library/process readers
        # against human journal commits until the derived local write finishes.
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT rowid AS journal_order,* FROM corrections WHERE series_id IN ('+','.join('?'for _ in ids)+') ORDER BY created,rowid',
            ids).fetchall()
        return _project_rows(store, book, series, rows, db)



def _project_rows(store, book, series, rows, journal_db):
    from .series_characters import series_state, _base_match, upsert_character
    families, histories = {}, {}
    for row in ordered(rows):
        validated = _validated_family(store, book, row, journal_db)
        if not validated:
            continue
        raw, family = validated
        evidence = {key: raw.get(key) for key in
            ('book_id', 'generation_id', 'selected_text', 'intended_stress', 'created')}
        evidence.update(report_id=row['report_id'], source='listener')
        for base, stressed in family.items():
            families[base] = (stressed, row, raw)
            histories.setdefault(base, []).append(evidence)
    applied, held = [], []
    with store.lock:
        _, records = series_state(store, book)
        for base, (stressed, receipt, raw) in families.items():
            exact = [row for row in records if row['character_key'] == base]
            matched = exact or [row for row in records
                if _base_match(row, raw['selected_text'], raw['intended_stress']) == (base, stressed)]
            if len(matched) > 1:
                held.append({'family': base, 'reason': 'Multiple retained character identities match this family.'})
                continue
            previous = matched[0] if matched else None
            state = (previous or {}).get('state') or {}
            pronunciations = {**(state.get('pronunciations') or {}), base: stressed}
            evidence = {row.get('report_id'): row for row in state.get('pronunciation_evidence', [])}
            evidence.update({row['report_id']: row for row in histories[base]})
            projection = {**(state.get('listener_family_projection') or {}), base: {
                'policy': POLICY, 'report_id': receipt['report_id'], 'created': receipt['created'],
                'journal_order': receipt['journal_order'], 'family': base, 'stressed': stressed,
                'raw_sha256': digest(json.loads(receipt['raw'])), 'resolver_sha256': file_digest(Path(__file__))}}
            incoming = {'pronunciations': pronunciations, 'pronunciation_source': 'listener',
                'pronunciation_evidence': list(evidence.values()), 'listener_family_projection': projection}
            if not previous:
                incoming['identity_source'] = 'listener_pronunciation_family'
            character = {'name': previous['canonical_name'] if previous else stressed.replace(ACUTE, ''),
                'aliases': (previous or {}).get('aliases') or []}
            if all(state.get(key) == value for key, value in incoming.items()):
                applied.append(base)
                continue
            saved = upsert_character(store, series, character, state=incoming)
            # Retain the same in-memory identities for aliases/shared components.
            if previous:
                previous['state'] = saved['state']
            else:
                records.append({**saved, 'canonical_name': character['name']})
            applied.append(base)
    return {'policy': POLICY, 'projected': applied, 'held': held}
