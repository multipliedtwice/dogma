"""Stage a separately pinned Qwen 1.7B Base demo for the Book 1 reader."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime"))

from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import now, uid
from scripts.seal_pack import seal

DATA = ROOT / "training-work/book1-listening-app"
SOURCE_PACK = ROOT / "training-work/book1-provisional-pack"
MODEL = ROOT / "training-work/models/Qwen3-TTS-12Hz-1.7B-Base"
DEMO_PACK = ROOT / "training-work/book1-17b-demo-pack"
SOURCE_GENERATION = "67539b21d79d03b223bfb666"


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size != source.stat().st_size:
            raise RuntimeError(f"Existing demo asset has a different size: {target}")
        return
    shutil.copy2(source, target)


def build_pack():
    DEMO_PACK.mkdir(exist_ok=True)
    for source in SOURCE_PACK.rglob("*"):
        if source.is_file() and source.name != "pack.json" and not source.is_relative_to(SOURCE_PACK / "models/speech"):
            copy_file(source, DEMO_PACK / source.relative_to(SOURCE_PACK))
    for source in MODEL.rglob("*"):
        if source.is_file() and ".cache" not in source.parts:
            copy_file(source, DEMO_PACK / "models/speech" / source.relative_to(MODEL))
    pack = json.loads((SOURCE_PACK / "pack.json").read_text(encoding="utf-8"))
    pack["name"] = "Reality Benders 1 · Qwen 1.7B Base demo · provisional"
    pack["files"] = {}
    (DEMO_PACK / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    seal(DEMO_PACK)
    return load_pack(str(DEMO_PACK), verify=True)


def stage(pack):
    store = Store(DATA)
    try:
        original = store.generation(SOURCE_GENERATION)
        if original["status"] != "paused":
            raise RuntimeError("Pause the 0.6B render before staging 1.7B")
        if any("\u0301" in s["spoken"] for s in original["segments"]):
            raise RuntimeError("The source generation contains rejected accent marks")
        config = {**original["config"], "pack_path": pack["_root"],
                  "pack_fingerprint": pack["_fingerprint"], "pack_name": pack["name"]}
        gid = uid()
        with store.lock, store.db:
            store.db.execute("INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (gid, original["book_id"], "Book 1 · Qwen 1.7B demo", now(), now(), "paused",
                 json.dumps(config, ensure_ascii=False), json.dumps(original["cast"], ensure_ascii=False),
                 json.dumps(original["analysis"], ensure_ascii=False), SOURCE_GENERATION))
            store.db.execute("INSERT INTO segments SELECT ?,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,'pending',NULL,0,'[]',warning FROM segments WHERE generation_id=?",
                             (gid, SOURCE_GENERATION))
        result = store.generation(gid)
        if len(result["segments"]) != len(original["segments"]):
            raise RuntimeError("Demo segment count changed")
        (DATA / "qwen-17b-demo.json").write_text(json.dumps({"generation_id": gid,
            "source_generation": SOURCE_GENERATION, "pack": pack["_root"],
            "pack_fingerprint": pack["_fingerprint"], "target_ready_segments": 12,
            "status": "staged"}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"generation_id": gid, "segments": len(result["segments"]),
                          "pack": pack["_root"]}), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    build_pack_result = build_pack()
    stage(build_pack_result)
