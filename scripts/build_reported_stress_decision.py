"""Make the reported-word comparison export a distinct TTS pronunciation decision."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "training-work" / "character-rb1" / "mixed-scene-probe"
FILES = {
    "source": "01-real.wav", "original": "01-narrator.wav",
    "acute": "01-narrator-stress-marked.wav",
    "uppercase": "01-narrator-stress-uppercase.wav",
}


def main():
    manifest = {key: {"file": name, "sha256": hashlib.sha256((BASE / name).read_bytes()).hexdigest()}
                for key, name in FILES.items()}
    (BASE / "stress-decision-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    digest = hashlib.sha256((BASE / "stress-decision-manifest.json").read_bytes()).hexdigest()
    audio = "".join(f"<article><h2>{label}</h2><audio controls preload='none' src='{FILES[key]}'></audio></article>" for key, label in
                    [("source", "Vlad's source"), ("original", "Original TTS"),
                     ("acute", "Combining acute TTS"), ("uppercase", "Uppercase vowel TTS")])
    page = """<!doctype html><html lang='ru'><meta charset='utf-8'><title>Следователя: listening decision</title>
<style>body{font:17px/1.5 system-ui;max-width:850px;margin:2rem auto;padding:0 1rem;background:#f5f7fb;color:#182235}article{background:white;padding:1rem;margin:1rem 0;border-radius:12px}audio{width:100%}label{display:block;margin:12px 0}button,select,input{font:inherit;padding:5px}</style>
<h1>«Следователя»: listening decision</h1><p>Expected: сле́дователя, with stress on the first syllable. Listen to the source and all three generated versions before selecting a usable correction. This is separate from the book-wide source-word queue.</p>""" + audio + """
<article><label><input id='listened' type='checkbox'> I listened to all four clips</label>
<label>Which generated version has the correct stress and natural delivery?
<select id='choice'><option value=''>Choose</option><option value='original'>Original TTS</option><option value='acute'>Combining acute TTS</option><option value='uppercase'>Uppercase vowel TTS</option><option value='none'>None</option><option value='unclear'>Unclear</option></select></label>
<label>Notes <input id='notes' placeholder='What you heard'></label>
<button id='export'>Export decision</button><span id='status'></span></article>
<script>const digest=""" + json.dumps(digest) + """;
const key='rb1-reported-stress-'+digest;
const saved=JSON.parse(localStorage.getItem(key)||'{}');
for(const id of ['listened','choice','notes']) {const el=document.getElementById(id);
  if(id==='listened') el.checked=!!saved[id]; else el.value=saved[id]||'';
  el.addEventListener('change',save); el.addEventListener('input',save);}
function decision(){return {listened:document.getElementById('listened').checked,
  choice:document.getElementById('choice').value,notes:document.getElementById('notes').value.trim()};}
function save(){localStorage.setItem(key,JSON.stringify(decision()));document.getElementById('status').textContent=' Saved in this browser';}
document.getElementById('export').onclick=()=>{const payload={schema:1,manifest_sha256:digest,decision:decision()};
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  a.download='rb1-sledovatelya-stress-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};</script></html>"""
    (BASE / "stress-decision.html").write_text(page, encoding="utf-8")
    print("wrote", BASE / "stress-decision.html")


if __name__ == "__main__":
    main()
