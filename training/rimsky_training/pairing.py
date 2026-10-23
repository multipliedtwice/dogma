from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from rimsky.books import split_text
from rimsky.preparation import speech_sources
from rimsky.util import AppError, digest, write_json
from .common import json_read, new_directory, number, rows_write
from .corpus import load_corpus, select_chapter, select_recording

TOKEN = re.compile(r'\S+', re.UNICODE)
COMPOUND_JOIN = re.compile('[-‐‑‒–—]')
MAX_MATCH_TOKENS = 30000


def tokens(text):
    result = []
    for match in TOKEN.finditer(text):
        kept = [(index, char) for index, char in enumerate(match.group())
                if char == "'" or unicodedata.category(char)[0] in ('L', 'N')]
        if not kept or not any(unicodedata.category(char)[0] in ('L', 'N') for _, char in kept):
            continue
        word = unicodedata.normalize('NFKC', ''.join(char for _, char in kept)).casefold().replace('ё', 'е')
        result.append((word, match.start() + kept[0][0], match.start() + kept[-1][0] + 1))
    return result


def compound_tokens(text):
    """Lexical tokens with hyphenated compounds split into their components.

    Transcript comparison treats «колонны-ноги» and «колонны ноги» as the same
    words; each component keeps its own source offsets. Timestamped aligner
    units keep using tokens(), which joins a compound as the aligner does.
    """
    result = []
    for match in TOKEN.finditer(text):
        start = match.start()
        for piece in COMPOUND_JOIN.split(match.group()):
            result.extend((word, start + left, start + right) for word, left, right in tokens(piece))
            start += len(piece) + 1
    return result


def validate_timestamp_text(text, words):
    if not isinstance(text, str) or [word for word, _, _ in tokens(text)] != [word for item in words for word, _, _ in tokens(item['text'])]:
        raise AppError('Timestamped words do not cover the independent ASR transcript. No training transcript was accepted.')


def validate_transcript(document, recording):
    if not isinstance(document, dict) or document.get('schema') != 1:
        raise AppError('A schema-1 timestamped transcript is required.')
    if document.get('recording_sha256') != recording['sha256']:
        raise AppError('Transcript belongs to different audio.')
    words = document.get('words')
    if not isinstance(words, list) or not 1 <= len(words) <= MAX_MATCH_TOKENS:
        raise AppError('Transcript is empty or exceeds the bounded chapter matcher. Split the recording first.')
    result, previous = [], 0.
    duration = recording['audio']['duration']
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get('text'), str):
            raise AppError('Malformed transcript word.')
        start = number(word.get('start'), 'word start', previous, duration)
        end = number(word.get('end'), 'word end', start, duration)
        lexical = tokens(word['text'])
        if end <= start or len(lexical) != 1:
            raise AppError('Each timestamp must describe one nonempty lexical word with positive duration.')
        result.append({'text': word['text'], 'token': lexical[0][0], 'start': start, 'end': end})
        previous = end
    if 'windows' in document:
        windows = document['windows']
        if not isinstance(windows, list) or not windows:
            raise AppError('Invalid ASR window provenance.')
        cursor, boundary = 0, 0.
        for window in windows:
            if not isinstance(window, dict) or type(window.get('words')) is not int or window['words'] < 0:
                raise AppError('Invalid ASR window provenance.')
            start = number(window.get('start'), 'window start', boundary, duration)
            end = number(window.get('end'), 'window end', start, duration)
            count = window['words']
            selected = result[cursor:cursor + count]
            if len(selected) != count or any(word['start'] < start or word['end'] > end + .001 for word in selected):
                raise AppError('ASR timestamps do not match their source window.')
            validate_timestamp_text(window.get('text'), selected)
            cursor += count
            boundary = end
        if cursor != len(result):
            raise AppError('ASR windows do not cover all timestamped words.')
    return result


def propose_pairs(corpus_path: Path, recording_id: str, chapter_id: str, transcript: Path, output: Path):
    root, corpus = load_corpus(corpus_path)
    recording = select_recording(corpus, recording_id)
    book, _ = select_chapter(root, corpus, recording['book_id'], chapter_id)
    document = json_read(transcript)
    words = validate_transcript(document, recording)
    sources = []
    for source in speech_sources(book, [chapter_id]):
        # Ebook display passages can last well over the 30-second training limit.
        # Keep exact offsets into the original paragraph while making shorter
        # candidates that can be checked against the independently timed audio.
        for start, end, text in split_text(source['text'], limit=120):
            sources.append({**source, 'id': f'{source["id"]}-{start}-{end}',
                            'start': source['start'] + start,
                            'end': source['start'] + end, 'text': text})
    source_tokens, owners = [], []
    for source in sources:
        entries = tokens(source['text'])
        owners.append((source, len(source_tokens), len(entries)))
        source_tokens.extend(token[0] for token in entries)
    if not source_tokens or len(source_tokens) > MAX_MATCH_TOKENS:
        raise AppError('Chapter exceeds the bounded matcher or has no speech tokens.')
    mapping = {}
    for block in SequenceMatcher(None, source_tokens, [word['token'] for word in words], autojunk=False).get_matching_blocks():
        if block.size >= 2:
            mapping.update((block.a + offset, block.b + offset) for offset in range(block.size))
    proposals, rejected = [], []
    previous_end = 0.
    for source, first, count in owners:
        indices = [mapping.get(first + index) for index in range(count)]
        matched = [index for index in indices if index is not None]
        # A short ebook phrase can still be located when ASR mishears a word.
        # Keep it as an unreviewed proposal with the disagreement visible.
        phrase = bool(count >= 4 and indices[0] is not None and indices[-1] is not None
                      and len(matched) / count >= .8
                      and matched[-1] - matched[0] + 1 <= count + 2)
        if phrase:
            selected = words[matched[0]:matched[-1] + 1]
            phrase = bool(1 <= selected[-1]['end'] - selected[0]['start'] <= 10
                          and all(right['start'] - left['end'] <= 1.2
                                  for left, right in zip(selected, selected[1:])))
        if not phrase:
            rejected.append({'source_id': source['source_id'], 'start': source['start'], 'end': source['end'],
                             'reason': 'Whole phrase lacked enough ASR anchors; exact fragments may still be proposed.'})
        spans = [(0, count, 'phrase')] if phrase else []
        if not phrase:
            offset = 0
            while offset < count:
                if indices[offset] is None:
                    offset += 1
                    continue
                stop = offset + 1
                while stop < count and indices[stop] == indices[stop - 1] + 1:
                    stop += 1
                if stop - offset >= 8:
                    spans.append((offset, stop, 'fragment'))
                offset = stop
        lexical = tokens(source['text'])
        for left, right, kind in spans:
            selected = words[indices[left]:indices[right - 1] + 1]
            start, end = selected[0]['start'], selected[-1]['end']
            if not 1 <= end - start <= 30 or start < previous_end:
                rejected.append({'source_id': source['source_id'], 'start': source['start'], 'end': source['end'],
                                 'reason': 'Matched clip overlaps another or is outside the 1–30 second preparation policy.'})
                continue
            text_start = source['start'] if kind == 'phrase' else source['start'] + lexical[left][1]
            text_end = source['end'] if kind == 'phrase' else source['start'] + lexical[right - 1][2]
            passage = source['text'] if kind == 'phrase' else source['text'][lexical[left][1]:lexical[right - 1][2]]
            key = [corpus['revision'], recording_id, source['id'], text_start, text_end, start, end]
            proposals.append({'id': 'clip_' + digest(key)[:20], 'corpus_revision': corpus['revision'],
                              'narrator_id': corpus['narrator']['id'], 'book_id': recording['book_id'],
                              'chapter_id': chapter_id, 'source_id': source['source_id'],
                              'text_start': text_start, 'text_end': text_end,
                              'source_text': passage, 'spoken_text': passage,
                              'recording_id': recording_id, 'recording_sha256': recording['sha256'],
                              'audio_start': start, 'audio_end': end,
                              'asr_text': ' '.join(word['text'] for word in selected),
                              'alignment_kind': kind,
                              'asr_anchor_ratio': round(sum(index is not None for index in indices[left:right]) / (right - left), 3),
                              'performance': 'unreviewed', 'character_id': '', 'delivery': 'unreviewed',
                              'review': {'accepted': False, 'reviewer': '', 'text_checked': False,
                                         'boundaries_checked': False, 'speaker_checked': False, 'clean_speech': False},
                              'text_exception': '', 'notes': ''})
            previous_end = end
    report = {'schema': 1, 'corpus_revision': corpus['revision'], 'recording_id': recording_id,
              'book_id': recording['book_id'], 'chapter_id': chapter_id, 'proposed': len(proposals),
              'unmatched_sources': rejected, 'reviewed': 0,
              'limitations': 'Lexical matching only. Timings, omitted/added words, music, cast and delivery require listening review.'}
    with new_directory(output) as stage:
        rows_write(stage / 'review.jsonl', proposals)
        write_json(stage / 'pairing-report.json', report)
    return report
