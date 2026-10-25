from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
import math
import unicodedata
import wave
from pathlib import Path

from .util import AppError
from .audio_source import open_shared_read
from .preparation import default_chapters, narratable


def chapter_selection(book, chapters=None, allowed=None):
    available = [chapter['id'] for chapter in book['chapters']]
    permitted = set(available if allowed is None else allowed)
    requested = list(allowed) if chapters is None and allowed is not None else default_chapters(book) if chapters is None else chapters
    if not isinstance(requested, list) or not requested or not all(isinstance(item, str) for item in requested):
        raise AppError('Select at least one valid chapter.')
    if len(set(requested)) != len(requested) or not set(requested) <= permitted:
        raise AppError('Invalid chapter selection.')
    return [item for item in available if item in requested]


def validate_coverage(book, selected, segments):
    sources = {
        block['id']: (chapter['id'], block['text'])
        for chapter in book['chapters'] if chapter['id'] in selected
        for block in chapter['blocks'] if narratable(block)
    }
    expected = {source_id: 0 for source_id in sources}
    previous_order = -1
    source_order = {source_id: index for index, source_id in enumerate(sources)}
    for segment in segments:
        source_id = segment['source_id']
        if source_id not in sources:
            raise AppError('Audio manifest references an unexpected source passage.')
        chapter_id, text = sources[source_id]
        start, end = segment['start'], segment['end']
        order = source_order[source_id]
        if (type(start) is not int or type(end) is not int or start != expected[source_id]
                or not start < end <= len(text) or segment['chapter_id'] != chapter_id
                or order < previous_order or segment['text'] != text[start:end]):
            raise AppError('Audio manifest has missing, duplicated, reordered, or changed source text.')
        if not isinstance(segment['spoken'], str) or not segment['spoken'].strip():
            raise AppError('An audio passage has no spoken text.')
        expected[source_id] = end
        previous_order = order
    if (not sources or not any(expected.values())
            or any(expected[key] != len(text) and (expected[key] != 0
                   or not all(char.isspace() or unicodedata.category(char).startswith('P') for char in text))
                   for key, (_, text) in sources.items())):
        raise AppError('Audio manifest does not cover every expected source passage.')


def read_audio(path: Path, *, stream=None):
    try:
        with ExitStack() as stack:
            source = stream if stream is not None else stack.enter_context(open_shared_read(path))
            source.seek(0)
            with wave.open(source, 'rb') as audio:
                rate = audio.getframerate()
                count = audio.getnframes()
                channels = audio.getnchannels()
                width = audio.getsampwidth()
                if audio.getcomptype() != 'NONE' or channels != 1 or width != 2 or not 8000 <= rate <= 192000:
                    raise AppError('Saved audio must be mono PCM16 WAV at a supported sample rate.')
                if not 0 < count / rate < 300:
                    raise AppError('Saved audio has an invalid duration.')
                remaining = count * channels * width
                while remaining:
                    chunk = audio.readframes(min(65536, remaining // (channels * width)))
                    if not chunk:
                        raise AppError('Saved audio is truncated.')
                    remaining -= len(chunk)
            source.seek(0)
            checksum = hashlib.file_digest(source, 'sha256').hexdigest()
            source.seek(0)
    except (OSError, EOFError, wave.Error) as error:
        raise AppError('Saved audio cannot be decoded.') from error
    return {'duration': count / rate, 'samples': count, 'sample_rate': rate, 'sha256': checksum}


def accepted_audio(path: Path, *, stream=None, metadata_stream=None):
    try:
        if metadata_stream is None:
            metadata = json.loads(path.with_suffix('.json').read_text('utf-8'))
        else:
            metadata_stream.seek(0)
            metadata = json.load(metadata_stream)
    except (OSError, ValueError) as error:
        raise AppError('Saved audio metadata is missing or invalid.') from error
    actual = read_audio(path, stream=stream)
    if not isinstance(metadata, dict) or metadata.get('sha256') != actual['sha256']:
        raise AppError('Saved audio checksum mismatch.')
    duration = metadata.get('duration')
    if (type(duration) not in (int, float) or not math.isfinite(duration)
            or abs(duration - actual['duration']) > 1 / actual['sample_rate']
            or metadata.get('samples') != actual['samples'] or metadata.get('sample_rate') != actual['sample_rate']):
        raise AppError('Saved audio metadata does not match its measured samples.')
    warning = metadata.get('warning', '')
    if not isinstance(warning, str):
        raise AppError('Saved audio warning is invalid.')
    reset_cues = metadata.get('reset_cues', False)
    if type(reset_cues) is not bool:
        raise AppError('Saved audio repair state is invalid.')
    return {**actual, 'warning': warning, 'reset_cues': reset_cues}


def lexical_text(text):
    return ''.join(char.casefold() for char in unicodedata.normalize('NFKC', text)
                   if unicodedata.category(char)[0] in ('L', 'N'))


def validate_cues(cues, text, duration):
    if not isinstance(cues, list) or not 1 <= len(cues) <= 1000:
        raise AppError('Alignment did not return a complete cue sequence.')
    previous = 0.0
    output = []
    for cue in cues:
        if not isinstance(cue, dict) or set(cue) != {'start', 'end', 'text'}:
            raise AppError('Invalid alignment cue.')
        start, end = cue['start'], cue['end']
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= previous <= start < end <= duration + .05
                or not isinstance(cue['text'], str) or not cue['text'].strip()):
            raise AppError('Alignment timestamps or text are invalid.')
        end = min(float(end), duration)
        if start >= end:
            raise AppError('Alignment cue starts outside the audio.')
        output.append({'start': float(start), 'end': end, 'text': cue['text']})
        previous = end
    if lexical_text(''.join(cue['text'] for cue in output)) != lexical_text(text):
        raise AppError('Alignment text does not cover the spoken passage; using passage subtitles.')
    return output
