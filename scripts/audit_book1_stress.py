"""Predict stresses for every book-1 render segment; never edits source text."""

import argparse
import collections
import html
import json
import re
from pathlib import Path

from ruaccent import RUAccent


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "stress-rb1"
ROUTING = ROOT / "training-work" / "character-rb1" / "book1-render-routing.jsonl"
WORD = re.compile(r"[А-Яа-яЁё]+")
VOWELS = set("аеёиоуыэюя")


def vowel_count(word):
    return sum(c.lower() in VOWELS for c in word)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    model = RUAccent()
    model.load(omograph_model_size="tiny", use_dictionary=True, device="CPU",
               workdir=str(BASE / "model"), tiny_mode=True)
    rows = [json.loads(x) for x in ROUTING.open(encoding="utf-8")]
    if args.limit:
        rows = rows[:args.limit]
    output_path = BASE / ("stress-predictions-smoke.jsonl" if args.limit else "stress-predictions.jsonl")
    counts = collections.Counter()
    risks = []
    with output_path.open("w", encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            original = row["text"]
            predicted = model.process_all(original)
            words = WORD.findall(original)
            counts["words"] += len(words)
            counts["multisyllable"] += sum(vowel_count(w) > 1 for w in words)
            flags = []
            for w in words:
                low = w.lower()
                if vowel_count(w) < 2:
                    continue
                if low in model.omographs:
                    flags.append({"word": w, "kind": "homograph", "options": model.omographs[low]})
                    counts["homograph_occurrences"] += 1
                elif low not in model.accents:
                    flags.append({"word": w, "kind": "model_or_rule"})
                    counts["model_or_rule_occurrences"] += 1
                if w[0].isupper() and not original.lstrip().startswith(w):
                    counts["capitalized_occurrences"] += 1
            item = {
                "chapter_id": row["chapter_id"], "source_id": row["source_id"],
                "start": row["start"], "end": row["end"], "mode": row["mode"],
                "source_text": original, "predicted_text": predicted,
                "flags": flags, "human_audio_reviewed": False,
            }
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
            counts["segments"] += 1
            if flags:
                risks.append(item)
            if i % 250 == 0:
                print(f"{i}/{len(rows)}", flush=True)
    counts["flagged_segments"] = len(risks)
    counts["human_audio_reviewed"] = 0
    counts["reported_issue_source_id"] = "c0006-p00002"
    (BASE / "summary.json").write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")
    priority = sorted(risks, key=lambda r: (r["source_id"] != "c0006-p00002", -sum(f["kind"] == "homograph" for f in r["flags"]), -len(r["flags"])))
    cards = []
    for item in priority[:500]:
        flags = ", ".join(dict.fromkeys(f["word"] + " [" + f["kind"] + "]" for f in item["flags"]))
        cards.append("<article><h3>" + html.escape(item["source_id"] + " · " + item["mode"]) + "</h3><p>" +
                     html.escape(item["source_text"]) + "</p><p><b>Predicted:</b> " +
                     html.escape(item["predicted_text"]) + "</p><small>" + html.escape(flags) + "</small></article>")
    page = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Book 1 stress audit</title>
<style>body{font:17px/1.5 system-ui;max-width:1100px;margin:auto;padding:24px;background:#faf8f2;color:#222}article{background:white;border:1px solid #ddd;border-radius:10px;padding:12px 20px;margin:14px 0}h3{margin:0}small{color:#555}</style>
<h1>Book 1 stress audit</h1><p>RUAccent predictions are review prompts, not proof of spoken stress. Source text is unchanged. The first card contains the reported issue. Full results are in stress-predictions.jsonl.</p>""" + "\n".join(cards) + "</html>"
    (BASE / "review.html").write_text(page, encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
