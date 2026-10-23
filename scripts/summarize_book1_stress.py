"""Summarize RUAccent predictions into a compact lexical review queue."""

import collections
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "stress-rb1"
ORIG = re.compile(r"[А-Яа-яЁё]+")
PRED = re.compile(r"[+А-Яа-яЁё]+")


def main():
    records = [json.loads(x) for x in (BASE / "stress-predictions.jsonl").open(encoding="utf-8")]
    inventory = collections.defaultdict(lambda: {"forms": collections.Counter(), "examples": []})
    mismatches = []
    for r in records:
        source = ORIG.findall(r["source_text"])
        predicted = PRED.findall(r["predicted_text"])
        if len(source) != len(predicted):
            mismatches.append({"source_id": r["source_id"], "source_count": len(source), "predicted_count": len(predicted)})
            continue
        for w, p in zip(source, predicted):
            key = w.lower()
            if not any(f["kind"] == "homograph" and f["word"].lower() == key for f in r["flags"]):
                continue
            entry = inventory[key]
            entry["forms"][p.lower()] += 1
            if len(entry["examples"]) < 8:
                entry["examples"].append({"source_id": r["source_id"], "source_text": r["source_text"], "predicted": p})
    sortable = sorted(inventory.items(), key=lambda x: -sum(x[1]["forms"].values()))
    out = {
        "schema": 1, "model": "RUAccent 1.5.8.3 tiny, dictionary enabled",
        "acoustic_reviewed": False, "homograph_unique": len(inventory),
        "token_alignment_mismatches": mismatches,
        "words": [{"word": w, "count": sum(d["forms"].values()), "predicted_forms": dict(d["forms"]), "examples": d["examples"]} for w, d in sortable],
    }
    (BASE / "homograph-inventory.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    cards = []
    for w, d in sortable[:150]:
        forms = ", ".join(f"{f}: {n}" for f, n in d["forms"].most_common())
        examples = "".join("<li><b>" + html.escape(e["source_id"]) + "</b> " + html.escape(e["source_text"]) + "</li>" for e in d["examples"][:3])
        cards.append(f"<details><summary><b>{html.escape(w)}</b> · {sum(d['forms'].values())} · {html.escape(forms)}</summary><ul>{examples}</ul></details>")
    page = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Book 1 homographs</title>
<style>body{font:17px/1.5 system-ui;max-width:1050px;margin:auto;padding:24px;background:#faf8f2;color:#222}details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:9px 0}li{margin:8px 0}</style>
<h1>Book 1 homograph review</h1><p>Predicted stresses only. Listen to source and generated audio before accepting a pronunciation. Expand a word to see source passages.</p>""" + "\n".join(cards) + "</html>"
    (BASE / "homograph-review.html").write_text(page, encoding="utf-8")
    print(json.dumps({"homograph_unique": len(inventory), "alignment_mismatches": len(mismatches), "top": [(w, sum(d["forms"].values())) for w,d in sortable[:15]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
