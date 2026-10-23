"""Freeze automatic triage for the unfinished Higgs stress A/B cases.

This report is deliberately separate from the browser's human-listening
answers.  It may finish machine triage and create retake holds, but it never
manufactures a human verdict or authorizes production promotion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "training-work/higgs-stress-pilot/generated-audio-regression-v1"


def load(relative: str) -> dict:
    return json.loads((SUITE / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    value = hashlib.sha256()
    with (SUITE / relative).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    plan = load("plan.json")
    witness = load("stress-witness-report.json")
    voice = load("voice-preservation-report.json")
    baseline_asr = load("baseline/word-checks-1.7B.json")["checks"]
    candidate_asr = load("candidate/word-checks-1.7B.json")["checks"]
    secondary = load("secondary-asr-whisper-small/report.json")["checks"]
    cases = {case["id"]: case for case in plan["cases"]}

    expected = [f"reported-{number:03d}" for number in range(20, 27)]
    if any(identifier not in cases for identifier in expected):
        raise ValueError("Frozen A/B plan no longer contains cases 020--026")
    if voice["summary"]["triage_queue_count"]:
        raise ValueError("Voice-preservation triage must be resolved first")

    secondary_by_audio = {
        check["audio_sha256"]: check for check in secondary.values()
    }

    # These outcomes answer the narrow A/B question using only reproducible
    # measurements.  A hold means regenerate/evaluate; it does not ask the
    # listener to adjudicate the same clip again.
    outcomes = {
        "reported-020": (
            "b",
            "resolved",
            "Both versions are word-exact and follow the intended stress; B has a much stronger acoustic margin.",
        ),
        "reported-021": (
            "b",
            "hold_intelligibility_for_next_render",
            "B fixes the measured stress placement and is closer on the invented proper name, but both recognizers remain uncertain around the rare verb form.",
        ),
        "reported-022": (
            "same",
            "resolved",
            "Both versions are word-exact and follow the intended stress with strong margins.",
        ),
        "reported-023": (
            "same",
            "resolved",
            "Both versions are word-exact and follow the intended stress.",
        ),
        "reported-024": (
            "both_wrong",
            "hold_stress_retakes",
            "B fixes the baseline word substitution, but its target stress is acoustically suspect; neither take is safe enough to pass.",
        ),
        "reported-025": (
            "a",
            "candidate_regression",
            "Both target stresses follow, but two independent recognizers agree that B corrupts the opening words while A is exact.",
        ),
        "reported-026": (
            "b",
            "resolved",
            "Both versions are word-exact; only B's acoustic argmax lands on the intended stressed vowel.",
        ),
    }

    rows = []
    for identifier in expected:
        verdict, status, reason = outcomes[identifier]
        per_variant = {}
        for variant, checks in (
            ("baseline", baseline_asr),
            ("candidate", candidate_asr),
        ):
            primary = checks[identifier]
            second = secondary_by_audio.get(primary["audio_sha256"])
            per_variant[variant] = {
                "stress": witness["cases"][identifier][variant],
                "primary_asr": {
                    "status": primary["status"],
                    "heard": primary["heard"],
                    "audio_sha256": primary["audio_sha256"],
                },
                "secondary_asr": None
                if second is None
                else {"status": second["status"], "heard": second["heard"]},
            }
        rows.append(
            {
                "id": identifier,
                "target": cases[identifier]["intended_stress"],
                "automatic_verdict": verdict,
                "status": status,
                "reason": reason,
                "evidence": per_variant,
            }
        )

    report = {
        "schema": 1,
        "kind": "higgs_stress_ab_automatic_completion",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete_automatic_triage_with_retakes",
        "production_approved": False,
        "human_evidence": {
            "reviewed_count": 19,
            "total_count": 26,
            "aggregate_finding": "The listener reported an impressive improvement in stress correctness and overall naturalness after 19 comparisons.",
            "exact_case_answers_available": False,
            "remaining_case_ids_basis": "The UI advances sequentially, so 020--026 are treated as the unfinished tail. This is an explicit assumption, not recovered browser state.",
            "rule": "This report does not convert automatic judgments into human-listening labels. A later exported browser review overrides automatic case verdicts.",
        },
        "automatic_summary": {
            "cases": 7,
            "candidate_better": 3,
            "same": 2,
            "baseline_better": 1,
            "both_wrong": 1,
            "next_model_retakes": ["reported-021", "reported-024", "reported-025"],
            "manual_review_required_to_continue_scaling": False,
            "production_promotion_allowed": False,
        },
        "voice_preservation": voice["summary"],
        "cases": rows,
        "sources": {
            relative: sha256(relative)
            for relative in (
                "plan.json",
                "stress-witness-report.json",
                "voice-preservation-report.json",
                "baseline/word-checks-1.7B.json",
                "candidate/word-checks-1.7B.json",
                "secondary-asr-whisper-small/report.json",
            )
        },
        "decision": "Continue the already-running scale experiment. Do not promote the current candidate; re-render the held/regressed cases against the next checkpoint.",
    }
    target = SUITE / "automatic-review-completion-v1.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["automatic_summary"], ensure_ascii=False, indent=2))
    print(target)


if __name__ == "__main__":
    main()
