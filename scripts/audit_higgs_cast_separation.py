"""Hash-bound, offline measurement of reference and rendered character voices.

Loads only the existing Qwen speaker encoder on CPU. Higgs remains the speech
engine. This report never changes casting, protected labels or published audio.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.util import file_digest, write_json, digest
from rimsky.voice_separation import separation_report


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--characters', nargs='+', default=['vlad', 'estir', 'german', 'mozes', 'loko', 'gundahar', 'vel'])
    parser.add_argument('--samples', type=int, default=8)
    args = parser.parse_args()
    if args.samples < 2:
        parser.error('--samples must be at least two')
    folder = args.folder.resolve()
    out = folder / 'cast-separation'
    out.mkdir(exist_ok=True)
    import numpy as np
    import soundfile as sf
    import librosa
    import torch
    from safetensors import safe_open
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSSpeakerEncoderConfig
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSSpeakerEncoder, mel_spectrogram

    model = ROOT / 'training-work/models/Qwen3-TTS-12Hz-0.6B-Base'
    config = read(model / 'config.json')
    files = {p.name: file_digest(p) for p in [model / 'config.json', *sorted(model.glob('*.safetensors'))]}
    signature = digest({'model_files': files, 'implementation': file_digest(Path(__file__)),
                        'diagnostics': file_digest(ROOT / 'runtime/rimsky/voice_separation.py'), 'device': 'cpu'})
    torch.set_num_threads(4)
    encoder = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**config['speaker_encoder_config']))
    state = {}
    for path in model.glob('*.safetensors'):
        with safe_open(path, framework='pt', device='cpu') as reader:
            for name in reader.keys():
                if name.startswith('speaker_encoder.'):
                    state[name.removeprefix('speaker_encoder.')] = reader.get_tensor(name).float()
    encoder.load_state_dict(state, strict=True)
    encoder.eval()
    del state
    cache_path = out / 'features.json'
    cache = read(cache_path) if cache_path.exists() else {}
    if cache.get('encoder_sha256') != signature:
        cache = {'encoder_sha256': signature, 'audio': {}}

    def feature(path, expected_hash):
        if file_digest(path) != expected_hash:
            raise ValueError('Audio bytes changed: ' + str(path))
        if expected_hash in cache['audio']:
            return cache['audio'][expected_hash]
        audio, sr = sf.read(path, dtype='float32', always_2d=True)
        mono = audio.mean(axis=1)
        y = librosa.resample(mono, orig_sr=sr, target_sr=24000) if sr != 24000 else mono
        with torch.inference_mode():
            mel = mel_spectrogram(torch.from_numpy(y).unsqueeze(0), n_fft=1024,
                                  num_mels=128, sampling_rate=24000, hop_size=256,
                                  win_size=1024, fmin=0, fmax=12000).transpose(1, 2)
            vector = encoder(mel)[0].numpy().astype(float)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        item = {'audio_sha256': expected_hash, 'seconds': len(mono) / sr, 'embedding': vector.tolist()}
        cache['audio'][expected_hash] = item
        write_json(cache_path, cache)
        return item

    plans = {}
    candidates = defaultdict(list)
    co_scene = defaultdict(set)
    for master in sorted((folder / 'audio-v4').glob('*/master')):
        path = master / 'render-plan.json'
        if not path.exists():
            continue
        plan = read(path)
        receipt = read(master / 'receipts.json')
        if receipt['plan_sha256'] != file_digest(path):
            raise ValueError('Master receipt no longer matches plan')
        plans[str(path.relative_to(folder))] = file_digest(path)
        for u in plan['units']:
            key = u['speaker']
            if u['mode'] != 'speech' or key not in args.characters:
                continue
            take = receipt['takes'][u['id']]
            if take['key'] != u['key']:
                raise ValueError('Take synthesis key changed')
            co_scene[(u['chapter_id'], str(u.get('scene')))].add(key)
            if take.get('duration', 0) >= 2 and (u.get('delivery_profile') or {}).get('technique', 'ordinary') == 'ordinary':
                candidates[key].append((master, u, take))
    entries = []
    references = {}
    for key in sorted(candidates):
        rows = candidates[key]
        # Deterministic spread across chapters and passages, not repeated takes.
        indices = np.linspace(0, len(rows) - 1, min(args.samples, len(rows)), dtype=int)
        for index in indices:
            master, unit, take = rows[index]
            measured = feature(master / take['audio'], take['audio_sha256'])
            ref = unit['synthesis']['reference']
            references[ref['sha256']] = feature(ROOT / ref['audio'], ref['sha256'])
            entries.append({'character': key, 'unit': unit['id'], 'text': unit['text'],
                            'family': unit['render_voice'], 'reference_sha256': ref['sha256'], **measured})
            print('MEASURED', key, unit['id'], flush=True)
    groups = {key: [e['embedding'] for e in entries if e['character'] == key] for key in args.characters}
    report = separation_report(groups)
    for pair in report['pairs']:
        pair['shared_scenes'] = sum(set(pair['characters']).issubset(people) for people in co_scene.values())
        a, b = pair['characters']
        ar = {e['reference_sha256'] for e in entries if e['character'] == a}
        br = {e['reference_sha256'] for e in entries if e['character'] == b}
        pair['same_reference'] = bool(ar & br)
        pair['reference_cosine_distance'] = min(
            float(1 - np.dot(references[x]['embedding'], references[y]['embedding'])) for x in ar for y in br)
    report.update({'schema': 1, 'source_plans': plans, 'encoder_sha256': signature, 'model_files': files,
                   'examples': entries, 'references': references,
                   'missing_characters': sorted(set(args.characters) - set(candidates)),
                   'method': 'CPU Qwen speaker encoder; normalized cosine distance; generated utterances versus reference voices; leave-one-utterance-out centroids.',
                   'limits': ['Diagnostic only; no perceptual separation threshold is calibrated.',
                              'Speaker embeddings can merge different performed voices of the same actor.',
                              'Content and delivery affect embeddings; compare multiple utterances.',
                              'No casting, labels or audio changed.']})
    write_json(out / 'report.json', report)
    print(json.dumps({'characters': report['characters'], 'closest_pairs': report['pairs'][:6]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
