"""Add temporary Book 1/Book 2 interleaved playback to a proposal bucket board."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Mixed-mode adapter mismatch: {old[:100]}")
    return text.replace(old, new, 1)


def install(board: Path) -> None:
    script = board / "bucket-review.js"
    js = script.read_text(encoding="utf-8")
    if "let book1Comparisons=" not in js:
        js = replace_once(js,
            "let playbackPositions={}, cursorTime=0, cursorAudio=null, gapPending=false, queueComplete=false, playPending=false, cancelLoad=null, lastPositionSave=0;",
            "let playbackPositions={}, cursorTime=0, cursorAudio=null, gapPending=false, queueComplete=false, playPending=false, cancelLoad=null, lastPositionSave=0;\nlet book1Comparisons=new Map(),mixedMode=false,mixedBucket='',mixedOrder=[];")
        old_filtered = '''function filteredIds() {
  // Sort the complete filtered bucket before pagination. Stable sorting keeps
  // source order within each group; existing playback queues keep their cursor.
  return (buckets.get(selected)||[])
    .filter(id=>$('#filter').value==='all'||(verified(byId.get(id))?$('#filter').value==='verified':$('#filter').value==='pending'))
    .sort((a,b)=>Number(hasReviewWarning(b))-Number(hasReviewWarning(a))||pilotReviewPriority(b)-pilotReviewPriority(a));
}'''
        new_filtered = '''function filteredIds() {
  const base=baseFilteredIds();
  if(!mixedMode)return base;
  if(mixedBucket!==selected||!mixedOrder.length)refreshMixedOrder();
  const allowed=new Set([...base,...(book1Comparisons.get(selected)||[])]);
  const kept=mixedOrder.filter(id=>allowed.has(id));
  for(const id of base)if(!kept.includes(id))kept.push(id);
  return kept;
}
function baseFilteredIds() {
  // Sort the complete filtered bucket before pagination. Stable sorting keeps
  // source order within each group; existing playback queues keep their cursor.
  return (buckets.get(selected)||[])
    .filter(id=>$('#filter').value==='all'||(verified(byId.get(id))?$('#filter').value==='verified':$('#filter').value==='pending'))
    .sort((a,b)=>Number(hasReviewWarning(b))-Number(hasReviewWarning(a))||pilotReviewPriority(b)-pilotReviewPriority(a));
}
function refreshMixedOrder(anchor='') {
  const base=baseFilteredIds(),references=[...(book1Comparisons.get(selected)||[])];
  const anchorIndex=base.indexOf(anchor),prefix=anchorIndex>=0?base.slice(0,anchorIndex+1):[],tail=anchorIndex>=0?base.slice(anchorIndex+1):base;
  const mixed=[];for(let i=0;i<tail.length;i++){if(i<references.length)mixed.push(references[i]);mixed.push(tail[i]);}
  mixedBucket=selected;mixedOrder=[...prefix,...mixed];
}'''
        js = replace_once(js, old_filtered, new_filtered)
        js = replace_once(js, "function card(row) {", "function card(row) {\n  if(row.comparison_book===1)return referenceCard(row);")
        js = replace_once(js, "    `${splitControls(row)}${compactDetails(row)}</article>`;\n}\nfunction writeURL",
            '''    `${splitControls(row)}${compactDetails(row)}</article>`;
}
function referenceCard(row){
  const {voice,mood}=labels(row);
  return `<article class="book-reference" data-id="${escapeHTML(row.id)}"><div class="clip-heading"><h2>Book 1 reference</h2><span class="reference-label">Book 1</span></div>`+
    `<p class="small"><b>${escapeHTML(voiceLabel(voice))} · ${escapeHTML(MOOD_LABELS[mood]||mood)}</b> · protected human label · playback only</p>`+
    `<p class="text">${escapeHTML(row.source_text.trim())}</p><audio controls preload="metadata" data-clip="${escapeHTML(row.id)}" src="${escapeHTML(audioURL(precision[row.id].audio))}"></audio></article>`;
}
function writeURL''')
        js = replace_once(js, "  const row=byId.get(id);if(!row||!eligible(row))return false;",
            "  const row=byId.get(id);if(!row||!eligible(row))return false;\n  if(row.comparison_book===1)return mixedMode&&bucketKey(labels(row).voice,labels(row).mood)===queueBucket;")
        js = replace_once(js, "  $('#confirm-listened').disabled=!ids.some(id=>listened.get(id)===fingerprint(byId.get(id))&&!verified(byId.get(id))&&!hasStagedLabels(id));",
            "  $('#confirm-listened').disabled=!ids.some(id=>listened.get(id)===fingerprint(byId.get(id))&&!verified(byId.get(id))&&!hasStagedLabels(id));\n  if(mixedMode){const references=book1Comparisons.get(selected)?.length||0;$('#bucket-summary').textContent+=` · ${references} temporary Book 1 comparisons after the current clip`;}")
        rows_line = "  rows=raw.trim().split('\\n').map(JSON.parse).sort((a,b)=>a.recording_id.localeCompare(b.recording_id)||a.audio_start-b.audio_start);byId=new Map(rows.map(row=>[row.id,row]));"
        js = replace_once(js, rows_line, rows_line + '''
  const comparisonResponse=await fetch('book1-comparisons.json',{cache:'no-store'});
  if(comparisonResponse.ok){const comparisonDoc=await comparisonResponse.json();
    for(const [key,items] of Object.entries(comparisonDoc.buckets||{})){const ids=[];for(const item of items){const row={id:item.id,comparison_book:1,source_text:item.text,paragraph_text:item.text,base_voice_id_proposed:item.voice_family,mood_proposed:[item.mood],recording_id:'rb1-reference',audio_start:0};ids.push(row.id);byId.set(row.id,row);precision[row.id]={status:'precision_pass',audio:item.audio,audio_sha256:item.audio_sha256,training_approved:false};quality[row.id]={audio_sha256:item.audio_sha256,reasons:[]};}book1Comparisons.set(key,ids);}
  }''')
        old_bucket = "$('#bucket').onchange=()=>{stopSequence();selected=$('#bucket').value;pageIndex=0;restorePosition();render(true);};"
        toggle = '''$('#mixed-mode').onchange=()=>{
  rememberPosition(true);const active=queueBucket===selected?currentQueueClip():'';const oldQueue=[...queue],oldIndex=queueIndex;
  mixedMode=$('#mixed-mode').checked;
  if(mixedMode)refreshMixedOrder(byId.get(active)?.comparison_book===1?'':active);
  else {mixedBucket='';mixedOrder=[];}
  const nextOrder=filteredIds();
  if(active){
    if(!mixedMode&&byId.get(active)?.comparison_book===1){const nextBook2=oldQueue.slice(oldIndex+1).find(id=>byId.get(id)?.comparison_book!==1),start=nextOrder.indexOf(nextBook2);queue=[active,...(start>=0?nextOrder.slice(start):nextOrder)];queueIndex=0;}
    else {queue=nextOrder;queueIndex=Math.max(0,queue.indexOf(active));}
    queueBucket=selected;queueFilter=$('#filter').value;queueComplete=false;
  }
  const position=active?filteredIds().indexOf(active):-1;if(position>=0)pageIndex=Math.floor(position/PAGE_SIZE);
  render(false,active);$('#save-status').textContent=mixedMode?'Mixed mode on: Book 1 references will play next and alternate with Book 2.':'Mixed mode off: temporary Book 1 references removed.';
};
$('#bucket').onchange=()=>{stopSequence();selected=$('#bucket').value;pageIndex=0;if(mixedMode)refreshMixedOrder();restorePosition();render(true);};'''
        js = replace_once(js, old_bucket, toggle)
        js = replace_once(js, "const id=audio.dataset.clip,row=byId.get(id);if(!id||!row||hasStagedLabels(id))return;",
            "const id=audio.dataset.clip,row=byId.get(id);if(!id||!row||row.comparison_book===1||hasStagedLabels(id))return;")
        script.write_text(js, encoding="utf-8")

    for name in ("index.html", "buckets.html"):
        path = board / name
        original = html = path.read_text(encoding="utf-8")
        if 'id="mixed-mode"' not in html:
            html = replace_once(html, '<label class="check"><input type="checkbox" id="follow" checked> Follow clip</label>',
                '<label class="check"><input type="checkbox" id="follow" checked> Follow clip</label>\n<label class="check"><input type="checkbox" id="mixed-mode"> Mixed mode</label>')
        if ".book-reference{" not in html:
            html = html.replace("</style>", ".book-reference{border-color:#849b88;background:#f7faf6}.book-reference .reference-label{display:inline-block;background:#dfeadf;border-radius:20px;padding:3px 9px;font-size:.85rem;font-weight:700}.book-reference h2{margin:0}\n</style>")
        if html != original:
            path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="training-work/character-rb2-full/fine-tune-all")
    args = parser.parse_args()
    install(ROOT / args.board)
