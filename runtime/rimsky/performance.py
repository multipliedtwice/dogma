"""Auditable delivery cues shared by analysis, rendering, and corpus review.

These are text-based intentions. They are not claims about the source narrator's
recorded performance; a human must verify those labels against source audio.
"""
from __future__ import annotations

import re


HEADING = re.compile(
    r"^(?:(?:глава|часть)\s+(?:\d+|[ivxlcdm]+|первая|вторая|третья|четв[её]ртая|"
    r"пятая|шестая|седьмая|восьмая|девятая|десятая)\b|"
    r"(?:пролог|эпилог|введение)(?:\s*[.:]|\s*$))", re.I)
EXCITED_ATTRIBUTION = re.compile(
    r"\b(?:восторженно|радостно|ликующе)\s+"
    r"(?:прокричал[а-я]*|воскликнул[а-я]*|сказал[а-я]*|крикнул[а-я]*)\b", re.I)


def performance_plan(part: dict, context: list[dict] = ()) -> dict:
    """Turn a validated speaker/delivery decision into stable synthesis cues."""
    text = part['text'].strip()
    delivery = part['delivery']
    source_id = part.get('source_id', '')
    cues = []
    pace = 'normal'
    ending_pace = None
    before = after = 0.0
    context_decision = part.get('context') or {}
    if (context_decision.get('mood') == 'fast_action' and
            context_decision.get('confidence') in ('high', 'medium')):
        cues.append('fast_action')
        pace = 'brisk'
    if part.get('kind') == 'heading' or HEADING.match(text):
        cues.append('section_heading')
        pace, before, after = 'deliberate', 0.85, 1.15
    if delivery == 'enumeration':
        cues.append('enumeration')
        pace = 'brisk'
    if text.endswith(('…', '...')):
        cues.append('trailing_ellipsis')
        if pace == 'brisk':
            ending_pace = 'deliberate'
        else:
            pace = 'deliberate'
        after = max(after, 0.8)
    elif '…' in text or '...' in text:
        cues.append('internal_ellipsis')
    if delivery == 'excited':
        cues.append('excited_speech')
    if delivery == 'stunned':
        cues.append('stunned_reaction')
        pace = 'deliberate'
    if delivery == 'excited' and any(
        EXCITED_ATTRIBUTION.search(item.get('text', '')) for item in context
    ):
        cues.append('explicit_excited_attribution')
    speaker_words = set(re.findall(r'[a-z0-9]+', str(part['speaker']).casefold()))
    if (('mechanical' in speaker_words and bool(speaker_words & {'game', 'stats', 'stat', 'system', 'ui'})) or
            (context_decision.get('channel') == 'game_ui' and
             context_decision.get('confidence') in ('high', 'medium'))):
        cues.append('mechanical_voice')
    return {'schema': 2, 'delivery': delivery, 'pace': pace,
            'mood': context_decision.get('mood', 'neutral'),
            'presentation_channel': context_decision.get('channel', 'direct'),
            'content_register': context_decision.get('content_register', 'prose'),
            'context_confidence': context_decision.get('confidence', 'low'),
            'context_evidence': context_decision.get('evidence', []),
            'ending_pace': ending_pace,
            'pause_before_seconds': before, 'pause_after_seconds': after,
            'cues': cues, 'source_id': source_id,
            'basis': 'text_intent_unverified_audio'}
