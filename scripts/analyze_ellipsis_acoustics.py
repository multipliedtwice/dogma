"""Compare ellipsis cadence in the source audiobook and Higgs draft.

ASR timings are approximate; this is an acoustic screening report, not a
perceptual listening score or a replacement for narrator review.
"""
from __future__ import annotations

import json
import math
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.store import Store

GENERATION = 'd25eec1c040193e489281531'
OUTPUT = ROOT / 'training-work/higgs-tts-3-pilot/ellipsis-acoustics.json'


def words(text: str) -> list[str]:
    return re.findall(r'[\u0400-\u04ffa-z0-9]+', text.casefold().replace('\u0451', '\u0435'))


def match_last_word(text: str, transcript: list[dict]):
    target = words(text)[-5:]
    if not target:
        return None
    width = len(target)
    matches = []
    for index in range(width - 1, len(transcript)):
        sample = [words(item['text'])[0] if words(item['text']) else ''
                  for item in transcript[index-width+1:index+1]]
        score = SequenceMatcher(None, ' '.join(target), ' '.join(sample)).ratio()
        matches.append((score, index))
    score, index = max(matches)
    return (index, score) if score >= .8 else None


def source_audio(path: Path, start: float, end: float):
    with sf.SoundFile(path) as file:
        rate = file.samplerate
        file.seek(max(0, round(start * rate)))
        samples = file.read(round((end - start) * rate), dtype='float32', always_2d=True)
    mono = samples.mean(axis=1)
    return librosa.resample(mono, orig_sr=rate, target_sr=16000)


def generated_audio(path: Path):
    samples, rate = sf.read(path, dtype='float32')
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    return librosa.resample(samples, orig_sr=rate, target_sr=16000)


def end_of_speech(audio: np.ndarray, sr=16000) -> float:
    hop = 160
    rms = librosa.feature.rms(y=audio, frame_length=400, hop_length=hop, center=True)[0]
    if not len(rms):
        return 0.0
    threshold = max(float(np.max(rms)) * .035, .001)
    active = np.flatnonzero(rms > threshold)
    return min(len(audio) / sr, float(active[-1] * hop / sr)) if len(active) else 0.0


def contour(audio: np.ndarray, sr=16000):
    if len(audio) < round(.15 * sr):
        return {'pitch_change_semitones': None, 'loudness_change_db': None}
    hop = 160
    f0 = librosa.yin(audio, fmin=65, fmax=360, sr=sr, frame_length=1024, hop_length=hop)
    rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=hop)[0]
    frames = min(len(f0), len(rms))
    if frames < 6:
        return {'pitch_change_semitones': None, 'loudness_change_db': None}
    f0, rms = f0[:frames], rms[:frames]
    usable = (f0 > 65) & (f0 < 360) & (rms > max(float(np.max(rms))*.1, .001))
    first, last = slice(0, max(1,frames//3)), slice(max(1,2*frames//3),frames)
    def median_pitch(section):
        values = f0[section][usable[section]]
        return float(np.median(values)) if len(values) else None
    low, high = median_pitch(first), median_pitch(last)
    pitch = round(12*math.log2(high/low), 2) if low and high else None
    volume = round(20*math.log10((float(np.mean(rms[last]))+1e-8)/
                                   (float(np.mean(rms[first]))+1e-8)),2)
    return {'pitch_change_semitones': pitch, 'loudness_change_db': volume}


def main():
    store = Store(ROOT / 'training-work/book1-listening-app')
    try:
        generation = store.generation(GENERATION)
        transcripts = {}
        rows = []
        for segment in generation['segments']:
            if (segment['status'] != 'ready' or not segment['audio_hash'] or
                    not segment['text'].rstrip().endswith(('\u2026', '...'))):
                continue
            recording_index = int(segment['chapter_id'][1:]) - 4
            if recording_index < 0:
                continue
            key = f'{recording_index:02d}'
            if key not in transcripts:
                path = ROOT / f'training-work/full-book-aligned-recovered/rb1-{key}/transcript.json'
                if not path.is_file():
                    continue
                transcripts[key] = json.loads(path.read_text(encoding='utf-8'))['words']
            transcript = transcripts[key]
            match = match_last_word(segment['text'], transcript)
            if not match:
                continue
            index, score = match
            last = transcript[index]
            mp3s = list((ROOT / 'content/reality-benders-1').glob(key + '_*.mp3'))
            if len(mp3s) != 1:
                continue
            next_start = (transcript[index+1]['start'] if index+1 < len(transcript)
                          else sf.info(mp3s[0]).duration)
            wav = store.root / 'audio' / f"{segment['audio_hash']}.wav"
            if not wav.is_file():
                continue
            source_start = float(last['start'])
            source_end = float(last['end'])
            original = source_audio(mp3s[0], source_start, source_end)
            generated = generated_audio(wav)
            speech_end = end_of_speech(generated)
            final_window = generated[max(0, round((speech_end-.7)*16000)):
                                     round(speech_end*16000)]
            rows.append({'id': segment['id'], 'recording': key,
                         'last_word_source': last['text'], 'match_score': round(score,3),
                         'source_last_word_seconds': round(source_end-source_start,3),
                         'source_pause_after_seconds': round(max(0,next_start-source_end),3),
                         'generated_pause_after_seconds': round(max(0,len(generated)/16000-speech_end),3),
                         'source_final_word': contour(original),
                         'generated_final_window': contour(final_window)})
        OUTPUT.write_text(json.dumps({'schema':1,'cases':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'cases':len(rows),'report':str(OUTPUT),
                          'sample':rows[:12]},ensure_ascii=False,indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
