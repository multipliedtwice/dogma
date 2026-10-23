"""Install shared mixed-delivery controls without rebuilding frozen candidates."""
import argparse
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def mix_display(js):
    js=js.replace('${splitMixHeader(row)}','').replace('${splitMixSummary(row)}','').replace('\n  refreshSplitMixSummaries();','')
    old='<label>Mood<select data-field="mood" aria-label="Mood">${options(mood,true)}</select></label>'
    js=js.replace(old,'${cardMoodControl(row,mood)}',1)
    js=js.replace("if(byId.get(id)?.parent_id)return '<span class=\"acoustic-warning\">Split audio: boundary check pending</span>';", "if(byId.get(id)?.parent_id)return precision[id]?.alignment_hold?'<span class=\"acoustic-warning\">Check parent audio alignment</span>':'';")
    js=js.replace('<div class="muted"><span class="tag">${escapeHTML(row.chapter_id)}</span><span class="tag">${escapeHTML(row.presentation_channel_proposed||\'Direct voice\')}</span><span class="tag">${escapeHTML(row.id)}</span></div>','')
    js=js.replace('${row.parent_id?splitPartNotice(row):pilotHints(row)}','')
    js=js.replace('<button data-action="confirm" class="primary">Fits this bucket</button>','')
    js=js.replace('If yours still belongs here, keep its label and use Fits this bucket after listening.',
                  'If yours still belongs here, keep its labels unchanged and finish playing it.')
    js=js.replace('delete after.confirmed_voice_id;delete after.bucket_review;setDecision(id,after);',
                  'delete after.confirmed_voice_id;delete after.bucket_review;delete after.playback_acceptance;delete after.listening_evidence;setDecision(id,after);')
    js=js.replace('${splitControls(row)}<details><summary>Comment (optional)</summary><textarea aria-label="Comment" data-field="comment" placeholder="What sounds different?">${escapeHTML(d.bucket_comment||\'\')}</textarea></details>',
                  '${splitControls(row)}${compactDetails(row)}')
    js=js.replace("${verified(row)?(row.parent_id?'Labels verified; split audio needs boundary check':'Verified in this bucket'):'Listen and check this clip'}",'')
    js=js.replace("const comment=el.querySelector('[data-field=comment]');if(comment.value===old.comment)","const comment=el.querySelector('[data-field=comment]');if(comment&&comment.value===old.comment)")
    js=js.replace("function updateSummary() {", "function updateSummary() {",1)
    if 'if(proposalMode)$(\'#bucket-summary\').textContent=' not in js:
        js=js.replace("  const stats=acousticState.stats.get(selected),flagged=", "  if(proposalMode)$('#bucket-summary').textContent=selected?`${bucketLabel(selected)}: ${ids.length} clips`:'No clips in this bucket.';\n  const stats=acousticState.stats.get(selected),flagged=",1)
    if "if(proposalMode)$('#summary').textContent=" not in js:
        js=js.replace('  updateSummary();\n}\nfunction filteredIds()',"  if(proposalMode)$('#summary').textContent=`${rows.length} clips in ${buckets.size} tentative buckets. Move a clip only when its label is wrong.`;\n  updateSummary();\n}\nfunction filteredIds()",1)
    if "$('#confirm-listened').hidden=true" not in js:
        js=js.replace('  store=new Book1ReviewStore(summary);',"  if(proposalMode){$('#confirm-listened').hidden=true;$('#filter').closest('label').hidden=true;$('#filter').value='all';$('#confirm-listened').closest('section').querySelector('h2').textContent='Review clips';}\n  store=new Book1ReviewStore(summary);",1)
    if "const contexts=await fetch('source-context.json'" not in js:
        js=js.replace('  store=new Book1ReviewStore(summary);',"  const contexts=await fetch('source-context.json',{cache:'no-store'});if(contexts.ok){const data=await contexts.json();if(data.candidates_sha256!==summary.candidates_sha256)throw Error('Text context does not match this corpus');sourceContexts=data.sources;}\n  store=new Book1ReviewStore(summary);",1)
    js=js.replace("$('#filter').value=['pending','verified'].includes(params.get('show'))?params.get('show'):'all';", "$('#filter').value=proposalMode?'all':['pending','verified'].includes(params.get('show'))?params.get('show'):'all';")
    return js


def adapt(js):
    if 'let splitReview=' in js:
        js=js.split('\n// Controls for the shared bucket player.')[0]
        js=js.replace("${row.parent_id?'<p class=\"small\">Split part: labels are separate. Audio boundaries still need checking before training.</p>':pilotHints(row)}", "${row.parent_id?splitPartNotice(row):pilotHints(row)}")
        if "if(byId.get(id)?.parent_id)return" not in js:
            js=js.replace('function acousticWarningHeader(id) {', 'function acousticWarningHeader(id) {\n  if(byId.get(id)?.parent_id)return \'<span class="acoustic-warning">Split audio: boundary check pending</span>\';')
        return mix_display(js)+'\n'+(ROOT/'scripts/book_bucket_split_controls.js').read_text(encoding='utf-8')
    def replace(old,new):
        nonlocal js
        if js.count(old)!=1:raise ValueError('Split UI adapter mismatch: '+old[:90])
        js=js.replace(old,new,1)
    replace("const decision=id=>store.decisions[id]||{};", "let splitReview=null;\nconst decision=id=>splitReview?.getDecision(id,store.decisions)||store.decisions[id]||{};\nconst setDecision=(id,value)=>splitReview?splitReview.setDecision(id,value,store.decisions):store.decisions[id]=value;")
    replace("function rebuildBuckets() {", "function rebuildBuckets() {\n  if(splitReview){rows=splitReview.activeRows(store.decisions);for(const child of splitReview.children.values()){const parentPrecision=precision[child.parent_id]||{},parentQuality=quality[child.parent_id]||{};byId.set(child.id,child);precision[child.id]={status:'split_review_pending',audio:splitReview.media.get(child.id).url,audio_sha256:child.audio_sha256,training_approved:false,parent_status:parentPrecision.status,alignment_hold:parentPrecision.status==='review_only_asr_match'};quality[child.id]={audio_sha256:child.audio_sha256,reasons:[...(parentQuality.reasons||[])],parent_audio_sha256:parentPrecision.audio_sha256};}}")
    if "['pilot_asr_pass','review_only_asr_match'].includes(p?.status)" in js:
        replace("['pilot_asr_pass','review_only_asr_match'].includes(p?.status)",
                "['pilot_asr_pass','review_only_asr_match','split_review_pending'].includes(p?.status)")
    else:
        replace("p?.status==='pilot_asr_pass'", "(p?.status==='pilot_asr_pass'||p?.status==='split_review_pending')")
    replace("function eligible(row) {", "function eligible(row) {\n  if(splitReview?.active(row.id,store.decisions))return false;")
    replace("const audioURL = path=>", "const audioURL = path=>path.startsWith('blob:')?path:")
    # Every child edit is nested under its immutable parent ID, so the existing
    # conflict-aware save endpoint and protected archive continue to work.
    replace("store.decisions[id]=after;", "setDecision(id,after);")
    replace("store.decisions[id]=restored;", "setDecision(id,restored);")
    replace("const d=decision(id);store.decisions[id]={", "const d=decision(id);setDecision(id,{")
    replace("checked_at:new Date().toISOString()}};", "checked_at:new Date().toISOString()}});")
    replace("store.decisions[id]={...decision(id),bucket_comment:event.target.value};", "setDecision(id,{...decision(id),bucket_comment:event.target.value});")
    replace("store.decisions[id]={...decision(id),[field]:event.target.checked};delete store.decisions[id].bucket_review;", "const next={...decision(id),[field]:event.target.checked};delete next.bucket_review;setDecision(id,next);")
    replace("${pilotHints(row)}", "${row.parent_id?splitPartNotice(row):pilotHints(row)}")
    replace("function acousticWarningHeader(id) {", "function acousticWarningHeader(id) {\n  if(byId.get(id)?.parent_id)return '<span class=\"acoustic-warning\">Split audio: boundary check pending</span>';")
    replace("<details><summary>Comment (optional)</summary>", "${splitControls(row)}<details><summary>Comment (optional)</summary>")
    replace("async function init() {", "async function prepareSavedSplits(){if(!splitReview)return;await splitReview.prepare(store.decisions,id=>audioURL(precision[id].audio));}\nasync function init() {")
    replace("await store.load();loaded=true;", "await store.load();\n  const splitResponse=await fetch('split-plan.json',{cache:'no-store'});\n  if(splitResponse.ok){splitReview=new BucketSplitReview(await splitResponse.json(),rows,summary.candidates_sha256);await prepareSavedSplits();}\n  loaded=true;")
    replace("await store.save();if(generation===saveGeneration)", "await store.save();await prepareSavedSplits();if(generation===saveGeneration)")
    replace("await store.refresh();rebuildBuckets();", "await store.refresh();await prepareSavedSplits();rebuildBuckets();")
    replace("${verified(row)?'Verified in this bucket':'Listen and check this clip'}", "${verified(row)?(row.parent_id?'Labels verified; split audio needs boundary check':'Verified in this bucket'):'Listen and check this clip'}")
    js+='\n'+(ROOT/'scripts/book_bucket_split_controls.js').read_text(encoding='utf-8')
    return mix_display(js)


def install(board):
    script=board/'bucket-review.js'
    script.write_text(adapt(script.read_text(encoding='utf-8')),encoding='utf-8')
    shutil.copyfile(ROOT/'scripts/book_bucket_splits.js',board/'bucket-splits.js')
    for name in ['index.html','buckets.html']:
        path=board/name;html=path.read_text(encoding='utf-8')
        html=html.replace('Start here: listen, choose the voice and mood, then use <b>Move clip</b>. Use <b>Fits this bucket</b> when both labels are right. Your answers save automatically.',
                          'Listen to each clip. If both labels are right, leave them unchanged; completing playback records that review. Changing a voice or mood saves the move immediately. Choosing a mood from the dropdown or suggestions saves it and plays the next clip. Your answers save automatically.')
        if 'src="bucket-splits.js' not in html:
            html=html.replace('<script src="bucket-review.js">','<script src="bucket-splits.js?v=3"></script><script src="bucket-review.js">')
        old_css='.split-mix-badge{background:#dbeae0;color:#234b38;font-weight:700}.split-mix-summary{background:#f2f7f2;border:1px solid #c0d4c5;border-radius:8px;padding:12px;margin:12px 0}.split-mix-summary p{margin:5px 0}.split-mix-summary ol{margin:8px 0;padding-left:24px}.split-mix-summary li{margin:8px 0}.split-mix-summary a{font-weight:600;color:#23543d}\n'
        html=html.replace(old_css,'')
        if '.split-mix-mood{' not in html:
            html=html.replace('</style>', '.split-mix-mood{display:block;width:100%;text-align:left;background:white}\n</style>')
        if '.clip-context{' not in html:
            html=html.replace('</style>', '.clip-context{margin-top:12px;border-top:1px solid #d7e0d7;padding-top:8px}.clip-context .text{font-size:1rem}.clip-details{margin-top:12px}\n</style>')
        if '.clip-status:empty{' not in html:
            html=html.replace('</style>', '.clip-status:empty{display:none}\n</style>')
        path.write_text(html,encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--board',default='training-work/character-rb2/fine-tune-all');args=parser.parse_args()
    install(ROOT/args.board)
