"""Publish reviewed Higgs retakes and valid audio checksums for the app demo."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.integrity import accepted_audio, read_audio
from rimsky.store import Store
from rimsky.util import write_json

PILOT = ROOT / "training-work/higgs-tts-3-pilot"
DATA = ROOT / "training-work/book1-listening-app"
PLAN = PILOT / "book1-demo.json"


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    gid = plan["generation_id"]
    opening = PILOT / "opening"

    # These are separate model outputs. Concatenation adds no silence and does
    # not change playback speed or the original EPUB text in the reader.
    pieces = [sf.read(PILOT / name, dtype="float32") for name in ("p5-a-plain.wav", "p5-b.wav")]
    if any(rate != 24000 or audio.ndim != 1 for audio, rate in pieces):
        raise ValueError("The two Higgs sentence takes must be mono 24 kHz WAV")
    merged = np.concatenate([audio for audio, _ in pieces])
    sf.write(opening / "c0004-p00003-362-729.wav", merged, 24000, subtype="PCM_16")
    (opening / "c0004-p00003-362-729.prompt.txt").write_text(
        "Sentence 1: plain source text\nSentence 2: <|prosody:speed_slow|> + source text\n"
        "Two Higgs takes concatenated without added silence or stretching.\n", encoding="utf-8")
    shutil.copy2(PILOT / "heading-retry.wav", opening / "c0004-p00001-0-24.wav")
    (opening / "c0004-p00001-0-24.prompt.txt").write_text(
        "<|prosody:speed_slow|>Введение: Первый контакт.\n", encoding="utf-8")

    store = Store(DATA)
    try:
        with store.lock, store.db:
            for item in plan["segments"]:
                source = opening / f"{item['id']}.wav"
                key = hashlib.sha256(source.read_bytes()).hexdigest()
                target = DATA / "audio" / f"{key}.wav"
                if not target.exists():
                    shutil.copy2(source, target)
                metadata = read_audio(target)
                write_json(target.with_suffix(".json"), {**metadata, "warning": ""})
                accepted_audio(target)
                store.db.execute(
                    "UPDATE segments SET audio_hash=?,duration=?,cues='[]' WHERE generation_id=? AND id=?",
                    (key, metadata["duration"], gid, item["id"]))
                item.update(audio_hash=key, duration=metadata["duration"], wav=str(source))
        plan["status"] = "ready"
        plan["audit"] = "All WAV files pass app checksum and PCM validation."
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"generation_id": gid, "segments": len(plan["segments"]),
                          "duration": round(sum(item["duration"] for item in plan["segments"]), 2)}))
    finally:
        store.close()


if __name__ == "__main__":
    main()
