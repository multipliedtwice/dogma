from __future__ import annotations

import contextlib
import tempfile
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from rimsky.analysis import DELIVERIES
from rimsky.performance import performance_plan
from rimsky.references import inspect_reference
from rimsky.util import AppError, bounded_text, file_digest, require_id, write_json
from .common import new_directory, number, owned_file, rows_read, rows_write, seal_json
from .corpus import load_corpus, select_chapter, select_recording
from .pairing import tokens


def checked_rows(root, corpus, rows):
    accepted, seen, chapter_cache = [], set(), {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('review'), dict):
            raise AppError('Invalid clip review.')
        review = row['review']
        if review.get('accepted') is not True:
            continue
        clip_id = require_id(row.get('id'))
        if clip_id in seen:
            raise AppError('Duplicate reviewed clip ID.')
        seen.add(clip_id)
        if row.get('corpus_revision') != corpus['revision'] or row.get('narrator_id') != corpus['narrator']['id']:
            raise AppError('A reviewed clip belongs to a different corpus or narrator.')
        bounded_text(review.get('reviewer'), 200)
        if not all(review.get(key) is True for key in ('text_checked', 'boundaries_checked', 'speaker_checked', 'clean_speech')):
            raise AppError('Accepted clips need explicit transcript, timing, speaker and clean-speech review.')
        for identity in ('book_id', 'chapter_id', 'source_id', 'recording_id'):
            require_id(row.get(identity))
        performance = require_id(row.get('performance'))
        if performance == 'unreviewed' or row.get('delivery') not in DELIVERIES:
            raise AppError('Reviewed performance and delivery labels are required.')
        reviewed_cues = review.get('performance_cues', [])
        allowed_cues = {'section_heading', 'enumeration', 'trailing_ellipsis',
                        'internal_ellipsis', 'excited_speech',
                        'explicit_excited_attribution', 'mechanical_voice', 'stunned_reaction'}
        if not isinstance(reviewed_cues, list) or len(set(map(str, reviewed_cues))) != len(reviewed_cues) or any(
                not isinstance(cue, str) or cue not in allowed_cues for cue in reviewed_cues):
            raise AppError('Invalid audio-reviewed performance cue.')
        if reviewed_cues and review.get('performance_checked') is not True:
            raise AppError('Performance cues require review against the source audiobook.')
        recording = select_recording(corpus, row.get('recording_id'))
        if recording['book_id'] != row.get('book_id') or recording['sha256'] != row.get('recording_sha256'):
            raise AppError('Clip recording does not match its book or frozen audio.')
        key = (row.get('book_id'), row.get('chapter_id'))
        if key not in chapter_cache:
            _, chapter = select_chapter(root, corpus, *key)
            chapter_cache[key] = {block['id']: block for block in chapter['blocks']}
        source = chapter_cache[key].get(row.get('source_id'))
        if source is not None and 'knigolub.net' in source['text'].casefold():
            raise AppError('Website watermark cannot be accepted as audiobook training text.')
        a, b = row.get('text_start'), row.get('text_end')
        if source is None or type(a) is not int or type(b) is not int or not 0 <= a < b <= len(source['text']):
            raise AppError('Reviewed clip has invalid source offsets.')
        if row.get('source_text') != source['text'][a:b]:
            raise AppError('Reviewed source text differs from the preserved ebook passage.')
        spoken = bounded_text(row.get('spoken_text'), 4000)
        if [t[0] for t in tokens(spoken)] != [t[0] for t in tokens(row['source_text'])]:
            bounded_text(row.get('text_exception'), 2000)
        start = number(row.get('audio_start'), 'clip start', 0, recording['audio']['duration'])
        end = number(row.get('audio_end'), 'clip end', start, recording['audio']['duration'])
        if not 1 <= end - start <= 30:
            raise AppError('Reviewed clips must be between 1 and 30 seconds; split long performances before training.')
        intent = performance_plan({'text': row['source_text'], 'delivery': row['delivery'],
                                   'speaker': row.get('speaker', 'narrator'),
                                   'source_id': row['source_id'], 'kind': source.get('kind')})
        accepted.append({**row, 'spoken_text': spoken, 'performance': performance,
                         'performance_intent': intent,
                         'audio_start': start, 'audio_end': end})
    by_recording = defaultdict(list)
    for row in accepted:
        by_recording[row['recording_id']].append(row)
    for group in by_recording.values():
        previous = -1.
        for row in sorted(group, key=lambda item: item['audio_start']):
            if row['audio_start'] < previous:
                raise AppError('Overlapping reviewed clips would repeat training audio.')
            previous = row['audio_end']
    return accepted


@contextlib.contextmanager
def decoded_recording(source: Path, duration: float, parent: Path, ffmpeg: str):
    with tempfile.TemporaryDirectory(prefix='.decoded-recording-', dir=parent) as temp:
        path = Path(temp) / 'recording.wav'
        command = [ffmpeg, '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0', '-vn',
                   '-map_metadata', '-1', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le',
                   '-rf64', 'auto', '-f', 'wav', str(path)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AppError('Could not decode the source recording within the preparation budget.') from error
        if result.returncode:
            raise AppError('FFmpeg rejected the source recording: ' + result.stderr.decode(errors='replace')[-1000:])
        info = inspect_reference(path)
        if info['sample_rate'] != 24000 or info['channels'] != 1 or abs(info['duration'] - duration) > .05:
            raise AppError('Decoded recording does not match the frozen source duration or required audio format.')
        yield path


def cut_audio(source: Path, output: Path, start: float, end: float, ffmpeg: str):
    command = [ffmpeg, '-nostdin', '-v', 'error', '-ss', f'{start:.9f}', '-accurate_seek', '-i', str(source),
               '-t', f'{end-start:.9f}', '-map', '0:a:0', '-vn', '-map_metadata', '-1',
               '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', '-f', 'wav', str(output)]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AppError('Local clip extraction failed or exceeded its time limit.') from error
    if result.returncode:
        raise AppError('FFmpeg rejected a training clip: ' + result.stderr.decode(errors='replace')[-1000:])
    info = inspect_reference(output)
    if info['sample_rate'] != 24000 or info['channels'] != 1 or abs(info['duration'] - (end - start)) > .025:
        raise AppError('Extracted clip duration or audio format is wrong.')
    return {**info, 'sha256': file_digest(output)}


def build_dataset(corpus_path: Path, reviews: list[Path], output: Path, reference_id: str,
                  performance: str, heldout_books=(), heldout_chapters=(), ffmpeg=None):
    root, corpus = load_corpus(corpus_path)
    performance = require_id(performance)
    rows = checked_rows(root, corpus, [row for path in reviews for row in rows_read(path)])
    rows = [row for row in rows if row['performance'] == performance]
    book_ids = {entry['id'] for entry in corpus['books']}
    if set(heldout_books) - book_ids or not heldout_books and not heldout_chapters:
        raise AppError('Name at least one complete held-out book or chapter for evaluation.')
    chapter_keys = set()
    for item in heldout_chapters:
        if not isinstance(item, str) or ':' not in item:
            raise AppError('Held-out chapter format is book-id:chapter-id.')
        key = tuple(item.split(':', 1))
        select_chapter(root, corpus, *key)
        chapter_keys.add(key)
    is_heldout = lambda row: row['book_id'] in heldout_books or (row['book_id'], row['chapter_id']) in chapter_keys
    train, validation = [row for row in rows if not is_heldout(row)], [row for row in rows if is_heldout(row)]
    if not train or not validation:
        raise AppError('Both training and held-out data need reviewed clips of the selected performance.')
    reference = next((row for row in train if row['id'] == reference_id), None)
    if reference is None:
        raise AppError('The constant speaker reference must be a reviewed TRAINING clip, never held-out audio.')
    normalize = lambda text: ' '.join(t[0] for t in tokens(text))
    if {normalize(row['spoken_text']) for row in train} & {normalize(row['spoken_text']) for row in validation}:
        raise AppError('Identical normalized transcripts cross the training/evaluation split.')
    executable = ffmpeg or shutil.which('ffmpeg')
    if not executable:
        raise AppError('Local FFmpeg is required to prepare training clips.')
    with new_directory(output) as stage:
        (stage / 'clips').mkdir()
        metadata, split_rows = [], {'train': [], 'validation': []}
        conditional_rows = {'train': [], 'validation': []}
        hashes = {}
        groups = defaultdict(list)
        for split, group in (('train', train), ('validation', validation)):
            for row in group:
                groups[row['recording_id']].append((split, row))
        decoded = {}
        for recording_id, group in groups.items():
            recording = select_recording(corpus, recording_id)
            with decoded_recording(owned_file(root, recording['path']), recording['audio']['duration'], stage, executable) as canonical:
                for split, row in group:
                    relative = f'clips/{row["id"]}.wav'
                    info = cut_audio(canonical, stage / relative, row['audio_start'], row['audio_end'], executable)
                    if info['sha256'] in hashes:
                        raise AppError('Duplicate decoded clips would contaminate the dataset.')
                    hashes[info['sha256']] = split
                    decoded[row['id']] = (relative, info)
        for split, group in (('train', train), ('validation', validation)):
            for row in group:
                relative, info = decoded[row['id']]
                split_rows[split].append({'audio': relative, 'text': row['spoken_text'],
                                         'ref_audio': f'clips/{reference_id}.wav'})
                if row['review'].get('performance_checked') is True:
                    conditional_rows[split].append({'audio': relative,
                        'text': row['spoken_text'], 'delivery': row['delivery'],
                        'performance_cues': row['review'].get('performance_cues', []),
                        'speaker': row.get('speaker', 'narrator')})
                metadata.append({**row, 'split': split, 'audio': relative, 'decoded_audio': info})
        for split, data in split_rows.items():
            rows_write(stage / f'{split}.raw.jsonl', data)
            rows_write(stage / f'{split}.conditional.jsonl', conditional_rows[split])
        rows_write(stage / 'provenance.jsonl', metadata)
        load_corpus(root)
        cue_coverage = {split: dict(sorted((cue, sum(cue in row['review'].get('performance_cues', [])
                                               for row in group))
                                             for cue in ('section_heading', 'enumeration',
                                                         'trailing_ellipsis', 'internal_ellipsis',
                                                         'excited_speech', 'explicit_excited_attribution',
                                                         'mechanical_voice', 'stunned_reaction')))
                        for split, group in (('train', train), ('validation', validation))}
        manifest = seal_json(stage, 'dataset.json', {'schema': 1, 'corpus_revision': corpus['revision'],
                    'narrator': corpus['narrator'], 'rights': corpus['rights'], 'performance': performance,
                    'reference_id': reference_id, 'reference_text': reference['spoken_text'],
                    'heldout_books': list(heldout_books), 'heldout_chapters': sorted(':'.join(key) for key in chapter_keys),
                    'training_clips': len(train), 'validation_clips': len(validation),
                    'training_seconds': sum(decoded[row['id']][1]['duration'] for row in train),
                    'validation_seconds': sum(decoded[row['id']][1]['duration'] for row in validation),
                    'audio_reviewed_cue_coverage': cue_coverage,
                    'conditional_clips': {split: len(items) for split, items in conditional_rows.items()},
                    'backend': 'qwen3-tts-official-single-speaker',
                    'limitations': 'Official single-speaker SFT ignores conditional sidecars and does not train instruction control; reviewed labels are retained for a compatible future trainer.'})
    return {key: value for key, value in manifest.items() if key != 'files'}
