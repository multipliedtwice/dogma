"""Timestamped pipeline events for per-iteration time and retake accounting.

The preview worker sets RIMSKY_EVENT_LOG to its generation's events.jsonl;
child processes (native renders, recognizer audits, witnesses, CLI model calls)
inherit it. Each record is one JSON line: time, process, kind and fields.
Events never change any gate or output; with no log configured they are no-ops.
"""
from __future__ import annotations
import json
import os
import contextvars
import threading
import time
from contextlib import contextmanager

_LOCK = threading.Lock()
_CTX = contextvars.ContextVar('rimsky_event_context', default={})


def mark(stage: str, **fields) -> None:
    """Start a named stage; later events carry it until the next mark (durations come from consecutive marks)."""
    _CTX.set({**{k: v for k, v in _CTX.get().items() if k in ('section',)}, **fields, 'stage': stage})
    event('mark')


def section(name: str) -> None:
    _CTX.set({'section': name})
    event('mark', stage='section-start')


def event(kind: str, **fields) -> None:
    path = os.environ.get('RIMSKY_EVENT_LOG')
    if not path:
        return
    record = {'t': round(time.time(), 3), 'pid': os.getpid(), 'kind': kind, **_CTX.get(), **fields}
    line = json.dumps(record, ensure_ascii=False, default=str) + '\n'
    with _LOCK:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line)


@contextmanager
def span(kind: str, **fields):
    """Record kind.start and kind.end (with seconds); the yielded dict adds end fields."""
    start = time.time(); extra: dict = {}
    event(kind + '.start', **fields)
    try:
        yield extra
    finally:
        event(kind + '.end', seconds=round(time.time() - start, 3), **fields, **extra)
