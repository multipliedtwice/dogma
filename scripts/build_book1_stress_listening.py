"""Pair high-priority stress words with exact-text audiobook clips."""

import collections
import hashlib
import html
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "stress-rb1"
CHARACTER = ROOT / "training-work" / "character-rb1"


def read_jsonl(path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def main():
    inventory = json.loads((BASE / "homograph-inventory.json").read_text(encoding="utf-8"))
    clips = collections.defaultdict(list)
    for name in ("audio-candidates.jsonl", "sentence-audio-candidates.jsonl", "recovered-audio-candidates.jsonl"):
        for clip in read_jsonl(CHARACTER / name):
            path = Path(clip["audio"])
            if path.is_file() and clip.get("source_id"):
                clips[clip["source_id"]].append(clip)
    queue = []
    for entry in inventory["words"]:
        if entry["count"] < 3:
            continue
        forms = entry["predicted_forms"]
        # Conflicting model predictions deserve review ahead of stable forms.
        if len(forms) < 2 and entry["count"] < 15:
            continue
        options = []
        seen_audio = set()
        for example in entry["examples"]:
            for clip in clips.get(example["source_id"], []):
                if not re.search(r"(?<![А-Яа-яЁё])" + re.escape(entry["word"]) + r"(?![А-Яа-яЁё])", clip["text"], re.I):
                    continue
                if clip["audio"] in seen_audio:
                    continue
                seen_audio.add(clip["audio"])
                options.append({
                    "source_id": example["source_id"], "text": clip["text"],
                    "audio": clip["audio"], "audio_sha256": clip.get("audio_sha256"),
                    "audio_reviewed": False,
                })
                break
            if len(options) >= 2:
                break
        if options:
            queue.append({"word": entry["word"], "count": entry["count"],
                          "kind": "homograph", "predicted_forms": forms, "source_clips": options,
                          "human_stress_reviewed": False})
    # Cast names and nicknames need a separate check even when they are absent
    # from the homograph dictionary. The cast list is reviewed text metadata.
    groups = json.loads((CHARACTER / "group-counts.json").read_text(encoding="utf-8"))
    names = {part.lower() for label in groups if "·" not in label for part in label.split()
             if len(part) >= 4 and sum(c in "аеёиоуыэюя" for c in part) >= 2}
    existing = {item["word"] for item in queue}
    rows = list(read_jsonl(BASE / "stress-predictions.jsonl"))
    for name in sorted(names - existing):
        occurrences = [r for r in rows if re.search(r"(?<![А-Яа-яЁё])" + re.escape(name) + r"(?![А-Яа-яЁё])", r["source_text"], re.I)]
        if not occurrences:
            continue
        options = []
        seen_audio = set()
        for row in occurrences:
            for clip in clips.get(row["source_id"], []):
                if clip["audio"] in seen_audio or not re.search(r"(?<![А-Яа-яЁё])" + re.escape(name) + r"(?![А-Яа-яЁё])", clip["text"], re.I):
                    continue
                seen_audio.add(clip["audio"])
                options.append({"source_id": row["source_id"], "text": clip["text"],
                                "audio": clip["audio"], "audio_sha256": clip.get("audio_sha256"),
                                "audio_reviewed": False})
                break
            if len(options) >= 2:
                break
        if options:
            queue.append({"word": name, "count": len(occurrences), "kind": "cast_name",
                          "predicted_forms": {}, "source_clips": options,
                          "human_stress_reviewed": False})
    queue.sort(key=lambda x: (x["kind"] != "cast_name", x["word"] != "следователя",
                              len(x["predicted_forms"]) < 2, -x["count"]))
    (BASE / "listening-queue.json").write_text(json.dumps({"schema": 1, "items": queue}, ensure_ascii=False, indent=2), encoding="utf-8")
    queue_hash = hashlib.sha256((BASE / "listening-queue.json").read_bytes()).hexdigest()
    cards = []
    for item in queue:
        sources = []
        for clip in item["source_clips"]:
            rel = os.path.relpath(clip["audio"], BASE).replace(os.sep, "/")
            key = html.escape(item["word"] + "|" + clip["audio_sha256"], quote=True)
            controls = ("<div class='review' data-key='" + key + "'><label><input type='checkbox' class='listened'> I listened</label> "
                        "<select class='verdict'><option value=''>Choose result</option><option value='confirmed'>Stress identified</option>"
                        "<option value='unclear'>Audio unclear</option><option value='text_mismatch'>Clip text mismatch</option></select> "
                        "<input class='heard' placeholder='Heard form, e.g. сле́дователя' aria-label='Heard stressed form'> "
                        "<input class='notes' placeholder='Notes / context' aria-label='Notes'></div>")
            sources.append("<p><b>" + html.escape(clip["source_id"]) + "</b> · " + html.escape(clip["text"]) +
                           "</p><audio controls preload='none' src='" + html.escape(rel, quote=True) + "'></audio>" + controls)
        forms = ", ".join(html.escape(k) + " (" + str(v) + ")" for k,v in item["predicted_forms"].items())
        label = "Cast name" if item["kind"] == "cast_name" else "Model options: " + forms
        cards.append("<article><h2>" + html.escape(item["word"]) + " · " + str(item["count"]) +
                     "</h2><p>" + label + "</p>" + "".join(sources) + "</article>")
    # The reported generated example has a dedicated four-way listening board.
    intro = """<!doctype html><html lang='ru'><meta charset='utf-8'><title>Book 1 stress listening</title>
<style>body{font:17px/1.5 system-ui;max-width:1050px;margin:auto;padding:24px;background:#faf8f2;color:#222}article{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px 20px;margin:14px 0}audio{width:100%}.review{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px;padding-top:10px;border-top:1px solid #ddd}input,select,button{font:inherit;padding:5px}button{margin:10px 0}</style>
<h1>Book 1 stress listening queue</h1><p>Listen to Vlad's source clip and record what you actually hear. Model options are prompts, not corrections. For the reported generated error, use the <a href='../character-rb1/mixed-scene-probe/stress-decision.html'>four-way comparison and decision</a>.</p><button id='export'>Export listening decisions</button><span id='status'></span>"""
    script = """<script>
const queueHash = """ + json.dumps(queue_hash) + """;
const storageKey = 'rb1-stress-review-' + queueHash;
const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
document.querySelectorAll('.review').forEach(box => {
  const d = saved[box.dataset.key] || {};
  box.querySelector('.listened').checked = !!d.listened;
  box.querySelector('.verdict').value = d.verdict || '';
  box.querySelector('.heard').value = d.heard || '';
  box.querySelector('.notes').value = d.notes || '';
  box.addEventListener('change', save); box.addEventListener('input', save);
});
function collect() {
  const decisions = {};
  document.querySelectorAll('.review').forEach(box => {
    const d = {listened: box.querySelector('.listened').checked,
      verdict: box.querySelector('.verdict').value,
      heard: box.querySelector('.heard').value.trim(),
      notes: box.querySelector('.notes').value.trim()};
    if (d.listened || d.verdict || d.heard || d.notes) decisions[box.dataset.key] = d;
  });
  return decisions;
}
function save() { localStorage.setItem(storageKey, JSON.stringify(collect()));
  document.getElementById('status').textContent = ' Saved in this browser'; }
document.getElementById('export').onclick = () => {
  const payload = {schema:1, queue_sha256:queueHash, decisions:collect()};
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  a.download='rb1-stress-review.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
};
</script>"""
    (BASE / "listening-review.html").write_text(intro + "\n".join(cards) + script + "</html>", encoding="utf-8")
    print(json.dumps({"queued_words": len(queue), "board_words": len(queue),
                      "source_clips": sum(len(x["source_clips"]) for x in queue)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
