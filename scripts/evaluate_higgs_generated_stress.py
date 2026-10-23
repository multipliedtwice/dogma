"""Score paired stress-regression WAVs with the calibrated acoustic witness.

This is supporting evidence only.  It compares baseline and candidate on the
same sentences/seeds, verifies every receipt/hash, and emits the small queue
whose target word could not be aligned or whose candidate remains suspect.
It never promotes a checkpoint or treats the witness as human truth.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "scripts")]
from rimsky.util import file_digest, write_json
from measure_stress_witness import ACUTE, analyze, margin, release, scores, sentence_words


def intended_target(selected: str, marked: str) -> tuple[str, int]:
    """Return the whitespace/hyphen component carrying the mark and its local vowel index."""
    from measure_stress_witness import VOWEL_LETTERS
    components = [item for item in marked.replace("-", " ").split() if ACUTE in item]
    if len(components) != 1:
        raise ValueError("Expected one marked target component: " + marked)
    component = components[0]
    before = component[:component.index(ACUTE)].lower()
    return component.replace(ACUTE, ""), sum(char in VOWEL_LETTERS for char in before) - 1


def score_take(path: Path, text: str, selected: str, intended: str, weights: list[float], theta: float) -> dict:
    try:
        words, status = analyze(path, text)
    except Exception as error:  # noqa: BLE001 - immutable evidence records the exact failure
        return {"status": "unmeasured", "reason": f"{type(error).__name__}: {error}"}
    if status != "ok":
        return {"status": "unmeasured", "reason": status}
    target_word, target = intended_target(selected, intended)
    exact = [word for word in words if word["word"].casefold() == target_word.casefold()]
    if not exact and target_word[:1].isupper():
        exact = [word for word in words if word["word"].casefold().startswith(target_word.casefold())]
    if len(exact) != 1 or target < 0 or target >= exact[0]["vowels"]:
        return {"status": "unmeasured", "reason": "target-word-alignment", "aligned_candidates": len(exact)}
    word = exact[0]
    values = scores(word["features"], weights)
    value = float(margin(word["features"], weights, target))
    return {
        "status": "follows" if value >= -theta else "suspect",
        "word": word["word"], "word_index": word["index"], "intended_vowel": target,
        "predicted_vowel": int(values.argmax()), "margin": value,
        "scores": [round(float(item), 4) for item in values], "theta": theta,
    }


def evaluate(folder: Path, theta: float) -> dict:
    frozen = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    calibration_path = ROOT / "training-work/stress-witness-v1/human-calibration.json"
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    weights = calibration["final_weights_list"]
    by_case = {}
    for variant in ("baseline", "candidate"):
        variant_folder = folder / variant
        render_plan_path = variant_folder / "render-plan.json"
        render_plan = json.loads(render_plan_path.read_text(encoding="utf-8"))
        receipts = json.loads((variant_folder / "receipts.json").read_text(encoding="utf-8"))
        if receipts["plan_sha256"] != file_digest(render_plan_path):
            raise ValueError("Receipt no longer matches render plan: " + variant)
        for unit in render_plan["units"]:
            take = receipts["takes"][unit["id"]]
            wav = variant_folder / take["audio"]
            if file_digest(wav) != take["audio_sha256"]:
                raise ValueError("Rendered audio changed: " + str(wav))
            case = next(item for item in frozen["cases"] if item["id"] == unit["id"])
            by_case.setdefault(unit["id"], {"selected_text": case["selected_text"],
                                                   "intended_stress": case["intended_stress"]})[variant] = score_take(
                wav, unit["synthesis"]["text"], case["selected_text"], case["intended_stress"], weights, theta)
            print("STRESS", variant, unit["id"], by_case[unit["id"]][variant]["status"], flush=True)
    release()
    summary = {}
    for variant in ("baseline", "candidate"):
        values = [row[variant] for row in by_case.values()]
        measured = [row for row in values if row["status"] != "unmeasured"]
        summary[variant] = {
            "cases": len(values), "measured": len(measured),
            "follows": sum(row["status"] == "follows" for row in measured),
            "suspect": sum(row["status"] == "suspect" for row in measured),
            "unmeasured": len(values) - len(measured),
            "argmax_correct": sum(row.get("predicted_vowel") == row.get("intended_vowel") for row in measured),
            "mean_margin": (sum(row["margin"] for row in measured) / len(measured)) if measured else None,
        }
    queue = [identifier for identifier, row in by_case.items()
             if row["candidate"]["status"] != "follows"]
    improved = sum(
        row["baseline"].get("margin", float("-inf")) < row["candidate"].get("margin", float("-inf"))
        for row in by_case.values()
    )
    report = {
        "schema": 1, "status": "complete_pending_independent_audio_gates",
        "production_approved": False, "plan_sha256": file_digest(folder / "plan.json"),
        "calibration_sha256": file_digest(calibration_path), "theta": theta,
        "summary": summary, "candidate_margin_improved_cases": improved,
        "review_queue": queue, "cases": by_case,
        "limitations": [
            "The acoustic witness is calibrated triage, not human truth.",
            "Word-level ASR and voice-preservation gates are separate and still required.",
        ],
    }
    write_json(folder / "stress-witness-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--theta", type=float, default=2.5)
    args = parser.parse_args()
    result = evaluate(args.folder.resolve(), args.theta)
    print(json.dumps({"summary": result["summary"], "review_queue": result["review_queue"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
