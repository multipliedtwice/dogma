"""Blind two-provider agreement gate (development evaluation; not enabled in production).

The second provider answers the same frozen request as the primary: same source,
context and pre-primary registry snapshot. It never sees the primary's answer,
repair request, evidence, descriptions or new characters. One own repair is allowed
for a semantically invalid second answer, using only that answer and its validation
error. A span is accepted only when both validated answers give the same fixed
speech structure and the same already-registered identity unit by unit. New
identities created in the pass cannot be aligned and stay held. Disagreement, a
missing provider, an indivisible capacity failure and an answer still invalid after
its repair hold; there is no voting and no retry until agreement.

An identity counts as known only when both frozen requests offered it; registry
growth from sibling passages or primary repairs never upgrades a cached answer.
For spans held because both answers created a new person, a counterfactual
source anchor is recorded for evaluation only. It needs the dash remark right
after the speech to be a deterministic named speech attribution (the existing
first-tier rule: one speech verb, at most two adverbs, no pronoun, the name last,
in the exact form) naming each answer's new character, and that name must be the
only proper name of the remark. It never changes the hold.
"""
from __future__ import annotations

import re

from .boundaries import boundary_units
from .casting import words

ATTRIBUTION_END = (',', '!', '?', '…')


def unit_labels(parts, source):
    labels = [None] * len(boundary_units(source['text']))
    for part in parts:
        if part['target_id'] == source['id'] and 'first' in part:
            for index in range(part['first'], part['last'] + 1):
                labels[index] = part['speaker']
    return labels


def structure_key(chunks):
    return [(chunk.get('start'), chunk.get('end'), chunk.get('mode')) for chunk in chunks]


def raw_passage(reply, tid):
    passages = reply.get('passages') if isinstance(reply, dict) else None
    return next((item for item in passages if isinstance(item, dict) and item.get('id') == tid), None) if isinstance(passages, list) else None


def proposal(kind, reply, offered, fixed, tid, invalid=''):
    """One provider answer for one target, kept whole for audit."""
    return {'kind': kind, 'offered': list(offered), 'structure': list(fixed.get(tid, [])), 'passage': raw_passage(reply, tid),
            'characters': reply.get('characters') if isinstance(reply, dict) and isinstance(reply.get('characters'), list) else [],
            'valid': not invalid, **({'error': invalid} if invalid else {})}


def request_summary(payload, before, after):
    return {'segments': [item['id'] for item in payload['segments']], 'before': [item['id'] for item in before],
            'after': [item['id'] for item in after], 'offered': [entry['id'] for entry in payload['cast']]}


def outcome(parts, fixed, tid, invalid=None, updated=(), registered=frozenset()):
    own = [part for part in parts if part['target_id'] == tid or not tid]
    used = {part['speaker'] for part in own}
    return {'parts': own, 'fixed': list(fixed.get(tid, [])), 'invalid': invalid,
            'characters': {character['id']: {key: character.get(key) for key in ('name', 'aliases', 'evidence', 'kind')}
                           for character in updated if character['id'] in used and character['id'] not in registered}}


def unavailable(trigger, reason, summary=None):
    result = outcome([], {}, '', (trigger, reason))
    return {**result, 'first': result, 'offered': [], 'request': summary or {},
            'attempts': [{'kind': 'original', 'valid': False, 'error': reason, 'category': trigger}]}


def labels(character):
    return {words(label) for label in [character.get('name') or '', *(character.get('aliases') or [])] if isinstance(label, str) and words(label)}


def attribution_anchor(source, units, first, lo, hi, common):
    """The one proper name of the dash remark attributing units lo..hi, with its exact range, or a refusal reason."""
    from .analysis import proper
    text = source['text']
    speech = text[units[lo][0]:units[hi][1]].rstrip()
    if hi + 1 < len(units) and first[hi + 1] == 'narrator' and text[units[hi + 1][0]:units[hi + 1][1]].lstrip().startswith('—') \
            and speech.rstrip('»"“”').endswith(ATTRIBUTION_END):
        start, end = units[hi + 1]
    else:
        return None, 'no adjacent dash attribution remark'
    runs, current = [], None
    for match in re.finditer(r'\w+', text[start:end]):
        if proper(match.group(), common):
            if current and text[start + current[1]:start + match.start()].strip() == '':
                current[1] = match.end()
            else:
                current = [match.start(), match.end()]
                runs.append(current)
        else:
            current = None
    if len(runs) != 1:
        return None, 'no proper name in the remark' if not runs else 'more than one proper name in the remark'
    left, right = runs[0]
    return {'target_id': source['id'], 'start': source['start'] + start + left, 'end': source['start'] + start + right,
            'text': text[start + left:start + right], 'remark': text[start:end]}, ''


def alignment(source, units, first, lo, hi, characters, registry, common):
    from .speaker_resolution import named_attribution_speaker
    anchor, reason = attribution_anchor(source, units, first, lo, hi, common)
    if anchor is None:
        return {'aligned': False, 'reason': reason}
    surface = words(anchor['text'])
    if any(surface not in labels(character) for character in characters):
        return {'aligned': False, 'reason': 'a new character does not carry the remark name in that exact form', 'anchor': anchor}
    if any(named_attribution_speaker(anchor['remark'].lstrip(), [{'id': 'candidate', 'name': character.get('name') or '',
                                                                  'aliases': character.get('aliases') or []}]) != 'candidate' for character in characters):
        return {'aligned': False, 'reason': 'the remark is not a deterministic named speech attribution', 'anchor': anchor}
    if any(surface in labels(character) for character in registry):
        return {'aligned': False, 'reason': 'a registered character already has this name', 'anchor': anchor}
    return {'aligned': True, 'anchor': anchor}


def agreement_records(batch, parts, fixed, answers, shared, hold_record, stage='final', primary_characters=None, names=None, common=frozenset(), registry=()):
    """Hold every span the two answers do not prove together; mark agreed spans resolved."""
    records = []
    for source in batch:
        known = shared[source['id']]
        answer = answers[source['id']] if stage == 'final' else answers[source['id']]['first']
        units = boundary_units(source['text'])
        first = unit_labels(parts, source)
        if None in first:
            continue
        def held(lo, hi, trigger, other=None, reason=''):
            record = hold_record(source, source['start'] + units[lo][0], source['start'] + units[hi][1], first[lo], 'agreement', trigger, reason)
            records.append({**record, **({'secondary_speaker': other} if other is not None else {})})
        if answer['invalid']:
            held(0, len(units) - 1, answer['invalid'][0], reason=answer['invalid'][1])
            continue
        if structure_key(fixed.get(source['id'], [])) != structure_key(answer['fixed']):
            held(0, len(units) - 1, 'provider_structure_disagreement')
            continue
        second = unit_labels(answer['parts'], source)
        spans = []
        for index, (a, b) in enumerate(zip(first, second)):
            fresh = a not in ('narrator', 'unknown') and a not in known
            if a == b and not fresh:
                continue
            if spans and spans[-1][1] == index - 1 and (first[spans[-1][0]], second[spans[-1][0]]) == (a, b):
                spans[-1][1] = index
            else:
                spans.append([index, index])
        for lo, hi in spans:
            new = first[lo] not in known and first[lo] not in ('narrator', 'unknown') and second[lo] not in known and second[lo] not in ('narrator', 'unknown')
            held(lo, hi, 'provider_new_identity' if new or first[lo] == second[lo] else 'provider_disagreement', second[lo])
            if new and primary_characters is not None:
                pair = [primary_characters.get(first[lo]), answer.get('characters', {}).get(second[lo])]
                records[-1]['alignment'] = (alignment(source, units, first, lo, hi, pair, registry, common) if all(pair)
                                            else {'aligned': False, 'reason': 'new character record missing'})
        disputed = {index for lo, hi in spans for index in range(lo, hi + 1)}
        lo = None
        for index in range(len(units) + 1):
            agreed = index < len(units) and index not in disputed and first[index] not in ('narrator', 'unknown')
            if lo is not None and (not agreed or first[index] != first[lo]):
                records.append({**hold_record(source, source['start'] + units[lo][0], source['start'] + units[index - 1][1], first[lo], 'agreement',
                                              'provider_agreement'), 'status': 'resolved', 'secondary_speaker': second[lo]})
                lo = None
            if agreed and lo is None:
                lo = index
    return records
