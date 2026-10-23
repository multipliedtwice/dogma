"""Replace the rejected accent-mark draft with an unmarked listening version."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.rimsky.store import Store

DATA = ROOT / "training-work/book1-listening-app"
BASE = "1b15b833cc8ee135b527c30c"
REJECTED = "30018358d6cb7847de89704c"


def main():
    store = Store(DATA)
    try:
        active_path = DATA / "active-listening-generation.json"
        if active_path.exists():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            if active.get("replaces") == REJECTED:
                result = store.generation(active["generation_id"])
                if any("\u0301" in s["spoken"] for s in result["segments"]):
                    raise RuntimeError("Existing rollback contains accent marks")
                print(json.dumps({"generation_id": result["id"], "ready":
                      sum(s["status"] == "ready" for s in result["segments"]),
                      "total": len(result["segments"])}), flush=True)
                return
        old = store.generation(REJECTED)
        base = store.generation(BASE)
        if old["status"] != "paused" or base["status"] != "paused":
            raise RuntimeError("Pause both generations before rollback")
        base_by_id = {s["id"]: s for s in base["segments"]}
        changed = [s for s in old["segments"] if "\u0301" in s["spoken"]]
        if len(changed) != 12:
            raise RuntimeError(f"Expected 12 marked segments, found {len(changed)}")
        changes = [{"id": s["id"], "spoken": base_by_id[s["id"]]["spoken"]} for s in changed]
        new = store.revise(REJECTED, changes, "Book 1 · unmarked listening draft")
        gid = new["id"]
        with store.lock, store.db:
            for s in changed:
                source = base_by_id[s["id"]]
                target = store.db.execute("SELECT spoken,speaker,delivery FROM segments WHERE generation_id=? AND id=?",
                                          (gid, s["id"])).fetchone()
                if (source["status"] == "ready" and target["spoken"] == source["spoken"]
                        and target["speaker"] == source["speaker"]
                        and target["delivery"] == source["delivery"]):
                    store.db.execute("UPDATE segments SET status='ready',audio_hash=?,duration=?,cues=?,warning=? WHERE generation_id=? AND id=?",
                                     (source["audio_hash"], source["duration"],
                                      json.dumps(source["cues"], ensure_ascii=False),
                                      source["warning"], gid, s["id"]))
            row = store.db.execute("SELECT value FROM positions WHERE book_id=?", (old["book_id"],)).fetchone()
            if row:
                position = json.loads(row["value"])
                if position.get("generation_id") == REJECTED:
                    position["generation_id"] = gid
                    position["offset"] = 0
                    store.db.execute("UPDATE positions SET value=? WHERE book_id=?",
                                     (json.dumps(position), old["book_id"]))
        active = {"generation_id": gid, "replaces": REJECTED,
                  "reason": "User listening review rejected combining acute stress marks.",
                  "stress_corrections_accepted": False}
        active_path.write_text(
            json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = store.generation(gid)
        if any("\u0301" in s["spoken"] for s in result["segments"]):
            raise RuntimeError("Rollback still contains accent marks")
        print(json.dumps({"generation_id": gid, "changed": len(changed),
                          "ready": sum(s["status"] == "ready" for s in result["segments"]),
                          "total": len(result["segments"])}), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
