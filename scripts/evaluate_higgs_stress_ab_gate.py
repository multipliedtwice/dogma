"""Combine stress, intelligibility and voice evidence for a Higgs A/B.

This is an automatic hold/triage gate.  It never creates human-listening
labels and never promotes a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "scripts")]
from rimsky.util import file_digest, write_json
from rimsky.name_tolerance import tolerance


OK = {"asr_exact_match", "nonverbal_not_spoken_text"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def accepted_asr(check: dict | None, source_text: str) -> tuple[bool, list[dict]]:
    if check is None:
        return False, []
    if check["status"] in OK:
        return True, []
    expected = check.get("expected_words") or []
    heard = check.get("heard_words") or []
    corrected, deviations = tolerance(expected, heard, source_text)
    return corrected == expected and bool(deviations), deviations


def evaluate(folder: Path) -> dict:
    plan_path = folder / "plan.json"
    plan = load(plan_path)
    witness = load(folder / "stress-witness-report.json")
    voice = load(folder / "voice-preservation-report.json")
    if witness["plan_sha256"] != file_digest(plan_path):
        raise ValueError("Stress witness is not bound to this plan")

    primary = {}
    for variant in ("baseline", "candidate"):
        render_plan = folder / variant / "render-plan.json"
        report = load(folder / variant / "word-checks-1.7B.json")
        if report["plan_sha256"] != file_digest(render_plan):
            raise ValueError("ASR report is not bound to " + variant)
        primary[variant] = report["checks"]

    secondary_path = folder / "secondary-asr-whisper-small/report.json"
    secondary = load(secondary_path)["checks"] if secondary_path.is_file() else {}
    secondary_by_audio = {row["audio_sha256"]: row for row in secondary.values()}
    candidate_plan = load(folder / "candidate/render-plan.json")
    candidate_receipts = load(folder / "candidate/receipts.json")["takes"]
    candidate_units = {unit["id"]: unit for unit in candidate_plan["units"]}
    selected = {
        identifier: {
            "unit": unit,
            "receipt": candidate_receipts[identifier],
            "path": str((folder / "candidate" / candidate_receipts[identifier]["audio"]).resolve()),
            "word_check": primary["candidate"][identifier],
        }
        for identifier, unit in candidate_units.items()
    }
    if secondary_path.is_file():
        from higgs_secondary_verification import apply_secondary
        resolved_candidate = apply_secondary(selected, [secondary_path])
    else:
        resolved_candidate = selected
    voice_holds = set(voice["triage_queue"])
    rows = []
    for case in plan["cases"]:
        identifier = case["id"]
        stress = witness["cases"][identifier]
        candidate_asr = primary["candidate"][identifier]
        second = secondary_by_audio.get(candidate_asr["audio_sha256"])
        source_text = case["synthesis"]["text"]
        primary_ok, primary_tolerance = accepted_asr(candidate_asr, source_text)
        secondary_ok, secondary_tolerance = accepted_asr(second, source_text)
        resolved_check = resolved_candidate[identifier]["word_check"]
        resolved_ok = resolved_check["status"] in OK
        reasons = []
        if stress["candidate"]["status"] != "follows":
            reasons.append("candidate_stress_" + stress["candidate"]["status"])
        if not primary_ok and not secondary_ok and not resolved_ok:
            reasons.append("candidate_intelligibility_disagreement")
        if identifier in voice_holds:
            reasons.append("candidate_voice_preservation")
        rows.append(
            {
                "id": identifier,
                "target": case["intended_stress"],
                "status": "hold" if reasons else "automatic_pass",
                "hold_reasons": reasons,
                "priority_regression": identifier in {"reported-021", "reported-024", "reported-025"},
                "stress": stress,
                "primary_asr": {
                    variant: {
                        "status": primary[variant][identifier]["status"],
                        "heard": primary[variant][identifier].get("heard", ""),
                        "audio_sha256": primary[variant][identifier]["audio_sha256"],
                    }
                    for variant in ("baseline", "candidate")
                },
                "candidate_secondary_asr": None
                if second is None
                else {"status": second["status"], "heard": second.get("heard", "")},
                "intelligibility_acceptance": {
                    "accepted": primary_ok or secondary_ok or resolved_ok,
                    "basis": (
                        "primary_exact" if candidate_asr["status"] in OK
                        else "primary_tolerance" if primary_ok
                        else "independent_rules" if resolved_ok
                        else "secondary_exact" if second is not None and second["status"] in OK
                        else "secondary_tolerance" if secondary_ok
                        else "unresolved"
                    ),
                    "tolerated_deviations": (
                        resolved_check.get("tolerated_deviations")
                        or primary_tolerance
                        or secondary_tolerance
                    ),
                    "resolved_check": resolved_check,
                },
            }
        )
    holds = [row["id"] for row in rows if row["status"] == "hold"]
    return {
        "schema": 1,
        "kind": "higgs_stress_generated_audio_automatic_gate",
        "status": "complete_with_holds" if holds else "complete_automatic_pass",
        "production_approved": False,
        "plan_sha256": file_digest(plan_path),
        "summary": {
            "cases": len(rows),
            "automatic_pass": len(rows) - len(holds),
            "holds": len(holds),
            "hold_case_ids": holds,
            "priority_regression_holds": [
                row["id"] for row in rows if row["priority_regression"] and row["status"] == "hold"
            ],
            "candidate_stress": witness["summary"]["candidate"],
            "voice_preservation": voice["summary"],
        },
        "cases": rows,
        "policy": [
            "An automatic pass is not a human-listening label.",
            "Any acoustic-stress, two-recognizer intelligibility, or speaker-embedding issue remains held.",
            "Production promotion requires the separate production decision after this gate.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    report = evaluate(folder)
    write_json(folder / "automatic-gate-report.json", report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
