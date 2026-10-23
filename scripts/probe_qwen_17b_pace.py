"""Compare generated pace with three original Vlad narration references."""

import html
import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "training-work/book1-listening-app"
MODEL = ROOT / "training-work/models/Qwen3-TTS-12Hz-1.7B-Base"
OUTPUT = ROOT / "training-work/qwen-17b-pace-probe"
REFERENCE_BANK = ROOT / "training-work/character-rb1/provisional-narrator-reference-bank.json"
GENERATION = "9eaffc3307f50ca0077e3332"
SEGMENT = "c0004-p00002-0-378"


def metrics(audio, rate, words):
    frame = max(1, int(rate * .02))
    rms = np.asarray([np.sqrt(np.mean(piece * piece)) for offset in range(0, len(audio), frame)
                      if len(piece := audio[offset:offset + frame]) >= frame // 2])
    threshold = max(.005, np.quantile(rms, .9) * .08)
    quiet = rms < threshold
    runs = []
    start = None
    for index, value in enumerate(quiet):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((index - start) * .02)
            start = None
    if start is not None:
        runs.append((len(quiet) - start) * .02)
    duration = len(audio) / rate
    return {"duration_seconds": round(duration, 2), "words_per_minute": round(words / duration * 60, 1),
            "pause_seconds_120ms": round(sum(value for value in runs if value >= .12), 2),
            "pause_count_120ms": sum(value >= .12 for value in runs)}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        text = db.execute("SELECT spoken FROM segments WHERE generation_id=? AND id=?",
                          (GENERATION, SEGMENT)).fetchone()[0]
        baseline_hash = db.execute("SELECT audio_hash FROM segments WHERE generation_id=? AND id=?",
                                   (GENERATION, SEGMENT)).fetchone()[0]
    if "\u0301" in text:
        raise RuntimeError("The pace probe must use the unmarked source text")
    words = len(re.findall(r"\w+", text, re.UNICODE))
    bank = json.loads(REFERENCE_BANK.read_text(encoding="utf-8"))["candidate_references"]
    refs = [("current", bank[0]), ("deliberate", bank[2]), ("unhurried", bank[9])]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model = Qwen3TTSModel.from_pretrained(str(MODEL), device_map="cuda:0", dtype=dtype,
                                          attn_implementation="sdpa", local_files_only=True)
    results = []
    for label, ref in refs:
        prompt = model.create_voice_clone_prompt(ref_audio=ref["audio"], ref_text=ref["text"],
                                                  x_vector_only_mode=False)
        torch.manual_seed(42)
        started = time.monotonic()
        wavs, rate = model.generate_voice_clone(text=text, language="Auto", voice_clone_prompt=prompt,
                                                 max_new_tokens=2048)
        audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        filename = f"{label}.wav"
        sf.write(OUTPUT / filename, audio, rate, format="WAV", subtype="PCM_16")
        row = {"label": label, "file": filename, "reference_id": ref["id"],
               "reference_text": ref["text"], "render_seconds": round(time.monotonic()-started, 2),
               **metrics(audio, rate, words)}
        results.append(row)
        print(json.dumps({key: value for key, value in row.items() if key != "reference_text"}), flush=True)
    source = DATA / "audio" / f"{baseline_hash}.wav"
    original, original_rate = sf.read(source, dtype="float32")
    results.insert(0, {"label": "book-demo", "file": "book-demo.wav", "reference_id": bank[0]["id"],
                       **metrics(np.asarray(original).reshape(-1), original_rate, words)})
    sf.write(OUTPUT / "book-demo.wav", original, original_rate, format="WAV", subtype="PCM_16")
    (OUTPUT / "result.json").write_text(json.dumps({"model": str(MODEL), "segment_id": SEGMENT,
        "text": text, "words": words, "seed": 42, "generation_playback_rate": 1.0,
        "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    cards = "".join(f"<section><h2>{html.escape(row['label'])}</h2><audio controls preload='none' src='{row['file']}'></audio><p>{row['duration_seconds']} seconds · {row['words_per_minute']} words/minute · {row['pause_seconds_120ms']} seconds of measured pauses</p></section>" for row in results)
    (OUTPUT / "review.html").write_text("<!doctype html><html lang='ru'><meta charset='utf-8'><title>Qwen 1.7B pace review</title><style>body{font:17px/1.5 system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;background:#f3f5f1;color:#1c3028}section{background:white;padding:1rem;margin:1rem 0;border-radius:12px}audio{width:100%}</style><h1>Qwen 1.7B: narrator pace</h1><p>All generated clips use the same unmarked Book 1 text at normal playback speed. Listen for Vlad likeness, stress, comma pauses, and natural flow. The measurements are screening signals, not a quality verdict.</p><blockquote>" + html.escape(text) + "</blockquote>" + cards + "</html>", encoding="utf-8")
    print(str((OUTPUT / "review.html").resolve()), flush=True)


if __name__ == "__main__":
    main()
