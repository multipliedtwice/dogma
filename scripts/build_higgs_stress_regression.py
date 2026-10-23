"""Freeze listener-reported stress failures as an immutable regression suite.

The listener database remains the source of truth.  This builder is read-only:
it verifies every referenced WAV by SHA-256, joins the exact production segment
and keeps conflicting labels out of the adjudicated subset.  Conversation-only
requirements may be recorded as pending cases until their source context and
audio are bound; they are never silently counted as passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACUTE = "\u0301"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def plain(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace(ACUTE, ""))


def validate_label(selected: str, intended: str) -> None:
    if intended.count(ACUTE) != 1:
        raise ValueError(f"Expected exactly one stress mark: {intended!r}")
    if plain(selected) != plain(intended):
        raise ValueError(f"Stress label changes letters: {selected!r} -> {intended!r}")


def _rows(database: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            """SELECT r.*, s.text AS segment_text, s.spoken AS segment_spoken,
                      s.speaker, s.delivery, s.duration, s.performance
                 FROM stress_reports r
                 LEFT JOIN segments s
                   ON s.generation_id=r.generation_id AND s.id=r.segment_id
                WHERE r.kind='stress' AND r.intended_stress IS NOT NULL
                ORDER BY r.created, r.id"""
        )]
    finally:
        connection.close()


def build(database: Path, output: Path, required: list[tuple[str, str]],
          decisions: list[dict] | None = None) -> dict:
    database = database.resolve()
    audio_root = database.parent / "audio"
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in _rows(database):
        validate_label(row["selected_text"], row["intended_stress"])
        if row["segment_text"] is None:
            raise ValueError("Reported segment is missing: " + row["id"])
        audio_hash = row.get("audio_hash")
        audio = audio_root / f"{audio_hash}.wav"
        if not audio_hash or not audio.is_file() or digest(audio) != audio_hash:
            raise ValueError("Reported audio is missing or changed: " + row["id"])
        key = (row["generation_id"], row["segment_id"], row["selected_text"], row["text_offset"])
        grouped[key].append(row)

    decisions = decisions or []
    decision_map = {}
    for decision in decisions:
        ids = tuple(sorted(decision["report_ids"]))
        if ids in decision_map:
            raise ValueError("Duplicate adjudication for " + ",".join(ids))
        validate_label(decision["selected_text"], decision["intended_stress"])
        decision_map[ids] = decision
    used_decisions = set()
    cases, adjudicated = [], []
    for number, (key, rows) in enumerate(grouped.items(), 1):
        labels = sorted({row["intended_stress"] for row in rows})
        row = rows[0]
        report_ids = tuple(sorted(item["id"] for item in rows))
        decision = decision_map.get(report_ids)
        if decision:
            if decision["selected_text"] != row["selected_text"] or decision["intended_stress"] not in labels:
                raise ValueError("Adjudication does not match reported candidates: " + ",".join(report_ids))
            used_decisions.add(report_ids)
            intended = decision["intended_stress"]
            status = "adjudicated"
        else:
            intended = labels[0] if len(labels) == 1 else None
            status = "adjudicated" if len(labels) == 1 else "pending_context_adjudication"
        case = {
            "id": f"reported-{number:03d}",
            "status": status,
            "report_ids": list(report_ids),
            "book_id": row["book_id"],
            "generation_id": row["generation_id"],
            "chapter_id": row["chapter_id"],
            "source_id": row["source_id"],
            "segment_id": row["segment_id"],
            "selected_text": row["selected_text"],
            "intended_stress": intended,
            "label_candidates": labels,
            "text_offset": row["text_offset"],
            "audio_offset": row["audio_offset"],
            "passage_text": row["passage_text"],
            "spoken_text": row["spoken_text"],
            "segment_text": row["segment_text"],
            "segment_spoken": row["segment_spoken"],
            "speaker": row["speaker"],
            "delivery": row["delivery"],
            "duration": row["duration"],
            "performance": json.loads(row["performance"] or "{}"),
            "audio_sha256": row["audio_hash"],
            "audio": str(audio_root / f'{row["audio_hash"]}.wav'),
            "class": "proper_name" if row["selected_text"][:1].isupper() else "lexical_or_morphology",
            "basis": "explicit_listener_report",
        }
        if decision:
            case["adjudication"] = {key: value for key, value in decision.items()
                                     if key not in {"report_ids", "selected_text", "intended_stress"}}
        cases.append(case)
        if status == "adjudicated":
            adjudicated.append(case["id"])

    unused = set(decision_map) - used_decisions
    if unused:
        raise ValueError("Adjudications do not match a conflict: " + repr(sorted(unused)))

    pending_required = []
    for selected, intended in required:
        validate_label(selected, intended)
        pending_required.append({
            "id": "required-" + hashlib.sha256((selected + "\0" + intended).encode("utf-8")).hexdigest()[:12],
            "status": "pending_source_context_and_audio",
            "selected_text": selected,
            "intended_stress": intended,
            "basis": "explicit_conversation_correction",
        })

    counts = Counter(case["status"] for case in cases)
    manifest = {
        "schema": 1,
        "purpose": "frozen_generated_audio_stress_regression",
        "production_approved": False,
        "database": str(database),
        "database_sha256": digest(database),
        "reported_rows": sum(len(rows) for rows in grouped.values()),
        "distinct_reported_cases": len(cases),
        "adjudicated_case_ids": adjudicated,
        "counts_by_status": dict(sorted(counts.items())),
        "cases": cases,
        "required_external_cases": pending_required,
        "gate": {
            "historical_audio_is_failure_evidence_only": True,
            "candidate_requires_paired_same-input_renders": True,
            "pending_cases_cannot_count_as_passes": True,
        },
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--required-case", action="append", default=[], metavar="PLAIN|STRESSED",
        help="Conversation-only required case; remains pending until context/audio are bound.",
    )
    parser.add_argument("--adjudications", type=Path,
                        help="JSON decisions for conflicts, bound by the complete report-id set.")
    args = parser.parse_args()
    required = []
    for value in args.required_case:
        if "|" not in value:
            parser.error("--required-case must be PLAIN|STRESSED")
        required.append(tuple(value.split("|", 1)))
    decisions = []
    if args.adjudications:
        source = json.loads(args.adjudications.read_text(encoding="utf-8"))
        if source.get("schema") != 1 or not isinstance(source.get("decisions"), list):
            parser.error("Invalid adjudications file")
        decisions = source["decisions"]
    result = build(args.database, args.output, required, decisions)
    print(json.dumps({key: result[key] for key in (
        "reported_rows", "distinct_reported_cases", "counts_by_status", "adjudicated_case_ids"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
