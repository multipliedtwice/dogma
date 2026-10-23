"""Regenerate the reported introduction pacing and Shikharsa pronunciation clips.

This keeps prior content-addressed WAVs in place and publishes each retake only
after the new file passes the same integrity gate as the main render.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from render_book1_higgs_half import (CLI, DATA, OUT, PLAN, ROOT, TAG,
                                     padded_audio, prompt_for, reference_routes,
                                     valid_wav)

sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.production_higgs_model import resolve_production_higgs_model

MODEL = resolve_production_higgs_model(ROOT).path

TARGETS = (
    "c0004-p00002-0-378",
    "c0004-p00002-378-522",
    "c0004-p00004-0-358",
    "c0008-p00026-97-491",
    "c0010-p00046-0-73",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", default=list(TARGETS))
    parser.add_argument("--out-name", default="feedback-retakes")
    parser.add_argument("--internal-ellipsis", action="store_true")
    parser.add_argument("--batch-chunk-size", default="80")
    args = parser.parse_args()
    targets = tuple(args.ids)
    if not args.out_name or Path(args.out_name).name != args.out_name:
        raise ValueError("The retake folder must be a simple name")
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.integrity import accepted_audio, read_audio
    from rimsky.store import Store
    from rimsky.util import now, write_json

    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    generation_id = plan["generation_id"]
    retakes = OUT / args.out_name
    retakes.mkdir(exist_ok=True)
    routes = reference_routes()
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        db.row_factory = sqlite3.Row
        if args.internal_ellipsis:
            targets = tuple(row['id'] for row in db.execute(
                "SELECT id,spoken FROM segments WHERE generation_id=? "
                "AND status='ready' ORDER BY ordinal", (generation_id,))
                if re.search(r'(?:…|\.{3})\s*\S', row['spoken']))
        rows = db.execute(
            "SELECT * FROM segments WHERE generation_id=? AND id IN (" +
            ",".join("?" for _ in targets) + ") ORDER BY ordinal",
            (generation_id, *targets),
        ).fetchall()
    if len(rows) != len(targets) or any(row["status"] != "ready" for row in rows):
        raise RuntimeError("The reported retake targets must all be ready")
    groups: dict[tuple[Path, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(routes.get(row["speaker"], routes["narrator"]), []).append(row)
    store = Store(DATA)
    try:
        for group_index, ((voice, reference_text), group) in enumerate(groups.items(), 1):
            folder = retakes / f"group-{group_index}"
            inputs, outputs = folder / "input", folder / "output"
            inputs.mkdir(parents=True, exist_ok=True)
            outputs.mkdir(exist_ok=True)
            for row in group:
                (inputs / f"{row['id']}.txt").write_text(prompt_for(row), encoding="utf-8")
            command = [str(CLI), "--task", "tts", "--family", "higgs_audio_tts",
                       "--model", str(MODEL), "--backend", "cuda", "--threads", "8",
                       "--max-tokens", "1024", "--text-chunk-mode", "tag_aware",
                       "--voice-ref", str(voice), "--reference-text", reference_text,
                       "--batch-text-dir", str(inputs), "--out-dir", str(outputs),
                       "--text-chunk-size", args.batch_chunk_size]
            if not all(valid_wav(outputs / f"{row['id']}.wav") for row in group):
                with (folder / "render.log").open("a", encoding="utf-8") as log:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                            timeout=60 * 60, check=False)
                if result.returncode:
                    print(f"Retake batch {group_index} needs individual retries", flush=True)
            for row in group:
                source = outputs / f"{row['id']}.wav"
                if valid_wav(source):
                    continue
                original = prompt_for(row)
                attempts = (("short", original, "60"),
                            ("smaller", original, "40")) if args.internal_ellipsis else (
                            ("short", original, "80"),
                            ("plain", TAG.sub("", original), "60"))
                for variant, prompt, chunk_size in attempts:
                    single = [str(CLI), "--task", "tts", "--family", "higgs_audio_tts",
                              "--model", str(MODEL), "--backend", "cuda", "--threads", "8",
                              "--max-tokens", "1024", "--text-chunk-mode", "tag_aware",
                              "--voice-ref", str(voice), "--reference-text", reference_text,
                              "--text", prompt, "--out", str(source),
                              "--text-chunk-size", chunk_size]
                    with (folder / "render.log").open("a", encoding="utf-8") as log:
                        log.write(f"\nRETRY {row['id']} {variant}\n")
                        subprocess.run(single, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=60 * 30, check=False)
                    if valid_wav(source):
                        (folder / f"{row['id']}.render-variant.txt").write_text(
                            variant, encoding="utf-8")
                        break
            for row in group:
                source = outputs / f"{row['id']}.wav"
                if not valid_wav(source):
                    raise RuntimeError(f"Invalid retake WAV for {row['id']}")
                published_source = padded_audio(source, row['id'], row['spoken'])
                key = hashlib.sha256(published_source.read_bytes()).hexdigest()
                target = DATA / "audio" / f"{key}.wav"
                if not target.exists():
                    try:
                        os.link(published_source, target)
                    except OSError:
                        shutil.copy2(published_source, target)
                metadata = read_audio(target)
                write_json(target.with_suffix(".json"), {**metadata, "warning": ""})
                accepted_audio(target)
                with store.lock, store.db:
                    store.db.execute(
                        "UPDATE segments SET audio_hash=?,duration=?,warning='',delivery=? "
                        "WHERE generation_id=? AND id=?",
                        (key, metadata["duration"],
                         "enumeration" if row["id"] == "c0004-p00002-378-522" else
                         "excited" if row["id"] == "c0005-p00024-0-25" else row["delivery"],
                         generation_id, row["id"]),
                    )
                    store.db.execute("UPDATE generations SET updated=? WHERE id=?",
                                     (now(), generation_id))
                print(json.dumps({"id": row["id"], "old_duration": row["duration"],
                                  "new_duration": metadata["duration"], "audio_hash": key}),
                      flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
