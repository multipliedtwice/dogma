"""Seed a larger YO-CPT stress curation run from an immutable checkpoint.

Audio is hard-linked when possible. The source remains untouched, while the
destination gets a new pinned configuration and resumes from the exact stored
Hugging Face iterator state instead of replaying the source prefix.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from curate_yo_cpt_stress import STATE_VERSION, atomic_json, write_outputs
from train_higgs_local import verify_dataset


def link_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, target)
        except OSError:
            shutil.copy2(path, target)


def seed(source: Path, destination: Path, *, target_hours: float,
         max_disk_gb: float, max_temp_gb: float) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}")
    verify_dataset(source)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((source / "curation-state.json").read_text(encoding="utf-8"))
    if manifest.get("complete") is not True or state.get("complete") is not True:
        raise ValueError("Expansion source must be a completed immutable curation checkpoint.")
    if state.get("version") != STATE_VERSION or not state.get("stream_state"):
        raise ValueError("Expansion source lacks the exact resumable stream state.")
    if state.get("scanned_rows") != manifest.get("scanned_rows"):
        raise ValueError("Expansion source state and manifest disagree on the stream cursor.")

    destination.mkdir(parents=True)
    link_tree(source / "audio", destination / "audio")
    link_tree(source / "provenance", destination / "provenance")
    (destination / ".hf-cache").mkdir()
    (destination / ".tmp").mkdir()

    config = dict(state["config"])
    config.update(
        target_hours=target_hours,
        max_disk_bytes=int(max_disk_gb * 1024**3),
        max_temp_bytes=int(max_temp_gb * 1024**3),
        selection_mode="scan-entire-pinned-source",
    )
    state["config"] = config
    state["complete"] = False
    state.pop("completion_reason", None)
    state["expanded_from"] = {
        "path": str(source),
        "manifest_sha256": verify_dataset(source),
        "scanned_rows": state["scanned_rows"],
        "seeded_unix": time.time(),
    }
    state["updated_unix"] = time.time()
    atomic_json(destination / "curation-state.json", state)
    write_outputs(destination, state)
    return {
        "destination": str(destination),
        "scanned_rows": state["scanned_rows"],
        "accepted": len(state["accepted"]),
        "seconds": state["seconds"],
        "hardlinked_audio": len(list((destination / "audio").glob("*.wav"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--target-hours", type=float, default=1000.0)
    parser.add_argument("--max-disk-gb", type=float, default=24.0)
    parser.add_argument("--max-temp-gb", type=float, default=2.0)
    args = parser.parse_args()
    if min(args.target_hours, args.max_disk_gb, args.max_temp_gb) <= 0:
        raise SystemExit("Expansion limits must be positive.")
    print(json.dumps(seed(args.source, args.destination, target_hours=args.target_hours,
                          max_disk_gb=args.max_disk_gb, max_temp_gb=args.max_temp_gb),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
