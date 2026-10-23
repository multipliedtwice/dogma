"""Validate a listened comparison for the reported следователя TTS error."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "character-rb1" / "mixed-scene-probe"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output", type=Path, default=BASE / "human-reviewed-stress-decision.json")
    args = parser.parse_args()
    if not args.reviewer.strip():
        raise ValueError("Reviewer name required")
    manifest_path = BASE / "stress-decision-manifest.json"
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    export = json.loads(args.review.read_text(encoding="utf-8"))
    if export.get("schema") != 1 or export.get("manifest_sha256") != digest:
        raise ValueError("Review does not match the current four audio files")
    decision = export.get("decision", {})
    if decision.get("listened") is not True or decision.get("choice") not in ("original", "acute", "uppercase", "none", "unclear"):
        raise ValueError("All four clips must be heard and a choice recorded")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest.values():
        if hashlib.sha256((BASE / item["file"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Audio changed after review board creation")
    result = {"schema": 1, "reviewer": args.reviewer.strip(), "word": "следователя",
              "expected_stress": "сле́дователя", "manifest_sha256": digest,
              "selected_version": decision["choice"], "notes": str(decision.get("notes", "")).strip(),
              "human_pronunciation_reviewed": True,
              "correction_accepted": decision["choice"] in ("acute", "uppercase")}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_version": result["selected_version"], "correction_accepted": result["correction_accepted"]}))


if __name__ == "__main__":
    main()
