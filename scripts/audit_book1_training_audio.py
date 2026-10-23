"""Conservatively screen Book 1 review clips without changing the review IDs."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'training-work/character-rb1/fine-tune-all'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def words(text: str) -> list[str]:
    import re
    return re.findall(r'[\w]+', text.casefold().replace('ё', 'е'))


def screen(row: dict) -> dict:
    path = ROOT / row['audio']
    failures = []
    if not path.is_file() or digest(path) != row['audio_sha256']:
        return {'id': row['id'], 'status': 'hold', 'reasons': ['missing_or_changed_audio']}
    with wave.open(str(path), 'rb') as wav:
        channels, width, rate, count = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if (channels, width, rate) != (1, 2, 24000):
            failures.append('unexpected_pcm_format')
        if count == 0:
            return {'id': row['id'], 'status': 'hold', 'reasons': ['empty_audio']}
        samples = struct.unpack('<' + str(count * channels) + 'h', wav.readframes(count))
    duration = count / rate
    if abs(duration - (row['audio_end'] - row['audio_start'])) > .06:
        failures.append('duration_mismatch')
    peak = max(abs(value) for value in samples) / 32768
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
    rms_dbfs = 20 * math.log10(max(rms, 1e-9))
    clipped_fraction = sum(abs(value) >= 32760 for value in samples) / len(samples)
    if peak >= .999 or clipped_fraction > .0001:
        failures.append('clipping')
    if rms_dbfs < -42 or rms_dbfs > -9:
        failures.append('level_outlier')
    edge_count = min(int(rate * .01), len(samples) // 4)
    edge_rms = max(math.sqrt(sum(value * value for value in edge) / edge_count)
                   for edge in (samples[:edge_count], samples[-edge_count:])) / 32768
    edge_to_rms = edge_rms / max(rms, 1e-9)
    if edge_to_rms > .75:
        failures.append('possible_cut_boundary')
    if row.get('flags') or row['role'] == 'mixed':
        failures.append('alignment_or_role_flag')
    source, asr = words(row['source_text']), words(row.get('asr_text') or '')
    ratio = SequenceMatcher(None, source, asr, autojunk=False).ratio() if asr else None
    if ratio is None or ratio < .88:
        failures.append('asr_text_uncertain')
    return {'id': row['id'], 'status': 'screen_pass' if not failures else 'hold',
            'reasons': failures, 'rms_dbfs': round(rms_dbfs, 2),
            'peak': round(peak, 4), 'clipped_fraction': round(clipped_fraction, 6),
            'edge_to_rms': round(edge_to_rms, 3),
            'asr_text_ratio': round(ratio, 3) if ratio is not None else None,
            'audio_sha256': row['audio_sha256']}


def main() -> None:
    candidates = FOLDER / 'candidates.jsonl'
    rows = [json.loads(line) for line in candidates.read_text(encoding='utf-8').splitlines() if line]
    results = [screen(row) for row in rows]
    output = {'schema': 1, 'candidates_sha256': digest(candidates),
              'method': 'PCM format, integrity, length, signal level, clipping, boundary energy, alignment flags, ASR text similarity',
              'limit': 'A screen pass is not proof of speaker identity, exact boundaries, or absence of music/noise.',
              'counts': dict(Counter(result['status'] for result in results)),
              'clips': {result.pop('id'): result for result in results}}
    (FOLDER / 'quality-screen.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name in ('fine-tune-candidates', 'fine-tune-expanded'):
        folder = FOLDER.parent / name
        source = folder / 'candidates.jsonl'
        ids = [json.loads(line)['id'] for line in source.read_text(encoding='utf-8').splitlines() if line]
        subset = {**output, 'candidates_sha256': digest(source),
                  'clips': {cid: output['clips'][cid] for cid in ids}}
        subset['counts'] = dict(Counter(item['status'] for item in subset['clips'].values()))
        (folder / 'quality-screen.json').write_text(json.dumps(subset, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(output['counts']))


if __name__ == '__main__':
    main()
