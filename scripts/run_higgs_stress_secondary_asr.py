"""Run independent Whisper over primary-ASR disagreements in a stress A/B."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "scripts")]
from rimsky.util import file_digest, write_json
from audit_higgs_secondary_asr import audit


def snapshot(folder: Path) -> Path:
    plan_path = folder / "render-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    receipts = json.loads((folder / "receipts.json").read_text(encoding="utf-8"))
    primary = json.loads((folder / "word-checks-1.7B.json").read_text(encoding="utf-8"))
    if receipts["plan_sha256"] != file_digest(plan_path) or primary["plan_sha256"] != file_digest(plan_path):
        raise ValueError("Stress A/B audit binding changed: " + str(folder))
    units = {unit["id"]: unit for unit in plan["units"]}
    rows = {}
    for identifier, check in primary["checks"].items():
        if check["status"] == "asr_exact_match":
            continue
        take = receipts["takes"][identifier]
        audio = folder / take["audio"]
        if file_digest(audio) != take["audio_sha256"] or take["audio_sha256"] != check["audio_sha256"]:
            raise ValueError("Stress A/B audio changed: " + identifier)
        rows[identifier] = {"unit": units[identifier], "receipt": take,
                            "path": str(audio.resolve()), "word_check": check}
    path = folder / "secondary-asr-snapshot.json"
    write_json(path, rows)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    paths = [snapshot(folder / variant) for variant in ("baseline", "candidate")]
    audit(paths, folder / "secondary-asr-whisper-small")


if __name__ == "__main__":
    main()
