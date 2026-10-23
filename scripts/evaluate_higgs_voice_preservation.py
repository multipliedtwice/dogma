"""Measure paired baseline/candidate voice preservation with a frozen speaker encoder.

The diagnostic is hash-bound and offline.  It does not provide a calibrated
perceptual pass/fail threshold; it produces paired cosine measurements and a
small conservative review queue instead of inventing listening approval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.util import digest, file_digest, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()

    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from safetensors import safe_open
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSSpeakerEncoderConfig
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSSpeakerEncoder, mel_spectrogram

    model = ROOT / "training-work/models/Qwen3-TTS-12Hz-0.6B-Base"
    config = json.loads((model / "config.json").read_text(encoding="utf-8"))
    files = {path.name: file_digest(path) for path in [model / "config.json", *sorted(model.glob("*.safetensors"))]}
    signature = digest({"model_files": files, "implementation": file_digest(Path(__file__)), "device": "cpu"})
    torch.set_num_threads(4)
    encoder = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**config["speaker_encoder_config"]))
    state = {}
    for path in model.glob("*.safetensors"):
        with safe_open(path, framework="pt", device="cpu") as reader:
            for name in reader.keys():
                if name.startswith("speaker_encoder."):
                    state[name.removeprefix("speaker_encoder.")] = reader.get_tensor(name).float()
    encoder.load_state_dict(state, strict=True)
    encoder.eval()
    del state

    cache = {}
    def feature(path: Path) -> tuple[str, np.ndarray]:
        audio_hash = file_digest(path)
        if audio_hash in cache:
            return audio_hash, cache[audio_hash]
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        mono = audio.mean(axis=1)
        wave = librosa.resample(mono, orig_sr=rate, target_sr=24000) if rate != 24000 else mono
        with torch.inference_mode():
            mel = mel_spectrogram(torch.from_numpy(wave).unsqueeze(0), n_fft=1024, num_mels=128,
                                  sampling_rate=24000, hop_size=256, win_size=1024,
                                  fmin=0, fmax=12000).transpose(1, 2)
            vector = encoder(mel)[0].numpy().astype(float)
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        cache[audio_hash] = vector
        return audio_hash, vector

    plans, receipts = {}, {}
    for variant in ("baseline", "candidate"):
        plan_path = folder / variant / "render-plan.json"
        plans[variant] = json.loads(plan_path.read_text(encoding="utf-8"))
        receipts[variant] = json.loads((folder / variant / "receipts.json").read_text(encoding="utf-8"))
        if receipts[variant]["plan_sha256"] != file_digest(plan_path):
            raise ValueError("Receipt/plan mismatch: " + variant)
    units = {variant: {unit["id"]: unit for unit in plan["units"]} for variant, plan in plans.items()}
    if set(units["baseline"]) != set(units["candidate"]):
        raise ValueError("Paired voice comparison has different case sets")

    rows = []
    for index, identifier in enumerate(sorted(units["baseline"]), 1):
        vectors = {}
        hashes = {}
        for variant in ("baseline", "candidate"):
            take = receipts[variant]["takes"][identifier]
            path = folder / variant / take["audio"]
            if file_digest(path) != take["audio_sha256"]:
                raise ValueError("Audio changed: " + str(path))
            hashes[variant], vectors[variant] = feature(path)
        reference_path = ROOT / units["baseline"][identifier]["synthesis"]["reference"]["audio"]
        hashes["reference"], vectors["reference"] = feature(reference_path)
        cosine = lambda a, b: float(np.dot(vectors[a], vectors[b]))
        row = {
            "id": identifier, "audio_sha256": hashes,
            "baseline_candidate_cosine": cosine("baseline", "candidate"),
            "baseline_reference_cosine": cosine("baseline", "reference"),
            "candidate_reference_cosine": cosine("candidate", "reference"),
        }
        row["reference_similarity_delta"] = row["candidate_reference_cosine"] - row["baseline_reference_cosine"]
        rows.append(row)
        print("VOICE", index, "/", len(units["baseline"]), identifier, flush=True)
    queue = [row["id"] for row in rows
             if row["baseline_candidate_cosine"] < .85 or row["reference_similarity_delta"] < -.05]
    report = {
        "schema": 1, "status": "complete_diagnostic", "production_approved": False,
        "encoder_sha256": signature, "model_files": files,
        "summary": {
            "cases": len(rows),
            "mean_baseline_candidate_cosine": float(np.mean([row["baseline_candidate_cosine"] for row in rows])),
            "mean_reference_similarity_delta": float(np.mean([row["reference_similarity_delta"] for row in rows])),
            "median_reference_similarity_delta": float(np.median([row["reference_similarity_delta"] for row in rows])),
            "triage_queue_count": len(queue),
        },
        "triage_queue": queue, "rows": rows,
        "limits": [
            "Speaker-embedding cosine is an automatic regression diagnostic, not listening approval.",
            "The queue thresholds are conservative triage heuristics, not calibrated perceptual boundaries.",
        ],
    }
    write_json(folder / "voice-preservation-report.json", report)
    print(json.dumps({"summary": report["summary"], "triage_queue": queue}, indent=2))


if __name__ == "__main__":
    main()
