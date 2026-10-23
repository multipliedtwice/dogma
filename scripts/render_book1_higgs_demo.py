"""Render a short, separately selectable Book 1 Higgs demonstration.

The source EPUB text and paragraph offsets come from the existing Book 1
generation. Audio is rendered at the model's natural speed; no time stretching
or synthetic silence is applied. Existing generations are left untouched.
"""

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.production_higgs_model import resolve_production_higgs_model

PILOT = ROOT / "training-work/higgs-tts-3-pilot"
DATA = ROOT / "training-work/book1-listening-app"
MANIFEST = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
CLI = PILOT / "audio-cpp-cuda/audiocpp_cli.exe"
MODEL = resolve_production_higgs_model(ROOT).path
SOURCE_GENERATION = "9eaffc3307f50ca0077e3332"
CHAPTER = "c0004"
COUNT = 5
OUT = PILOT / "opening"
PLAN = PILOT / "book1-demo.json"
CONTROL = re.compile(r"<\|prosody:[a-z_]+\|>")


def prompt_for(text: str) -> str:
    text = text.strip()
    prompt = "<|prosody:speed_slow|>" + text
    if re.sub(r"\s+", " ", CONTROL.sub("", prompt)).strip() != re.sub(r"\s+", " ", text):
        raise ValueError("Control tags changed the source words")
    return prompt


def render(segment: sqlite3.Row) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / f"{segment['id']}.wav"
    prompt_file = OUT / f"{segment['id']}.prompt.txt"
    prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else prompt_for(segment["spoken"])
    if not wav.exists():
        command = [str(CLI), "--task", "tts", "--family", "higgs_audio_tts",
                   "--model", str(MODEL), "--backend", "cuda", "--threads", "8",
                   "--text", prompt, "--voice-ref", str(PILOT / MANIFEST["reference_audio"]),
                   "--reference-text", MANIFEST["reference_text"],
                   "--max-tokens", "2048", "--out", str(wav)]
        started = time.monotonic()
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", timeout=3600)
        (OUT / f"{segment['id']}.log").write_text(result.stdout, encoding="utf-8")
        if result.returncode or not wav.exists():
            raise RuntimeError(f"Higgs failed on {segment['id']}: {result.stdout[-1500:]}")
        prompt_file.write_text(prompt, encoding="utf-8")
        print(json.dumps({"id": segment["id"], "render_seconds": round(time.monotonic()-started, 1)}), flush=True)
    info = sf.info(wav)
    if info.samplerate != 24000 or info.duration < 0.4:
        raise ValueError(f"Unexpected Higgs WAV: {wav}")
    return {"id": segment["id"], "wav": str(wav), "duration": round(info.duration, 3),
            "prompt": prompt}


def publish(rows: list[sqlite3.Row], rendered: list[dict]) -> str:
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.integrity import read_audio
    from rimsky.store import Store
    from rimsky.util import now, uid, write_json

    store = Store(DATA)
    try:
        original = store.generation(SOURCE_GENERATION)
        config = {**original["config"], "chapters": [CHAPTER], "align_words": False,
                  "pack_name": "Higgs TTS 3 Q8 · local opening demo",
                  "pack_path": "", "pack_fingerprint": "79746822045b5bf8f9ab2bda87b16cd3f8ea3d9e319cbcf887a87aa1b537a74a",
                  "engine": "higgs_audio_tts_q8_external_demo", "model_file": str(MODEL),
                  "reference_audio": str(PILOT / MANIFEST["reference_audio"])}
        for item in rendered:
            source = Path(item["wav"])
            key = hashlib.sha256(source.read_bytes()).hexdigest()
            target = DATA / "audio" / f"{key}.wav"
            if not target.exists():
                shutil.copy2(source, target)
            item["audio_hash"] = key
            write_json(target.with_suffix(".json"), {**read_audio(target), "warning": ""})
        gid = uid()
        with store.lock, store.db:
            store.db.execute(
                "INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis,parent_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (gid, original["book_id"], "Book 1 · Higgs TTS 3 opening demo", now(), now(), "ready",
                 json.dumps(config, ensure_ascii=False), json.dumps(original["cast"], ensure_ascii=False),
                 json.dumps(original["analysis"], ensure_ascii=False), SOURCE_GENERATION))
            store.db.executemany(
                "INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,"
                "status,audio_hash,duration,cues,warning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(gid, row["id"], row["ordinal"], row["chapter_id"], row["source_id"], row["start"],
                  row["end"], row["text"], row["spoken"], row["speaker"], row["delivery"],
                  "ready", audio["audio_hash"], audio["duration"], "[]", "")
                 for row, audio in zip(rows, rendered, strict=True)])
        PLAN.write_text(json.dumps({"generation_id": gid, "source_generation": SOURCE_GENERATION,
                                    "segments": rendered, "status": "ready"}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return gid
    finally:
        store.close()


def main() -> None:
    if PLAN.exists():
        saved = json.loads(PLAN.read_text(encoding="utf-8"))
        if saved.get("status") == "ready":
            print(json.dumps({"generation_id": saved["generation_id"], "status": "already_ready"}))
            return
    if not CLI.exists() or not MODEL.exists():
        raise FileNotFoundError("Install the audio.cpp runtime and Higgs GGUF first")
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM segments WHERE generation_id=? AND chapter_id=? ORDER BY ordinal LIMIT ?",
            (SOURCE_GENERATION, CHAPTER, COUNT)).fetchall()
    if len(rows) != COUNT or any("\u0301" in row["spoken"] for row in rows):
        raise RuntimeError("Book 1 opening source changed or has rejected accent marks")
    rendered = []
    for index, row in enumerate(rows, 1):
        print(json.dumps({"segment": index, "total": COUNT, "id": row["id"],
                          "characters": len(row["spoken"])}), flush=True)
        rendered.append(render(row))
    print(json.dumps({"generation_id": publish(rows, rendered), "status": "ready"}), flush=True)


if __name__ == "__main__":
    main()
