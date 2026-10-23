"""Validate an exported human stress review against the frozen listening queue."""

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "stress-rb1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output", type=Path, default=BASE / "human-reviewed-stresses.json")
    args = parser.parse_args()
    reviewer = args.reviewer.strip()
    if not reviewer:
        raise ValueError("Reviewer name is required")
    queue_path = BASE / "listening-queue.json"
    queue_sha = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    by_clip = {item["word"] + "|" + clip["audio_sha256"]: (item, clip)
               for item in queue["items"] for clip in item["source_clips"]}
    export = json.loads(args.review.read_text(encoding="utf-8"))
    if export.get("schema") != 1 or export.get("queue_sha256") != queue_sha or not isinstance(export.get("decisions"), dict):
        raise ValueError("Review does not match current listening queue")
    accepted = []
    unresolved = []
    for key, decision in export["decisions"].items():
        if key not in by_clip or not isinstance(decision, dict):
            raise ValueError(f"Unknown review clip: {key}")
        item, clip = by_clip[key]
        word = item["word"]
        verdict = decision.get("verdict")
        if verdict not in ("confirmed", "unclear", "text_mismatch"):
            raise ValueError(f"Missing or invalid verdict: {word}")
        if decision.get("listened") is not True:
            raise ValueError(f"Audio was not attested as listened: {word}")
        heard = unicodedata.normalize("NFC", str(decision.get("heard", "")).strip())
        if verdict == "confirmed":
            plain = unicodedata.normalize("NFD", heard).replace("\u0301", "").replace("+", "")
            plain = unicodedata.normalize("NFC", plain).lower()
            if plain != word.lower() and plain.replace("ё", "е") != word.lower().replace("ё", "е"):
                raise ValueError(f"Heard form does not match word: {word}")
            if not ("\u0301" in unicodedata.normalize("NFD", heard) or "+" in heard or "ё" in heard.lower()):
                raise ValueError(f"Confirmed form needs an explicit stress mark: {word}")
        path = Path(clip["audio"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != clip["audio_sha256"]:
            raise ValueError(f"Source audio changed: {word}, {clip['source_id']}")
        row = {"word": word, "verdict": verdict, "heard": heard,
               "notes": str(decision.get("notes", "")).strip(),
               "source_id": clip["source_id"], "audio_sha256": clip["audio_sha256"]}
        (accepted if verdict == "confirmed" else unresolved).append(row)
    report = {"schema": 1, "reviewer": reviewer, "queue_sha256": queue_sha,
              "queue_words": len(queue["items"]), "queue_clips": len(by_clip),
              "reviewed_clips": len(export["decisions"]),
              "confirmed_source_stresses": accepted, "unresolved": unresolved,
              "generated_tts_pronunciation_reviewed": False}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"confirmed": len(accepted), "unresolved": len(unresolved)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
