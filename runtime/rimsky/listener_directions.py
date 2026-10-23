"""Source-bound listener instructions, separate from protected corpus labels."""
from __future__ import annotations

import json
from pathlib import Path

from .util import file_digest

SOUND_DIMENSIONS = {'voice_weight', 'voice_depth', 'brightness', 'roughness', 'perceived_age'}


def read_directions(path: Path, book: dict) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if set(data) != {'schema', 'source_sha256', 'casting', 'delivery'} or data['schema'] != 1:
        raise ValueError('Invalid listener directions schema')
    if data['source_sha256'] != book['source_hash']:
        raise ValueError('Listener directions belong to a different book')
    if not isinstance(data['casting'], dict) or not isinstance(data['delivery'], list):
        raise ValueError('Invalid listener directions collections')
    for actor, row in data['casting'].items():
        if not actor or set(row) != {'voice_family', 'preferred_source_character', 'instruction', 'listener_evidence'}:
            raise ValueError('Invalid fixed casting direction')
        if any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError('Empty fixed casting direction')
    blocks = {b['id']: b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')}
    spans = set()
    for row in data['delivery']:
        if set(row) != {'speaker', 'source_id', 'start', 'end', 'text', 'voice_family',
                        'source_character', 'route', 'mood', 'listener_evidence'}:
            raise ValueError('Invalid scoped delivery direction')
        start, end = row['start'], row['end']
        text = blocks.get(row['source_id'], '')
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != row['text']):
            raise ValueError('Listener delivery text/offsets do not match source')
        if any(not isinstance(v, str) or not v.strip() for k, v in row.items() if k not in ('start', 'end')):
            raise ValueError('Empty scoped delivery direction')
        span = (row['source_id'], start, end)
        if any(old[0] == span[0] and max(old[1],start) < min(old[2],end) for old in spans):
            raise ValueError('Overlapping listener delivery scope')
        spans.add(span)
    return {'sha256': file_digest(path), 'data': data}


def validate_palette(binding: dict, units: list[dict], groups: dict) -> None:
    data = binding['data']
    actors = {u['speaker'] for u in units}
    if set(data['casting']) - actors:
        raise ValueError('Fixed casting direction references an absent identity')
    for row in data['casting'].values():
        if not any(g['voice_family'] == row['voice_family'] for g in groups.values()):
            raise ValueError('Fixed casting family has no reviewed palette')
    sources = {u['source_id'] for u in units}
    for row in data['delivery']:
        # A run over some chapters checks only the directions for its passages;
        # a direction for a requested passage must still match exactly once.
        if row['source_id'] not in sources:
            continue
        matches = [u for u in units if u['source_id'] == row['source_id']
                   and u.get('start') == row['start'] and u.get('end') == row['end']
                   and u['text'] == row['text'] and u['speaker'] == row['speaker']]
        if len(matches) != 1:
            raise ValueError('Scoped delivery requires exactly one matching synthesis unit')
        delivery_template(row, groups)


def delivery_template(row: dict, groups: dict) -> str:
    matches = [key for key, g in groups.items()
               if g['voice_family'] == row['voice_family'] and g['character'] == row['source_character']
               and g['route'] == row['route'] and row['mood'] in g['moods']]
    if len(matches) != 1:
        raise ValueError('Listener performance has no unique reviewed palette template')
    return matches[0]


def scoped_delivery(binding: dict | None, unit: dict) -> dict | None:
    if binding is None:
        return None
    for row in binding['data']['delivery']:
        if (unit['source_id'], unit.get('start'), unit.get('end'), unit['speaker'], unit['text']) == (
                row['source_id'], row['start'], row['end'], row['speaker'], row['text']):
            return row
    return None


def validate_cast(binding: dict | None, cast: dict, groups: dict) -> None:
    if binding is None:
        return
    for actor, direction in binding['data']['casting'].items():
        key = cast[actor]['template_id']
        if key not in groups or groups[key]['voice_family'] != direction['voice_family']:
            raise ValueError('Cast contradicts fixed listener family: ' + actor)


def scene_allocation(units: list[dict], cast: dict | None, groups: dict, binding: dict | None) -> dict:
    """Conservative paragraph-linked scene candidates, never distinctness approval."""
    scenes = {}
    counts = {}
    for unit in units:
        if unit.get('mode') != 'speech' or unit['speaker'] == 'narrator':
            continue
        actor = unit['speaker']
        counts[actor] = counts.get(actor, 0) + 1
        key = (unit.get('chapter_id'), unit.get('scene'))
        scenes.setdefault(key, set()).add(actor)
    pairs = {}
    for (chapter, scene), actors in scenes.items():
        for left in sorted(actors):
            for right in sorted(a for a in actors if a > left):
                pairs.setdefault((left, right), []).append({'chapter_id': chapter, 'scene': scene})
    effective = {}
    for actor in counts:
        fixed = (binding or {}).get('data', {}).get('casting', {}).get(actor)
        template = (cast or {}).get(actor, {}).get('template_id')
        effective[actor] = fixed['voice_family'] if fixed else groups.get(template, {}).get('voice_family')
    baselines = {actor: (cast or {}).get(actor, {}).get('baseline_mood') for actor in counts}
    interactions = []
    for pair, locations in sorted(pairs.items()):
        same_family = bool(effective[pair[0]] and effective[pair[0]] == effective[pair[1]])
        same_signature = same_family and bool(baselines[pair[0]]) and baselines[pair[0]] == baselines[pair[1]]
        interactions.append({'actors': list(pair), 'scenes': locations, 'same_family': same_family,
            'baseline_moods': [baselines[a] for a in pair], 'same_family_and_mood': same_signature,
            'resolution_options': ['qualified_stable_family_mood_pair', 'qualified_distinct_references', 'recast'],
            'qualification': 'unresolved'})
    return {'priority_order': sorted(counts, key=lambda a: (-counts[a], a)),
            'speech_unit_counts': counts, 'interactions': interactions,
            'effective_families': effective,
            'limits': 'Scene candidates inherit existing scene boundaries. A shared family is not itself a conflict; stable mood/reference signatures can distinguish characters after listening qualification.'}


def family_descriptions(review_path: Path) -> dict:
    data=json.loads(review_path.read_text(encoding='utf-8'))
    if data.get('kind')=='voice_family_sound_catalog':
        from .voice_descriptors import load_catalog
        return load_catalog(review_path)
    latest = json.loads(review_path.with_name('latest.json').read_text(encoding='utf-8'))
    sha = file_digest(review_path)
    if latest['review_sha256'] != sha:
        raise ValueError('Voice descriptor registry checksum mismatch')
    review = data
    guidance=('casting_description','character_fit','avoid_for')
    families = {key: {'descriptors': value.get('descriptors', {}),
                      **{field:value.get(field,'') for field in guidance},
                      'basis': 'explicit_listener', 'reviewed_at': value.get('reviewed_at'),
                      'audio_evidence_sha256': value.get('audio_evidence_sha256', [])}
                for key, value in review['decisions'].items()
                if (SOUND_DIMENSIONS <= set(value.get('descriptors', {}))
                    or any(value.get(field, '').strip() for field in guidance))}
    return {'sha256': sha, 'families': families}
