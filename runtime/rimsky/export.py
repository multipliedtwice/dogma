from __future__ import annotations
import html
from contextlib import ExitStack, nullcontext
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import wave
import zipfile
from fractions import Fraction
from pathlib import Path
from .audio_source import open_audio_pair
from .books import ABBREVIATIONS
from .packs import resolve_binary
from .store import speaker_review_holds
from .integrity import accepted_audio, chapter_selection, lexical_text, validate_coverage, validate_cues
from .util import AppError, file_digest, now, uid

CAPTION_CHARACTERS = 42
CAPTION_SECONDS = 7.0
CAPTION_MINIMUM = 1.0
SENTENCE_END = re.compile(r'[.!?…]+["»”’)\]]*$')
CLAUSE_END = re.compile(r'[,;:—–]["»”’)\]]*$')
CLAUSE_START = re.compile(r'[—–«„“(\[]')
ENCODER_FLOOR = 1800
ENCODER_CEILING = 6 * 3600
ENCODER_REALTIME = 10
EXPORT_ORPHANED = 'The generation was deleted during export. No export was published.'


def timestamp(seconds: float, srt=False) -> str:
    milliseconds=round(seconds*1000)
    hours,milliseconds=divmod(milliseconds,3600000)
    minutes,milliseconds=divmod(milliseconds,60000)
    seconds,milliseconds=divmod(milliseconds,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{"," if srt else "."}{milliseconds:03}'

def spoken_words(spoken, cues):
    clusters = []
    for index, char in enumerate(spoken):
        if clusters and unicodedata.category(char)[0] == 'M':
            clusters[-1][1] = index + 1
        else:
            clusters.append([index, index + 1])
    letters = [lexical_text(spoken[start:end]) for start, end in clusters]
    sizes = [len(lexical_text(cue['text'])) for cue in cues]
    if ''.join(letters) != ''.join(lexical_text(cue['text']) for cue in cues):
        return None
    owners = [cluster for cluster, text in zip(clusters, letters) for _ in text]
    words, position, start = [], 0, None
    for cue, size in zip(cues, sizes):
        if not size:
            if words:
                words[-1]['end'] = cue['end']
            elif start is None:
                start = cue['start']
            continue
        span = (owners[position][0], owners[position + size - 1][1])
        position += size
        if words and span[0] < words[-1]['span'][1]:
            return None
        words.append({'start': cue['start'] if start is None else start, 'end': cue['end'], 'span': span})
        start = None
    if not words:
        return None
    bounds = [0]
    for previous, current in zip(words, words[1:]):
        gap = spoken[previous['span'][1]:current['span'][0]]
        bounds.append(previous['span'][1] + next((index for index, char in enumerate(gap) if char.isspace()), len(gap)))
    bounds.append(len(spoken))
    return [{'start': word['start'], 'end': word['end'], 'text': spoken[left:right]} for word, left, right in zip(words, bounds, bounds[1:])]

def caption_text(words):
    return ' '.join(''.join(word['text'] for word in words).split())

def caption_seconds(words):
    return words[-1]['end'] - words[0]['start']

def caption_fits(words):
    return len(caption_text(words)) <= CAPTION_CHARACTERS and caption_seconds(words) <= CAPTION_SECONDS

def caption_break(before, after):
    text = before[-1]['text'].rstrip()
    end = SENTENCE_END.search(text)
    word = re.search(r'[^\W\d_]*$', text[:end.start()]).group() if end and text[end.start()] == '.' else ''
    if len(word) == 1 and word != 'я' or word.lower() in ABBREVIATIONS:
        return -1
    if end and not after['text'].lstrip()[:1].islower():
        return 2
    return int(bool(CLAUSE_END.search(text) or CLAUSE_START.match(after['text'].lstrip()))
               and len(caption_text(before)) >= CAPTION_CHARACTERS // 3)

def caption_pair(left, right, later):
    words = [*left, *right]
    if caption_fits(words):
        return [words]
    cuts = [(caption_break(words[:cut], words[cut]), cut if later else -cut, cut) for cut in range(1, len(words))
            if all(caption_fits(part) and caption_seconds(part) >= CAPTION_MINIMUM for part in (words[:cut], words[cut:]))]
    score, _, cut = max(cuts, default=(-1, 0, 0))
    return [words[:cut], words[cut:]] if score >= 0 else None

def caption_lines(words):
    lines, current = [], []
    for word in words:
        while current and not caption_fits([*current, word]):
            following = [*current[1:], word]
            cut = max((caption_break(current[:index], following[index - 1]), index) for index in range(1, len(current) + 1))[1]
            lines.append(current[:cut])
            current = current[cut:]
        current.append(word)
    lines.append(current)
    settled = []
    for line in filter(None, lines):
        pair = settled and min(caption_seconds(settled[-1]), caption_seconds(line)) < CAPTION_MINIMUM and caption_pair(settled[-1], line, caption_seconds(line) < CAPTION_MINIMUM)
        if pair:
            settled[-1:] = pair
        else:
            settled.append(line)
    return [{'start': line[0]['start'], 'end': line[-1]['end'], 'text': caption_text(line)} for line in settled]

def segment_captions(segment):
    if not segment['cues']:
        return [{'start': 0, 'end': segment['duration'], 'text': segment['spoken']}]
    words = spoken_words(segment['spoken'], segment['cues']) or [{**cue, 'text': cue['text'] + ' '} for cue in segment['cues']]
    return caption_lines(words)

def captions(segments,srt=False,offset=0.0):
    lines=[] if srt else ['WEBVTT','']
    count=0
    for segment in segments:
        for cue in segment_captions(segment):
            count+=1
            text=cue['text'].replace('\r',' ').replace('\n',' ')
            lines.extend([str(count),f'{timestamp(offset+cue["start"],srt)} --> {timestamp(offset+cue["end"],srt)}',text if srt else html.escape(text, quote=False),''])
        offset+=segment['duration']
    return '\n'.join(lines)

def metadata_escape(text):
    for char in ('\\','=',';','#','\n'):
        text=text.replace(char,'\\'+char)
    return text

def concat_list(path: Path, segments, root, stop=None):
    lines=[]
    for segment in segments:
        check_export_stop(stop)
        wav=root/'audio'/f'{segment["audio_hash"]}.wav'
        meta=wav.with_suffix('.json')
        if not wav.is_file() or not meta.is_file():
            raise AppError('A saved audio segment is missing.')
        if json.loads(meta.read_text('utf-8')).get('sha256')!=file_digest(wav):
            raise AppError('Saved audio checksum mismatch. Create a new generation with the same pack to repair missing or corrupt passages.')
        name=str(wav).replace('\\','/').replace("'","'\\''")
        lines.append(f"file '{name}'")
    path.write_text('\n'.join(lines)+'\n','utf-8')

def check_export_stop(stop):
    if stop is not None and stop.is_set():
        raise AppError('Export cancelled. No incomplete output was published.', 'export_cancelled')


def encoder_timeout(segments):
    seconds = float(sum((Fraction(segment['samples'], segment['sample_rate']) for segment in segments), Fraction(0)))
    return min(ENCODER_CEILING, max(ENCODER_FLOOR, ENCODER_FLOOR / 3 + seconds / ENCODER_REALTIME))


def run_encoder(command, stop=None, timeout=ENCODER_FLOOR):
    check_export_stop(stop)
    if stop is None:
        return subprocess.run(command, capture_output=True, timeout=timeout)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_export_stop(stop)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    output, error = process.communicate(timeout=min(.2, remaining))
                    check_export_stop(stop)
                    return subprocess.CompletedProcess(command, process.returncode, output, error)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()


def chapter_timeline(book, segments, opening=None):
    offset = Fraction(opening['samples'], opening['sample_rate']) if opening else Fraction(0)
    output = [{'title': 'Dogma', 'start': 0, 'end': round(offset * 1000)}] if opening else []
    for chapter in book['chapters']:
        items = [segment for segment in segments if segment['chapter_id'] == chapter['id']]
        if items:
            start = offset
            offset += sum((Fraction(segment['samples'], segment['sample_rate']) for segment in items), Fraction(0))
            output.append({'title': chapter['title'], 'start': round(start * 1000), 'end': round(offset * 1000)})
    return output


def place_music(items, overlays, offset=0.0):
    """Position music layers on the speech timeline of `items`.

    Music never shifts speech: layers are mixed under it from their anchor
    segment's start, with their scene window on the same clock. Beds and
    ambience stop with the speech; only ending music may run past the final
    words, and that tail is returned so verification can expect it.
    """
    starts, offset = {}, Fraction(str(offset))
    for segment in items:
        starts[segment['id']] = offset
        offset += Fraction(segment['samples'], segment['sample_rate'])
    placed = []
    for item in overlays:
        if item['anchor_segment_id'] not in starts:
            continue
        base = float(starts[item['anchor_segment_id']])
        window = item.get('window')
        absolute_transition=lambda w:{**w,'start':base+w['start'],'end':base+w['end']}
        placed.append({**item, 'at': base + item['start'],
                       **({'transition':absolute_transition(item['transition'])} if item.get('transition') else {}),
                       **({'transitions':[absolute_transition(w) for w in item['transitions']]} if item.get('transitions') else {}),
                       'window': [base + window[0], base + window[1], window[2]] if window else None})
    tail = max([0.0] + [item['at'] + item['duration'] - float(offset) for item in placed if item.get('kind') == 'ending'])
    return placed, tail, float(offset)


MUSIC_RATE = 24000
MUSIC_END_FADE = 1.5


def mix_music(placed, speech, tail, destination, stop=None):
    """Render every placed layer into one mono 24 kHz track: speech length plus `tail`.

    Bed and ambience layers follow their scene window and fade out over the
    last MUSIC_END_FADE seconds of speech; ending music plays out in full."""
    import numpy as np
    import soundfile as sf
    from .music import window_gain
    from .narration_transitions import envelope,POLICY as TRANSITION_POLICY
    sources = {}

    def source(path):
        if path not in sources:
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            mono = audio.mean(axis=1)
            if rate == 2 * MUSIC_RATE:
                mono = (mono[0:len(mono) // 2 * 2:2] + mono[1:len(mono) // 2 * 2:2]) / 2
            elif rate != MUSIC_RATE:
                raise AppError('Music layer has an unexpected sample rate; no export was published.')
            sources[path] = mono
        return sources[path]

    total, end = round((speech + tail) * MUSIC_RATE), round(speech * MUSIC_RATE)
    block = 60 * MUSIC_RATE
    with sf.SoundFile(destination, 'w', MUSIC_RATE, 1, 'PCM_16') as out:
        for first in range(0, total, block):
            check_export_stop(stop)
            last = min(total, first + block)
            mixed = np.zeros(last - first, 'float32')
            for item in placed:
                begin = round(item['at'] * MUSIC_RATE)
                data = source(item['path'])
                finish = begin + len(data) if item.get('kind') == 'ending' else min(begin + len(data), end)
                low, high = max(begin, first), min(finish, last)
                if low >= high:
                    continue
                piece = data[low - begin:high - begin] * item['gain']
                if item.get('kind') == 'transition':
                    t = np.arange(low, high) / MUSIC_RATE
                    piece=piece*envelope(item['transition'],t)
                elif item.get('kind') != 'ending':
                    t = np.arange(low, high) / MUSIC_RATE
                    piece = piece * window_gain(item['window'], t) * np.clip((speech - t) / MUSIC_END_FADE, 0, 1)
                    for transition in item.get('transitions',[]):
                        piece*=1-np.clip(envelope(transition,t)/TRANSITION_POLICY['speech_gain'],0,1)
                mixed[low - first:high - first] += piece
            out.write(np.clip(mixed, -1, 1))


def music_filter(first_input):
    """FFmpeg filter graph mixing mono speech [0:a] with the pre-mixed music track."""
    return f'[0:a][{first_input}:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]'


def verify_encoded_audio(path, segments, codec, probe, stop=None, chapters=None, extra=0.0):
    command = [probe, '-v', 'error', '-show_streams', '-show_chapters', '-show_format', '-of', 'json', str(path)]
    try:
        result = run_encoder(command, stop)
    except subprocess.TimeoutExpired as error:
        raise AppError('Export verification timed out. No incomplete output was published.') from error
    if result.returncode:
        raise AppError('Encoded audio could not be inspected; no export was published.')
    try:
        details = json.loads(result.stdout)
        streams = [stream for stream in details['streams'] if stream.get('codec_type') == 'audio']
        if len(streams) != 1:
            raise ValueError('expected one audio stream')
        stream = streams[0]
        duration = float(stream.get('duration', details.get('format', {}).get('duration', 'nan')))
        expected = float(sum((Fraction(segment['samples'], segment['sample_rate']) for segment in segments), Fraction(0))) + extra
        if (not math.isfinite(duration) or duration <= 0 or duration < expected - .005 or duration > expected + .15
                or stream['codec_name'] != codec or int(stream['sample_rate']) != 24000 or stream['channels'] != 1):
            raise ValueError('audio format or duration does not match the saved segments')
        if chapters is not None:
            actual = details.get('chapters', [])
            if len(actual) != len(chapters):
                raise ValueError('chapter count does not match')
            for item, wanted in zip(actual, chapters):
                start, end = float(item['start_time']), float(item['end_time'])
                if (not math.isfinite(start) or not math.isfinite(end) or start >= end
                        or abs(start - wanted['start'] / 1000) > .003
                        or abs(end - wanted['end'] / 1000) > .003
                        or item.get('tags', {}).get('title') != wanted['title']):
                    raise ValueError('chapter title or timing does not match')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise AppError('Encoded audio does not match the complete saved-audio manifest. No export was published.') from error
    check_export_stop(stop)


def capture_audio(source: Path, destination: Path, audio, metadata):
    # A pathname can be replaced after admission. Link only if it still names
    # our captured file; otherwise copy from the retained handle, never reopen it.
    linked = False
    try:
        os.link(source, destination)
        original, candidate = os.fstat(audio.fileno()), destination.stat()
        linked = bool(original.st_ino and candidate.st_ino and os.path.samestat(original, candidate))
    except OSError:
        pass
    if not linked:
        destination.unlink(missing_ok=True)
        audio.seek(0)
        with destination.open('wb') as target:
            shutil.copyfileobj(audio, target, 1024 * 1024)
    metadata.seek(0)
    destination.with_suffix('.json').write_bytes(metadata.read())


def freeze_audio(snapshot: Path):
    info = accepted_audio(snapshot)
    if info['reset_cues']:
        raise AppError('Saved audio repair is incomplete. Resume preparation before exporting.')
    return info


def normalize_opening(source, destination, rate, ffmpeg, stop=None):
    """Match the speech PCM format before using FFmpeg's concat demuxer.

    The approved branding master may be stereo. A separate temporary conversion
    avoids interpreting later mono speech as stereo or changing its playback rate.
    """
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
               '-i', str(source), '-vn', '-ac', '1', '-ar', str(rate),
               '-c:a', 'pcm_s16le', str(destination)]
    try:
        result = run_encoder(command, stop)
    except subprocess.TimeoutExpired as error:
        raise AppError('Branded opening conversion timed out. No export was published.') from error
    if result.returncode:
        raise AppError('Branded opening conversion failed. No export was published.')
    try:
        with wave.open(str(destination), 'rb') as audio:
            info = {'samples': audio.getnframes(), 'sample_rate': audio.getframerate()}
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or info['sample_rate'] != rate:
                raise ValueError('unexpected converted opening format')
        info['duration'] = info['samples'] / info['sample_rate']
        with wave.open(str(source), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
        if abs(info['duration'] - duration) > 1 / rate:
            raise ValueError('converted opening duration changed')
    except (OSError, wave.Error, ValueError) as error:
        raise AppError('Branded opening conversion is invalid. No export was published.') from error
    info['sha256'] = file_digest(destination)
    destination.with_suffix('.json').write_text(json.dumps(info), 'utf-8')
    return info


def build_export(store,gid,format,chapters,binaries: Path,stop=None,guard=nullcontext,music=None):
    from .opening import opening_hashes
    with store.lock,store.transaction():
        selected=store.generation(gid)
        current=store.current_generation(selected['book_id'])
        gid=current['id']if current else gid
        snapshot=store.playback_generation(gid)
        hashes={s['audio_hash']for s in snapshot['segments']if s['audio_hash']}
        hashes.update(opening_hashes(snapshot))
        layers=music(snapshot['segments'])if music else []
        hashes.update(Path(row['path']).stem for row in layers)
        lease=store.audio_lease(hashes)
        lease.__enter__()
    try:
        return _build_export(store,gid,format,chapters,binaries,stop,guard,(lambda _:layers)if music else None,snapshot)
    finally:lease.__exit__(None,None,None)


def _build_export(store,gid,format,chapters,binaries: Path,stop,guard,music,snapshot):
    from .opening import select_opening, verify_opening_audio
    check_export_stop(stop)
    with tempfile.TemporaryDirectory(dir=store.root/'work') as work:
        folder = Path(work)
        with guard():
            with store.lock:
                generation = snapshot
                book = store.book(generation['book_id'])
            selected = chapter_selection(book, chapters, generation['config']['chapters'])
            first_chapter = chapter_selection(book, None, generation['config']['chapters'])[0]
            opening = select_opening(generation)
            if first_chapter not in selected:
                opening = None
            segments = [dict(s) for s in generation['segments'] if s['chapter_id'] in selected]
            validate_coverage(book, selected, segments)
            holds = speaker_review_holds(generation['segments'], generation['analysis'].get('speaker_resolution', []), generation['analysis'].get('speaker_verdicts'))[1]
            pending = {s['id'] for s in generation['segments'] if s['status'] == 'review'} | holds
            pending.difference_update(s['id']for s in generation['segments']if s.get('replacement_status')and s['status']=='ready'and s['audio_hash'])
            held = len({s['id'] for s in segments} & pending)
            if held:
                raise AppError(f'{held} held passage(s) still need review. A complete export was not made; held text is never omitted or narrated as accepted audio.', 'held_segments', held=held)
            if any(s['status'] != 'ready' or not s['audio_hash'] for s in segments):
                raise AppError('Every segment in the selected chapters must be ready before export.')
            if format not in ('m4b', 'mp3', 'chapters', 'vtt', 'srt'):
                raise AppError('Unsupported export format.')
            (folder/'audio').mkdir()
            opening_source = None
            if opening:
                check_export_stop(stop)
                source = store.root/'audio'/f'{opening["audio_hash"]}.wav'
                opening_source = folder/'opening-master.wav'
                with open_audio_pair(source) as (audio, metadata):
                    capture_audio(source, opening_source, audio, metadata)
            grouped = {}
            for segment in segments:
                grouped.setdefault(segment['audio_hash'], []).append(segment)
            for key, expected in grouped.items():
                check_export_stop(stop)
                source = store.root/'audio'/f'{key}.wav'
                # Bound live handles to one WAV/metadata pair, even for a whole book.
                # A repair of an as-yet uncaptured segment must not attach its new
                # audio to the old cue rows. Capture those rows and handles together.
                with ExitStack() as resources:
                    with store.lock:
                        audio, metadata = resources.enter_context(open_audio_pair(source))
                    capture_audio(source, folder/'audio'/f'{key}.wav', audio, metadata)
                check_export_stop(stop)
        opening_info = verify_opening_audio(opening_source, opening) if opening else None
        opening_duration = opening_info['duration'] if opening_info else 0.0
        rates = set()
        snapshots = {}
        for segment in segments:
            check_export_stop(stop)
            key = segment['audio_hash']
            if key not in snapshots:
                snapshots[key] = freeze_audio(folder/'audio'/f'{key}.wav')
            info = snapshots[key]
            segment['duration'] = info['duration']
            segment['samples'] = info['samples']
            segment['sample_rate'] = info['sample_rate']
            rates.add(info['sample_rate'])
            if segment['cues']:
                try:
                    segment['cues'] = validate_cues(segment['cues'], segment['spoken'], info['duration'])
                except AppError:
                    segment['cues'] = []
        if len(rates) > 1 and format not in ('vtt', 'srt'):
            raise AppError('This generation contains incompatible sample rates; audio was not concatenated with incorrect timing.')
        export_id = uid()
        suffix = 'zip' if format == 'chapters' else format
        name = f'{export_id}.{suffix}'
        output = store.root/'exports'/name
        temporary = folder/f'completed.{suffix}'
        if format in ('vtt', 'srt'):
            temporary.write_text(captions(segments, format == 'srt', opening_duration), 'utf-8')
        else:
            ffmpeg = resolve_binary('ffmpeg', binaries)
            if not ffmpeg:
                raise AppError('FFmpeg is missing from the local runtime.')
            probe = resolve_binary('ffprobe', binaries)
            if not probe:
                raise AppError('FFprobe is missing; encoded audio cannot be verified before export.')
            opening_item = None
            if opening:
                converted = folder/'audio'/'opening.wav'
                info = normalize_opening(opening_source, converted, next(iter(rates)), ffmpeg, stop)
                opening_item = {'audio_hash': 'opening', **info}
            listing = folder/'audio.txt'
            # Music takes are captured now, like speech, and only when every take
            # for the selection is ready; otherwise the export is speech only.
            overlays, copies = [], {}
            for item in music(segments) if music else []:
                check_export_stop(stop)
                if item['path'] not in copies:
                    copies[item['path']] = folder/f'music-{len(copies)}.wav'
                    shutil.copyfile(item['path'], copies[item['path']])
                overlays.append({**item, 'path': str(copies[item['path']])})
            def encode(items, destination, chapter_metadata=None, title=None):
                prefix = opening_item if opening_item and items[0]['chapter_id'] == first_chapter else None
                encoded_items = [prefix, *items] if prefix else items
                concat_list(listing, encoded_items, folder, stop)
                placed, tail, speech = place_music(items, overlays, prefix['duration'] if prefix else 0)
                command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                           '-f', 'concat', '-safe', '0', '-i', str(listing)]
                if chapter_metadata:
                    command += ['-f', 'ffmetadata', '-i', str(chapter_metadata), '-map_metadata', '1', '-map_chapters', '1']
                if placed:
                    track = folder/'music-track.wav'
                    mix_music(placed, speech, tail, track, stop)
                    command += ['-i', str(track), '-filter_complex', music_filter(2 if chapter_metadata else 1), '-map', '[mixed]']
                command += ['-vn', '-ac', '1', '-ar', '24000', '-metadata', 'title=' + (title or book['title']),
                            '-metadata', 'artist=' + book['author']]
                command += ['-c:a','aac','-b:a','80k','-movflags','+faststart','-f','ipod'] if format == 'm4b' else ['-c:a','libmp3lame','-b:a','96k','-f','mp3']
                command += [str(destination)]
                try:
                    result = run_encoder(command, stop, timeout=encoder_timeout(encoded_items))
                except subprocess.TimeoutExpired as error:
                    raise AppError('FFmpeg export timed out. No incomplete output was published.') from error
                concat_list(listing, encoded_items, folder, stop)
                if result.returncode or not destination.is_file() or destination.stat().st_size < 128:
                    raise AppError('FFmpeg export failed: ' + result.stderr.decode(errors='replace')[:500])
                verify_encoded_audio(destination, encoded_items, 'aac' if format == 'm4b' else 'mp3', probe, stop,
                                     chapter_timeline(book, items, prefix) if chapter_metadata else None, extra=tail)
            if format == 'chapters':
                with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_STORED) as archive:
                    for index, chapter in enumerate(book['chapters']):
                        items = [s for s in segments if s['chapter_id'] == chapter['id']]
                        if not items:
                            continue
                        file = folder/f'{index+1:03}.mp3'
                        encode(items, file, title=chapter['title'])
                        archive.write(file, file.name)
                        archive.writestr(f'{index+1:03}.vtt', captions(items, offset=opening_duration if opening and chapter['id'] == first_chapter else 0))
                    archive.writestr('chapters.json', json.dumps([
                        {'number': i+1, 'title': c['title']} for i, c in enumerate(book['chapters'])
                        if c['id'] in selected and any(s['chapter_id'] == c['id'] for s in segments)
                    ], ensure_ascii=False, indent=2))
            else:
                metadata = folder/'chapters.ffmeta'
                lines = [';FFMETADATA1', 'title='+metadata_escape(book['title']), 'artist='+metadata_escape(book['author'])]
                for entry in chapter_timeline(book, segments, opening_item):
                    lines.extend(['[CHAPTER]', 'TIMEBASE=1/1000', f'START={entry["start"]}',
                                  f'END={entry["end"]}', 'title='+metadata_escape(entry['title'])])
                metadata.write_text('\n'.join(lines)+'\n', 'utf-8')
                encode(segments, temporary, metadata if format == 'm4b' else None)
        if not temporary.is_file() or not temporary.stat().st_size:
            raise AppError('Export did not produce a file.')
        if opening:
            verify_opening_audio(opening_source, opening)
        with guard(), store.lock:
            check_export_stop(stop)
            if not store.rows('SELECT id FROM generations WHERE id=?', (gid,)):
                raise AppError(EXPORT_ORPHANED)
            size = temporary.stat().st_size
            os.replace(temporary, output)
            try:
                with store.transaction():
                    store.db.execute('INSERT INTO exports VALUES(?,?,?,?,?)', (export_id,gid,name,format,now()))
                    store.db.execute('INSERT INTO export_snapshots VALUES(?,?)',(export_id,json.dumps({
                        'generation_id':gid,'revision':snapshot.get('revision'),'pending_updates':snapshot.get('pending_replacements',0),
                        'parts':[{k:s[k]for k in ('id','source_id','start','end','audio_hash')}for s in segments]},ensure_ascii=False)))
            except Exception:
                output.unlink(missing_ok=True)
                raise
    return {'id':export_id,'file':name,'format':format,'size':size,'title':book['title'],
            'source_revision':snapshot.get('revision'),'pending_updates':snapshot.get('pending_replacements',0)}
