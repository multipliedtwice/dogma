"""Stage book 1's reviewed text route as a provisional app generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from rimsky.integrity import validate_coverage
from rimsky.books import split_text
from rimsky.packs import load_pack
from rimsky.preparation import preparation_report
from rimsky.store import Store
from rimsky.util import AppError, now

BASE = ROOT / "training-work/character-rb1"


def stage(data_dir: Path, pack_dir: Path):
    plan = json.loads((BASE / "book1-app-draft-plan.json").read_text(encoding="utf-8"))
    route_path = BASE / "book1-render-routing.jsonl"
    if hashlib.sha256(route_path.read_bytes()).hexdigest() != plan["routing_sha256"]:
        raise AppError("Book 1 route changed since draft validation.")
    route = [json.loads(line) for line in route_path.open(encoding="utf-8")]
    pack = load_pack(str(pack_dir), verify=True)
    if "provisional" not in pack["name"].lower():
        raise AppError("This staging command accepts only a labelled provisional pack.")
    available = {voice["id"] for voice in pack["voices"]}
    missing = set(plan["voices_required"]) - available
    if missing - {"rb1_tamara"}:
        raise AppError(f"Provisional pack lacks routed voices: {sorted(missing)}")
    store = Store(data_dir)
    try:
        store.update_settings({"pack_path": pack["_root"], "device": "cuda", "align_words": False})
        book = store.import_data((ROOT / "content/reality-benders-1.epub").read_bytes(), "reality-benders-1.epub")
        if book["id"] != plan["book_id"] or book["source_hash"] != plan["source_hash"]:
            raise AppError("Imported EPUB differs from reviewed book 1.")
        existing = [g for g in book["generations"] if g["name"] == "Book 1 · provisional listening draft"]
        if existing:
            return store.generation(existing[0]["id"])
        excluded = set(plan["excluded_readable_watermarks"])
        for chapter in book["chapters"]:
            for block in chapter["blocks"]:
                if block["id"] in excluded:
                    block["exclude_from_narration"] = True
        book["preparation"] = preparation_report(book)
        store.execute("UPDATE books SET manifest=? WHERE id=?", (json.dumps({k:v for k,v in book.items() if k not in ('generations','bookmarks','visuals','position','reading_position','visual_revision')}, ensure_ascii=False), book["id"]))
        config = {"pack_path": pack["_root"], "pack_fingerprint": pack["_fingerprint"],
                  "pack_name": pack["name"], "device": "cuda", "align_words": False}
        generation = store.create_generation(book["id"], config, "Book 1 · provisional listening draft", plan["chapters"])
        gid = generation["id"]
        cast = [{"id": "narrator", "name": "Vlad Rimsky · provisional", "aliases": [],
                 "description": "Narrator", "voice_id": "vlad-rimsky", "voice_name": "Narrator · provisional", "evidence": []}]
        roles = sorted({row["speaker"] for row in route if row["speaker"] not in ("narrator", "unknown")})
        cast.extend({"id": role, "name": role.replace("rb1_", "").replace("_", " "), "aliases": [],
                     "description": "Unreviewed audiobook reference", "voice_id": role if role in available else "vlad-rimsky",
                     "voice_name": role if role in available else "Narrator fallback", "evidence": []} for role in roles)
        analysis = {"complete": True, "source": "reviewed_book1_epub_text_route", "provisional": True,
                    "casting_warnings": ["Listening draft: voice references are unreviewed; channel effects are not implemented.",
                                         "23 speech segments use narrator fallback because their speaker reference is unresolved or missing."]}
        pieces = [(row, row['start'] + start, row['start'] + end, text)
                  for row in route for start, end, text in split_text(row['text'])]
        entries = [(gid, f"{row['source_id']}-{start}-{end}", index*100+1,
                    row["chapter_id"], row["source_id"], start, end, text, text,
                    row["speaker"], "neutral", "pending", None, 0, "[]", "Provisional listening draft")
                   for index, (row, start, end, text) in enumerate(pieces)]
        with store.lock, store.db:
            store.db.execute("DELETE FROM segments WHERE generation_id=?", (gid,))
            store.db.executemany("INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", entries)
            store.db.execute("UPDATE generations SET cast=?,analysis=?,updated=? WHERE id=?",
                             (json.dumps(cast, ensure_ascii=False), json.dumps(analysis, ensure_ascii=False), now(), gid))
        result = store.generation(gid)
        validate_coverage(store.book(book["id"]), plan["chapters"], result["segments"])
        return result
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "training-work/book1-listening-app")
    parser.add_argument("--pack", type=Path, default=ROOT / "training-work/book1-provisional-pack")
    args = parser.parse_args()
    generation = stage(args.data_dir, args.pack)
    print(json.dumps({"book_id": generation["book_id"], "generation_id": generation["id"],
                      "segments": len(generation["segments"]), "status": generation["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
