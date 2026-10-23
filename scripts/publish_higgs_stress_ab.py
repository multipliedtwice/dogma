"""Publish the frozen Higgs stress regression as a LAN-accessible A/B audition.

The page is deliberately listener-first: ASR and acoustic-witness results are
hidden so they cannot bias the initial judgment. Verdicts are kept in browser
storage and can be exported as JSON; publishing never promotes either model.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "training-work/higgs-stress-pilot/generated-audio-regression-v1"
PAGE = ROOT / "training-work/character-rb3-full/fine-tune-all/stress-finetune"
MEDIA = ROOT / "training-work/character-rb3/labeling-full/clips/stress-finetune"
MEDIA_URL = "/character-rb3/labeling-full/clips/stress-finetune"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def publish_audio(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != sha256(source):
            raise ValueError(f"Published audio changed: {target}")
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    if sha256(target) != sha256(source):
        raise ValueError(f"Published audio hash mismatch: {target}")


def main() -> None:
    plan = load(SOURCE / "plan.json")
    witness = load(SOURCE / "stress-witness-report.json")
    baseline_asr = load(SOURCE / "baseline/word-checks-1.7B.json")
    candidate_asr = load(SOURCE / "candidate/word-checks-1.7B.json")
    PAGE.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in plan["cases"]:
        case_id = case["id"]
        for model in ("baseline", "candidate"):
            publish_audio(
                SOURCE / model / f"{case_id}.wav",
                MEDIA / model / f"{case_id}.wav",
            )
        rows.append(
            {
                "id": case_id,
                "selected_text": case["selected_text"],
                "intended_stress": case["intended_stress"],
                "text": case["synthesis"]["text"],
                "baseline_url": f"{MEDIA_URL}/baseline/{case_id}.wav",
                "candidate_url": f"{MEDIA_URL}/candidate/{case_id}.wav",
                "diagnostics": {
                    "baseline_stress": witness["cases"][case_id]["baseline"],
                    "candidate_stress": witness["cases"][case_id]["candidate"],
                    "baseline_asr": baseline_asr["checks"][case_id],
                    "candidate_asr": candidate_asr["checks"][case_id],
                },
            }
        )

    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка обучения ударения Higgs</title>
<style>
:root{{--ink:#182a24;--muted:#607069;--paper:#f7f7f2;--card:#fff;--line:#cbd7d1;--green:#1f6649;--pale:#eaf1e8;--amber:#a85d00}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:18px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:auto;padding:28px 24px 80px}} h1{{font:700 42px/1.1 Georgia,serif;margin:0 0 12px}} .lede{{max-width:850px;color:var(--muted)}}
.toolbar{{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#f7f7f2ee;backdrop-filter:blur(8px);padding:14px 0;border-bottom:1px solid var(--line)}}
button,select{{font:inherit;border:1px solid #adc0b6;border-radius:11px;background:#f3f7f3;color:var(--ink);padding:10px 14px;cursor:pointer}} button:hover{{border-color:var(--green)}} .count{{margin-left:auto;color:var(--muted)}}
.case{{margin:24px 0;padding:24px;border:1px solid var(--line);border-radius:19px;background:var(--card);box-shadow:0 10px 35px #173a2710}}
.case-head{{display:flex;gap:16px;justify-content:space-between;align-items:start}} .case h2{{font:700 27px/1.2 Georgia,serif;margin:0}} .target{{display:inline-block;background:var(--pale);border-left:4px solid #8dab9a;border-radius:7px;padding:7px 12px;margin-top:10px}}
.sentence{{font:25px/1.55 Georgia,serif;background:#fafaf7;border-radius:13px;padding:16px;margin:18px 0}}
.players{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .take{{border:1px solid var(--line);border-radius:14px;padding:15px}} .take h3{{margin:0 0 10px;font-size:18px}} audio{{width:100%}}
.verdicts{{display:flex;gap:9px;flex-wrap:wrap;margin-top:17px}} .verdicts button.active{{background:var(--green);border-color:var(--green);color:white}} textarea{{width:100%;min-height:65px;margin-top:12px;border:1px solid var(--line);border-radius:11px;padding:11px;font:inherit;resize:vertical}}
details{{margin-top:13px;color:var(--muted)}} pre{{white-space:pre-wrap;font-size:13px}} .unreviewed{{color:var(--amber)}}
@media(max-width:720px){{main{{padding:18px 12px 60px}}h1{{font-size:32px}}.players{{grid-template-columns:1fr}}.sentence{{font-size:22px}}.count{{width:100%;margin-left:0}}}}
</style></head><body><main>
<h1>Проверка обучения ударения</h1>
<p class="lede">26 фрагментов, на которых раньше было замечено неверное ударение. В обеих версиях одинаковы текст, голос-опора и seed; отличается только модель. <b>A</b> — текущая production-модель, <b>B</b> — кандидат после обучения. Ничего ещё не установлено в production.</p>
<div class="toolbar"><button id="previous">← Назад</button><button id="next">Дальше →</button><select id="filter"><option value="all">Все примеры</option><option value="unreviewed">Только непроверенные</option><option value="different">Только различающиеся</option></select><button id="export">Скачать ответы</button><span class="count" id="count"></span></div>
<div id="cases"></div>
<script>const rows={data};
const KEY='rimsky-higgs-stress-ab-v1'; let saved=JSON.parse(localStorage.getItem(KEY)||'{{}}'); let index=0;
const esc=s=>String(s).replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
function store(){{localStorage.setItem(KEY,JSON.stringify(saved));render()}}
function visible(){{let f=document.querySelector('#filter').value;return rows.filter(r=>f==='all'||(f==='unreviewed'&&!saved[r.id]?.verdict)||(f==='different'&&['a','b','both_wrong'].includes(saved[r.id]?.verdict)))}}
function render(){{let list=visible();if(!list.length){{document.querySelector('#cases').innerHTML='<p>Здесь всё проверено.</p>';document.querySelector('#count').textContent='0';return}}index=Math.max(0,Math.min(index,list.length-1));let r=list[index],v=saved[r.id]||{{}};document.querySelector('#count').textContent=`${{index+1}} из ${{list.length}} · всего отмечено ${{Object.keys(saved).filter(k=>saved[k].verdict).length}}/26`;
document.querySelector('#cases').innerHTML=`<section class="case"><div class="case-head"><div><h2>${{esc(r.id)}}</h2><div class="target">Проверить: <b>${{esc(r.intended_stress)}}</b></div></div><span class="${{v.verdict?'':'unreviewed'}}">${{v.verdict?'Ответ сохранён':'Не проверено'}}</span></div><div class="sentence">${{esc(r.text)}}</div><div class="players"><div class="take"><h3>A · текущая production-модель</h3><audio controls preload="metadata" src="${{r.baseline_url}}"></audio></div><div class="take"><h3>B · кандидат после обучения</h3><audio controls preload="metadata" src="${{r.candidate_url}}"></audio></div></div><div class="verdicts">${{[['a','A лучше'],['b','B лучше'],['same','Одинаково хорошо'],['both_wrong','Оба неверно']].map(([x,t])=>`<button data-v="${{x}}" class="${{v.verdict===x?'active':''}}">${{t}}</button>`).join('')}}</div><textarea placeholder="Комментарий, если нужен">${{esc(v.note||'')}}</textarea><details><summary>Автоматическая диагностика (смотреть после прослушивания)</summary><pre>${{esc(JSON.stringify(r.diagnostics,null,2))}}</pre></details></section>`;
document.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{{saved[r.id]={{...saved[r.id],verdict:b.dataset.v,saved_at:new Date().toISOString()}};store()}});let note=document.querySelector('textarea');note.onchange=()=>{{saved[r.id]={{...saved[r.id],note:note.value,saved_at:new Date().toISOString()}};store()}};document.querySelectorAll('audio').forEach(a=>a.onplay=()=>document.querySelectorAll('audio').forEach(other=>{{if(other!==a)other.pause()}}));}}
document.querySelector('#previous').onclick=()=>{{index--;render();scrollTo({{top:0,behavior:'smooth'}})}};document.querySelector('#next').onclick=()=>{{index++;render();scrollTo({{top:0,behavior:'smooth'}})}};document.querySelector('#filter').onchange=()=>{{index=0;render()}};
document.querySelector('#export').onclick=()=>{{let payload={{schema:1,kind:'higgs_stress_ab_listener_review',exported_at:new Date().toISOString(),answers:saved}};let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}}));a.download='higgs-stress-ab-review.json';a.click();URL.revokeObjectURL(a.href)}};render();</script></main></body></html>"""
    (PAGE / "index.html").write_text(document, encoding="utf-8")
    (PAGE / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "production_approved": False,
                "cases": len(rows),
                "plan_sha256": sha256(SOURCE / "plan.json"),
                "audio": {
                    row["id"]: {
                        "baseline": sha256(SOURCE / "baseline" / f"{row['id']}.wav"),
                        "candidate": sha256(SOURCE / "candidate" / f"{row['id']}.wav"),
                    }
                    for row in rows
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Published {len(rows)} pairs to {PAGE}")


if __name__ == "__main__":
    main()
