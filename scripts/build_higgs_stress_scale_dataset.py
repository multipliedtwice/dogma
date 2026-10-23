"""Build the production-scale continuation set for the Higgs stress candidate.

Only stress clips not used by the successful five-hour pilot are added. The
protected reviewed production corpus is replayed as ordinary CE rehearsal so
voice-family and natural-delivery behavior remain anchored. Human labels are
read-only and are never changed or inferred here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from export_higgs_training import rows, sha, write
from train_higgs_local import verify_dataset


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha(target) != sha(source):
            raise ValueError(f"Conflicting content-addressed audio: {target}")
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def seconds(items: list[dict]) -> float:
    return sum(float((item.get("metadata") or {}).get("seconds", 0)) for item in items)


def build(stress: Path, exclude: Path, rehearsal: Path, output: Path) -> dict:
    stress, exclude, rehearsal, output = map(Path.resolve, (stress, exclude, rehearsal, output))
    if output.exists():
        raise ValueError(f"Use a fresh immutable output directory: {output}")
    protected = (ROOT / "manual-labeling").resolve()
    if output == protected or protected in output.parents:
        raise ValueError("Derived training output may not be written into protected manual labeling.")
    source_hashes = {
        "stress": verify_dataset(stress),
        "excluded_pilot": verify_dataset(exclude),
        "reviewed_rehearsal": verify_dataset(rehearsal),
    }
    old_ids = {
        item["id"]
        for split in ("train", "validation")
        for item in rows(exclude / f"{split}.jsonl")
    }
    stress_train = [item for item in rows(stress / "train.jsonl") if item["id"] not in old_ids]
    stress_validation = [item for item in rows(stress / "validation.jsonl") if item["id"] not in old_ids]
    rehearsal_train = rows(rehearsal / "train.jsonl")
    if not stress_train or not stress_validation or not rehearsal_train:
        raise ValueError("Scale dataset requires new stress train/validation clips and reviewed rehearsal clips.")

    def tagged(item: dict, origin: str) -> dict:
        return {
            **item,
            "id": f"{origin}:{item['id']}",
            "metadata": {**(item.get("metadata") or {}), "scale_dataset_origin": origin},
        }

    train = [tagged(item, "stress-new") for item in stress_train]
    train.extend(tagged(item, "reviewed-rehearsal") for item in rehearsal_train)
    validation = [tagged(item, "stress-new") for item in stress_validation]
    output.mkdir(parents=True)
    (output / "train.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in train), encoding="utf-8"
    )
    (output / "validation.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in validation), encoding="utf-8"
    )

    for item, source in [
        *((item, stress) for item in stress_train),
        *((item, stress) for item in stress_validation),
        *((item, rehearsal) for item in rehearsal_train),
    ]:
        for field in ("audio", "ref_audio"):
            relative = Path(item[field])
            link_or_copy(source / relative, output / relative)

    report = {
        "schema": 1,
        "purpose": "production-voice stress continuation with reviewed rehearsal",
        "sources": source_hashes,
        "exclusion": "Every clip ID used by the successful five-hour pilot is removed.",
        "counts": {
            "stress_new_train": len(stress_train),
            "stress_new_validation": len(stress_validation),
            "reviewed_rehearsal_train": len(rehearsal_train),
            "total_train": len(train),
            "total_validation": len(validation),
        },
        "hours": {
            "stress_new_train": seconds(stress_train) / 3600,
            "stress_new_validation": seconds(stress_validation) / 3600,
            "reviewed_rehearsal_train": seconds(rehearsal_train) / 3600,
        },
        "human_label_policy": "read-only replay; no automatic label changes",
    }
    write(output / "report.json", report)
    files = {
        path.relative_to(output).as_posix(): sha(path)
        for path in output.rglob("*") if path.is_file()
    }
    write(output / "manifest.json", {
        "schema": 1,
        "builder_sha256": sha(Path(__file__)),
        "files": files,
    })
    verify_dataset(output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / "training-work/higgs-stress-pilot"
    parser.add_argument("--stress", type=Path, default=base / "yo-cpt-ru-consensus-full-v3")
    parser.add_argument("--exclude", type=Path, default=base / "yo-cpt-ru-consensus-5h-v2")
    parser.add_argument(
        "--rehearsal", type=Path,
        default=ROOT / "training-work/higgs-production/datasets/reviewed-20261012-v1",
    )
    parser.add_argument("--output", type=Path, default=base / "production-scale-dataset-v1")
    args = parser.parse_args()
    print(json.dumps(build(args.stress, args.exclude, args.rehearsal, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
