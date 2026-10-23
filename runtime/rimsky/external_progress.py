"""Read-only progress of supervised narration jobs that run outside the app.

The Higgs preview renderer (scripts/render_higgs_palette_preview.py) keeps its
own checkpoints. This module never writes to them; it reports, per requested
chapter, how many passages are verified, held for a listener decision or not
yet rendered, plus a remaining-time estimate from this machine's measured
first-pass speed. Held passages are reported separately: they wait for a
decision, so they are not counted as remaining render time.
"""
from __future__ import annotations
import json
import re
import statistics
import time
from pathlib import Path

OK = ('asr_exact_match', 'nonverbal_not_spoken_text')
FALLBACK_SECONDS_PER_UNIT = 4.5
RETAKE_SECONDS_PER_UNIT = 20.0
_cache: dict[str, tuple[float, dict]] = {}


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def discover(root: Path) -> list[Path]:
    base = root / 'training-work'
    if not base.is_dir():
        return []
    return sorted(p.parent for p in base.glob('*/*/supervision.json') if (p.parent / 'progress.json').exists())


def _requested_chapters(supervision: dict, state: dict) -> list[str]:
    for event in reversed(supervision.get('journal', [])):
        command = event.get('command') or []
        if event.get('event') == 'started' and '--chapters' in command:
            return command[command.index('--chapters') + 1:]
    return list(state.get('chapters', {}))


def _chunk_selection(chunk: Path) -> Path | None:
    """The effective selection of one chunk: its most recent top-level checkpoint."""
    options = [chunk / 'selected.json', *chunk.glob('presentation-*/selected.json'),
               *chunk.glob('number-groups-v2/selected.json'), *chunk.glob('presentation-*/number-groups-v2/selected.json')]
    options = [p for p in options if p.exists()]
    return max(options, key=lambda p: p.stat().st_mtime_ns) if options else None


def _seconds_per_unit(generation: Path) -> float:
    rates = []
    for plan in sorted(generation.glob('c*/chunk-*/**/take-0/render-plan.json'), key=lambda p: p.stat().st_mtime_ns)[-12:]:
        receipts = _read(plan.parent / 'receipts.json') or {}
        audits = [p for p in plan.parent.glob('word-checks-1.7B*.json') if 'normalized' not in p.name]
        units = len(receipts.get('takes', {}))
        if units >= 20 and audits:
            rates.append((max(p.stat().st_mtime for p in audits) - plan.stat().st_mtime) / units)
    rates = [r for r in rates if 0 < r < 120]
    return statistics.median(rates) if rates else FALLBACK_SECONDS_PER_UNIT


def summarize(generation: Path) -> dict:
    state = _read(generation / 'progress.json') or {}
    supervision = _read(generation / 'supervision.json') or {}
    folder = generation.parent
    book = _read(folder / 'source' / 'book.json') or {}
    titles = {c['id']: c.get('title', c['id']) for c in book.get('chapters', [])}
    held_ids: dict[str, list[str]] = {}
    for section, row in (state.get('held_sections') or {}).items():
        ids = [x.strip() for x in str(row.get('reason', '')).split(': ', 1)[-1].split(',') if re.match(r'c\d+-u\d+', x.strip())]
        held_ids.setdefault(section.split('/')[0], []).extend(ids)
    chapters = []
    for cid in _requested_chapters(supervision, state):
        plan = next((folder / 'audio-v7' / cid).rglob('render-plan.json'), None) if (folder / 'audio-v7' / cid).exists() else None
        total = len((_read(plan) or {}).get('units', [])) if plan else 0
        ready = state.get('chapters', {}).get(cid) or {}
        seen = set(); verified = set()
        for chunk in sorted((generation / cid).glob('chunk-*')) if (generation / cid).exists() else []:
            selection = _chunk_selection(chunk)
            for uid, row in ((_read(selection) or {}) if selection else {}).items():
                seen.add(uid)
                if row.get('word_check', {}).get('status') in OK:
                    verified.add(uid)
        if ready.get('complete'):
            done = max(0, ready.get('units', total) - ready.get('word_flags', 0))
        else:
            done = len(verified)
        held = sorted(set(held_ids.get(cid, [])) - verified) if not ready.get('complete') else []
        chapters.append({'id': cid, 'title': titles.get(cid, cid), 'total': total, 'verified': min(done, total),
                         'rendered': len(seen), 'held': held, 'open_flags': ready.get('word_flags'),
                         'published_minutes': round(ready.get('seconds', 0) / 60, 1) if ready else 0,
                         'complete': bool(ready.get('complete')), 'music': bool(ready.get('audio_with_music'))})
    total = sum(c['total'] for c in chapters); verified = sum(c['verified'] for c in chapters)
    held = sum(len(c['held']) for c in chapters)
    unrendered = sum(max(0, c['total'] - c['rendered']) for c in chapters if not c['complete'])
    recovering = max(0, total - verified - held - unrendered)
    seconds_per_unit = _seconds_per_unit(generation)
    alive = bool(supervision.get('worker_alive'))
    batch = sorted((generation / 'batched-recovery').glob('round-*')) if (generation / 'batched-recovery').exists() else []
    stage = ('batched_recovery' if batch and not (batch[-1] / 'complete.json').exists() and alive else
             state.get('status') or supervision.get('generation_status'))
    return {'id': generation.name, 'title': book.get('title') or folder.name, 'status': stage,
            'supervision': supervision.get('status'), 'worker_alive': alive, 'updated': supervision.get('updated'),
            'active': {'chapter': state.get('active_chapter'), 'chunk': state.get('active_chunk')},
            'chapters': chapters, 'total': total, 'verified': verified, 'held': held,
            'percent': round(100 * verified / total, 1) if total else 0,
            'eta_seconds': round(unrendered * seconds_per_unit + recovering * RETAKE_SECONDS_PER_UNIT) if alive else None,
            'eta_basis': {'unrendered_units': unrendered, 'recovering_units': recovering,
                          'seconds_per_unit': round(seconds_per_unit, 2), 'retake_seconds_per_unit': RETAKE_SECONDS_PER_UNIT},
            'error': state.get('error')}


def external_jobs(root: Path, ttl: float = 10.0) -> list[dict]:
    result = []
    for generation in discover(root):
        key = str(generation)
        stamp = max((generation / n).stat().st_mtime for n in ('progress.json', 'supervision.json'))
        cached = _cache.get(key)
        if cached and cached[0] == stamp and time.monotonic() - cached[1].get('_at', 0) < ttl:
            result.append({k: v for k, v in cached[1].items() if k != '_at'}); continue
        try:
            summary = summarize(generation)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            summary = {'id': generation.name, 'title': generation.parent.name, 'status': 'unreadable', 'error': str(exc)}
        _cache[key] = (stamp, {**summary, '_at': time.monotonic()})
        result.append(summary)
    return result
