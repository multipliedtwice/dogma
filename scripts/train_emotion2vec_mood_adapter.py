"""Train and evaluate a custom audiobook-mood adapter for emotion2vec+.

The protected Book 1/2 evidence remains immutable.  The outer evaluation split
is chapter-separated and comes from the frozen assignment benchmark.  Book 2
examples are explicit corrections, so their training loss is down-weighted.

Stages:
  extract      Cache frame-level emotion2vec+ features for exact reviewed WAVs.
  frozen-head  Train an attention/statistics head with the encoder frozen.
  conditional-head
               Predict mood conditional on the reviewed voice family.  This
               models each family's performance palette without turning sparse
               voice/mood pairs into unrelated classes.
  relative-head
               Compare absolute emotion features with distance from the
               training-only neutral reference for the assigned voice family.
  conditional-last-blocks
               Fine-tune only the final encoder blocks for the conditional
               voice-family palette objective.
  last-blocks  Continue from the frozen head while unfreezing the last N blocks.

No stage changes reviews, corpus gates, production settings, or source audio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "training-work/assignment-benchmark/m1-20261012"
MODEL = ROOT / "training-work/models/emotion2vec-plus-base"
MODEL_REVISION = "b318240bfe67db81a8c572ecb37ce9c3759b81c9"
OUT = ROOT / "training-work/mood-adaptation/e2v-base-books12-v1"
FRAME_CACHE = OUT / "frame-features"
WAVE_CACHE = OUT / "wave-features"
TIMBRE_CACHE = OUT / "timbre-features"
SEED = 20261018
EMBED_DIM = 768
ACOUSTIC_KEYS = [
    "median_pitch_hz", "pitch_range_semitones", "active_level_dbfs",
    "average_level_dbfs", "level_range_db", "internal_pause_fraction",
    "envelope_peaks_per_second", "periodic_fraction",
    "pitch_start_offset_semitones", "pitch_end_offset_semitones",
    "pitch_ending_change_semitones",
]
CONTOUR_BINS = 12


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audio_path(row: dict) -> Path:
    if row["book"] == "reality-benders-1":
        return ROOT / "manual-labeling/reality-benders-1/audio" / (row["audio_sha256"] + ".wav")
    if row["book"] == "reality-benders-2-full":
        return ROOT / "training-work/character-rb2/full/clips" / (row["id"] + ".wav")
    raise ValueError("Unexpected evidence book: " + str(row.get("book")))


def load_evidence(verify_audio: bool = False) -> tuple[list[dict], dict, dict]:
    manifest_path = BENCHMARK / "evidence-manifest.json"
    manifest = read(manifest_path)
    evidence_path = BENCHMARK / "synthesis-evidence.jsonl"
    split_path = BENCHMARK / "split.json"
    if sha(evidence_path) != manifest["outputs"]["synthesis-evidence.jsonl"]:
        raise ValueError("Frozen synthesis evidence changed")
    if sha(split_path) != manifest["outputs"]["split.json"]:
        raise ValueError("Frozen chapter split changed")
    rows = jsonl(evidence_path)
    seen = set()
    for row in rows:
        key = (row["book"], row["id"])
        if key in seen:
            raise ValueError("Duplicate evidence row: " + repr(key))
        seen.add(key)
        if (row.get("mood_basis") != "human" or row.get("voice_basis") != "human"
                or row.get("boundary_held") or row.get("mood") in (None, "", "mood_pending")):
            raise ValueError("Non-human or held mood evidence: " + repr(key))
        path = audio_path(row)
        if not path.is_file():
            raise FileNotFoundError(path)
        if verify_audio and sha(path) != row["audio_sha256"]:
            raise ValueError("Exact audio changed: " + repr(key))
        row["audio"] = str(path)
    return rows, read(split_path), {
        "evidence_manifest_sha256": sha(manifest_path),
        "synthesis_evidence_sha256": sha(evidence_path),
        "split_sha256": sha(split_path),
        "model_files": {p.name: sha(p) for p in sorted(MODEL.iterdir()) if p.is_file()},
    }


def outer_partition(row: dict, split: dict) -> str:
    return ("test" if row["chapter_id"] in
            set(split["books"][row["book"]]["test_chapters"]) else "development")


def supported_labels(rows: list[dict], split: dict, minimum_clips: int = 10,
                     minimum_chapters: int = 3) -> list[str]:
    development = [row for row in rows if outer_partition(row, split) == "development"]
    counts = Counter(row["mood"] for row in development)
    chapters = defaultdict(set)
    for row in development:
        chapters[row["mood"]].add((row["book"], row["chapter_id"]))
    return sorted(label for label, count in counts.items()
                  if count >= minimum_clips and len(chapters[label]) >= minimum_chapters)


def inner_partition(rows: list[dict], split: dict, labels: list[str]) -> tuple[list[int], list[int], list[int]]:
    """Return train, validation, and untouched outer-test indices."""
    import numpy as np
    from sklearn.model_selection import StratifiedGroupKFold

    selected = [i for i, row in enumerate(rows) if row["mood"] in labels]
    development = [i for i in selected if outer_partition(rows[i], split) == "development"]
    outer_test = [i for i in selected if outer_partition(rows[i], split) == "test"]
    y = np.asarray([rows[i]["mood"] for i in development])
    groups = np.asarray([rows[i]["book"] + ":" + rows[i]["chapter_id"] for i in development])
    folds = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED)
    train_local, validation_local = next(folds.split(np.zeros(len(development)), y, groups))
    train = [development[i] for i in train_local]
    validation = [development[i] for i in validation_local]
    if ({(rows[i]["book"], rows[i]["chapter_id"]) for i in train}
            & {(rows[i]["book"], rows[i]["chapter_id"]) for i in validation}):
        raise AssertionError("Inner split leaked a chapter")
    return train, validation, outer_test


def metrics(actual, probabilities, labels: list[str]) -> dict:
    import numpy as np
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

    actual = np.asarray(actual)
    probability = np.asarray(probabilities)
    order = np.argsort(-probability, axis=1)
    predicted = np.asarray(labels)[order[:, 0]]
    return {
        "clips": int(len(actual)),
        "top1": float(accuracy_score(actual, predicted)),
        "top3": float(np.mean([truth in np.asarray(labels)[rank[:3]]
                               for truth, rank in zip(actual, order)])),
        "macro_f1": float(f1_score(actual, predicted, average="macro", zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
    }


def evaluate_probabilities(rows: list[dict], indices: list[int], probabilities, labels: list[str]) -> dict:
    import numpy as np

    result = {"all": metrics([rows[i]["mood"] for i in indices], probabilities, labels), "books": {}}
    probabilities = np.asarray(probabilities)
    for book in sorted({rows[i]["book"] for i in indices}):
        selected = [n for n, i in enumerate(indices) if rows[i]["book"] == book]
        result["books"][book] = metrics([rows[indices[n]]["mood"] for n in selected],
                                         probabilities[selected], labels)
    return result


def voice_metrics(rows: list[dict], indices: list[int], probabilities, labels: list[str],
                  minimum_clips: int = 5) -> dict:
    """Break out sufficiently represented families without leaking into training."""
    import numpy as np

    probabilities = np.asarray(probabilities)
    result = {}
    for voice in sorted({rows[i]["voice"] for i in indices}):
        selected = [n for n, i in enumerate(indices) if rows[i]["voice"] == voice]
        if len(selected) >= minimum_clips:
            result[voice] = metrics([rows[indices[n]]["mood"] for n in selected],
                                    probabilities[selected], labels)
    return result


def extract_frames(rows: list[dict], device: str, limit: int = 0) -> dict:
    import numpy as np
    import setuptools  # noqa: F401 - restores distutils compatibility on Python 3.12
    import torch
    from funasr import AutoModel

    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is unavailable")
    FRAME_CACHE.mkdir(parents=True, exist_ok=True)
    pending = [row for row in rows if not (FRAME_CACHE / (row["audio_sha256"] + ".npy")).is_file()]
    if limit:
        pending = pending[:limit]
    if not pending:
        return {"extracted": 0, "cached": len(rows)}
    model = AutoModel(model=str(MODEL), device=device, ncpu=4, disable_update=True,
                      disable_pbar=True, disable_log=True, log_level="ERROR")
    started = time.monotonic()
    for number, row in enumerate(pending, 1):
        path = Path(row["audio"])
        if sha(path) != row["audio_sha256"]:
            raise ValueError("Exact audio changed before extraction: " + row["id"])
        result = model.generate(input=str(path), granularity="frame", extract_embedding=True,
                                disable_pbar=True, disable_log=True)[0]
        features = np.asarray(result["feats"], dtype=np.float16)
        if features.ndim != 2 or features.shape[1] != EMBED_DIM or not np.isfinite(features).all():
            raise ValueError(f"Invalid frame features for {row['id']}: {features.shape}")
        destination = FRAME_CACHE / (row["audio_sha256"] + ".npy")
        temporary = destination.with_suffix(".npy.tmp")
        with temporary.open("wb") as stream:
            np.save(stream, features, allow_pickle=False)
        temporary.replace(destination)
        if number % 25 == 0 or number == len(pending):
            print(f"frames {number}/{len(pending)} elapsed={time.monotonic()-started:.1f}s", flush=True)
    return {"extracted": len(pending), "cached": len(rows)}


def _wave_summary_job(item):
    """Process-safe waveform/prosody extractor for one exact clip."""
    path_text, expected_hash = item
    import librosa
    import numpy as np
    import soundfile as sf
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import butter, find_peaks, sosfilt
    from book1_pitch_contour import pitch_contour

    path = Path(path_text)
    if sha(path) != expected_hash:
        raise ValueError("Exact audio changed during waveform extraction: " + str(path))
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    rate, frame, hop = 16000, 1024, 256
    y = librosa.resample(mono, orig_sr=sample_rate, target_sr=rate) if sample_rate != rate else mono
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    active = rms > max(float(rms.max()) * .08, .0015)
    indices = np.flatnonzero(active)
    if len(indices) < 5:
        raise ValueError("Insufficient active audio: " + str(path))
    first, last = int(indices[0]), int(indices[-1])
    span = max(.1, (last - first + 1) * hop / rate)
    f0 = librosa.yin(y, fmin=55, fmax=450, sr=rate, frame_length=frame, hop_length=hop)
    frames = librosa.util.frame(np.pad(y, (frame // 2, frame // 2)),
                                frame_length=frame, hop_length=hop).T
    pitch, times = [], []
    for index in indices:
        if index >= len(f0) or index >= len(frames):
            continue
        lag = int(round(rate / f0[index]))
        signal = frames[index] - frames[index].mean()
        if 1 <= lag < frame // 2:
            left, right = signal[:-lag], signal[lag:]
            correlation = float(np.dot(left, right) /
                                max(1e-10, np.linalg.norm(left) * np.linalg.norm(right)))
            if correlation >= .65 and 57 < f0[index] < 440:
                pitch.append(float(f0[index])); times.append(float(index * hop / rate))
    pitch = np.asarray(pitch)
    contour = pitch_contour(times, pitch.tolist(), first * hop / rate,
                            (last + 1) * hop / rate, bins=CONTOUR_BINS)
    band = butter(4, [300, 3000], btype="bandpass", fs=rate, output="sos")
    envelope = np.sqrt(np.mean(librosa.util.frame(
        np.pad(sosfilt(band, y) ** 2, (160, 160)), frame_length=320, hop_length=160), axis=0))
    envelope = gaussian_filter1d(envelope, 2)
    peaks, _ = find_peaks(envelope, distance=10,
                          prominence=max(float(envelope.max()) * .09, .001))
    average_level = float(10 * np.log10(max(float(np.mean(mono.astype(np.float64) ** 2)), 1e-18)))
    feature = {
        "median_pitch_hz": float(np.median(pitch)) if len(pitch) >= 8 else None,
        "pitch_range_semitones": (float(12 * np.log2(np.percentile(pitch, 90) /
                                                       np.percentile(pitch, 10)))
                                    if len(pitch) >= 8 else None),
        "active_level_dbfs": float(np.median(db[active])),
        "average_level_dbfs": average_level,
        "level_range_db": float(np.percentile(db[active], 90) - np.percentile(db[active], 10)),
        "internal_pause_fraction": float(1 - active[first:last + 1].mean()),
        "envelope_peaks_per_second": float(len(peaks) / span),
        "periodic_fraction": float(len(pitch) / len(indices)),
        **contour,
    }
    values = [feature.get(key) for key in ACOUSTIC_KEYS]
    values += feature.get("relative_pitch_curve_semitones", [None] * CONTOUR_BINS)
    return expected_hash, np.asarray([np.nan if value is None else value for value in values],
                                     dtype=np.float32)


def extract_wave_features(rows: list[dict], limit: int = 0, workers: int = 4) -> dict:
    """Cache exact-audio prosody features, reusing hash-matched prior measurements."""
    import numpy as np
    from concurrent.futures import ProcessPoolExecutor

    WAVE_CACHE.mkdir(parents=True, exist_ok=True)
    wanted = {row["audio_sha256"] for row in rows}
    reused = 0
    prior_files = [
        ROOT / "training-work/character-rb1/label-audit-20260929/audio-features.jsonl",
        ROOT / "training-work/character-rb2/full/audio-features.jsonl",
        ROOT / "training-work/character-rb2/pilot/audio-features.jsonl",
    ]
    for prior in prior_files:
        if not prior.is_file():
            continue
        with prior.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                audio_hash = item.get("audio_sha256")
                destination = WAVE_CACHE / (str(audio_hash) + ".npy")
                if audio_hash not in wanted or destination.is_file():
                    continue
                values = [item.get(key) for key in ACOUSTIC_KEYS]
                values += item.get("relative_pitch_curve_semitones", [None] * CONTOUR_BINS)
                vector = np.asarray([np.nan if value is None else value for value in values],
                                    dtype=np.float32)
                if vector.shape != (len(ACOUSTIC_KEYS) + CONTOUR_BINS,):
                    raise ValueError("Invalid prior waveform feature: " + str(audio_hash))
                with destination.with_suffix(".npy.tmp").open("wb") as output:
                    np.save(output, vector, allow_pickle=False)
                destination.with_suffix(".npy.tmp").replace(destination)
                reused += 1
    pending = [row for row in rows
               if not (WAVE_CACHE / (row["audio_sha256"] + ".npy")).is_file()]
    if limit:
        pending = pending[:limit]
    started = time.monotonic()
    jobs = [(row["audio"], row["audio_sha256"]) for row in pending]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for number, (audio_hash, vector) in enumerate(executor.map(_wave_summary_job, jobs), 1):
            destination = WAVE_CACHE / (audio_hash + ".npy")
            with destination.with_suffix(".npy.tmp").open("wb") as output:
                np.save(output, vector, allow_pickle=False)
            destination.with_suffix(".npy.tmp").replace(destination)
            if number % 25 == 0 or number == len(pending):
                print(f"wave {number}/{len(pending)} elapsed={time.monotonic()-started:.1f}s", flush=True)
    return {"extracted": len(pending), "reused": reused,
            "cached": sum((WAVE_CACHE / (row["audio_sha256"] + ".npy")).is_file()
                          for row in rows)}


def extract_timbre_features(rows: list[dict], device: str, limit: int = 0) -> dict:
    """Cache Qwen speaker/timbre embeddings for exact protected audio."""
    import numpy as np
    import soundfile as sf
    import torch
    import librosa
    from safetensors import safe_open
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSSpeakerEncoderConfig
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSSpeakerEncoder, mel_spectrogram

    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is unavailable")
    TIMBRE_CACHE.mkdir(parents=True, exist_ok=True)
    wanted = {row["audio_sha256"] for row in rows}
    reused = 0
    for prior in [ROOT / "training-work/character-rb1/label-audit-20260929/audio-features.jsonl",
                  ROOT / "training-work/character-rb2/full/audio-features.jsonl",
                  ROOT / "training-work/character-rb2/pilot/audio-features.jsonl"]:
        if not prior.is_file():
            continue
        with prior.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                audio_hash, embedding = item.get("audio_sha256"), item.get("speaker_embedding")
                destination = TIMBRE_CACHE / (str(audio_hash) + ".npy")
                if audio_hash not in wanted or not embedding or destination.is_file():
                    continue
                vector = np.asarray(embedding, dtype=np.float32)
                if vector.shape != (1024,) or not np.isfinite(vector).all():
                    raise ValueError("Invalid prior timbre feature: " + str(audio_hash))
                with destination.with_suffix(".npy.tmp").open("wb") as output:
                    np.save(output, vector, allow_pickle=False)
                destination.with_suffix(".npy.tmp").replace(destination); reused += 1
    pending = [row for row in rows
               if not (TIMBRE_CACHE / (row["audio_sha256"] + ".npy")).is_file()]
    if limit:
        pending = pending[:limit]
    if not pending:
        return {"extracted": 0, "reused": reused, "cached": len(rows)}
    model_path = ROOT / "training-work/models/Qwen3-TTS-12Hz-0.6B-Base"
    config = read(model_path / "config.json")
    encoder = Qwen3TTSSpeakerEncoder(
        Qwen3TTSSpeakerEncoderConfig(**config["speaker_encoder_config"]))
    state = {}
    for weights in model_path.glob("*.safetensors"):
        with safe_open(weights, framework="pt", device="cpu") as reader:
            for name in reader.keys():
                if name.startswith("speaker_encoder."):
                    state[name.removeprefix("speaker_encoder.")] = reader.get_tensor(name).float()
    encoder.load_state_dict(state, strict=True)
    encoder = encoder.eval().to(device); del state
    started = time.monotonic()
    for number, row in enumerate(pending, 1):
        path = Path(row["audio"])
        if sha(path) != row["audio_sha256"]:
            raise ValueError("Exact audio changed during timbre extraction: " + row["id"])
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        mono = audio.mean(axis=1)
        signal = librosa.resample(mono, orig_sr=sample_rate, target_sr=24000) \
            if sample_rate != 24000 else mono
        with torch.inference_mode():
            tensor = torch.from_numpy(signal).unsqueeze(0).to(device)
            mel = mel_spectrogram(tensor, n_fft=1024, num_mels=128, sampling_rate=24000,
                                  hop_size=256, win_size=1024, fmin=0, fmax=12000).transpose(1, 2)
            vector = encoder(mel)[0].float().cpu().numpy()
        vector /= max(float(np.linalg.norm(vector)), 1e-9)
        destination = TIMBRE_CACHE / (row["audio_sha256"] + ".npy")
        with destination.with_suffix(".npy.tmp").open("wb") as output:
            np.save(output, vector.astype(np.float32), allow_pickle=False)
        destination.with_suffix(".npy.tmp").replace(destination)
        if number % 25 == 0 or number == len(pending):
            print(f"timbre {number}/{len(pending)} elapsed={time.monotonic()-started:.1f}s", flush=True)
    return {"extracted": len(pending), "reused": reused,
            "cached": sum((TIMBRE_CACHE / (row["audio_sha256"] + ".npy")).is_file()
                          for row in rows)}


def torch_components():
    import numpy as np
    import torch
    from torch import nn
    from torch.utils.data import Dataset

    class FrameDataset(Dataset):
        def __init__(self, rows, indices, label_to_index):
            self.rows, self.indices, self.label_to_index = rows, indices, label_to_index

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, position):
            row = self.rows[self.indices[position]]
            path = FRAME_CACHE / (row["audio_sha256"] + ".npy")
            value = np.load(path, allow_pickle=False).astype(np.float32, copy=False)
            if value.ndim != 2 or value.shape[1] != EMBED_DIM:
                raise ValueError("Invalid cached frame feature: " + str(path))
            return torch.from_numpy(value), self.label_to_index[row["mood"]], row["book"]

    class AudioDataset(Dataset):
        def __init__(self, rows, indices, label_to_index):
            self.rows, self.indices, self.label_to_index = rows, indices, label_to_index

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, position):
            import soundfile as sf
            import torchaudio.functional as AF
            row = self.rows[self.indices[position]]
            audio, sample_rate = sf.read(row["audio"], dtype="float32", always_2d=True)
            signal = torch.from_numpy(audio.mean(axis=1))
            if sample_rate != 16000:
                signal = AF.resample(signal, sample_rate, 16000)
            signal = signal.clamp(-1, 1)
            return signal, self.label_to_index[row["mood"]], row["book"]

    class VoiceFrameDataset(Dataset):
        def __init__(self, rows, indices, label_to_index, voice_to_index):
            self.rows, self.indices = rows, indices
            self.label_to_index, self.voice_to_index = label_to_index, voice_to_index

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, position):
            row = self.rows[self.indices[position]]
            path = FRAME_CACHE / (row["audio_sha256"] + ".npy")
            value = np.load(path, allow_pickle=False).astype(np.float32, copy=False)
            if value.ndim != 2 or value.shape[1] != EMBED_DIM:
                raise ValueError("Invalid cached frame feature: " + str(path))
            return (torch.from_numpy(value), self.label_to_index[row["mood"]],
                    self.voice_to_index.get(row["voice"], 0), row["book"])

    class VoiceAudioDataset(Dataset):
        def __init__(self, rows, indices, label_to_index, voice_to_index):
            self.rows, self.indices = rows, indices
            self.label_to_index, self.voice_to_index = label_to_index, voice_to_index

        def __len__(self):
            return len(self.indices)

        def __getitem__(self, position):
            import soundfile as sf
            import torchaudio.functional as AF
            row = self.rows[self.indices[position]]
            audio, sample_rate = sf.read(row["audio"], dtype="float32", always_2d=True)
            signal = torch.from_numpy(audio.mean(axis=1))
            if sample_rate != 16000:
                signal = AF.resample(signal, sample_rate, 16000)
            return (signal.clamp(-1, 1), self.label_to_index[row["mood"]],
                    self.voice_to_index.get(row["voice"], 0), row["book"])

    class MoodHead(nn.Module):
        def __init__(self, classes, hidden=256):
            super().__init__()
            self.norm = nn.LayerNorm(EMBED_DIM)
            self.attention = nn.Sequential(nn.Linear(EMBED_DIM, 128), nn.Tanh(), nn.Linear(128, 1))
            self.classifier = nn.Sequential(nn.Linear(EMBED_DIM * 2, hidden), nn.GELU(),
                                            nn.Dropout(.2), nn.Linear(hidden, classes))

        def forward(self, frames, padding_mask=None):
            value = self.norm(frames.float())
            score = self.attention(value).squeeze(-1)
            if padding_mask is not None:
                score = score.masked_fill(padding_mask, torch.finfo(score.dtype).min)
            weight = torch.softmax(score, dim=1).unsqueeze(-1)
            mean = (weight * value).sum(dim=1)
            variance = (weight * (value - mean.unsqueeze(1)).square()).sum(dim=1).clamp_min(1e-6)
            return self.classifier(torch.cat([mean, variance.sqrt()], dim=1))

    class VoiceConditionedMoodHead(nn.Module):
        """Shared mood semantics with a low-rank, regularized voice-family adapter."""
        def __init__(self, classes, voices, hidden=256, voice_dim=48, voice_dropout=.2):
            super().__init__()
            self.voice_dropout = voice_dropout
            self.norm = nn.LayerNorm(EMBED_DIM)
            self.attention = nn.Sequential(nn.Linear(EMBED_DIM, 128), nn.Tanh(), nn.Linear(128, 1))
            self.acoustic = nn.Sequential(nn.Linear(EMBED_DIM * 2, hidden), nn.GELU(), nn.Dropout(.2))
            # Index zero is the unknown/shared family used for family dropout
            # during training and for a genuinely unseen production family.
            self.voice_embedding = nn.Embedding(voices + 1, voice_dim, padding_idx=0)
            self.film = nn.Linear(voice_dim, hidden * 2)
            self.classifier = nn.Linear(hidden, classes)
            nn.init.zeros_(self.film.weight)
            nn.init.zeros_(self.film.bias)

        def forward(self, frames, padding_mask, voices):
            value = self.norm(frames.float())
            score = self.attention(value).squeeze(-1)
            if padding_mask is not None:
                score = score.masked_fill(padding_mask, torch.finfo(score.dtype).min)
            weight = torch.softmax(score, dim=1).unsqueeze(-1)
            mean = (weight * value).sum(dim=1)
            variance = (weight * (value - mean.unsqueeze(1)).square()).sum(dim=1).clamp_min(1e-6)
            acoustic = self.acoustic(torch.cat([mean, variance.sqrt()], dim=1))
            conditioned_voices = voices
            if self.training and self.voice_dropout:
                drop = torch.rand_like(voices.float()) < self.voice_dropout
                conditioned_voices = voices.masked_fill(drop, 0)
            scale, shift = self.film(self.voice_embedding(conditioned_voices)).chunk(2, dim=1)
            adapted = acoustic * (1 + .25 * torch.tanh(scale)) + shift
            return self.classifier(adapted)

    return (np, torch, nn, FrameDataset, AudioDataset, VoiceFrameDataset,
            VoiceAudioDataset, MoodHead, VoiceConditionedMoodHead)


def collate_frames(batch):
    import torch
    frames, labels, books = zip(*batch)
    maximum = max(len(value) for value in frames)
    values = torch.zeros(len(frames), maximum, EMBED_DIM)
    mask = torch.ones(len(frames), maximum, dtype=torch.bool)
    for i, value in enumerate(frames):
        values[i, :len(value)] = value
        mask[i, :len(value)] = False
    return values, mask, torch.tensor(labels), list(books)


def collate_audio(batch):
    import torch
    signals, labels, books = zip(*batch)
    maximum = max(len(value) for value in signals)
    values = torch.zeros(len(signals), maximum)
    mask = torch.ones(len(signals), maximum, dtype=torch.bool)
    for i, value in enumerate(signals):
        values[i, :len(value)] = value
        mask[i, :len(value)] = False
    return values, mask, torch.tensor(labels), list(books)


def collate_voice_frames(batch):
    import torch
    frames, labels, voices, books = zip(*batch)
    maximum = max(len(value) for value in frames)
    values = torch.zeros(len(frames), maximum, EMBED_DIM)
    mask = torch.ones(len(frames), maximum, dtype=torch.bool)
    for i, value in enumerate(frames):
        values[i, :len(value)] = value
        mask[i, :len(value)] = False
    return values, mask, torch.tensor(labels), torch.tensor(voices), list(books)


def collate_voice_audio(batch):
    import torch
    signals, labels, voices, books = zip(*batch)
    maximum = max(len(value) for value in signals)
    values = torch.zeros(len(signals), maximum)
    mask = torch.ones(len(signals), maximum, dtype=torch.bool)
    for i, value in enumerate(signals):
        values[i, :len(value)] = value
        mask[i, :len(value)] = False
    return values, mask, torch.tensor(labels), torch.tensor(voices), list(books)


def loss_weights(rows: list[dict], indices: list[int], labels: list[str]):
    import numpy as np
    counts = Counter(rows[i]["mood"] for i in indices)
    # Square-root balancing is less aggressive than inverse frequency for the
    # long tail. Book 2 consists mainly of corrections, so it cannot define the
    # natural deployment prior by itself.
    class_weights = np.asarray([math.sqrt(max(counts.values()) / counts[label]) for label in labels], dtype=np.float32)
    class_weights /= class_weights.mean()
    return class_weights


def run_epoch(model, loader, optimizer, device, class_weights, scaler=None,
              book2_weight=.5, accumulation=1):
    import numpy as np
    import torch
    import torch.nn.functional as F

    training = optimizer is not None
    model.train(training)
    if training:
        optimizer.zero_grad(set_to_none=True)
    all_probabilities, all_actual, total_loss = [], [], 0.0
    for step, (values, mask, labels, books) in enumerate(loader, 1):
        values, mask, labels = values.to(device), mask.to(device), labels.to(device)
        enabled = device == "cuda"
        with torch.set_grad_enabled(training), torch.amp.autocast("cuda", enabled=enabled, dtype=torch.float16):
            logits = model(values, mask)
            losses = F.cross_entropy(logits.float(), labels, weight=class_weights, reduction="none")
            sample = torch.tensor([book2_weight if book == "reality-benders-2-full" else 1.
                                   for book in books], device=device)
            loss = (losses * sample).sum() / sample.sum()
        if training:
            if scaler:
                scaler.scale(loss / accumulation).backward()
                if step % accumulation == 0 or step == len(loader):
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad(set_to_none=True)
            else:
                (loss / accumulation).backward()
                if step % accumulation == 0 or step == len(loader):
                    torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
                    optimizer.step(); optimizer.zero_grad(set_to_none=True)
        total_loss += float(loss.detach()) * len(labels)
        all_probabilities.append(torch.softmax(logits.detach().float(), -1).cpu().numpy())
        all_actual.extend(labels.cpu().tolist())
    return total_loss / max(1, len(all_actual)), np.concatenate(all_probabilities), all_actual


def run_conditional_epoch(model, loader, optimizer, device, class_weights,
                          book2_weight=.5, scaler=None, accumulation=1):
    import numpy as np
    import torch
    import torch.nn.functional as F

    training = optimizer is not None
    model.train(training)
    if training:
        optimizer.zero_grad(set_to_none=True)
    all_probabilities, all_actual, total_loss = [], [], 0.0
    for step, (values, mask, labels, voices, books) in enumerate(loader, 1):
        values, mask = values.to(device), mask.to(device)
        labels, voices = labels.to(device), voices.to(device)
        with torch.set_grad_enabled(training), torch.amp.autocast(
                "cuda", enabled=device == "cuda", dtype=torch.float16):
            logits = model(values, mask, voices)
            losses = F.cross_entropy(logits.float(), labels, weight=class_weights, reduction="none")
            sample = torch.tensor([book2_weight if book == "reality-benders-2-full" else 1.
                                   for book in books], device=device)
            loss = (losses * sample).sum() / sample.sum()
        if training:
            if scaler:
                scaler.scale(loss / accumulation).backward()
                if step % accumulation == 0 or step == len(loader):
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(
                        [p for p in model.parameters() if p.requires_grad], 1.)
                    scaler.step(optimizer); scaler.update()
                    optimizer.zero_grad(set_to_none=True)
            else:
                (loss / accumulation).backward()
                if step % accumulation == 0 or step == len(loader):
                    torch.nn.utils.clip_grad_norm_(
                        [p for p in model.parameters() if p.requires_grad], 1.)
                    optimizer.step(); optimizer.zero_grad(set_to_none=True)
        total_loss += float(loss.detach()) * len(labels)
        all_probabilities.append(torch.softmax(logits.detach().float(), -1).cpu().numpy())
        all_actual.extend(labels.cpu().tolist())
    return total_loss / max(1, len(all_actual)), np.concatenate(all_probabilities), all_actual


def train_frozen_head(rows, split, provenance, args):
    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from torch.utils.data import DataLoader
    _, _, _, FrameDataset, _, _, _, MoodHead, _ = torch_components()

    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    missing = [rows[i]["audio_sha256"] for i in train + validation + outer_test
               if not (FRAME_CACHE / (rows[i]["audio_sha256"] + ".npy")).is_file()]
    if missing:
        raise ValueError(f"Missing {len(missing)} frame caches; run extract first")
    label_to_index = {label: i for i, label in enumerate(labels)}
    device = args.device
    model = MoodHead(len(labels)).to(device)
    weights = torch.tensor(loss_weights(rows, train, labels), device=device)
    loaders = {
        "train": DataLoader(FrameDataset(rows, train, label_to_index), batch_size=args.head_batch,
                            shuffle=True, collate_fn=collate_frames, num_workers=0),
        "validation": DataLoader(FrameDataset(rows, validation, label_to_index), batch_size=args.head_batch,
                                 shuffle=False, collate_fn=collate_frames, num_workers=0),
        "test": DataLoader(FrameDataset(rows, outer_test, label_to_index), batch_size=args.head_batch,
                           shuffle=False, collate_fn=collate_frames, num_workers=0),
    }
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.head_lr, weight_decay=1e-3)
    best, history, stale = None, [], 0
    checkpoint = OUT / "frozen-head.pt"
    for epoch in range(1, args.head_epochs + 1):
        train_loss, _, _ = run_epoch(model, loaders["train"], optimizer, device, weights,
                                     book2_weight=args.book2_weight)
        valid_loss, probability, actual_index = run_epoch(model, loaders["validation"], None, device, weights,
                                                           book2_weight=args.book2_weight)
        actual = [labels[i] for i in actual_index]
        predicted = np.asarray(labels)[probability.argmax(1)]
        score = float(f1_score(actual, predicted, average="macro", zero_division=0))
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": valid_loss,
                        "validation_macro_f1": score})
        print(json.dumps(history[-1]), flush=True)
        if best is None or score > best["score"] + 1e-5:
            best = {"score": score, "epoch": epoch}
            torch.save({"schema": 1, "head": model.state_dict(), "labels": labels,
                        "model_revision": MODEL_REVISION, "provenance": provenance}, checkpoint)
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                break
    saved = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(saved["head"])
    _, test_probability, _ = run_epoch(model, loaders["test"], None, device, weights,
                                        book2_weight=args.book2_weight)
    report = {
        "schema": 1, "stage": "frozen-head", "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels, "best": best, "history": history,
        "counts": {"all_evidence": len(rows), "train": len(train), "validation": len(validation),
                   "outer_test": len(outer_test)},
        "outer_test": evaluate_probabilities(rows, outer_test, test_probability, labels),
        "policy": {"book2_training_weight": args.book2_weight,
                   "minimum_clips": args.minimum_clips, "minimum_chapters": args.minimum_chapters,
                   "outer_split": "frozen assignment benchmark chapters",
                   "inner_split": "first deterministic StratifiedGroupKFold(5) chapter fold"},
        "provenance": provenance,
        "automatic_acceptance": False,
    }
    write(OUT / "frozen-head-report.json", report)
    return report


def train_conditional_head(rows, split, provenance, args):
    """Train p(mood | audio, reviewed voice family) on the frozen encoder."""
    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from torch.utils.data import DataLoader
    (_, _, _, _, _, VoiceFrameDataset, _, MoodHead,
     VoiceConditionedMoodHead) = torch_components()

    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    missing = [rows[i]["audio_sha256"] for i in train + validation + outer_test
               if not (FRAME_CACHE / (rows[i]["audio_sha256"] + ".npy")).is_file()]
    if missing:
        raise ValueError(f"Missing {len(missing)} frame caches; run extract first")
    label_to_index = {label: i for i, label in enumerate(labels)}
    # Learn the vocabulary from training only. Index zero remains a real unseen-
    # family fallback and validation/test metadata cannot shape the model.
    voices = sorted({rows[i]["voice"] for i in train})
    voice_to_index = {voice: i + 1 for i, voice in enumerate(voices)}
    device = args.device
    model = VoiceConditionedMoodHead(
        len(labels), len(voices), voice_dropout=args.voice_dropout).to(device)

    # Begin at the best universal head exactly: the FiLM layer is zero, so this
    # is a controlled test of whether voice-family conditioning adds signal.
    frozen_path = OUT / "frozen-head.pt"
    if not frozen_path.is_file():
        raise FileNotFoundError("Run frozen-head before conditional-head")
    frozen = torch.load(frozen_path, map_location=device, weights_only=False)
    if frozen["labels"] != labels or frozen["model_revision"] != MODEL_REVISION:
        raise ValueError("Frozen head is incompatible with this corpus/model")
    universal = MoodHead(len(labels)).to(device)
    universal.load_state_dict(frozen["head"])
    model.norm.load_state_dict(universal.norm.state_dict())
    model.attention.load_state_dict(universal.attention.state_dict())
    model.acoustic[0].load_state_dict(universal.classifier[0].state_dict())
    model.classifier.load_state_dict(universal.classifier[3].state_dict())

    weights = torch.tensor(loss_weights(rows, train, labels), device=device)
    loaders = {
        "train": DataLoader(VoiceFrameDataset(rows, train, label_to_index, voice_to_index),
                            batch_size=args.head_batch, shuffle=True,
                            collate_fn=collate_voice_frames, num_workers=0),
        "validation": DataLoader(VoiceFrameDataset(rows, validation, label_to_index, voice_to_index),
                                 batch_size=args.head_batch, shuffle=False,
                                 collate_fn=collate_voice_frames, num_workers=0),
        "test": DataLoader(VoiceFrameDataset(rows, outer_test, label_to_index, voice_to_index),
                           batch_size=args.head_batch, shuffle=False,
                           collate_fn=collate_voice_frames, num_workers=0),
    }
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.head_lr, weight_decay=1e-3)
    checkpoint = OUT / "conditional-head.pt"
    best, history, stale = None, [], 0
    for epoch in range(1, args.head_epochs + 1):
        train_loss, _, _ = run_conditional_epoch(
            model, loaders["train"], optimizer, device, weights, args.book2_weight)
        valid_loss, probability, actual_index = run_conditional_epoch(
            model, loaders["validation"], None, device, weights, args.book2_weight)
        actual = [labels[i] for i in actual_index]
        predicted = np.asarray(labels)[probability.argmax(1)]
        score = float(f1_score(actual, predicted, average="macro", zero_division=0))
        history.append({"epoch": epoch, "train_loss": train_loss,
                        "validation_loss": valid_loss, "validation_macro_f1": score})
        print(json.dumps(history[-1]), flush=True)
        if best is None or score > best["score"] + 1e-5:
            best = {"score": score, "epoch": epoch}; stale = 0
            torch.save({"schema": 1, "head": model.state_dict(), "labels": labels,
                        "voices": voices, "model_revision": MODEL_REVISION,
                        "voice_dropout": args.voice_dropout,
                        "provenance": provenance}, checkpoint)
        else:
            stale += 1
            if stale >= args.patience:
                break
    saved = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(saved["head"])
    _, test_probability, _ = run_conditional_epoch(
        model, loaders["test"], None, device, weights, args.book2_weight)
    palette = defaultdict(Counter)
    for i in train:
        palette[rows[i]["voice"]][rows[i]["mood"]] += 1
    report = {
        "schema": 1, "stage": "conditional-head",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels, "voices": voices, "best": best, "history": history,
        "counts": {"all_evidence": len(rows), "train": len(train),
                   "validation": len(validation), "outer_test": len(outer_test),
                   "unseen_validation_voices": sum(rows[i]["voice"] not in voice_to_index
                                                   for i in validation),
                   "unseen_outer_test_voices": sum(rows[i]["voice"] not in voice_to_index
                                                   for i in outer_test)},
        "outer_test": evaluate_probabilities(rows, outer_test, test_probability, labels),
        "outer_test_by_voice": voice_metrics(rows, outer_test, test_probability, labels),
        "training_palette": {voice: dict(counts) for voice, counts in sorted(palette.items())},
        "policy": {"formulation": "p(mood | exact audio, reviewed voice family)",
                   "voice_adapter": "48-dimensional FiLM with family dropout",
                   "voice_dropout": args.voice_dropout,
                   "unknown_voice_index": 0,
                   "book2_training_weight": args.book2_weight,
                   "outer_split": "frozen assignment benchmark chapters",
                   "inner_split": "first deterministic StratifiedGroupKFold(5) chapter fold",
                   "voice_vocabulary_source": "inner training partition only"},
        "provenance": provenance,
        "automatic_acceptance": False,
    }
    write(OUT / "conditional-head-report.json", report)
    return report


def fixed_frame_summary(row: dict):
    """Deterministic mean/std summary used by the relative-family experiment."""
    import numpy as np

    value = np.load(FRAME_CACHE / (row["audio_sha256"] + ".npy"),
                    allow_pickle=False).astype(np.float32, copy=False)
    if value.ndim != 2 or value.shape[1] != EMBED_DIM:
        raise ValueError("Invalid cached frame features for " + row["id"])
    return np.concatenate([value.mean(axis=0), value.std(axis=0)])


def family_references(rows: list[dict], train: list[int], summaries) -> tuple[dict, dict]:
    """Build voice-relative references from the inner training partition only."""
    import numpy as np

    global_neutral = [summaries[i] for i in train if rows[i]["mood"] == "neutral"]
    if not global_neutral:
        raise ValueError("Training partition has no neutral reference")
    fallback = np.mean(global_neutral, axis=0)
    references, basis = {}, {}
    for voice in sorted({rows[i]["voice"] for i in train}):
        neutral = [summaries[i] for i in train
                   if rows[i]["voice"] == voice and rows[i]["mood"] == "neutral"]
        family = [summaries[i] for i in train if rows[i]["voice"] == voice]
        if len(neutral) >= 3:
            references[voice] = np.mean(neutral, axis=0)
            basis[voice] = {"kind": "family_neutral", "clips": len(neutral)}
        elif len(family) >= 3:
            references[voice] = np.mean(family, axis=0)
            basis[voice] = {"kind": "family_all_moods_fallback", "clips": len(family)}
        else:
            references[voice] = fallback
            basis[voice] = {"kind": "global_neutral_fallback", "clips": len(global_neutral)}
    return references, basis


def train_relative_head(rows, split, provenance, args):
    """Test whether family-relative emotion features beat universal features."""
    import joblib
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, f1_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    required = train + validation + outer_test
    missing = [rows[i]["audio_sha256"] for i in required
               if not (FRAME_CACHE / (rows[i]["audio_sha256"] + ".npy")).is_file()]
    if missing:
        raise ValueError(f"Missing {len(missing)} frame caches; run extract first")
    summaries = {i: fixed_frame_summary(rows[i]) for i in required}
    references, reference_basis = family_references(rows, train, summaries)
    global_reference = np.mean([summaries[i] for i in train if rows[i]["mood"] == "neutral"], axis=0)
    voices = sorted({rows[i]["voice"] for i in train})

    def matrix(indices, relative):
        result = []
        for i in indices:
            absolute = summaries[i]
            if relative:
                reference = references.get(rows[i]["voice"], global_reference)
                numeric = np.concatenate([absolute, absolute - reference])
                result.append(np.concatenate([numeric, [voices.index(rows[i]["voice"])
                                                         if rows[i]["voice"] in voices else -1]]))
            else:
                result.append(absolute)
        return np.asarray(result, dtype=np.float32)

    def selection_score(actual, probabilities):
        predicted = np.asarray(labels)[probabilities.argmax(1)]
        return float(np.mean([
            np.mean(np.asarray(actual) == predicted),
            f1_score(actual, predicted, average="macro", zero_division=0),
            balanced_accuracy_score(actual, predicted),
        ]))

    outcomes, fitted = {}, {}
    for name, relative in (("universal_fixed", False), ("voice_relative", True)):
        x_train, x_validation = matrix(train, relative), matrix(validation, relative)
        y_train = np.asarray([rows[i]["mood"] for i in train])
        y_validation = np.asarray([rows[i]["mood"] for i in validation])
        sample_weight = np.asarray([args.book2_weight if rows[i]["book"] == "reality-benders-2-full"
                                    else 1. for i in train])
        trials = []
        for c in (.003, .01, .03, .1, .3):
            if relative:
                numeric_columns = list(range(EMBED_DIM * 4))
                transformer = ColumnTransformer([
                    ("numeric", StandardScaler(), numeric_columns),
                    ("voice", OneHotEncoder(handle_unknown="ignore"), [EMBED_DIM * 4]),
                ])
                model = Pipeline([
                    ("features", transformer),
                    ("classifier", LogisticRegression(C=c, max_iter=1500, solver="lbfgs")),
                ])
            else:
                model = Pipeline([
                    ("features", StandardScaler()),
                    ("classifier", LogisticRegression(C=c, max_iter=1500, solver="lbfgs")),
                ])
            model.fit(x_train, y_train, classifier__sample_weight=sample_weight)
            probabilities = model.predict_proba(x_validation)
            aligned = np.zeros((len(validation), len(labels)))
            for j, label in enumerate(model.classes_):
                aligned[:, labels.index(str(label))] = probabilities[:, j]
            score = selection_score(y_validation, aligned)
            trials.append({"c": c, "selection_score": score,
                           **metrics(y_validation, aligned, labels)})
        choice = max(trials, key=lambda item: item["selection_score"])
        c = choice["c"]
        # Preserve the validation-selected estimator exactly for one untouched
        # outer evaluation. Do not refit on validation before this measurement.
        if relative:
            numeric_columns = list(range(EMBED_DIM * 4))
            transformer = ColumnTransformer([
                ("numeric", StandardScaler(), numeric_columns),
                ("voice", OneHotEncoder(handle_unknown="ignore"), [EMBED_DIM * 4]),
            ])
            model = Pipeline([("features", transformer),
                              ("classifier", LogisticRegression(C=c, max_iter=1500, solver="lbfgs"))])
        else:
            model = Pipeline([("features", StandardScaler()),
                              ("classifier", LogisticRegression(C=c, max_iter=1500, solver="lbfgs"))])
        model.fit(x_train, y_train, classifier__sample_weight=sample_weight)
        probability = model.predict_proba(matrix(outer_test, relative))
        aligned = np.zeros((len(outer_test), len(labels)))
        for j, label in enumerate(model.classes_):
            aligned[:, labels.index(str(label))] = probability[:, j]
        outcomes[name] = {"validation_trials": trials, "selected_c": c,
                          "outer_test": evaluate_probabilities(rows, outer_test, aligned, labels),
                          "outer_test_by_voice": voice_metrics(rows, outer_test, aligned, labels)}
        fitted[name] = model
        print(json.dumps({"model": name, "selected": choice,
                          "outer": outcomes[name]["outer_test"]["all"]}), flush=True)

    universal = outcomes["universal_fixed"]["outer_test"]["all"]
    relative = outcomes["voice_relative"]["outer_test"]["all"]
    report = {
        "schema": 1, "stage": "relative-head",
        "created_at": datetime.now(timezone.utc).isoformat(), "labels": labels,
        "counts": {"all_evidence": len(rows), "train": len(train),
                   "validation": len(validation), "outer_test": len(outer_test)},
        "models": outcomes,
        "outer_delta_relative_minus_universal": {
            key: relative[key] - universal[key]
            for key in ("top1", "top3", "macro_f1", "balanced_accuracy")},
        "reference_basis": reference_basis,
        "policy": {"formulation": "absolute emotion2vec stats plus deviation from voice-family baseline",
                   "neutral_reference_source": "inner training partition only",
                   "palette_is_not_a_hard_allow_list": True,
                   "book2_training_weight": args.book2_weight,
                   "outer_split": "frozen assignment benchmark chapters"},
        "provenance": provenance, "automatic_acceptance": False,
    }
    joblib.dump({"schema": 1, "model": fitted["voice_relative"], "labels": labels,
                 "voices": voices, "references": references,
                 "global_reference": global_reference, "provenance": provenance},
                OUT / "relative-head.joblib")
    write(OUT / "relative-head-report.json", report)
    return report


def current_baseline(rows, split, provenance, args):
    """Score the existing Book 1 classifier on the exact new outer split."""
    import joblib
    import numpy as np

    model_dir = ROOT / "training-work/character-rb1/label-predictor-3a743ea3d029"
    artifact = joblib.load(model_dir / "mood.joblib")
    sources = {
        "reality-benders-1": model_dir / "training-features.npz",
        "reality-benders-2-full": ROOT / "training-work/character-rb2/full/features.npz",
    }
    feature_rows = {}
    for book, path in sources.items():
        with np.load(path, allow_pickle=False) as data:
            identifiers = data["ids"].astype(str)
            audio_hashes = data["audio_sha256"].astype(str)
            # NPZ members are compressed. Materialize each matrix once instead
            # of decompressing it again for every row.
            matrices = [np.asarray(data[key]) for key in artifact["features"].split("+")]
            joined = np.concatenate(matrices, axis=1)
            for identifier, audio_hash, vector in zip(identifiers, audio_hashes, joined):
                feature_rows[(book, identifier)] = (audio_hash, vector)
    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    _, _, outer_test = inner_partition(rows, split, labels)
    matrix, scored_indices, exclusions = [], [], Counter()
    for index in outer_test:
        row = rows[index]
        saved = feature_rows.get((row["book"], row["id"]))
        if saved is None:
            exclusions["missing_feature"] += 1
            continue
        if saved[0] != row["audio_sha256"]:
            exclusions["audio_hash_changed_since_baseline"] += 1
            continue
        matrix.append(saved[1])
        scored_indices.append(index)
    probabilities = artifact["model"].predict_proba(np.asarray(matrix))
    model_labels = list(artifact["model"].classes_.astype(str))
    report = {
        "schema": 1, "stage": "current-baseline", "created_at": datetime.now(timezone.utc).isoformat(),
        "features": artifact["features"], "labels": model_labels,
        "outer_test": evaluate_probabilities(rows, scored_indices, probabilities, model_labels),
        "counts": {"all_evidence": len(rows), "outer_test": len(outer_test),
                   "scored_outer_test": len(scored_indices)},
        "exclusions": dict(exclusions),
        "unsupported_outer_labels": dict(Counter(rows[i]["mood"] for i in scored_indices
                                                  if rows[i]["mood"] not in model_labels)),
        "artifact_review_sha256": artifact["review_sha256"], "provenance": provenance,
        "automatic_acceptance": False,
    }
    write(OUT / "current-baseline-report.json", report)
    return report


def load_encoder(device):
    import setuptools  # noqa: F401
    from funasr import AutoModel
    wrapper = AutoModel(model=str(MODEL), device=device, ncpu=4, disable_update=True,
                        disable_pbar=True, disable_log=True, log_level="ERROR")
    return wrapper.model


def train_last_blocks(rows, split, provenance, args):
    import numpy as np
    import torch
    import torch.nn.functional as F
    from sklearn.metrics import f1_score
    from torch import nn
    from torch.utils.data import DataLoader
    _, _, _, _, AudioDataset, _, _, MoodHead, _ = torch_components()

    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    label_to_index = {label: i for i, label in enumerate(labels)}
    device = args.device
    if device != "cuda":
        raise ValueError("Limited encoder fine-tuning requires --device cuda")
    encoder = load_encoder(device)
    for parameter in encoder.parameters():
        parameter.requires_grad = False
    if args.unfreeze_blocks < 1 or args.unfreeze_blocks > len(encoder.blocks):
        raise ValueError("Invalid --unfreeze-blocks")
    for block in encoder.blocks[-args.unfreeze_blocks:]:
        for parameter in block.parameters():
            parameter.requires_grad = True
    head = MoodHead(len(labels)).to(device)
    frozen = torch.load(OUT / "frozen-head.pt", map_location=device, weights_only=False)
    if frozen["labels"] != labels or frozen["model_revision"] != MODEL_REVISION:
        raise ValueError("Frozen head is incompatible with this corpus/model")
    head.load_state_dict(frozen["head"])

    class Adapted(nn.Module):
        def __init__(self, encoder, head):
            super().__init__(); self.encoder = encoder; self.head = head

        def train(self, mode=True):
            super().train(mode)
            # Keep the frozen frontend/blocks deterministic. Enable dropout only
            # inside the selected final blocks and the custom head.
            self.encoder.eval()
            for block in self.encoder.blocks[-args.unfreeze_blocks:]:
                block.train(mode)
            self.head.train(mode)
            return self

        def forward(self, audio, raw_padding_mask):
            valid = (~raw_padding_mask).sum(1).clamp_min(1)
            mean = (audio * (~raw_padding_mask)).sum(1) / valid
            centred = (audio - mean[:, None]).masked_fill(raw_padding_mask, 0)
            variance = centred.square().sum(1) / valid
            audio = centred / (variance.sqrt()[:, None] + 1e-7)
            features = self.encoder.extract_features(audio, padding_mask=raw_padding_mask, mask=False)
            return self.head(features["x"], features.get("padding_mask"))

    model = Adapted(encoder, head).to(device)
    weights = torch.tensor(loss_weights(rows, train, labels), device=device)
    loaders = {
        "train": DataLoader(AudioDataset(rows, train, label_to_index), batch_size=args.encoder_batch,
                            shuffle=True, collate_fn=collate_audio, num_workers=0),
        "validation": DataLoader(AudioDataset(rows, validation, label_to_index), batch_size=args.encoder_batch,
                                 shuffle=False, collate_fn=collate_audio, num_workers=0),
        "test": DataLoader(AudioDataset(rows, outer_test, label_to_index), batch_size=args.encoder_batch,
                           shuffle=False, collate_fn=collate_audio, num_workers=0),
    }
    parameters = [{"params": head.parameters(), "lr": args.head_lr / 2},
                  {"params": [p for block in encoder.blocks[-args.unfreeze_blocks:]
                              for p in block.parameters() if p.requires_grad], "lr": args.encoder_lr}]
    optimizer = torch.optim.AdamW(parameters, weight_decay=1e-3)
    scaler = torch.amp.GradScaler("cuda")
    checkpoint = OUT / f"last-{args.unfreeze_blocks}-blocks.pt"
    best, history, stale = None, [], 0
    for epoch in range(1, args.encoder_epochs + 1):
        train_loss, _, _ = run_epoch(model, loaders["train"], optimizer, device, weights, scaler,
                                     args.book2_weight, args.accumulation)
        valid_loss, probability, actual_index = run_epoch(model, loaders["validation"], None, device, weights,
                                                           book2_weight=args.book2_weight)
        actual = [labels[i] for i in actual_index]
        predicted = np.asarray(labels)[probability.argmax(1)]
        score = float(f1_score(actual, predicted, average="macro", zero_division=0))
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": valid_loss,
                        "validation_macro_f1": score})
        print(json.dumps(history[-1]), flush=True)
        if best is None or score > best["score"] + 1e-5:
            best = {"score": score, "epoch": epoch}; stale = 0
            torch.save({"schema": 1, "head": head.state_dict(),
                        "encoder_blocks": {str(len(encoder.blocks)-args.unfreeze_blocks+i): block.state_dict()
                                           for i, block in enumerate(encoder.blocks[-args.unfreeze_blocks:])},
                        "labels": labels, "model_revision": MODEL_REVISION,
                        "unfreeze_blocks": args.unfreeze_blocks, "provenance": provenance}, checkpoint)
        else:
            stale += 1
            if stale >= args.patience:
                break
    saved = torch.load(checkpoint, map_location=device, weights_only=False)
    head.load_state_dict(saved["head"])
    for index, state in saved["encoder_blocks"].items():
        encoder.blocks[int(index)].load_state_dict(state)
    _, test_probability, _ = run_epoch(model, loaders["test"], None, device, weights,
                                        book2_weight=args.book2_weight)
    report = {
        "schema": 1, "stage": "last-blocks", "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels, "best": best, "history": history,
        "unfrozen_blocks": args.unfreeze_blocks,
        "counts": {"all_evidence": len(rows), "train": len(train), "validation": len(validation),
                   "outer_test": len(outer_test)},
        "outer_test": evaluate_probabilities(rows, outer_test, test_probability, labels),
        "policy": {"book2_training_weight": args.book2_weight, "encoder_lr": args.encoder_lr,
                   "head_lr": args.head_lr / 2, "batch": args.encoder_batch,
                   "accumulation": args.accumulation,
                   "outer_split": "frozen assignment benchmark chapters"},
        "provenance": provenance,
        "automatic_acceptance": False,
    }
    write(OUT / f"last-{args.unfreeze_blocks}-blocks-report.json", report)
    return report


def train_conditional_last_blocks(rows, split, provenance, args):
    """Limited encoder fine-tune for p(mood | audio, reviewed voice family)."""
    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from torch import nn
    from torch.utils.data import DataLoader
    (_, _, _, _, _, _, VoiceAudioDataset, _,
     VoiceConditionedMoodHead) = torch_components()

    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    label_to_index = {label: i for i, label in enumerate(labels)}
    source_path = OUT / "conditional-head.pt"
    if not source_path.is_file():
        raise FileNotFoundError("Run conditional-head before conditional-last-blocks")
    source = torch.load(source_path, map_location=args.device, weights_only=False)
    voices = source["voices"]
    voice_to_index = {voice: i + 1 for i, voice in enumerate(voices)}
    if (source["labels"] != labels or source["model_revision"] != MODEL_REVISION
            or voices != sorted({rows[i]["voice"] for i in train})):
        raise ValueError("Conditional head is incompatible with this corpus/split")
    if args.device != "cuda":
        raise ValueError("Limited encoder fine-tuning requires --device cuda")
    encoder = load_encoder(args.device)
    for parameter in encoder.parameters():
        parameter.requires_grad = False
    if args.unfreeze_blocks < 1 or args.unfreeze_blocks > len(encoder.blocks):
        raise ValueError("Invalid --unfreeze-blocks")
    for block in encoder.blocks[-args.unfreeze_blocks:]:
        for parameter in block.parameters():
            parameter.requires_grad = True
    head = VoiceConditionedMoodHead(
        len(labels), len(voices), voice_dropout=source["voice_dropout"]).to(args.device)
    head.load_state_dict(source["head"])

    class Adapted(nn.Module):
        def __init__(self, encoder, head):
            super().__init__(); self.encoder = encoder; self.head = head

        def train(self, mode=True):
            super().train(mode)
            self.encoder.eval()
            for block in self.encoder.blocks[-args.unfreeze_blocks:]:
                block.train(mode)
            self.head.train(mode)
            return self

        def forward(self, audio, raw_padding_mask, voice_indices):
            valid = (~raw_padding_mask).sum(1).clamp_min(1)
            mean = (audio * (~raw_padding_mask)).sum(1) / valid
            centred = (audio - mean[:, None]).masked_fill(raw_padding_mask, 0)
            variance = centred.square().sum(1) / valid
            audio = centred / (variance.sqrt()[:, None] + 1e-7)
            features = self.encoder.extract_features(
                audio, padding_mask=raw_padding_mask, mask=False)
            return self.head(features["x"], features.get("padding_mask"), voice_indices)

    model = Adapted(encoder, head).to(args.device)
    weights = torch.tensor(loss_weights(rows, train, labels), device=args.device)
    loaders = {
        "train": DataLoader(VoiceAudioDataset(rows, train, label_to_index, voice_to_index),
                            batch_size=args.encoder_batch, shuffle=True,
                            collate_fn=collate_voice_audio, num_workers=0),
        "validation": DataLoader(VoiceAudioDataset(rows, validation, label_to_index, voice_to_index),
                                 batch_size=args.encoder_batch, shuffle=False,
                                 collate_fn=collate_voice_audio, num_workers=0),
        "test": DataLoader(VoiceAudioDataset(rows, outer_test, label_to_index, voice_to_index),
                           batch_size=args.encoder_batch, shuffle=False,
                           collate_fn=collate_voice_audio, num_workers=0),
    }
    parameters = [
        {"params": head.parameters(), "lr": args.head_lr / 2},
        {"params": [p for block in encoder.blocks[-args.unfreeze_blocks:]
                    for p in block.parameters() if p.requires_grad], "lr": args.encoder_lr},
    ]
    optimizer = torch.optim.AdamW(parameters, weight_decay=1e-3)
    scaler = torch.amp.GradScaler("cuda")
    checkpoint = OUT / f"conditional-last-{args.unfreeze_blocks}-blocks.pt"
    best, history, stale = None, [], 0
    for epoch in range(1, args.encoder_epochs + 1):
        started = time.monotonic()
        train_loss, _, _ = run_conditional_epoch(
            model, loaders["train"], optimizer, args.device, weights,
            args.book2_weight, scaler, args.accumulation)
        valid_loss, probability, actual_index = run_conditional_epoch(
            model, loaders["validation"], None, args.device, weights,
            args.book2_weight)
        actual = [labels[i] for i in actual_index]
        predicted = np.asarray(labels)[probability.argmax(1)]
        score = float(f1_score(actual, predicted, average="macro", zero_division=0))
        history.append({"epoch": epoch, "train_loss": train_loss,
                        "validation_loss": valid_loss, "validation_macro_f1": score,
                        "elapsed_seconds": time.monotonic() - started})
        print(json.dumps(history[-1]), flush=True)
        if best is None or score > best["score"] + 1e-5:
            best = {"score": score, "epoch": epoch}; stale = 0
            torch.save({
                "schema": 1, "head": head.state_dict(),
                "encoder_blocks": {
                    str(len(encoder.blocks) - args.unfreeze_blocks + i): block.state_dict()
                    for i, block in enumerate(encoder.blocks[-args.unfreeze_blocks:])},
                "labels": labels, "voices": voices, "model_revision": MODEL_REVISION,
                "unfreeze_blocks": args.unfreeze_blocks,
                "voice_dropout": source["voice_dropout"], "provenance": provenance,
            }, checkpoint)
        else:
            stale += 1
            if stale >= args.patience:
                break
    saved = torch.load(checkpoint, map_location=args.device, weights_only=False)
    head.load_state_dict(saved["head"])
    for index, state in saved["encoder_blocks"].items():
        encoder.blocks[int(index)].load_state_dict(state)
    _, test_probability, _ = run_conditional_epoch(
        model, loaders["test"], None, args.device, weights, args.book2_weight)
    report = {
        "schema": 1, "stage": "conditional-last-blocks",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels, "voices": voices, "best": best, "history": history,
        "unfrozen_blocks": args.unfreeze_blocks,
        "counts": {"all_evidence": len(rows), "train": len(train),
                   "validation": len(validation), "outer_test": len(outer_test)},
        "outer_test": evaluate_probabilities(rows, outer_test, test_probability, labels),
        "outer_test_by_voice": voice_metrics(rows, outer_test, test_probability, labels),
        "policy": {"formulation": "fine-tuned emotion2vec p(mood | audio, reviewed voice family)",
                   "book2_training_weight": args.book2_weight,
                   "encoder_lr": args.encoder_lr, "head_lr": args.head_lr / 2,
                   "batch": args.encoder_batch, "accumulation": args.accumulation,
                   "outer_split": "frozen assignment benchmark chapters"},
        "provenance": provenance, "automatic_acceptance": False,
    }
    write(OUT / f"conditional-last-{args.unfreeze_blocks}-blocks-report.json", report)
    return report


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["audit", "baseline", "extract", "extract-wave",
                                          "extract-timbre", "frozen-head",
                                          "conditional-head", "relative-head", "last-blocks",
                                          "conditional-last-blocks"])
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--verify-audio", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Extraction smoke limit")
    parser.add_argument("--workers", type=int, default=4, help="Waveform extraction workers")
    parser.add_argument("--minimum-clips", type=int, default=10)
    parser.add_argument("--minimum-chapters", type=int, default=3)
    parser.add_argument("--book2-weight", type=float, default=.5)
    parser.add_argument("--head-batch", type=int, default=24)
    parser.add_argument("--head-lr", type=float, default=3e-4)
    parser.add_argument("--head-epochs", type=int, default=25)
    parser.add_argument("--voice-dropout", type=float, default=.2)
    parser.add_argument("--encoder-batch", type=int, default=1)
    parser.add_argument("--encoder-lr", type=float, default=1e-5)
    parser.add_argument("--encoder-epochs", type=int, default=6)
    parser.add_argument("--unfreeze-blocks", type=int, default=2)
    parser.add_argument("--accumulation", type=int, default=12)
    parser.add_argument("--patience", type=int, default=4)
    return parser.parse_args()


def main():
    args = parse_args()
    if not 0 < args.book2_weight <= 1:
        raise ValueError("--book2-weight must be in (0, 1]")
    if not 0 <= args.voice_dropout < 1:
        raise ValueError("--voice-dropout must be in [0, 1)")
    random.seed(SEED)
    import numpy as np
    np.random.seed(SEED)
    rows, split, provenance = load_evidence(args.verify_audio)
    labels = supported_labels(rows, split, args.minimum_clips, args.minimum_chapters)
    train, validation, outer_test = inner_partition(rows, split, labels)
    audit = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
             "evidence": len(rows), "books": dict(Counter(row["book"] for row in rows)),
             "supported_labels": labels, "counts": {"train": len(train), "validation": len(validation),
                                                       "outer_test": len(outer_test)},
             "provenance": provenance, "automatic_acceptance": False}
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / "audit.json", audit)
    if args.stage == "audit":
        print(json.dumps(audit, ensure_ascii=False, indent=2)); return
    if args.stage == "baseline":
        result = current_baseline(rows, split, provenance, args)
    elif args.stage == "extract":
        result = extract_frames(rows, args.device, args.limit)
    elif args.stage == "extract-wave":
        result = extract_wave_features(rows, args.limit, args.workers)
    elif args.stage == "extract-timbre":
        result = extract_timbre_features(rows, args.device, args.limit)
    elif args.stage == "frozen-head":
        result = train_frozen_head(rows, split, provenance, args)
    elif args.stage == "conditional-head":
        result = train_conditional_head(rows, split, provenance, args)
    elif args.stage == "relative-head":
        result = train_relative_head(rows, split, provenance, args)
    elif args.stage == "conditional-last-blocks":
        result = train_conditional_last_blocks(rows, split, provenance, args)
    else:
        result = train_last_blocks(rows, split, provenance, args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
