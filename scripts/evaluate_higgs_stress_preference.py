"""Measure whether Higgs assigns lower NLL to the audio's correct stress mark.

The evaluation is teacher-forced and deterministic: each held-out WAV is scored
against its correct marked text and an otherwise identical wrong-mark text.  It
does not claim that a detector heard the generated stress; it measures the exact
counterfactual preference trained by ``train_higgs_local.py``.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_higgs_training import read, rows, sha
from train_higgs_local import validate_counterfactual_text, verify_dataset


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(result_rows: list[dict], margin: float) -> dict:
    gaps = [float(row["gap_nats"]) for row in result_rows]
    return {
        "pairs": len(gaps),
        "mean_gap_nats": statistics.fmean(gaps) if gaps else None,
        "median_gap_nats": statistics.median(gaps) if gaps else None,
        "p10_gap_nats": percentile(gaps, 0.10),
        "p90_gap_nats": percentile(gaps, 0.90),
        "correct_preferred_fraction": sum(value > 0 for value in gaps) / len(gaps) if gaps else None,
        "margin_pass_fraction": sum(value >= margin for value in gaps) / len(gaps) if gaps else None,
        "margin_nats": margin,
    }


def stratify(result_rows: list[dict], margin: float) -> dict:
    buckets: dict[str, list[dict]] = {}
    for row in result_rows:
        target = row.get("target") or {}
        labels = {
            "vowels": target.get("vowel_count"),
            "correct_vowel": target.get("correct_vowel_index"),
            "stress_from_end": target.get("stress_from_end"),
        }
        for name, value in labels.items():
            if value is not None:
                buckets.setdefault(f"{name}:{value}", []).append(row)
    return {key: summarize(value, margin) for key, value in sorted(buckets.items())}


def _dataset_rows(folder: Path, partition: str, limit: int | None) -> list[dict]:
    candidates = rows(folder / f"{partition}.jsonl")
    selected = []
    for row in candidates:
        wrong = row.get("counterfactual_text")
        if wrong is None:
            continue
        validate_counterfactual_text(row.get("text"), wrong)
        selected.append(row)
        if limit and len(selected) >= limit:
            break
    if not selected:
        raise ValueError(f"No valid counterfactual pairs in {partition}.jsonl")
    return selected


def prepare_codes(folder: Path, codec_path: Path, output: Path, partition: str,
                  limit: int | None) -> tuple[list[dict], str]:
    """Encode held-out audio once and bind the resumable cache to exact inputs."""
    import numpy as np
    import soundfile as sf
    import torch
    from transformers import AutoModel

    dataset_hash = verify_dataset(folder)
    input_path = folder / f"{partition}.jsonl"
    binding_value = {
        "dataset_sha256": dataset_hash,
        "partition": partition,
        "partition_sha256": sha(input_path),
        "limit": limit,
        "codec_source": read(codec_path.parent / "codec-source.json"),
    }
    codes_path = output / "codes.jsonl"
    binding_path = output / "codes-binding.json"
    if codes_path.exists() or binding_path.exists():
        if not codes_path.exists() or not binding_path.exists():
            raise ValueError("Incomplete held-out codec cache; use a fresh output directory.")
        binding = read(binding_path)
        if {key: binding.get(key) for key in binding_value} != binding_value:
            raise ValueError("Held-out codec cache belongs to different inputs.")
        if binding.get("codes_sha256") != sha(codes_path):
            raise ValueError("Held-out codec cache checksum mismatch.")
        return rows(codes_path), dataset_hash

    records = _dataset_rows(folder, partition, limit)
    codec = AutoModel.from_pretrained(
        codec_path, dtype=torch.float32, local_files_only=True, trust_remote_code=False
    ).eval()
    audio_cache: dict[str, list] = {}

    def encode(name: str) -> list:
        if name not in audio_cache:
            audio, rate = sf.read(folder / name, dtype="float32", always_2d=True)
            if rate != 24000 or audio.shape[1] != 1 or not np.isfinite(audio).all():
                raise ValueError(f"Held-out WAV is not finite mono 24 kHz: {name}")
            waveform = torch.from_numpy(audio.T.copy()).unsqueeze(0)
            with torch.no_grad():
                encoded = codec.encode(waveform).audio_codes
            audio_cache[name] = encoded.squeeze(0).transpose(0, 1).long().tolist()
        return audio_cache[name]

    prepared = []
    for index, row in enumerate(records, 1):
        prepared.append({
            **row,
            "audio_codes": encode(row["audio"]),
            "ref_audio_codes": encode(row["ref_audio"]),
        })
        codes_path.parent.mkdir(parents=True, exist_ok=True)
        codes_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in prepared), encoding="utf-8"
        )
        print(f"Validation codec {index}/{len(records)} {row['id']}", flush=True)
    del codec
    gc.collect()
    atomic_json(binding_path, {**binding_value, "codes_sha256": sha(codes_path)})
    return prepared, dataset_hash


def evaluate(args: argparse.Namespace) -> dict:
    import torch
    from transformers import AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("Higgs stress preference evaluation requires CUDA on this machine.")
    started = time.monotonic()
    records, dataset_hash = prepare_codes(
        args.dataset, args.codec, args.output, args.partition, args.limit
    )
    sys.path.insert(0, str(args.upstream))
    from model.configuration import HiggsMultimodalQwen3Config
    from model.modeling import HiggsMultimodalQwen3ForConditionalGeneration
    from src.dataset import HiggsAudioSFTDataset
    from src.processor import HiggsAudioProcessor

    # Merged production checkpoints intentionally contain native weight shards
    # and config, but not the Hub remote-code helper files. Loading through the
    # pinned upstream config class supports both those checkpoints and the base
    # model without mutating either immutable directory.
    config = HiggsMultimodalQwen3Config.from_pretrained(args.model, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True, trust_remote_code=True)
    encoder = config.audio_encoder_config
    processor = HiggsAudioProcessor(tokenizer, int(encoder["num_codebooks"]), int(encoder["vocab_size"]))
    dataset = HiggsAudioSFTDataset(records, processor, int(encoder["num_codebooks"]))
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        llm_int8_skip_modules=["audio_head"],
    )
    model, loading = HiggsMultimodalQwen3ForConditionalGeneration.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
        quantization_config=quant,
        device_map={"": 0},
        output_loading_info=True,
    )
    if set(loading.get("missing_keys", [])) - {"audio_head.weight"} or loading.get("mismatched_keys") or loading.get("unexpected_keys"):
        raise RuntimeError("Higgs weights did not load completely: " + str(loading))
    model.tie_weights()
    adapter_hash = None
    if args.adapter:
        from peft import PeftModel
        checkpoint = args.adapter / "adapter_model.safetensors"
        if not checkpoint.is_file():
            raise ValueError(f"Missing adapter checkpoint: {checkpoint}")
        adapter_hash = sha(checkpoint)
        model = PeftModel.from_pretrained(model, args.adapter, is_trainable=False)
    model.eval()
    result_rows = []
    for index, record in enumerate(records):
        correct = {key: value.to("cuda") for key, value in dataset.collate_fn([dataset[index]]).items()}
        wrong_sample = dataset._pack_record({**record, "text": record["counterfactual_text"]})
        wrong = {key: value.to("cuda") for key, value in dataset.collate_fn([wrong_sample]).items()}
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            correct_output = model(**correct, use_cache=False)
            correct_nll = correct_output.all_sum_losses[:, args.codebook].float()
            wrong_output = model(**wrong, use_cache=False)
            wrong_nll = wrong_output.all_sum_losses[:, args.codebook].float()
        gap = float((wrong_nll - correct_nll).item())
        target = (record.get("metadata") or {}).get("stress_target")
        result_rows.append({
            "id": record.get("id"),
            "gap_nats": gap,
            "correct_nll": float(correct_nll.item()),
            "wrong_nll": float(wrong_nll.item()),
            "target": target,
        })
        del correct, wrong, wrong_sample, correct_output, wrong_output, correct_nll, wrong_nll
        if (index + 1) % 10 == 0:
            print(f"Stress preference {index + 1}/{len(records)}", flush=True)
    report = {
        "schema": 1,
        "status": "complete",
        "dataset_sha256": dataset_hash,
        "partition": args.partition,
        "model_source": read(args.model.parent / "base-source.json"),
        "adapter_sha256": adapter_hash,
        "codebook": args.codebook,
        "summary": summarize(result_rows, args.margin),
        "strata": stratify(result_rows, args.margin),
        "rows": result_rows,
        "elapsed_seconds": time.monotonic() - started,
    }
    atomic_json(args.output / "report.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    base = ROOT / "training-work/higgs-production"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dataset", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--partition", default="validation", choices=("train", "validation"))
    result.add_argument("--model", type=Path, default=base / "models/base")
    result.add_argument("--codec", type=Path, default=base / "models/codec")
    result.add_argument("--upstream", type=Path, default=base / "upstream/finetune")
    result.add_argument("--adapter", type=Path)
    result.add_argument("--limit", type=int, default=200)
    result.add_argument("--codebook", type=int, default=0)
    result.add_argument("--margin", type=float, default=5.0)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive")
    if args.codebook < 0 or args.margin <= 0:
        raise SystemExit("--codebook must be non-negative and --margin positive")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        report = evaluate(args)
    except Exception as error:
        atomic_json(args.output / "report.json", {
            "schema": 1,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        })
        raise
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
