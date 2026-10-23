"""Screen saved labels against exact source WAVs without changing reviews.

Audio measurements find unusual examples; they do not confirm an emotional
label. Text is displayed for identification only and never enters scoring.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from book1_pitch_contour import CONTOUR_SCHEMA, pitch_contour

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'training-work/character-rb1/fine-tune-all'
OUT = ROOT / 'training-work/character-rb1/label-audit-20260929'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def average_level_dbfs(mono):
    """Whole-clip RMS recording level, including pauses; not perceptual LUFS."""
    import numpy as np
    samples = np.asarray(mono, dtype=np.float64)
    if not samples.size or not np.isfinite(samples).all():
        raise ValueError('Invalid samples for average volume')
    return round(float(10 * np.log10(max(float(np.mean(samples * samples)), 1e-18))), 2)


def main():
    OUT.mkdir(exist_ok=True)
    raw_review = (BOARD / 'review-draft.json').read_bytes()
    review = json.loads(raw_review)
    candidates_hash = digest(BOARD / 'candidates.jsonl')
    if review['candidates_sha256'] != candidates_hash:
        raise ValueError('Review does not match candidates')
    (OUT / 'review-snapshot.json').write_bytes(raw_review)
    rows = [json.loads(line) for line in (BOARD / 'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    status = json.loads((BOARD / 'precision-v2/status.json').read_text(encoding='utf-8'))
    quality = json.loads((BOARD / 'quality-screen.json').read_text(encoding='utf-8'))
    for data in (status, quality):
        if data['candidates_sha256'] != candidates_hash:
            raise ValueError('Stale audio screen')
    taxonomy = json.loads((ROOT / 'training/voice_families.json').read_text(encoding='utf-8'))
    voices = {family['id'] for family in taxonomy['families']}
    aliases = taxonomy.get('legacy_aliases', {})
    mood_aliases = {'exhausted': 'tired', 'wistful': 'reminiscent',
                    'uncertain': 'hesitant', 'cheerful, excited': 'excited'}
    failures = {'missing_or_changed_audio', 'unexpected_pcm_format', 'empty_audio',
                'duration_mismatch', 'clipping', 'level_outlier'}
    selected, held = [], []
    for row in rows:
        decision = review['decisions'].get(row['id'])
        if not decision:
            continue
        voice = (decision.get('confirmed_voice_id') or row['base_voice_id_proposed']
                 if decision.get('voice_verdict') == 'yes' else decision.get('corrected_voice_id'))
        voice = aliases.get(voice, voice)
        mood = (', '.join(row['mood_proposed'] or ['neutral'])
                if decision.get('mood_verdict') == 'yes' else decision.get('corrected_mood'))
        mood = mood_aliases.get(mood, mood)
        precision = status['clips'].get(row['id'], {})
        screen = quality['clips'].get(row['id'], {})
        if (voice not in voices or not mood or precision.get('status') != 'precision_pass'
                or decision.get('rejected') or decision.get('boundary_start') or decision.get('boundary_end')
                or row['role'] == 'mixed' or screen.get('audio_sha256') != row['audio_sha256']
                or failures.intersection(screen.get('reasons', []))):
            held.append({'id': row['id'], 'source_id': row['source_id'],
                         'precision_status': precision.get('status'),
                         'boundary_start': bool(decision.get('boundary_start')),
                         'boundary_end': bool(decision.get('boundary_end'))})
            continue
        selected.append({**row, 'voice_family': voice, 'mood': mood,
                         'precision_audio': precision['audio'],
                         'precision_audio_sha256': precision['audio_sha256']})
    print(f'Audio audit: {len(selected)} usable labeled WAVs, {len(held)} held.', flush=True)

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import butter, find_peaks, sosfilt
    from scipy.ndimage import gaussian_filter1d
    from safetensors import safe_open
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSSpeakerEncoderConfig
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSSpeakerEncoder, mel_spectrogram

    torch.set_num_threads(4)
    model_dir = ROOT / 'training-work/models/Qwen3-TTS-12Hz-0.6B-Base'
    config = json.loads((model_dir / 'config.json').read_text(encoding='utf-8'))
    encoder = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**config['speaker_encoder_config']))
    state = {}
    for path in model_dir.glob('*.safetensors'):
        with safe_open(path, framework='pt', device='cpu') as reader:
            for name in reader.keys():
                if name.startswith('speaker_encoder.'):
                    state[name.removeprefix('speaker_encoder.')] = reader.get_tensor(name).float()
    encoder.load_state_dict(state, strict=True)
    encoder.eval()
    del state
    print('Loaded only local speaker encoder on CPU; no TTS generation.', flush=True)
    cache_path = OUT / 'audio-features.jsonl'
    cache = {item['audio_sha256']: item for item in
             (json.loads(line) for line in cache_path.read_text(encoding='utf-8').splitlines())} if cache_path.exists() else {}
    measurements = []
    hop, rate, frame = 256, 16000, 1024
    band = butter(4, [300, 3000], btype='bandpass', fs=rate, output='sos')
    with cache_path.open('a', encoding='utf-8') as output:
        for index, row in enumerate(selected):
            path = (ROOT / row['precision_audio']).resolve()
            if not path.is_relative_to((BOARD / 'precision-v2/clips').resolve()):
                raise ValueError('Unexpected precision audio path')
            if digest(path) != row['precision_audio_sha256']:
                raise ValueError(f'Changed WAV: {row["id"]}')
            feature = cache.get(row['precision_audio_sha256'])
            previous_feature = feature
            if feature is None or feature.get('contour_schema') != CONTOUR_SCHEMA:
                audio, sr = sf.read(path, dtype='float32', always_2d=True)
                mono = audio.mean(axis=1)
                y = librosa.resample(mono, orig_sr=sr, target_sr=rate) if sr != rate else mono
                rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
                db = 20 * np.log10(np.maximum(rms, 1e-9))
                active = rms > max(float(np.max(rms)) * .08, .0015)
                indices = np.flatnonzero(active)
                if len(indices) < 5:
                    raise ValueError(f'Insufficient audio: {row["id"]}')
                first, last = indices[0], indices[-1]
                speech_seconds = max(.1, (last - first + 1) * hop / rate)
                pause_fraction = float(1 - active[first:last + 1].mean())
                # YIN estimates are screened by waveform periodicity as well as level.
                f0 = librosa.yin(y, fmin=55, fmax=450, sr=rate, frame_length=frame, hop_length=hop)
                frames = librosa.util.frame(np.pad(y, (frame//2, frame//2)), frame_length=frame, hop_length=hop).T
                voiced_pitch, voiced_times = [], []
                for i in indices:
                    if i >= len(f0) or i >= len(frames):
                        continue
                    lag = int(round(rate / f0[i]))
                    signal = frames[i] - frames[i].mean()
                    if 1 <= lag < frame // 2:
                        left, right = signal[:-lag], signal[lag:]
                        correlation = float(np.dot(left, right) / max(1e-10, np.linalg.norm(left)*np.linalg.norm(right)))
                        if correlation >= .65 and 57 < f0[i] < 440:
                            voiced_pitch.append(float(f0[i]))
                            voiced_times.append(float(i*hop/rate))
                pitch = np.asarray(voiced_pitch)
                contour = pitch_contour(voiced_times,voiced_pitch,first*hop/rate,(last+1)*hop/rate)
                # Envelope peaks are a text-independent pace proxy, not syllable truth.
                envelope = np.sqrt(np.mean(librosa.util.frame(
                    np.pad(sosfilt(band, y)**2, (160,160)), frame_length=320, hop_length=160), axis=0))
                envelope = gaussian_filter1d(envelope, 2)
                peaks, _ = find_peaks(envelope, distance=10, prominence=max(float(envelope.max())*.09, .001))
                if previous_feature and previous_feature.get('speaker_embedding'):
                    embedding = np.asarray(previous_feature['speaker_embedding'])
                else:
                    a24 = librosa.resample(mono, orig_sr=sr, target_sr=24000) if sr != 24000 else mono
                    with torch.inference_mode():
                        mel = mel_spectrogram(torch.from_numpy(a24).unsqueeze(0), n_fft=1024,
                            num_mels=128, sampling_rate=24000, hop_size=256, win_size=1024,
                            fmin=0, fmax=12000).transpose(1,2)
                        embedding = encoder(mel)[0].numpy()
                embedding = embedding / max(float(np.linalg.norm(embedding)), 1e-9)
                feature = {'id': row['id'], 'audio_sha256': row['precision_audio_sha256'],
                           'duration_seconds': round(len(mono)/sr, 3),
                           'speech_span_seconds': round(speech_seconds, 3),
                           'median_pitch_hz': round(float(np.median(pitch)), 2) if len(pitch)>=8 else None,
                           'pitch_range_semitones': round(float(12*np.log2(np.percentile(pitch,90)/np.percentile(pitch,10))),2) if len(pitch)>=8 else None,
                           'active_level_dbfs': round(float(np.median(db[active])),2),
                           'average_level_dbfs': average_level_dbfs(mono),
                           'level_range_db': round(float(np.percentile(db[active],90)-np.percentile(db[active],10)),2),
                           'internal_pause_fraction': round(pause_fraction,4),
                           'envelope_peaks_per_second': round(len(peaks)/speech_seconds,3),
                           'periodic_fraction': round(len(pitch)/len(indices),4),
                           **contour,
                           'speaker_embedding': embedding.tolist()}
                output.write(json.dumps(feature)+'\n'); output.flush()
            elif 'average_level_dbfs' not in feature:
                # Enrich the cache without repeating pitch or speaker encoding.
                audio, _ = sf.read(path, dtype='float32', always_2d=True)
                feature = {**feature, 'average_level_dbfs': average_level_dbfs(audio.mean(axis=1))}
                output.write(json.dumps(feature)+'\n'); output.flush()
            cache[row['precision_audio_sha256']] = feature
            measurements.append({**row, 'acoustics': {k:v for k,v in feature.items() if k not in ('speaker_embedding','id','audio_sha256')},
                                 '_embedding': np.array(feature['speaker_embedding'])})
            if (index+1)%100 == 0 or index+1 == len(selected):
                print(f'Measured {index+1}/{len(selected)} WAVs.', flush=True)

    by_voice, by_bucket = defaultdict(list), defaultdict(list)
    for row in measurements:
        by_voice[row['voice_family']].append(row)
        by_bucket[(row['voice_family'], row['mood'])].append(row)
    def normalize(v):
        return v / max(float(np.linalg.norm(v)), 1e-9)
    def center(group, exclude=None):
        paragraphs = defaultdict(list)
        for item in group:
            if item['source_id'] != exclude:
                paragraphs[item['source_id']].append(item['_embedding'])
        return normalize(np.mean([normalize(np.mean(items,axis=0)) for items in paragraphs.values()],axis=0)) if paragraphs else None
    voice_centers = {v: center(group) for v,group in by_voice.items()
                     if len({r['source_id'] for r in group}) >= 4}
    features = ['median_pitch_hz','pitch_range_semitones','active_level_dbfs',
                'level_range_db','internal_pause_fraction','envelope_peaks_per_second']
    floors = np.array([.8, 1.5, 2.0, 2.0, .08, .6])
    def vector(row):
        values = [row['acoustics'][key] for key in features]
        if any(v is None for v in values):
            return None
        values[0] = 12*np.log2(values[0])
        return np.array(values)
    flags, nearest_correct = [], 0
    nearest_total = 0
    for row in measurements:
        reasons = []
        own = center(by_voice[row['voice_family']], row['source_id'])
        distances = {voice: float(1-np.dot(row['_embedding'], own if voice == row['voice_family'] else c))
                     for voice,c in voice_centers.items() if voice != row['voice_family'] or own is not None}
        nearest = min(distances, key=distances.get) if distances else None
        own_distance = float(1-np.dot(row['_embedding'],own)) if own is not None else None
        if row['voice_family'] in voice_centers and row['acoustics']['duration_seconds'] >= 3:
            nearest_total += 1
            nearest_correct += nearest == row['voice_family']
        if (nearest and nearest != row['voice_family'] and own_distance is not None
                and row['acoustics']['duration_seconds'] >= 3
                and own_distance - distances[nearest] > .08):
            reasons.append({'type':'voice_comparison','assigned_distance': round(own_distance,4),
                            'comparison_family':nearest,'comparison_distance':round(distances[nearest],4),
                            'distance_margin':round(own_distance-distances[nearest],4)})
        peers = [x for x in by_bucket[(row['voice_family'],row['mood'])] if x['source_id'] != row['source_id']
                 and x['acoustics']['duration_seconds'] >= 3 and vector(x) is not None]
        v = vector(row)
        if len({x['source_id'] for x in peers})>=6 and v is not None and row['acoustics']['duration_seconds'] >= 3:
            # One vote per paragraph, so a long paragraph cannot dominate a bucket.
            paragraphs = defaultdict(list)
            for x in peers:
                paragraphs[x['source_id']].append(vector(x))
            values = np.array([np.median(items,axis=0) for items in paragraphs.values()])
            median = np.median(values,axis=0)
            scale = np.maximum(1.4826*np.median(np.abs(values-median),axis=0), floors)
            z = (v-median)/scale
            if np.sum(np.abs(z)>3)>=2 or np.max(np.abs(z))>5:
                reasons.append({'type':'bucket_acoustic_outlier','peer_paragraphs':len(paragraphs),
                                'differences':[{'feature':name,'robust_deviation':round(float(value),2),
                                                'observed':row['acoustics'][name],
                                                'bucket_median':round(float(2**(median[i]/12) if i==0 else median[i]),3)}
                                               for i,(name,value) in enumerate(zip(features,z)) if abs(value)>3]})
        if reasons:
            comparisons = sorted([x for x in by_bucket[(row['voice_family'],row['mood'])]
                                  if x['source_id'] != row['source_id'] and x['acoustics']['duration_seconds']>=3],
                                 key=lambda x: 1-float(np.dot(x['_embedding'],own)) if own is not None else x['id'])[:3]
            other = sorted(by_voice.get(nearest,[]) if nearest != row['voice_family'] else [],
                           key=lambda x: 1-float(np.dot(row['_embedding'],x['_embedding'])))[:2]
            flags.append({'id':row['id'],'source_id':row['source_id'],'voice_family':row['voice_family'],
                          'mood':row['mood'],'text':row['source_text'],'audio':row['precision_audio'],
                          'audio_sha256':row['precision_audio_sha256'],'acoustics':row['acoustics'],
                          'reasons':reasons,'same_bucket_comparisons':[x['id'] for x in comparisons],
                          'other_family_comparisons':[x['id'] for x in other]})
    def priority(flag):
        return max([reason.get('distance_margin',0)*30 for reason in flag['reasons']]+
                   [abs(d['robust_deviation']) for reason in flag['reasons'] for d in reason.get('differences',[])])
    flags.sort(key=priority,reverse=True)
    buckets = []
    for (voice,mood),group in sorted(by_bucket.items()):
        buckets.append({'voice_family':voice,'mood':mood,'clips':len(group),
                        'paragraphs':len({x['source_id'] for x in group}),
                        'seconds':round(sum(x['acoustics']['duration_seconds'] for x in group),2),
                        'medians':{key: round(float(np.median([x['acoustics'][key] for x in group if x['acoustics'][key] is not None])),3)
                                   if any(x['acoustics'][key] is not None for x in group) else None for key in features}})
    report = {'schema':1,'created_at':datetime.now(timezone.utc).isoformat(),
              'review_updated_at':review.get('updated_at'),'review_sha256':hashlib.sha256(raw_review).hexdigest(),
              'review_changed_during_audit':digest(BOARD/'review-draft.json') != hashlib.sha256(raw_review).hexdigest(),
              'candidates_sha256':candidates_hash,'labeled_clips':len(selected)+len(held),
              'audio_measured_clips':len(measurements),'held_clips':held,
              'voice_counts':dict(Counter(x['voice_family'] for x in measurements)),
              'mood_counts':dict(Counter(x['mood'] for x in measurements)),
              'embedding_leave_paragraph_out_nearest_family_agreement':round(nearest_correct/max(1,nearest_total),3),
              'embedding_evaluated_clips':nearest_total,'buckets':buckets,'flags':flags,
              'methods':{'voice':'Local Qwen speaker encoder, normalized embeddings, equal paragraph weighting, leave-paragraph-out comparison.',
                         'bucket':'Pitch, pitch range, energy, energy range, pause fraction, waveform envelope peak rate. Robust median/MAD with minimum scales and six peer paragraphs.',
                         'text':'Used only to display clip identity; not used in scoring.'},
              'limits':['No perceptual listening or emotion classifier; flags are comparison requests, never corrections.',
                        'Speaker embeddings may merge expressive voices of the same actor; nearest family is not a voice verdict.',
                        'Pitch estimates can contain octave errors; envelope peak rate is an approximate pace measure.',
                        'Recording effects, phonetic content and short duration can produce differences unrelated to mood.',
                        'Small buckets cannot support statistical consistency conclusions. No labels are overwritten.']}
    (OUT/'acoustic-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    public = [{k:v for k,v in x.items() if k != '_embedding'} for x in measurements]
    (OUT/'measured-labels.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in public),encoding='utf-8')
    feature_report = {'schema':1,'candidates_sha256':candidates_hash,
                      'created_at':report['created_at'],
                      'method':'Waveform measurements only; no text or emotion inference.',
                      'clips':{x['id']:{'audio_sha256':x['precision_audio_sha256'],**x['acoustics']} for x in measurements}}
    feature_path = BOARD/'audio-acoustics.json'
    temporary = feature_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(feature_report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(feature_path)
    print(json.dumps({'measured':len(measurements),'flags':len(flags),'nearest_family_agreement':report['embedding_leave_paragraph_out_nearest_family_agreement'],
                      'review_changed':report['review_changed_during_audit'],'report':str(OUT/'acoustic-report.json')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
