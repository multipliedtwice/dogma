"""Read-only second pitch estimate and cached neural comparisons for warned WAVs."""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np
import librosa
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from book1_pitch_contour import pitch_contour

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'training-work/character-rb1'
OUT = BASE / 'warned-audio-review-20260929'
snapshot = json.loads((OUT / 'snapshot.json').read_text(encoding='utf-8'))
sources = json.loads((BASE / 'fine-tune-all/paragraph-sources.json').read_text(encoding='utf-8'))['sources']
rows = [json.loads(line) for line in (BASE / 'emotion2vec-benchmark-20260929/benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines()]
cache = BASE / 'emotion2vec-benchmark-20260929/features-b318240bfe67'
decisions = snapshot['review']['decisions']
aliases = json.loads((ROOT / 'training/voice_families.json').read_text(encoding='utf-8')).get('legacy_aliases', {})
mood_aliases = {'exhausted':'tired','wistful':'reminiscent','uncertain':'hesitant','cheerful, excited':'excited'}
def labels(row):
    d = decisions.get(row['id'], {})
    v = (d.get('confirmed_voice_id') or row['base_voice_id_proposed']) if d.get('voice_verdict') == 'yes' else d.get('corrected_voice_id')
    m = ', '.join(row['mood_proposed'] or ['neutral']) if d.get('mood_verdict') == 'yes' else d.get('corrected_mood')
    return aliases.get(v, v), mood_aliases.get(m, m)

vectors = {}
for row in rows:
    file = cache / (row['exact_audio_sha256'] + '.npz')
    if file.exists():
        with np.load(file) as data:
            vector = data['features'].astype(float)
            vectors[row['id']] = vector / np.linalg.norm(vector)

results = []
for index, warning in enumerate(snapshot['warnings']):
    row, precision = warning['row'], warning['precision']
    path = ROOT / precision['audio']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == precision['audio_sha256']
    samples, rate = sf.read(path, dtype='float32')
    y = librosa.resample(samples, orig_sr=rate, target_sr=16000)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=256)[0]
    active = rms > max(float(rms.max())*.08, .0015)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=16000, hop_length=256)
    edges = np.flatnonzero(active)
    f0, voiced, probability = librosa.pyin(y, fmin=55, fmax=450, sr=16000, frame_length=1024,
                                         hop_length=256, fill_na=np.nan)
    valid = active & voiced & np.isfinite(f0)
    pitch = pitch_contour(times[valid], f0[valid], times[edges[0]], times[edges[-1]]+256/16000)
    pitch['median_pitch_hz'] = float(np.nanmedian(f0[valid])) if valid.any() else None
    pitch['pitch_range_semitones'] = float(12*np.log2(np.nanpercentile(f0[valid],90)/np.nanpercentile(f0[valid],10))) if valid.any() else None
    pitch['voiced_fraction_of_active_frames'] = float(valid.sum()/active.sum())
    pitch['median_voicing_probability'] = float(np.median(probability[valid])) if valid.any() else None
    width = min(.85, max(.35, (times[edges[-1]]+256/16000-times[edges[0]])*.2))
    pitch['beginning_voiced_frames'] = int((valid & (times <= times[edges[0]]+width)).sum())
    pitch['ending_voiced_frames'] = int((valid & (times >= times[edges[-1]]+256/16000-width)).sum())
    with np.load(cache / (precision['audio_sha256']+'.npz')) as data:
        vector = data['features'].astype(float); vector /= np.linalg.norm(vector)
        broad = sorted(zip(data['labels'].tolist(), data['scores'].tolist()), key=lambda pair:pair[1], reverse=True)[:3]
    nearest = []
    for peer in rows:
        voice, mood = labels(peer)
        d = decisions.get(peer['id'], {})
        if voice != warning['labels']['voice'] or not mood or peer['source_id'] == row['source_id'] or d.get('boundary_start') or d.get('boundary_end') or d.get('rejected'):
            continue
        similarity = float(np.dot(vector, vectors[peer['id']]))
        nearest.append({'id':peer['id'], 'mood':mood, 'similarity':similarity,
                        'text':peer['source_text'], 'audio':peer['exact_audio'], 'chapter_id':peer['chapter_id']})
    nearest.sort(key=lambda item:item['similarity'], reverse=True)
    # Keep different source paragraphs among nearby examples.
    chosen, seen = [], set()
    by_id = {peer['id']:peer for peer in rows}
    for peer in nearest:
        source = by_id[peer['id']]['source_id']
        if source not in seen:
            chosen.append(peer); seen.add(source)
        if len(chosen) == 5: break
    context = sources.get(row['source_id'], {})
    chapter_sources = [key for key in sources if sources[key]['chapter_id'] == row['chapter_id']]
    position = chapter_sources.index(row['source_id']) if row['source_id'] in chapter_sources else -1
    surrounding = [{ 'id':key, **sources[key]} for key in chapter_sources[max(0,position-1):position+2]] if position>=0 else []
    siblings = [{'id':peer['id'], 'text':peer['source_text'], 'labels':labels(peer), 'acoustics':peer['acoustics'],
                 'audio':peer['exact_audio']} for peer in rows if peer['source_id']==row['source_id'] and peer['id']!=row['id']]
    results.append({**warning, 'second_pitch':pitch, 'generic_emotion_scores':broad,
                    'neural_neighbors':chosen, 'context':context, 'surrounding':surrounding, 'same_paragraph':siblings})
    (OUT/'measurements.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{index+1}/{len(snapshot['warnings'])} {row['id']} pyin median={pitch['median_pitch_hz']:.1f} Hz", flush=True)
print('Done. Saved measurements.json; no corpus files changed.', flush=True)
