"""Resumable Higgs listening render for the first half of Reality Benders 1.

Uses the already reviewed EPUB segment boundaries and stores each completed WAV
in the local app as soon as it passes the audio integrity check. The external
audio.cpp renderer is deliberately separate from the app's Qwen worker.
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
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from higgs_render_holds import render_issues, warning_with, with_holds, with_provenance  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.production_higgs_model import resolve_production_higgs_model

PILOT = ROOT / "training-work/higgs-tts-3-pilot"
DATA = ROOT / "training-work/book1-listening-app"
SENTENCE_SAFE = os.environ.get("RIMSKY_HIGGS_SENTENCE_SAFE") == "1"
OUT = PILOT / ("book1-half-sentence" if SENTENCE_SAFE else "book1-half")
CLI = PILOT / "audio-cpp-cuda/audiocpp_cli.exe"
MODEL = resolve_production_higgs_model(ROOT).path
REFERENCE = PILOT / "vlad_reference.wav"
SOURCE_GENERATION = "9eaffc3307f50ca0077e3332"
LAST_CHAPTER = "c0024"
PLAN = OUT / "plan.json"
TAG = re.compile(r"<\|(?:prosody|emotion|style):[a-z_]+\|>")
SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«—])")
PROMPT_VERSION = 8
BOUNDARY_VERSION = 1
ENUMERATION_SENTENCES = {
    "c0004-p00002-0-378": {1, 2, 3},
    "c0004-p00002-378-522": {0, 1, 2, 3, 4},
}
PRONUNCIATION_HINTS = {
    "Шихарса": "ШихАрса",
    "Нарочито": "НарочИто",
    "нарочито": "нарочИто",
}


def clean_spoken(text: str) -> str:
    # Combining accents are rejected by Higgs and were rejected in the demo.
    return text.replace("\u0301", "")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_part_heading(segment_id: str, spoken: str) -> bool:
    return (bool(re.fullmatch(r"c\d+-p\d+-0-\d+", segment_id)) and
            len(spoken.strip()) <= 120 and
            bool(re.match(r"^(?:Глава|Часть|Пролог|Эпилог|Введение)\b",
                          spoken.strip(), re.IGNORECASE)))


def boundary_padding(segment_id: str, spoken: str) -> tuple[float, float]:
    if is_part_heading(segment_id, spoken):
        return (0.85, 1.15)
    if spoken.rstrip().endswith(("…", "...")):
        return (0.0, 0.8)
    return (0.0, 0.0)


def padded_audio(source: Path, segment_id: str, spoken: str) -> Path:
    before, after = boundary_padding(segment_id, spoken)
    if not before and not after:
        return source
    destination = OUT / "boundary-audio" / f"v{BOUNDARY_VERSION}" / f"{segment_id}.wav"
    if valid_wav(destination) and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    samples, rate = sf.read(source, dtype="float32")
    if rate != 24000 or samples.ndim != 1:
        raise ValueError(f"Unexpected format for {segment_id}")
    padded = np.pad(samples, (round(before * rate), round(after * rate)))
    sf.write(destination, padded, rate, subtype="PCM_16")
    return destination


def prompt_for(row: sqlite3.Row) -> str:
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.performance import performance_plan
    source = clean_spoken(row["spoken"])
    source_id = row["source_id"] if "source_id" in row.keys() else row["id"]
    intent = performance_plan({"text": source, "delivery": row["delivery"],
                               "speaker": row["speaker"], "source_id": source_id})
    if row["id"] == "c0004-p00001-0-24":
        return "<|prosody:speed_slow|>" + source
    if source_id in ("c0005-p00024", "c0005-p00024-0-25"):
        return "<|emotion:elation|><|prosody:expressive_high|>" + source
    if row["id"] == "c0005-p00025-0-8":
        return "<|emotion:surprise|><|prosody:expressive_low|><|prosody:speed_slow|>И замер…"
    sentences = SENTENCE.split(source)
    prepared = []
    for i, sentence in enumerate(sentences):
        tags = ""
        opening_list = ("source_id" in row.keys() and row["source_id"] == "c0004-p00002"
                        and sentence.strip().startswith(("И получение", "И находка", "И появление",
                            "Падение", "Встреча", "Вторжение", "Война", "Истребление")))
        enumeration = (opening_list or i in ENUMERATION_SENTENCES.get(row["id"], set()) or
                       "enumeration" in intent["cues"])
        if enumeration:
            tags += "<|prosody:speed_fast|>"
        if row["delivery"] == "stunned":
            tags += "<|emotion:surprise|><|prosody:expressive_low|><|prosody:speed_slow|>"
        if row["speaker"] not in ("narrator", "mechanical_game_voice"):
            tags += {"excited": "<|emotion:enthusiasm|>",
                     "sad": "<|emotion:sadness|>",
                     "angry": "<|emotion:anger|>"}.get(row["delivery"], "")
        if row["speaker"] == "mechanical_game_voice":
            tags += "<|prosody:expressive_low|>"
        elif row["speaker"] == "narrator":
            if not enumeration and (i == 0 and len(sentence) >= 70 or sentence.rstrip().endswith("…") or
                                    "section_heading" in intent["cues"]):
                tags += "<|prosody:speed_slow|>"
        elif sentence.rstrip().endswith(("!", "?")):
            tags += "<|prosody:expressive_high|>"
        prepared.append(tags + sentence)
    prompt = " ".join(prepared)
    prompt = re.sub(r"(…|\.{3})(?=\s*\S)",
                    r"\1<|prosody:long_pause|>", prompt)
    if re.sub(r"\s+", " ", TAG.sub("", prompt)).strip() != re.sub(r"\s+", " ", source).strip():
        raise ValueError(f"Prompt changed the source words: {row['id']}")
    for spelling, hint in PRONUNCIATION_HINTS.items():
        prompt = prompt.replace(spelling, hint)
    return prompt


def rows_for_half(db: sqlite3.Connection) -> list[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM segments WHERE generation_id=? AND chapter_id<=? ORDER BY ordinal",
        (SOURCE_GENERATION, LAST_CHAPTER),
    ).fetchall()
    if not rows or rows[0]["chapter_id"] != "c0002" or rows[-1]["chapter_id"] != LAST_CHAPTER:
        raise RuntimeError("The Book 1 source or half-book boundary changed")
    if not SENTENCE_SAFE:
        return rows
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.books import sentence_spans
    from rimsky.performance import EXCITED_ATTRIBUTION
    book_id = db.execute("SELECT book_id FROM generations WHERE id=?", (SOURCE_GENERATION,)).fetchone()[0]
    manifest = json.loads(db.execute("SELECT manifest FROM books WHERE id=?", (book_id,)).fetchone()[0])
    original = {block["id"]: block["text"] for chapter in manifest["chapters"]
                for block in chapter["blocks"]}
    by_source: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        by_source.setdefault((row["chapter_id"], row["source_id"]), []).append(row)
    result = []
    for (chapter, source_id), items in by_source.items():
        source = "".join(item["text"] for item in items)
        if items[0]["start"] != 0 or source != original[source_id] or any(
                left["end"] != right["start"] for left, right in zip(items, items[1:])):
            raise RuntimeError(f"Source coverage changed for {source_id}")
        units = []
        for start, end in sentence_spans(source):
            overlapping = [(item, min(end, item["end"]) - max(start, item["start"]))
                           for item in items if item["start"] < end and item["end"] > start]
            characters = {item["speaker"] for item, _ in overlapping
                          if item["speaker"] not in ("narrator", "unknown")}
            candidates = [(item, size) for item, size in overlapping
                          if item["speaker"] in characters] if characters else overlapping
            speaker = max(candidates, key=lambda entry: entry[1])[0]["speaker"]
            expressive = [(item, size) for item, size in overlapping
                          if item["speaker"] == speaker and item["delivery"] != "neutral"]
            delivery = max(expressive, key=lambda entry: entry[1])[0]["delivery"] if expressive else "neutral"
            if speaker not in ("narrator", "unknown") and EXCITED_ATTRIBUTION.search(source):
                delivery = "excited"
            if source_id == "c0005-p00025" and source[start:end].strip() == "И замер…":
                delivery = "stunned"
            units.append({"chapter_id": chapter, "source_id": source_id,
                          "start": start, "end": end, "text": source[start:end],
                          "speaker": speaker, "delivery": delivery})
        grouped = []
        for item in units:
            previous = grouped[-1] if grouped else None
            if (previous and previous["speaker"] == item["speaker"]
                    and previous["delivery"] == item["delivery"]
                    and not previous["text"].rstrip().endswith(("…", "..."))
                    and len(previous["text"]) + len(item["text"]) <= 420):
                previous["end"] = item["end"]
                previous["text"] += item["text"]
            else:
                grouped.append(item)
        for item in grouped:
            item["id"] = f'{source_id}-{item["start"]}-{item["end"]}'
            item["spoken"] = item["text"]
            item["ordinal"] = len(result)
            result.append(item)
    return result


def prepare() -> dict:
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.store import Store
    from rimsky.performance import performance_plan
    from rimsky.util import now, uid

    OUT.mkdir(parents=True, exist_ok=True)
    if PLAN.exists():
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        if plan["source_generation"] != SOURCE_GENERATION or plan["last_chapter"] != LAST_CHAPTER:
            raise RuntimeError("Existing Higgs plan has a different source")
        if plan.get("prompt_version", 0) < PROMPT_VERSION:
            with sqlite3.connect(DATA / "library.sqlite3") as db:
                rows = rows_for_half(db)
                if SENTENCE_SAFE:
                    from rimsky.performance import performance_plan
                    db.executemany(
                        "UPDATE segments SET delivery=?,performance=? WHERE generation_id=? AND id=? AND status!='ready'",
                        [(row["delivery"], json.dumps(performance_plan(dict(row)), ensure_ascii=False),
                          plan["generation_id"], row["id"]) for row in rows])
                    db.commit()
                ready = {item[0] for item in db.execute(
                    "SELECT id FROM segments WHERE generation_id=? AND status='ready'", (plan["generation_id"],))}
            for row in rows:
                if row["id"] not in ready:
                    (OUT / row["chapter_id"] / "input" / f"{row['id']}.txt").write_text(prompt_for(row), encoding="utf-8")
            plan["prompt_version"] = PROMPT_VERSION
            PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return plan
    if not CLI.exists() or not MODEL.exists() or not REFERENCE.exists():
        raise FileNotFoundError("Higgs audio.cpp runtime, model, or reference is missing")
    store = Store(DATA)
    try:
        source = store.generation(SOURCE_GENERATION)
        rows = rows_for_half(store.db)
        prompts = {}
        for row in rows:
            prompts[row["id"]] = prompt_for(row)
        gid = uid()
        config = {**source["config"], "chapters": sorted({row["chapter_id"] for row in rows}),
                  "align_words": False, "engine": "higgs_audio_tts_q8_external_review",
                  "pack_name": "Higgs TTS 3 Q8 · sentence-safe first half" if SENTENCE_SAFE else "Higgs TTS 3 Q8 · first half review",
                  "pack_path": "", "pack_fingerprint": file_sha256(MODEL),
                  "model_file": str(MODEL), "reference_audio": str(REFERENCE),
                  "render_plan": str(PLAN)}
        with store.lock, store.db:
            store.db.execute(
                "INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis,parent_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (gid, source["book_id"], "Book 1 · Higgs sentence-safe first half" if SENTENCE_SAFE else "Book 1 · Higgs first half review", now(), now(), "rendering",
                 json.dumps(config, ensure_ascii=False), json.dumps(source["cast"], ensure_ascii=False),
                 json.dumps(source["analysis"], ensure_ascii=False), SOURCE_GENERATION),
            )
            store.db.executemany(
                "INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,performance) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(gid, row["id"], row["ordinal"], row["chapter_id"], row["source_id"],
                  row["start"], row["end"], row["text"], clean_spoken(row["spoken"]),
                  row["speaker"], row["delivery"], "pending",
                  json.dumps(performance_plan(dict(row)), ensure_ascii=False)) for row in rows],
            )
        # A plan is written only after the database transaction succeeds.
        plan = {"generation_id": gid, "source_generation": SOURCE_GENERATION,
                "last_chapter": LAST_CHAPTER, "segment_count": len(rows),
                "characters": sum(len(row["spoken"]) for row in rows),
                "chapters": sorted({row["chapter_id"] for row in rows}),
                "model_sha256": config["pack_fingerprint"], "status": "rendering",
                "prompt_version": PROMPT_VERSION, "sentence_safe": SENTENCE_SAFE}
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for chapter in plan["chapters"]:
            inputs = OUT / chapter / "input"
            inputs.mkdir(parents=True, exist_ok=True)
            for row in rows:
                if row["chapter_id"] == chapter:
                    (inputs / f"{row['id']}.txt").write_text(prompts[row["id"]], encoding="utf-8")
        return plan
    finally:
        store.close()


def valid_wav(path: Path) -> bool:
    try:
        info = sf.info(path)
        return info.samplerate == 24000 and info.channels == 1 and info.duration >= .35 and info.subtype == "PCM_16"
    except (OSError, RuntimeError):
        return False


def reference_routes() -> dict[str, tuple[Path, str]]:
    routes = {"narrator": (REFERENCE, json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))["reference_text"])}
    bank = json.loads((ROOT / "training-work/character-rb1/provisional-voice-reference-bank.json").read_text(encoding="utf-8"))
    game = json.loads((ROOT / "training-work/character-rb1/provisional-game-ui-reference-bank.json").read_text(encoding="utf-8"))
    for role, candidates in [(entry["character_id"], entry["candidate_references"]) for entry in bank["references"]] + [("mechanical_game_voice", game["candidate_references"])]:
        clean = [candidate for candidate in candidates
                 if set(candidate.get("flags", [])) <= {"sentence_fragment"}
                 and 3 <= candidate.get("duration", 0) <= 12
                 and Path(candidate["audio"]).is_file() and candidate.get("text", "").strip()]
        clean.sort(key=lambda candidate: bool(candidate.get("flags")))
        if clean:
            chosen = clean[0]
            audio = Path(chosen["audio"])
            if file_sha256(audio) == chosen["audio_sha256"]:
                routes[role] = (audio, chosen["text"].strip())
    return routes


def publish(plan: dict, chapter: str) -> int:
    sys.path.insert(0, str(ROOT / "runtime"))
    from rimsky.integrity import accepted_audio, read_audio
    from rimsky.store import Store
    from rimsky.util import now, write_json

    output = OUT / chapter / "output"
    routes = reference_routes()
    store = Store(DATA)
    count = 0
    try:
        rows = store.db.execute(
            "SELECT id,source_id,start,end,spoken,speaker,performance FROM segments "
            "WHERE generation_id=? AND chapter_id=? AND status!='ready' ORDER BY ordinal",
            (plan["generation_id"], chapter),
        ).fetchall()
        for row in rows:
            source = output / f"{row['id']}.wav"
            if not valid_wav(source):
                continue
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
            # Removed controls or a stand-in voice keep the take playable but
            # provisional, under a hold that only a new take clears.
            segment = dict(row)
            issues, _ = render_issues(segment, OUT / chapter, routes)
            performance = with_provenance(json.loads(row["performance"] or "{}"), issues, key)
            with store.lock, store.db:
                store.db.execute(
                    "UPDATE segments SET status='ready',audio_hash=?,duration=?,cues='[]',warning=?,performance=? "
                    "WHERE generation_id=? AND id=?",
                    (key, metadata["duration"], warning_with("", issues),
                     json.dumps(performance, ensure_ascii=False), plan["generation_id"], row["id"]),
                )
                analysis = json.loads(store.db.execute("SELECT analysis FROM generations WHERE id=?",
                                                       (plan["generation_id"],)).fetchone()["analysis"])
                store.db.execute("UPDATE generations SET updated=?,analysis=? WHERE id=?",
                                 (now(), json.dumps(with_holds(analysis, segment, issues, key), ensure_ascii=False),
                                  plan["generation_id"]))
            count += 1
        return count
    finally:
        store.close()


def run_chapter(plan: dict, chapter: str) -> None:
    folder = OUT / chapter
    inputs = folder / "input"
    outputs = folder / "output"
    outputs.mkdir(exist_ok=True)
    # One CUDA model load per chapter. If a batch is interrupted, only missing
    # segments enter the next batch, so this is safe to rerun.
    remaining = [item for item in sorted(inputs.glob("*.txt")) if not valid_wav(outputs / f"{item.stem}.wav")]
    if not remaining:
        print(json.dumps({"chapter": chapter, "recovered": publish(plan, chapter)}), flush=True)
        return
    pending = folder / "pending"
    pending.mkdir(exist_ok=True)
    routes = reference_routes()
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        speakers = dict(db.execute("SELECT id,speaker FROM segments WHERE generation_id=? AND chapter_id=?",
                                   (plan["generation_id"], chapter)).fetchall())
    common = [str(CLI), "--task", "tts", "--family", "higgs_audio_tts",
              "--model", str(MODEL), "--backend", "cuda", "--threads", "8",
              "--max-tokens", "2048", "--text-chunk-mode", "tag_aware", "--metrics"]
    failures = []
    grouped: dict[tuple[Path, str], list[Path]] = {}
    for item in remaining:
        grouped.setdefault(routes.get(speakers[item.stem], routes["narrator"]), []).append(item)
    batches = [(reference, items[start:start + 20]) for reference, items in grouped.items()
               for start in range(0, len(items), 20)]
    for batch_index, (reference, batch) in enumerate(batches, 1):
        voice, reference_text = reference
        for old in pending.glob("*.txt"):
            old.unlink()
        for item in batch:
            shutil.copy2(item, pending / item.name)
        voice_args = ["--voice-ref", str(voice), "--reference-text", reference_text]
        command = common + voice_args + ["--batch-text-dir", str(pending), "--out-dir", str(outputs),
                            "--batch-manifest-out", str(folder / "batch-manifest.json"),
                            "--text-chunk-size", "4096"]
        with (folder / "render.log").open("a", encoding="utf-8") as log:
            log.write(f"\nSTART {time.strftime('%Y-%m-%d %H:%M:%S')} batch={batch_index} count={len(batch)} ref={voice}\n")
            log.flush()
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=60 * 60 * 4, check=False)
            log.write(f"\nEXIT {result.returncode} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        published = publish(plan, chapter)
        missing = [item for item in batch if not valid_wav(outputs / f"{item.stem}.wav")]
        for item in missing:
            # A failed sentence stays whole. Retry without control tags using
            # the same voice; never hide a failed sentence behind a word cut.
            original_prompt = item.read_text(encoding="utf-8")
            plain_prompt = TAG.sub("", original_prompt)
            attempts = [("plain_same_voice", plain_prompt, voice_args)]
            for variant, prompt, attempt_voice in attempts:
                command = common + attempt_voice + ["--text", prompt,
                                    "--out", str(outputs / f"{item.stem}.wav"),
                                    "--text-chunk-size", "4096"]
                with (folder / "render.log").open("a", encoding="utf-8") as log:
                    log.write(f"\nRETRY {item.stem} variant={variant} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    log.flush()
                    retry = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=60 * 30, check=False)
                    log.write(f"\nRETRY EXIT {retry.returncode}\n")
                if valid_wav(outputs / f"{item.stem}.wav"):
                    (folder / f"{item.stem}.render-prompt.txt").write_text(prompt, encoding="utf-8")
                    (folder / f"{item.stem}.render-variant.txt").write_text(variant, encoding="utf-8")
                    published += publish(plan, chapter)
                    break
            if not valid_wav(outputs / f"{item.stem}.wav"):
                failures.append(item.stem)
        print(json.dumps({"chapter": chapter, "batch": batch_index,
                          "rendered": published, "failed": len(failures)}), flush=True)
    if failures:
        (folder / "failures.json").write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--through", default=LAST_CHAPTER, help="Stop after a chapter for a short verification run")
    args = parser.parse_args()
    plan = prepare()
    print(json.dumps({"generation_id": plan["generation_id"], "segments": plan["segment_count"],
                      "chapters": len(plan["chapters"]), "status": plan["status"]}), flush=True)
    if SENTENCE_SAFE:
        gate = subprocess.run([sys.executable, str(ROOT / 'scripts/audit_book1_sentence_chunks.py'),
                               plan['generation_id']], capture_output=True, text=True,
                              encoding='utf-8', timeout=120)
        if gate.returncode:
            raise RuntimeError('Sentence seam gate failed: ' + gate.stdout[-3000:] + gate.stderr[-500:])
        print(gate.stdout.strip(), flush=True)
    if args.prepare_only:
        return
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    try:
        for chapter in plan["chapters"]:
            run_chapter(plan, chapter)
            if chapter == args.through:
                break
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    if args.through == LAST_CHAPTER:
        with sqlite3.connect(DATA / "library.sqlite3") as db:
            ready, total = db.execute(
                "SELECT sum(status='ready'),count(*) FROM segments WHERE generation_id=?",
                (plan["generation_id"],),
            ).fetchone()
            if ready == total == plan["segment_count"]:
                db.execute("UPDATE generations SET status='ready' WHERE id=?", (plan["generation_id"],))
                plan["status"] = "ready"
            else:
                db.execute("UPDATE generations SET status='failed',error=? WHERE id=?",
                           (f"Higgs render needs retry: {total - ready} passages remain.", plan["generation_id"]))
                plan["status"] = "needs_retry"
            PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"ready": ready, "total": total, "status": plan["status"]}), flush=True)


if __name__ == "__main__":
    main()
