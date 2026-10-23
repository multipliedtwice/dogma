"""Render a controlled 1.7B Base comparison for a reported Russian stress error."""

import json
import argparse
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "training-work/book1-provisional-pack"
SOURCE = ROOT / "training-work/character-rb1/mixed-scene-probe/stress-probe.json"
OUTPUT = ROOT / "training-work/qwen-17b-stress-probe"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=("17b", "06b"), default="17b")
    args = parser.parse_args()
    model_name = "1.7B" if args.size == "17b" else "0.6B"
    model_path = ROOT / f"training-work/models/Qwen3-TTS-12Hz-{model_name}-Base"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    probe = json.loads(SOURCE.read_text(encoding="utf-8"))
    pack = json.loads((PACK / "pack.json").read_text(encoding="utf-8"))
    ref = next(v for v in pack["voices"] if v["id"] == "vlad-rimsky")["references"]["neutral"]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    started = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(str(model_path), device_map="cuda:0", dtype=dtype,
                                          attn_implementation="sdpa", local_files_only=True)
    print("loaded_seconds", round(time.monotonic() - started, 2), flush=True)
    prompt = model.create_voice_clone_prompt(ref_audio=str(PACK / ref["audio"]),
                                              ref_text=ref["text"], x_vector_only_mode=False)
    results = []
    for label, value in (("unmarked", probe["source_text"]),
                         ("acute", probe["render_text"])):
        torch.manual_seed(42)
        start = time.monotonic()
        wavs, rate = model.generate_voice_clone(text=value, language="Auto",
                                                 voice_clone_prompt=prompt, max_new_tokens=2048)
        samples = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        path = OUTPUT / f"{args.size}-{label}.wav"
        sf.write(path, samples, rate, format="WAV", subtype="PCM_16")
        row = {"label": label, "path": str(path), "seconds": round(time.monotonic()-start, 2),
               "duration": round(len(samples)/rate, 2),
               "peak_gpu_gb": round(torch.cuda.max_memory_allocated()/1e9, 2)}
        results.append(row)
        print(json.dumps(row), flush=True)
    (OUTPUT / f"result-{args.size}.json").write_text(json.dumps({"model": str(model_path), "dtype": str(dtype),
        "reference": ref, "source": probe["source_text"], "marked": probe["render_text"],
        "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
