"""Build a small, read-only Book 1 reference set for later-book bucket review."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEATURES = (
    "median_pitch_hz",
    "pitch_range_semitones",
    "active_level_dbfs",
    "internal_pause_fraction",
    "envelope_peaks_per_second",
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def representative_order(rows: list[dict], acoustics: dict[str, dict]) -> list[dict]:
    measured = [row for row in rows if row["id"] in acoustics]
    if not measured:
        return sorted(rows, key=lambda row: (row["recording_id"], row["audio_start"]))
    centers = {}
    scales = {}
    for feature in FEATURES:
        values = [float(acoustics[row["id"]][feature]) for row in measured if acoustics[row["id"]].get(feature) is not None]
        if not values:
            continue
        center = statistics.median(values)
        deviations = [abs(value - center) for value in values]
        centers[feature] = center
        scales[feature] = max(statistics.median(deviations), 1e-6)

    def distance(row: dict) -> tuple[float, float, str]:
        feature_row = acoustics.get(row["id"], {})
        parts = [min(6.0, abs(float(feature_row[name]) - centers[name]) / scales[name])
                 for name in centers if feature_row.get(name) is not None]
        duration = float(feature_row.get("duration_seconds") or row["audio_end"] - row["audio_start"])
        duration_penalty = 0 if 2.5 <= duration <= 11 else 2 + abs(duration - 6) / 4
        return ((sum(parts) / len(parts) if parts else 99) + duration_penalty, abs(duration - 6), row["id"])

    return sorted(rows, key=distance)


def build(board: Path, per_bucket: int = 4) -> dict:
    archive = ROOT / "manual-labeling" / "reality-benders-1"
    latest = read_json(archive / "latest.json")
    manifest = read_json(archive / "archive-manifest.json")
    if manifest.get("support_snapshot_review_sha256") != latest.get("review_sha256"):
        raise ValueError("Book 1 labels do not match the protected archive manifest")
    acoustics_doc = read_json(ROOT / "training-work" / "character-rb1" / "fine-tune-all" / "audio-acoustics.json")
    if acoustics_doc.get("candidates_sha256") != latest.get("candidates_sha256"):
        raise ValueError("Book 1 acoustic measurements do not match the protected candidate set")
    acoustics = acoustics_doc["clips"]
    rows = [json.loads(line) for line in (archive / "labels.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        audio = row.get("audio") or {}
        if (row.get("precision_status") != "precision_pass" or row.get("decision", {}).get("rejected")
                or not row.get("voice_family") or not row.get("mood") or not audio.get("archive")):
            continue
        groups[f'{row["voice_family"]}|{row["mood"]}'].append(row)

    selected = {}
    for bucket, candidates in sorted(groups.items()):
        picks = []
        used_sources = set()
        for row in representative_order(candidates, acoustics):
            if row["source_id"] in used_sources and len(candidates) > per_bucket:
                continue
            audio_path = archive / row["audio"]["archive"]
            if not audio_path.is_file() or sha256(audio_path) != row["audio"]["sha256"]:
                raise ValueError(f'Protected Book 1 audio is missing or changed: {row["id"]}')
            feature_row = acoustics.get(row["id"], {})
            picks.append({
                "id": f'book1_ref_{row["id"]}',
                "source_id": row["id"],
                "book_id": "reality-benders-1",
                "voice_family": row["voice_family"],
                "mood": row["mood"],
                "text": row["text"],
                "audio": f'manual-labeling/reality-benders-1/{row["audio"]["archive"]}',
                "audio_sha256": row["audio"]["sha256"],
                "duration_seconds": round(float(feature_row.get("duration_seconds") or row["audio_end"] - row["audio_start"]), 3),
            })
            used_sources.add(row["source_id"])
            if len(picks) >= per_bucket:
                break
        if picks:
            selected[bucket] = picks
    result = {
        "schema": 1,
        "source_review_sha256": latest["review_sha256"],
        "per_bucket": per_bucket,
        "buckets": selected,
    }
    board.mkdir(parents=True, exist_ok=True)
    (board / "book1-comparisons.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="training-work/character-rb2-full/fine-tune-all")
    parser.add_argument("--per-bucket", type=int, default=4)
    args = parser.parse_args()
    result = build(ROOT / args.board, args.per_bucket)
    print(json.dumps({"buckets": len(result["buckets"]), "references": sum(map(len, result["buckets"].values()))}))
