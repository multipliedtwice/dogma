"""Validate the reviewed book-1 text route as an app-playable draft plan."""

from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.integrity import validate_coverage

BASE = ROOT / "training-work" / "character-rb1"


def main():
    corpus = json.loads((ROOT / "training-work/corpus-rb1/corpus.json").read_text(encoding="utf-8"))
    book = json.loads((ROOT / "training-work/corpus-rb1" / corpus["books"][0]["manifest"]).read_text(encoding="utf-8"))
    source = ROOT / "content/reality-benders-1.epub"
    if hashlib.sha256(source.read_bytes()).hexdigest() != book["source_hash"]:
        raise ValueError("Book 1 EPUB changed since corpus routing")
    route_path = BASE / "book1-render-routing.jsonl"
    route = [json.loads(line) for line in route_path.open(encoding="utf-8")]
    summary = json.loads((BASE / "book1-render-routing-summary.json").read_text(encoding="utf-8"))
    if len(route) != summary["segments"]:
        raise ValueError("Routing count changed")
    excluded = {row["source_id"] for row in summary["excluded_source_blocks"]}
    matched = set()
    for chapter in book["chapters"]:
        for block in chapter["blocks"]:
            if block["id"] in excluded:
                block["exclude_from_narration"] = True
                matched.add(block["id"])
    if matched != excluded:
        raise ValueError("Watermark exclusions do not match source")
    chapters = list(dict.fromkeys(row["chapter_id"] for row in route))
    validate_coverage(book, chapters, [{**row, "spoken": row["text"]} for row in route])
    fallback = [row for row in route if not row["voice_id"]]
    report = {
        "schema": 1, "book_id": book["id"], "source_hash": book["source_hash"],
        "routing_sha256": hashlib.sha256(route_path.read_bytes()).hexdigest(),
        "chapters": chapters, "segments": len(route),
        "excluded_readable_watermarks": sorted(excluded),
        "modes": dict(collections.Counter(row["mode"] for row in route)),
        "voices_required": sorted({row["voice_id"] for row in route if row["voice_id"]}),
        "provisional_reference_segments": sum(not row["voice_reference_human_reviewed"] for row in route),
        "fallback_voice_segments": [{"source_id": row["source_id"], "speaker": row["speaker"], "start": row["start"]} for row in fallback],
        "channel_effects_unimplemented": dict(collections.Counter(row["channel"] for row in route if row["channel"] != "direct")),
        "ready_for_final_render": False,
        "ready_for_draft_staging": True,
    }
    (BASE / "book1-app-draft-plan.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"chapters": len(chapters), "segments": len(route), "excluded": len(excluded), "fallback": len(fallback)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
