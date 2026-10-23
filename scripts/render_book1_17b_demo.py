"""Render and pause after the opening Book 1 passages for a 1.7B demo."""

import json
import sqlite3
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "training-work/book1-listening-app"
PLAN = DATA / "qwen-17b-demo.json"
TARGET = 12


def call(operation, gid, token):
    request = urllib.request.Request("http://127.0.0.1:1421/api",
        data=json.dumps({"op": operation, "data": {"id": gid}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def snapshot(gid):
    with sqlite3.connect(DATA / "library.sqlite3") as db:
        status, error = db.execute("SELECT status,error FROM generations WHERE id=?", (gid,)).fetchone()
        ready = db.execute("SELECT count(*) FROM segments WHERE generation_id=? AND status='ready'", (gid,)).fetchone()[0]
    return status, error, ready


def main():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    gid = plan["generation_id"]
    token = json.loads((DATA / "runtime.stdout.run.log").read_text(encoding="utf-8").splitlines()[0])["token"]
    call("resume", gid, token)
    last_ready = -1
    deadline = time.monotonic() + 1200
    pause_requested = False
    while time.monotonic() < deadline:
        status, error, ready = snapshot(gid)
        if ready != last_ready:
            print(f"{ready}/{TARGET} opening passages ready; status={status}", flush=True)
            last_ready = ready
        if status == "failed":
            raise RuntimeError(error)
        if ready >= TARGET and not pause_requested:
            call("pause", gid, token)
            pause_requested = True
        if pause_requested and status == "paused":
            plan["status"] = "ready_for_review"
            plan["ready_segments"] = ready
            PLAN.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            print("Demo ready for review", flush=True)
            return
        time.sleep(3)
    call("pause", gid, token)
    raise TimeoutError("Stopped 1.7B demo after 20 minutes; check saved progress")


if __name__ == "__main__":
    main()
