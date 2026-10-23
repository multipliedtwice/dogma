"""Render-degradation holds for external Higgs listening drafts.

A take is degraded when a retry removed planned delivery controls from its
prompt, when a retry left no record of the prompt it used, or when the speaker
had no voice reference and the narrator stood in.
Such a take stays playable, carries a visible provisional warning and a
``render_degraded`` hold that blocks final acceptance until a new take replaces
it. Issues are derived only from files written at render time: the planned
input prompt, the prompt that actually produced the take and its retry variant.
"""
from __future__ import annotations

import re
from pathlib import Path

HOLD = 'render_degraded'
TAG = re.compile(r"<\|(?:prosody|emotion|style):[a-z_]+\|>")
NOTICES = {
    'control_stripped': 'Provisional take: a retry removed the planned delivery controls ({detail}). '
                        'Re-render it before final acceptance.',
    'controls_unverified': 'Provisional take: the retry that produced it left no record of its prompt, so its '
                           'delivery controls cannot be verified. Re-render it before final acceptance.',
    'voice_reference_missing': 'Provisional take: {detail} has no voice reference, so the narrator voice stood in. '
                               'Re-render it before final acceptance.',
}
NOTICE = re.compile(r'\s*Provisional take: .*?Re-render it before final acceptance\.')


def retry_variant(folder: Path, segment_id: str) -> str:
    path = folder / f'{segment_id}.render-variant.txt'
    return path.read_text(encoding='utf-8').strip() if path.is_file() else ''


def stripped_controls(folder: Path, segment_id: str) -> list[str] | None:
    """Planned control tags absent from the prompt that produced the take.

    Returns [] for a first-attempt take and None when a retry left no record of
    the prompt it used, so the loss cannot be established either way.
    """
    if not retry_variant(folder, segment_id):
        return []
    used, planned = folder / f'{segment_id}.render-prompt.txt', folder / 'input' / f'{segment_id}.txt'
    if not used.is_file() or not planned.is_file():
        return None
    kept = set(TAG.findall(used.read_text(encoding='utf-8')))
    return [tag for tag in dict.fromkeys(TAG.findall(planned.read_text(encoding='utf-8'))) if tag not in kept]


def render_issues(segment: dict, folder: Path, routes: dict) -> tuple[list[dict], bool]:
    """Issues of the take now stored for ``segment``, plus whether evidence was complete."""
    issues = []
    variant = retry_variant(folder, segment['id'])
    stripped = stripped_controls(folder, segment['id'])
    if stripped is None:
        # Missing evidence never clears a hold: only a verified take does.
        issues.append({'issue': 'controls_unverified', 'variant': variant})
    elif stripped:
        issues.append({'issue': 'control_stripped', 'variant': variant, 'controls': stripped})
    if segment['speaker'] != 'narrator' and (segment['speaker'] not in routes or variant == 'narrator_plain'):
        issues.append({'issue': 'voice_reference_missing', 'variant': variant, 'speaker': segment['speaker']})
    return issues, stripped is not None


def warning_with(warning: str, issues: list[dict]) -> str:
    """Existing warning text with exactly the current provisional notices."""
    kept = NOTICE.sub('', warning or '').strip()
    notices = [NOTICES[item['issue']].format(detail=', '.join(item.get('controls', ())) or item.get('speaker', ''))
               for item in issues]
    return ' '.join(part for part in (kept, *notices) if part)


def hold_items(segment: dict, issues: list[dict], audio_hash: str) -> list[dict]:
    return [{'target_id': segment['id'], 'source_id': segment['source_id'], 'start': segment['start'],
             'end': segment['end'], 'speaker': segment['speaker'], 'primary_speaker': segment['speaker'],
             'tier': 'render', 'triggers': [HOLD], 'status': 'review', 'render_issue': item['issue'],
             'render_variant': item['variant'], 'audio_hash': audio_hash,
             **({'stripped_controls': item['controls']} if item['issue'] == 'control_stripped' else {})}
            for item in issues]


def with_holds(analysis: dict, segment: dict, issues: list[dict], audio_hash: str) -> dict:
    """Analysis whose render holds for ``segment`` describe exactly its current take.

    Holds for an earlier take of the same passage are dropped, so a clean new
    take clears them; every other review item is kept unchanged.
    """
    items = analysis.get('speaker_resolution', [])
    mine = [item for item in items if HOLD in (item.get('triggers') or ()) and item.get('target_id') == segment['id']]
    wanted = hold_items(segment, issues, audio_hash)
    if mine == wanted:
        return analysis
    return {**analysis, 'speaker_resolution': [item for item in items if item not in mine] + wanted}


def with_provenance(performance: dict, issues: list[dict], audio_hash: str) -> dict:
    rest = {key: value for key, value in (performance or {}).items() if key != 'render_provenance'}
    return {**rest, 'render_provenance': {'audio_hash': audio_hash, 'issues': issues}} if issues else rest
