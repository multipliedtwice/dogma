"""Apply reviewed series priors and the frozen Books 1+2 audio model to Book 3.

This writes proposal overlays only.  Human reviews and raw Book 3 predictions
remain unchanged so Book 3 stays a valid untouched evaluation set.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from build_series_voice_prefill import (
    ROOT, character_profiles, load, match_character, normalized_name,
    presentation_voice, rows, save, sha,
)


BOOK1 = ROOT / "manual-labeling/reality-benders-1"
BOOK1_CAST = ROOT / "training-work/character-rb1/benchmark/reviewed_cast_v3.json"
BOOK2 = ROOT / "manual-labeling/reality-benders-2-full"
BOOK2_BOARD = ROOT / "training-work/character-rb2-full/fine-tune-all"
BOOK3 = ROOT / "manual-labeling/reality-benders-3"
BOARD = ROOT / "training-work/character-rb3-full/fine-tune-all"
PRESENTATION_PROFILE = ROOT / "training/narrator_presentation_profiles/vlad-rimsky.json"
VOICE_FAMILIES = ROOT / "training/voice_families.json"


def explicit_voice(row: dict, decision: dict) -> str | None:
    verdict = decision.get("voice_verdict")
    if verdict == "yes":
        return decision.get("confirmed_voice_id") or row.get("base_voice_id_proposed")
    if verdict == "no":
        return decision.get("corrected_voice_id")
    return None


def book2_profiles(candidates: list[dict], review: dict) -> dict[str, dict]:
    counts = defaultdict(Counter)
    chapters = defaultdict(set)
    labels = defaultdict(set)
    decisions = review.get("decisions", {})
    for row in candidates:
        name = normalized_name(row.get("character_id", ""))
        voice = explicit_voice(row, decisions.get(row["id"], {}))
        if (not name or name in {"unknown", "unresolved"} or not voice
                or row.get("role") != "character"):
            continue
        counts[name][voice] += 1
        chapters[name].add(row["chapter_id"])
        labels[name].add(row.get("character_id", ""))
    profiles = {}
    for name, families in counts.items():
        family, matching = families.most_common(1)[0]
        total = sum(families.values())
        if matching >= 3 and len(chapters[name]) >= 2 and matching / total >= .8:
            profiles[name] = {"voice_family": family, "support": total,
                              "matching_family": matching,
                              "purity": matching / total,
                              "chapters": len(chapters[name]),
                              "labels": sorted(labels[name])}
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    b1_latest = load(BOOK1 / "latest.json")
    b2_latest = load(BOOK2 / "latest.json")
    if sha(BOOK1 / "review.json") != b1_latest["review_sha256"]:
        raise ValueError("Book 1 protected review changed")
    if sha(BOOK2 / "review.json") != b2_latest["review_sha256"]:
        raise ValueError("Book 2 protected review changed")
    b1_rows = rows(BOOK1 / "labels.jsonl")
    if any(row.get("review_sha256") != b1_latest["review_sha256"] for row in b1_rows):
        raise ValueError("Book 1 derived labels are stale")
    b1_profiles, b1_aliases = character_profiles(b1_rows, load(BOOK1_CAST))
    b2_candidates = rows(BOOK2_BOARD / "candidates.jsonl")
    b2_review = load(BOOK2 / "review.json")
    b2_summary = load(BOOK2_BOARD / "summary.json")
    if (sha(BOOK2_BOARD / "candidates.jsonl") != b2_summary["candidates_sha256"] or
            b2_review.get("candidates_sha256") != b2_summary["candidates_sha256"]):
        raise ValueError("Book 2 decisions do not match the frozen candidate set")
    b2_by_name = book2_profiles(b2_candidates, b2_review)

    summary = load(BOARD / "summary.json")
    candidates = rows(BOARD / "candidates.jsonl")
    if sha(BOARD / "candidates.jsonl") != summary["candidates_sha256"]:
        raise ValueError("Book 3 candidates changed")
    existing = load(BOARD / "voice-proposal-overrides.json")
    opening_overrides = {
        clip_id: value for clip_id, value in existing.get("clips", {}).items()
        if value.get("base_voice_id_proposed") == "narrator_opening"
        and value.get("base_voice_proposal_basis", "").startswith("Structural proposal:")
    }
    presentation_profile = load(PRESENTATION_PROFILE)
    voices = [{"id": item["id"], "tags": []}
              for item in load(VOICE_FAMILIES)["families"]]
    overrides = {}
    reasons = Counter()
    for row in candidates:
        clip_id = row["id"]
        if clip_id in opening_overrides:
            overrides[clip_id] = opening_overrides[clip_id]
            reasons["opening_structure"] += 1
            continue
        audio = row["prediction"]["tasks"]["voice_family"]["top3"][0]
        channel = row.get("presentation_channel_proposed", "direct")
        register = row.get("content_register_proposed", "prose")
        fixed, warning = presentation_voice(row, "narrator_baseline",
                                             presentation_profile, voices)
        rule = ((presentation_profile.get("registers", {}).get(register)
                 if register != "prose" else None)
                or presentation_profile.get("channels", {}).get(channel, {}))
        if rule.get("mode") == "fixed" and fixed != "narrator_baseline":
            proposal = {"base_voice_id_proposed": fixed,
                        "base_voice_proposal_basis":
                            f"Reviewed presentation profile: {(register if register != 'prose' else channel).replace('_', ' ')}."}
            if warning:
                proposal.update(voice_prefill_review=True,
                                voice_prefill_alternative=audio["label"],
                                voice_prefill_audio_score=audio["score"],
                                voice_prefill_review_priority=3,
                                voice_prefill_review_reason=warning)
            overrides[clip_id] = proposal
            reasons["fixed_presentation"] += 1
            continue
        if row.get("role") == "narration" or row.get("character_id") == "narrator":
            overrides[clip_id] = {
                "base_voice_id_proposed": "narrator_baseline",
                "base_voice_proposal_basis": "Structural narrator role from the validated Book 3 context pass."}
            reasons["narrator_structure"] += 1
            continue
        name = normalized_name(row.get("character_id", ""))
        is_character = row.get("role") == "character" and name not in {"unknown", "unresolved"}
        prior = b2_by_name.get(name) if is_character else None
        basis = None
        if prior:
            selected = prior["voice_family"]
            basis = (f"Book 2 reviewed series prior: {prior['matching_family']}/{prior['support']} "
                     f"clips across {prior['chapters']} chapters.")
            reasons["book2_character_prior"] += 1
        else:
            b1 = (match_character(row.get("character_id", ""), b1_aliases, b1_profiles)
                  if is_character else None)
            selected = b1["voice_family"] if b1 else audio["label"]
            if b1:
                basis = (f"Book 1 reviewed series prior: {b1['matching_family']}/{b1['support']} "
                         f"clips across {b1['chapters']} chapters.")
                reasons["book1_character_prior"] += 1
            else:
                basis = "Frozen Books 1+2 audio model top-1; human review required."
                reasons["books12_audio_model"] += 1
        ordinary = selected
        selected, presentation_warning = presentation_voice(
            row, selected, presentation_profile, voices)
        if selected != ordinary:
            basis += (f" Reviewed {channel.replace('_', ' ')} presentation mapping uses "
                      f"{selected} for ordinary family {ordinary}.")
            reasons["mapped_presentation"] += 1
        proposal = {"base_voice_id_proposed": selected,
                    "base_voice_proposal_basis": basis}
        if presentation_warning or (selected != audio["label"] and audio["score"] >= .9):
            proposal.update(voice_prefill_review=True,
                            voice_prefill_alternative=audio["label"],
                            voice_prefill_audio_score=audio["score"],
                            voice_prefill_review_priority=3 if presentation_warning else 2,
                            voice_prefill_review_reason=
                                (presentation_warning or
                                 f"Reviewed series prior says {selected}; the frozen audio model says {audio['label']} at {audio['score']:.3f}."))
            reasons["strong_prior_audio_disagreement"] += 1
        overrides[clip_id] = proposal

    out = ROOT / "training-work/character-rb3-full/series-voice-prefill-v1"
    report = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "book1_review_sha256": b1_latest["review_sha256"],
              "book2_review_sha256": b2_latest["review_sha256"],
              "book3_candidates_sha256": summary["candidates_sha256"],
              "proposal_reasons": dict(reasons),
              "stable_book2_character_profiles": b2_by_name,
              "voice_review_flags": sum(bool(value.get("voice_prefill_review"))
                                          for value in overrides.values()),
              "raw_book3_predictions_changed": False,
              "human_reviews_changed": False}
    save(out / "report.json", report)
    overlay = {"schema": 2, "candidates_sha256": summary["candidates_sha256"],
               "description": "Book 3 proposals from structural roles, protected series decisions, and the frozen Books 1+2 audio model.",
               "book1_review_sha256": b1_latest["review_sha256"],
               "book2_review_sha256": b2_latest["review_sha256"],
               "policy_report": (out / "report.json").relative_to(ROOT).as_posix(),
               "voices": existing.get("voices", {}), "clips": overrides}
    save(out / "voice-proposal-overrides.json", overlay)
    if args.apply:
        save(BOARD / "voice-proposal-overrides.json", overlay)
        metadata = BOOK3 / "metadata/voice-proposal-overrides.json"
        save(metadata, overlay)
        manifest = load(BOOK3 / "archive-manifest.json")
        manifest["metadata"][metadata.name] = sha(metadata)
        save(BOOK3 / "archive-manifest.json", manifest)
    print(json.dumps({"applied": args.apply, "clips": len(overrides),
                      "reasons": dict(reasons)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
