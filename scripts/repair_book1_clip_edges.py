"""Re-cut Book 1 sentence candidates at detected quiet gaps in frozen source MP3s.

This leaves candidate IDs and the user's review untouched. The new audio needs
an independent transcript check before it can enter training.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import wave
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'training-work/character-rb1/fine-tune-all'
CORPUS = ROOT / 'training-work/corpus-rb1-current'
OUTPUT = FOLDER / 'precision-v2'
SILENCE = re.compile(r'silence_(start|end):\s*([0-9.]+)')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def quiet_intervals(source: Path) -> list[tuple[float, float]]:
    command = ['ffmpeg', '-nostdin', '-v', 'info', '-i', str(source), '-map', '0:a:0',
               '-vn', '-af', 'silencedetect=noise=-35dB:d=0.12', '-f', 'null', 'NUL']
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'Silence detection failed: {source}: {result.stderr[-1000:]}')
    starts, intervals = [], []
    for kind, raw in SILENCE.findall(result.stderr):
        value = float(raw)
        if kind == 'start':
            starts.append(value)
        elif starts:
            start = starts.pop(0)
            if value - start >= .12:
                intervals.append((start, value))
    return intervals


def boundary(intervals: list[tuple[float, float]], target: float) -> float | None:
    # The aligner can place its word edge a little inside a real silent gap.
    # Limit correction so a distant pause cannot silently skip a spoken word.
    nearby = []
    for start, end in intervals:
        if start > target + .65:
            break
        if end < target - .65:
            continue
        middle = (start + end) / 2
        distance = abs(middle - target)
        if distance <= .65:
            nearby.append((distance, middle, start, end))
    if not nearby:
        return None
    _, middle, _, _ = min(nearby)
    return round(middle, 6)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recording', help='Process one recording for a pilot')
    args = parser.parse_args()
    candidates = FOLDER / 'candidates.jsonl'
    rows = [json.loads(line) for line in candidates.read_text(encoding='utf-8').splitlines() if line]
    corpus = json.loads((CORPUS / 'corpus.json').read_text(encoding='utf-8'))
    recordings = {row['id']: row for row in corpus['recordings']}
    grouped = defaultdict(list)
    for row in rows:
        if not args.recording or row['recording_id'] == args.recording:
            grouped[row['recording_id']].append(row)
    OUTPUT.mkdir(exist_ok=True)
    clips = OUTPUT / 'clips'
    clips.mkdir(exist_ok=True)
    checkpoint = OUTPUT / 'repairs.jsonl'
    completed = {}
    if checkpoint.is_file():
        for line in checkpoint.read_text(encoding='utf-8').splitlines():
            if line:
                item = json.loads(line)
                completed[item['id']] = item
    with checkpoint.open('a', encoding='utf-8') as stream:
        for recording_id, candidates_in_recording in grouped.items():
            todo = [row for row in candidates_in_recording if row['id'] not in completed]
            if not todo:
                continue
            recording = recordings[recording_id]
            source = CORPUS / recording['path']
            if digest(source) != recording['sha256']:
                raise ValueError(f'Frozen source changed: {recording_id}')
            quiet = quiet_intervals(source)
            with tempfile.TemporaryDirectory(prefix='rb1-precision-', dir=OUTPUT) as temporary:
                decoded = Path(temporary) / 'recording.wav'
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                                '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '24000',
                                '-c:a', 'pcm_s16le', str(decoded)], check=True)
                with wave.open(str(decoded), 'rb') as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                        raise ValueError(f'Unexpected decoded PCM: {recording_id}')
                    for row in todo:
                        start = boundary(quiet, row['audio_start'])
                        end = boundary(quiet, row['audio_end'])
                        result = {'id': row['id'], 'original_audio_sha256': row['audio_sha256'],
                                  'recording_id': recording_id, 'status': 'hold'}
                        if start is None or end is None or not 1 <= end - start <= 30:
                            result['reason'] = 'no_nearby_quiet_boundary'
                        else:
                            first, last = round(start * 24000), round(end * 24000)
                            if first < 0 or last > wav.getnframes() or last <= first:
                                result['reason'] = 'source_interval_invalid'
                            else:
                                wav.setpos(first)
                                audio = wav.readframes(last - first)
                                target = clips / f'{row["id"]}.wav'
                                with wave.open(str(target), 'wb') as out:
                                    out.setnchannels(1)
                                    out.setsampwidth(2)
                                    out.setframerate(24000)
                                    out.writeframes(audio)
                                result.update(status='recut_unverified', audio_start=start, audio_end=end,
                                              audio=str(target.relative_to(ROOT)), audio_sha256=digest(target),
                                              start_shift=round(start-row['audio_start'], 4),
                                              end_shift=round(end-row['audio_end'], 4))
                        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
                        stream.flush()
                        completed[row['id']] = result
            counts = Counter(completed[row['id']]['status'] for row in candidates_in_recording)
            print(json.dumps({'recording': recording_id, 'quiet_intervals': len(quiet),
                              'counts': dict(counts)}, ensure_ascii=True), flush=True)
    report = {'schema': 2, 'candidates_sha256': digest(candidates),
              'method': 'FFmpeg -35 dB, 120 ms quiet-gap midpoint; 650 ms maximum edge shift',
              'limit': 'Re-cut audio is unverified until independently transcribed; quiet gaps do not establish text accuracy.',
              'counts': dict(Counter(item['status'] for item in completed.values())),
              'clips': completed}
    (OUTPUT / 'repairs.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['counts'], ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
