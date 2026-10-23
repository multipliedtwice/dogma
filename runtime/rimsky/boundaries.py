"""One source of candidate boundaries for analysis units and speech structure.

Units are candidates: the model chooses where speaker, role or delivery change.
Every structural dash, quotation edge and sentence end is kept; candidates are
never dropped to fit a context window.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .util import AppError

SENTENCE = re.compile(r'[.!?…]+[)\]]*\s+')
DASH = re.compile(r'(?<=[,.!?…:])\s*(?=[—–-]\s)')
QUOTES = re.compile('[«»“”„"‘’]')
GUILLEMET_SPANS = re.compile(r'«[^»\n]{1,4000}»|“[^”\n]{1,4000}”')
ASCII_ATTRIBUTION = re.compile(
    r'(?<=[,!?])\s+-(?=\s+(?:сказал[аи]?|спросил[аи]?|ответил[аи]?|'
    r'добавил[аи]?|возразил[аи]?|пояснил[аи]?|уточнил[аи]?|'
    r'представил(?:ся|ась))\b)', re.IGNORECASE)
TURN_END = re.compile(r'[.!?…][»”"]?\s*$')


def audible(text):
    return any(character.isalnum() for character in text)


def structural_cuts(text):
    cuts = set()
    for match in DASH.finditer(text):
        cuts.add(match.end())
    for match in QUOTES.finditer(text):
        at, quote = match.start(), match.group()
        if quote in '‘’' and 0 < at < len(text) - 1 and text[at - 1].isalpha() and text[at + 1].isalpha():
            continue
        opening = quote in '«„‘' or quote not in '»’' and (at == 0 or text[at - 1].isspace() or text[at - 1] in '([{')
        cut = at if opening else at + 1
        while cut < len(text) and text[cut].isspace():
            cut += 1
        cuts.add(cut)
    for index, character in enumerate(text):
        if character not in '—–' or index == 0 or not text[:index].strip():
            continue
        left, right = index - 1, index + 1
        while left >= 0 and unicodedata.combining(text[left]):
            left -= 1
        while right < len(text) and unicodedata.combining(text[right]):
            right += 1
        before = text[left] if left >= 0 else ''
        after = text[right] if right < len(text) else ''
        if not (before.isalnum() and after.isalnum()) and not (before.isalnum() and (not after or after.isspace())):
            cuts.add(index)
    for match in GUILLEMET_SPANS.finditer(text):
        start, end = match.span()
        if start > 0 and text[:start].strip():
            cuts.add(start)
        end += re.match(r'[,;:.!?…]*\s*', text[end:]).end()
        if end < len(text) and text[end:].strip():
            cuts.add(end)
    for match in ASCII_ATTRIBUTION.finditer(text):
        cuts.add(match.end() - 1)
    return {cut for cut in cuts if 0 < cut < len(text)}


@lru_cache(maxsize=8192)
def labelled_units(text):
    if not text:
        raise AppError('Cannot analyze empty source text.')
    speech = structural_cuts(text)
    cuts = {match.end(): 'sentence' for match in SENTENCE.finditer(text) if 0 < match.end() < len(text)}
    cuts.update(dict.fromkeys(speech, 'speech'))
    units = []
    for start, end in zip([0, *sorted(cuts)], [*sorted(cuts), len(text)]):
        if units and (not audible(text[start:end]) or not audible(text[units[-1][0]:units[-1][1]])):
            units[-1][1] = end
        else:
            units.append([start, end, cuts.get(start)])
    return tuple((start, end, kind) for start, end, kind in units)


def boundary_units(text):
    return tuple((start, end) for start, end, _ in labelled_units(text))


def structure_chunks(text):
    chunks = []
    for start, end, kind in labelled_units(text):
        if chunks and kind != 'speech':
            chunks[-1]['end'] = end
        else:
            chunks.append({'start': start, 'end': end})
    chunks = [{**chunk, 'text': text[chunk['start']:chunk['end']]} for chunk in chunks]
    if ''.join(chunk['text'] for chunk in chunks) != text:
        raise AppError('Candidate chunks did not preserve source text.')
    return chunks


def sentence_pieces(text, start, end):
    pieces = []
    for left, right, kind in labelled_units(text):
        if right <= start or left >= end:
            continue
        if pieces and kind != 'sentence' and left > start:
            pieces[-1][1] = min(right, end)
        else:
            pieces.append([max(left, start), min(right, end)])
    return [(left, right) for left, right in pieces if left < right]


def unit_range(text, start, end):
    units = boundary_units(text)
    starts = {unit_start: index for index, (unit_start, _) in enumerate(units)}
    ends = {unit_end: index for index, (_, unit_end) in enumerate(units)}
    if start not in starts or end not in ends or starts[start] > ends[end]:
        raise AppError('A proposed range does not follow the shared analysis units.')
    return starts[start], ends[end]


def unit_offsets(text, first, last):
    units = boundary_units(text)
    if not (type(first) is int and type(last) is int and 0 <= first <= last < len(units)):
        raise AppError('A unit range is outside the passage.')
    return units[first][0], units[last][1]


def starts_new_speech_turn(previous, current, previous_mode, current_mode):
    if previous_mode != 'speech' or current_mode != 'speech':
        return False
    if not current['text'].lstrip().startswith(('—', '–', '-')):
        return False
    return TURN_END.search(previous['text']) is not None
