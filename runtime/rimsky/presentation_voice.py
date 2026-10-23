"""Resolve a narrator-specific presentation channel into a trained voice family."""
from __future__ import annotations

from .casting import game_ui_voice

from .util import AppError, bounded_text


CHANNELS = {
    'direct', 'game_ui', 'radio', 'intercom', 'public_address',
    'recorded', 'telepathic', 'written_text',
}
REGISTERS = {
    'prose', 'game_system', 'telepathy', 'written_document', 'recorded_message',
    'transmitted_speech', 'item_or_stat_card', 'location_heading',
    'quoted_document', 'epigraph', 'poem', 'author_credit', 'stage_direction',
    'other_non_prose',
}
RULE_STATUSES = {'reviewed', 'provisional'}


def _validate_evidence(rule_name, evidence):
    if not isinstance(evidence, dict):
        raise AppError(f'Presentation evidence for {rule_name} must be an object.')
    for field in ('reviewed_clips', 'supporting_clips', 'books', 'characters'):
        if field in evidence and (not isinstance(evidence[field], int) or evidence[field] < 0):
            raise AppError(f'Invalid presentation evidence {field} for {rule_name}.')
    if ('supporting_clips' in evidence and 'reviewed_clips' in evidence and
            evidence['supporting_clips'] > evidence['reviewed_clips']):
        raise AppError(f'Presentation evidence exceeds reviewed clips for {rule_name}.')


def _review_warning(rule_name, rule, target=None):
    """Explain weak narrator evidence without discarding its useful suggestion."""
    evidence = rule.get('evidence', {})
    status = rule.get('status', 'reviewed')
    target_evidence = rule.get('target_evidence', {}).get(target)
    if target_evidence:
        status = target_evidence.get('status', status)
        evidence = target_evidence
    if status != 'provisional':
        return ''
    count = evidence.get('reviewed_clips')
    count_text = (f'{count} reviewed clip' + ('' if count == 1 else 's')) if count is not None else 'limited reviewed evidence'
    limitation = evidence.get('limitation')
    detail = f'; {limitation}' if isinstance(limitation, str) and limitation else ''
    return (f'The {rule_name.replace("_", " ")} voice mapping is provisional '
            f'({count_text}{detail}); review is required.')


def validate_presentation_profile(profile, voice_ids):
    """Validate a profile embedded in a sealed narrator pack."""
    if profile is None:
        return None
    if not isinstance(profile, dict) or profile.get('schema') != 1:
        raise AppError('Presentation voices must use schema 1.')
    narrator_id = bounded_text(profile.get('narrator_id'), 100)
    if narrator_id != profile.get('narrator_id'):
        raise AppError('Presentation narrator ID has surrounding whitespace.')
    channels = profile.get('channels')
    registers = profile.get('registers', {})
    if not isinstance(channels, dict) or not channels:
        raise AppError('Presentation voices need at least one channel rule.')
    if not isinstance(registers, dict):
        raise AppError('Presentation register rules must be an object.')
    unknown = set(channels) - CHANNELS
    if unknown:
        raise AppError(f'Unknown presentation channel: {sorted(unknown)[0]}')
    unknown = set(registers) - REGISTERS
    if unknown:
        raise AppError(f'Unknown content register: {sorted(unknown)[0]}')
    for rule_name, rule in [*channels.items(), *registers.items()]:
        if not isinstance(rule, dict) or rule.get('mode') not in ('speaker', 'fixed', 'mapped'):
            raise AppError(f'Invalid presentation rule for {rule_name}.')
        mode = rule['mode']
        status = rule.get('status', 'reviewed')
        if status not in RULE_STATUSES:
            raise AppError(f'Invalid presentation status for {rule_name}.')
        if 'evidence' in rule:
            _validate_evidence(rule_name, rule['evidence'])
        if mode == 'speaker':
            continue
        targets = []
        if mode == 'fixed':
            targets.append(rule.get('voice'))
        else:
            if rule.get('fallback', 'review') not in ('review', 'speaker'):
                raise AppError(f'Invalid presentation fallback for {rule_name}.')
            for field in ('by_assigned_voice', 'by_assigned_prefix', 'by_assigned_tag'):
                mapping = rule.get(field, {})
                if not isinstance(mapping, dict) or not all(
                    isinstance(key, str) and key and isinstance(value, str) and value
                    for key, value in mapping.items()
                ):
                    raise AppError(f'Invalid {field} map for {rule_name}.')
                targets.extend(mapping.values())
        if not targets or any(target not in voice_ids for target in targets):
            raise AppError(f'Presentation rule for {rule_name} references a missing voice.')
        target_evidence = rule.get('target_evidence', {})
        if not isinstance(target_evidence, dict) or any(
            target not in targets or not isinstance(value, dict) or
            value.get('status', status) not in RULE_STATUSES
            for target, value in target_evidence.items()
        ):
            raise AppError(f'Invalid target evidence for {rule_name}.')
        for target, value in target_evidence.items():
            _validate_evidence(f'{rule_name}/{target}', value)
    return profile


def _voice_tags(voices, assigned):
    return set(next((voice.get('tags', []) for voice in voices
                    if voice.get('id') == assigned), []))


def resolve_presentation_voice(performance, voices, assigned, profile=None):
    """Return ``(voice_id, warning)`` for one evidenced presentation channel.

    The profile belongs to the narrator/model pack. Radio-like transmission rules
    normally use ``speaker`` mode, while displayed or mediated content can use a
    fixed family or map the speaker's ordinary family/tag to another family.
    """
    if (performance.get('context_confidence') not in ('high', 'medium') or
            not performance.get('context_evidence')):
        return assigned, ''
    channel = performance.get('presentation_channel', 'direct')
    if channel not in CHANNELS:
        return assigned, ''
    available = {voice.get('id') for voice in voices}
    register = performance.get('content_register', 'prose')
    rule_name = channel
    rule = None
    if register in REGISTERS and register != 'prose':
        rule = (profile or {}).get('registers', {}).get(register)
        if rule:
            rule_name = register
        elif (profile or {}).get('registers') and channel == 'direct':
            return assigned, (f'No reviewed {register.replace("_", " ")} voice is configured '
                              'for this narrator; review is required.')
    if not rule:
        rule = (profile or {}).get('channels', {}).get(channel)
    if not rule:
        # Compatibility for older packs: retain their one safe, evidenced rule.
        if channel != 'game_ui':
            return assigned, ''
        candidates = [voice['id'] for voice in voices if game_ui_voice(voice)]
        if len(candidates) == 1:
            return candidates[0], ''
        return assigned, 'No single game readout voice is configured; the assigned voice is used.'
    if rule['mode'] == 'speaker':
        return assigned, _review_warning(rule_name, rule)
    if rule['mode'] == 'fixed':
        target = rule['voice']
    else:
        target = rule.get('by_assigned_voice', {}).get(assigned)
        if not target:
            for prefix, voice_id in rule.get('by_assigned_prefix', {}).items():
                if assigned.startswith(prefix):
                    target = voice_id
                    break
        if not target:
            tags = _voice_tags(voices, assigned)
            for tag, voice_id in rule.get('by_assigned_tag', {}).items():
                if tag in tags:
                    target = voice_id
                    break
        if not target and rule.get('fallback') == 'speaker':
            return assigned, ''
        if not target:
            return assigned, f'No reviewed {rule_name.replace("_", " ")} voice mapping fits this speaker; review is required.'
    if target not in available:
        return assigned, f'The configured {rule_name.replace("_", " ")} voice is missing from this pack.'
    return target, _review_warning(rule_name, rule, target)


def presentation_decision(performance, voices, assigned, profile=None):
    """Return ``(voice_id, warning, needs_review)``; any unresolved mapping is a review item."""
    voice, warning = resolve_presentation_voice(performance, voices, assigned, profile)
    return voice, warning, bool(warning)
