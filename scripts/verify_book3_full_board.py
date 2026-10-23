"""Verify the complete Book 3 board, archive, and frozen-model bindings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "training-work/character-rb3/labeling-full"
BOARD = ROOT / "training-work/character-rb3-full/fine-tune-all"
ARCHIVE = ROOT / "manual-labeling/reality-benders-3"
MODEL = ROOT / "training-work/series-label-predictor/books12-v1"


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> None:
    candidates_path = BOARD / "candidates.jsonl"
    candidates = rows(candidates_path)
    if not candidates or len(candidates) != len({row["id"] for row in candidates}):
        raise ValueError("Invalid or duplicate Book 3 candidates")
    if {row.get("book_id") for row in candidates} != {"reality-benders-3"}:
        raise ValueError("Candidate book binding is wrong")
    candidate_hash = sha(candidates_path)
    summary = json.loads((BOARD / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("candidates_sha256") != candidate_hash or
            summary.get("automatic_acceptance") is not False or
            summary.get("candidate_model_proposals") is not True):
        raise ValueError("Unsafe or stale Book 3 summary")
    model_latest = json.loads((MODEL / "latest.json").read_text(encoding="utf-8"))
    expected_models = {"voice_family": model_latest["artifacts"]["voice_family.joblib"],
                       "mood": model_latest["artifacts"]["mood.joblib"]}
    if summary.get("prediction_model_artifacts") != expected_models:
        raise ValueError("Board is not bound to the frozen Books 1+2 predictors")

    suggestions = {row["id"]: row for row in rows(SOURCE / "suggestions-books12-v1.jsonl")}
    if suggestions.keys() != {row["id"] for row in candidates}:
        raise ValueError("Suggestion/candidate IDs differ")
    for row in candidates:
        proposal = suggestions[row["id"]]
        if (proposal["audio_sha256"] != row["audio_sha256"] or
                proposal.get("automatic_acceptance") is not False or
                proposal.get("human_review_required") is not True):
            raise ValueError("Unsafe or stale prediction: " + row["id"])
        for task, expected in expected_models.items():
            if proposal["tasks"][task].get("model_artifact_sha256") != expected:
                raise ValueError("Prediction model hash mismatch: " + row["id"])
            if row["prediction"]["tasks"][task].get("model_artifact_sha256") != expected:
                raise ValueError("UI prediction model hash mismatch: " + row["id"])

    hybrid = json.loads((SOURCE / "hybrid/suggestions.json").read_text(encoding="utf-8"))
    if hybrid.get("report", {}).get("prediction_model_artifacts") != expected_models:
        raise ValueError("Hybrid mood report is not bound to the frozen Books 1+2 predictors")
    hybrid_clips = hybrid.get("clips", {})
    if hybrid_clips.keys() != {row["id"] for row in candidates}:
        raise ValueError("Hybrid mood/candidate IDs differ")
    for row in candidates:
        if (hybrid_clips[row["id"]].get("prediction_model_artifacts") != expected_models or
                row["hybrid"].get("prediction_model_artifacts") != expected_models):
            raise ValueError("UI mood suggestion model hash mismatch: " + row["id"])

    context = json.loads((SOURCE / "context.json").read_text(encoding="utf-8"))
    context_sources = {row["source_id"] for row in context["passages"]}
    missing_context = {row["source_id"] for row in candidates} - context_sources
    if missing_context:
        raise ValueError("Candidates lack analyzed context: " + repr(sorted(missing_context)[:10]))

    review_raw = (BOARD / "review-draft.json").read_bytes()
    review = json.loads(review_raw)
    if review.get("candidates_sha256") != candidate_hash or review.get("decisions"):
        raise ValueError("Book 3 must begin with a separate empty human review")
    if (ARCHIVE / "review.json").read_bytes() != review_raw:
        raise ValueError("Protected Book 3 review does not match the board")
    latest = json.loads((ARCHIVE / "latest.json").read_text(encoding="utf-8"))
    if latest.get("review_sha256") != hashlib.sha256(review_raw).hexdigest():
        raise ValueError("Protected Book 3 review hash is stale")

    overlay = json.loads((BOARD / "voice-proposal-overrides.json").read_text(encoding="utf-8"))
    if (overlay.get("candidates_sha256") != candidate_hash or
            overlay.get("book1_review_sha256") != sha(ROOT / "manual-labeling/reality-benders-1/review.json") or
            overlay.get("book2_review_sha256") != sha(ROOT / "manual-labeling/reality-benders-2-full/review.json") or
            overlay.get("clips", {}).keys() != {row["id"] for row in candidates}):
        raise ValueError("Series voice prefill is stale or incomplete")

    audio_index = json.loads((ARCHIVE / "audio-index.json").read_text(encoding="utf-8"))
    if audio_index.keys() != {row["id"] for row in candidates}:
        raise ValueError("Archived Book 3 audio index is incomplete")
    for row in candidates:
        archived = audio_index[row["id"]]
        path = ARCHIVE / archived["archive_path"]
        if archived["sha256"] != row["audio_sha256"] or sha(path) != row["audio_sha256"]:
            raise ValueError("Archived Book 3 WAV changed: " + row["id"])
    if sha(ARCHIVE / "metadata/source.epub") != "bb546ee442d31566c2eb8ece698c3c47be5558e5fd9a031a0853fe9f0ce28025":
        raise ValueError("Archived Book 3 source changed")
    html = (BOARD / "index.html").read_text(encoding="utf-8")
    if not all(f'value="book{number}"' in html for number in (1, 2, 3)):
        raise ValueError("Three-book selector is missing")
    if 'id="proposal-model-status"' not in html:
        raise ValueError("Suggestion-model provenance is missing from the UI")
    print(f"Verified Book 3 board: {len(candidates)} candidates, frozen model hashes, full context, empty isolated review, and {len(audio_index)} archived WAVs.")


if __name__ == "__main__":
    main()
