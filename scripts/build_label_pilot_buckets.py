"""Put measured Book 2 proposals straight into the established bucket player.

Proposals are separate from human decisions. Weak moods stay in Needs mood
review; each clip offers audio-model comparisons and protected Book 1 examples.
"""
import argparse, gzip, json, re, shutil
from pathlib import Path
from prepare_book_label_pilot import ROOT,sha,save
from manual_labeling_archive import mirror_review

def replace_once(source,old,new):
    if source.count(old)!=1:raise ValueError('Bucket adapter no longer matches its source: '+old[:70])
    return source.replace(old,new,1)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--predictor-suffix',default='');parser.add_argument('--suggestions',default='suggestions.jsonl');parser.add_argument('--update-unreviewed',action='store_true')
    parser.add_argument('--source-folder',type=Path,default=ROOT/'training-work/character-rb2/pilot')
    parser.add_argument('--board-folder',type=Path,default=ROOT/'training-work/character-rb2/fine-tune-all')
    parser.add_argument('--manual-book-id',default='reality-benders-2')
    parser.add_argument('--book-label',default='Book 2')
    parser.add_argument('--full-book',action='store_true')
    parser.add_argument('--candidate-model-proposals',action='store_true',
                        help='Show a frozen candidate top-1 as a review proposal; never auto-approve it.')
    parser.add_argument('--source-epub',type=Path,default=ROOT/'content/reality-benders-2.epub')
    parser.add_argument('--port',type=int,default=None)
    args=parser.parse_args()
    import numpy as np
    pilot=args.source_folder.resolve()
    board=args.board_folder.resolve();board.mkdir(parents=True,exist_ok=True)
    full=args.full_book or args.manual_book_id.endswith('-full')
    protected=ROOT/'manual-labeling/reality-benders-1'
    latest=json.loads((protected/'latest.json').read_text(encoding='utf-8'))
    archive=json.loads((protected/'archive-manifest.json').read_text(encoding='utf-8'))
    if latest['review_sha256']!=archive['support_snapshot_review_sha256'] or sha(protected/'review.json')!=latest['review_sha256']:raise ValueError('Refresh protected Book 1 archive first')
    model=ROOT/'training-work/character-rb1'/('label-predictor-'+latest['review_sha256'][:12]+args.predictor_suffix)
    old=[json.loads(l) for l in (model/'benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    index=json.loads((protected/'audio-index.json').read_text(encoding='utf-8'))['references']
    verified_media=set()
    features={r['id']:r for r in map(json.loads,(pilot/'audio-features.jsonl').read_text(encoding='utf-8').splitlines())}
    with np.load(model/'training-features.npz',allow_pickle=False) as f:
        vectors={id:v for id,v in zip(f['ids'].tolist(),f['timbre'])}
    by_voice={}
    for label in {r['voice_family'] for r in old}:
        exemplars=[r for r in old if r['voice_family']==label and r['id'] in vectors]
        if exemplars:
            matrix=np.asarray([vectors[r['id']] for r in exemplars],dtype=np.float32)
            matrix/=np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-9)
            by_voice[label]=(exemplars,matrix)
    predictions={r['id']:r for r in map(json.loads,(pilot/args.suggestions).read_text(encoding='utf-8').splitlines())}
    hybrid=json.loads((pilot/'hybrid/suggestions.json').read_text(encoding='utf-8'))
    old_by_id={r['id']:r for r in old}
    context=json.loads((pilot/'context.json').read_text(encoding='utf-8'))
    characters={c['id']:c['name'] for c in context['characters']}
    context_by_source={}
    for passage in context['passages']:
        context_by_source.setdefault(passage['source_id'],[]).append(passage)
    rows=[];precision={};quality={};acoustics={}
    audit_status={x['id']:x['status'] for x in map(json.loads,(pilot/'clip-asr-audit.jsonl').read_text(encoding='utf-8').splitlines())}
    for r in map(json.loads,(pilot/'checked-clips.jsonl').read_text(encoding='utf-8').splitlines()):
        r={**r,'book_id':args.manual_book_id}
        if sha(ROOT/r['exact_audio'])!=r['exact_audio_sha256']:raise ValueError('Changed pilot WAV')
        pred=predictions[r['id']];voice=pred['tasks']['voice_family']
        assigned=voice['top3'][0]['label'] if (voice['meets_exploratory_score_threshold'] or args.candidate_model_proposals) else 'voice_pending'
        parts=[p for p in context_by_source.get(r['source_id'],[]) if p['start']<r['text_end'] and p['end']>r['text_start']]
        part=max(parts,key=lambda p:min(p['end'],r['text_end'])-max(p['start'],r['text_start'])) if parts else None
        part_context=part.get('context') if part else None
        if part and not part_context:
            performance=part.get('performance',{})
            part_context={
                'mood':performance.get('mood',part.get('delivery','neutral')),
                'channel':performance.get('presentation_channel','direct'),
                'content_register':performance.get('content_register','prose'),
                'confidence':performance.get('context_confidence','unknown'),
                'evidence':performance.get('context_evidence',[]),
            }
        refs=[]
        current=np.asarray(features[r['id']]['speaker_embedding'])
        for proposed in voice['top3']:
            group=by_voice.get(proposed['label'])
            if not group:continue
            matching,matrix=group
            match=matching[int(np.argmax(matrix@current.astype(np.float32)))]
            media=index.get(match['exact_audio'].replace('\\','/'))
            if media is None:raise ValueError('Missing protected reference '+match['exact_audio'])
            if media['archive'] not in verified_media:
                if sha(protected/media['archive'])!=media['sha256']:raise ValueError('Changed Book 1 reference')
                verified_media.add(media['archive'])
            refs.append({'voice':proposed['label'],'mood':match['mood'],'text':match['source_text'],'audio':'manual-labeling/reality-benders-1/'+media['archive']})
        hint=hybrid['clips'][r['id']];mood_refs=[]
        for match in hint['measurements']['matches'][:3]:
            exemplar=old_by_id[match['example']];media=index[exemplar['exact_audio'].replace('\\','/')]
            if media['archive'] not in verified_media:
                if sha(protected/media['archive'])!=media['sha256']:raise ValueError('Changed mood reference')
                verified_media.add(media['archive'])
            mood_refs.append({'voice':exemplar['voice_family'],'mood':exemplar['mood'],'text':exemplar['source_text'],'audio':'manual-labeling/reality-benders-1/'+media['archive']})
        proposal_basis=('Frozen Books 1+2 candidate model; untouched Book 3 evaluation proposal, never automatic approval.'
                        if args.candidate_model_proposals else 'Audio-only Book 1 model.')
        row={**r,'audio_sha256':r['exact_audio_sha256'],'base_voice_id_proposed':assigned,
            'base_voice_proposal_basis':proposal_basis,'mood_proposed':[hint['proposed_mood']],
            'prediction':pred,'hybrid':hint,'mood_references':mood_refs,'references':refs,'role':'narration' if part and part['speaker']=='narrator' else 'character' if part and part['speaker'] not in ('unknown','unresolved') else 'unresolved',
            'character_id':characters.get(part['speaker'],part['speaker']) if part else '',
            'context_hint':part_context,
            'presentation_channel_proposed':part_context['channel'] if part_context else 'unresolved',
            'content_register_proposed':part_context.get('content_register', 'prose') if part_context else 'unresolved'}
        rows.append(row)
        review_only=audit_status[r['id']]!='exact_asr_match' or r.get('alignment_status')=='near_word_alignment_review_only'
        precision[r['id']]={'status':'review_only_asr_match' if review_only else 'pilot_asr_pass',
            'audio':r['exact_audio'],'audio_sha256':r['exact_audio_sha256'],'training_approved':False}
        quality[r['id']]={'audio_sha256':r['exact_audio_sha256'],
            'reasons':['Approximate word alignment or independent ASR; listen to both edges'] if review_only else [],
            'status':'review_only_not_training_approved' if review_only else 'independent_asr_match_not_human_approval'}
        acoustics[r['id']]={k:v for k,v in features[r['id']].items() if k not in ('speaker_embedding','id')}
    data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode('utf-8')
    candidates=board/'candidates.jsonl'
    changed=candidates.exists() and candidates.read_bytes()!=data
    if changed and (board/'review-draft.json').exists():
        previous=json.loads((board/'review-draft.json').read_text(encoding='utf-8'))
        if not args.update_unreviewed or previous['decisions']:
            raise ValueError('Frozen pilot changed; create a new version instead of invalidating human answers')
    candidates.write_bytes(data);digest=sha(candidates)
    model_hashes={task:predictions[rows[0]['id']]['tasks'][task].get('model_artifact_sha256') for task in ('voice_family','mood')} if rows else {}
    summary={'schema':1,'book_id':args.manual_book_id,'review_mode':'proposal_buckets','candidates_sha256':digest,'clips':len(rows),
             'scope':f'Complete {args.book_label} audio; unaligned passages remain held' if full else f'Selected checked sentences from {args.book_label}; not complete chapter coverage',
             'voice_prediction_review_sha256':latest['review_sha256'],'mood_prefill_enabled':False,'tentative_hybrid_buckets':True,'automatic_acceptance':False}
    summary['prediction_model_artifacts']=model_hashes
    summary['candidate_model_proposals']=args.candidate_model_proposals
    save(board/'summary.json',summary)
    # The shared bucket client treats this as an optional overlay.  Supplying an
    # explicit empty overlay avoids a noisy 404 in a newly generated book.
    source_book=json.loads((pilot/'source/book.json').read_text(encoding='utf-8'))
    opening_titles={chapter['id']:chapter['title'] for chapter in source_book['chapters']
                    if re.match(r'^\s*(введение|вступление|пролог|introduction|prologue)\b',chapter['title'],re.I)}
    # Opening is a structural voice-family proposal. Keep the per-clip mood
    # produced by the audio/context analysis unless the listener saves a mood.
    opening_overrides={r['id']:{'base_voice_id_proposed':'narrator_opening',
        'base_voice_proposal_basis':f'Structural proposal: performed passage in opening section “{opening_titles[r["chapter_id"]]}”; confirm the distinct register by listening.'}
        for r in rows if r['chapter_id'] in opening_titles}
    save(board/'voice-proposal-overrides.json',{'schema':1,'candidates_sha256':digest,
        'description':'Structural narrator-register proposals; they are not human decisions.',
        'voices':{'narrator_opening':{'description':'Distinct performed register used throughout a Reality Benders opening or prologue.','review_status':'listener_requested_family_needs_clip_confirmation'}},
        'clips':opening_overrides})
    for name,clips in [('precision-v2/status.json',precision),('quality-screen.json',quality),('audio-acoustics.json',acoustics)]:save(board/name,{'schema':1,'candidates_sha256':digest,'clips':clips})
    taxonomy=json.loads((ROOT/'training/voice_families.json').read_text(encoding='utf-8'))
    taxonomy['families'].insert(0,{'id':'voice_pending','group':'Pending','label':'Needs voice review / different voice'})
    save(board/'voice-families.json',taxonomy)
    review=board/'review-draft.json'
    if not review.exists() or changed:save(review,{'schema':1,'candidates_sha256':digest,'decisions':{}})
    mirror_review(board,review.read_bytes())
    for source,dest in [('book1_review_shared.js','review-shared.js'),('book1_bucket_acoustics.js','bucket-acoustics.js'),('book_navigation.js','book-navigation.js'),('bucket_voice_family_editor.js','voice-family-editor.js'),('bucket_character_mode.js','bucket-character-mode.js')]:shutil.copyfile(ROOT/'scripts'/source,board/dest)
    html=(ROOT/'scripts/book1_bucket_review.html').read_text(encoding='utf-8')
    start=html.index('<section class="intro">');end=html.index('<section><div class="toolbar">',start)
    html=html[:start]+'''<section class="intro"><h1>Book 2 · Bucket labeling pilot</h1>
<p>Listen to each clip. If both labels are right, leave them unchanged; completing playback records that review. Changing a voice or mood saves the move immediately. Choosing a mood from the dropdown or suggestions saves it and plays the next clip. Your answers save automatically.</p>
<p>These are checked sentence clips from Chapters 1 and 2. Groups are <b>unconfirmed proposals</b> from your Book 1 labels: voice model + mood model + pitch, pace, pauses and volume comparisons. New voices may not match those families. <b>Needs mood review</b> means there is not enough comparable audio. Book 1 examples are available on each clip.</p>
<p>Text context is shown separately. Label how Vlad actually sounds, even when the text suggests a different emotion. Nothing is automatically approved for training.</p>
<p id="proposal-model-status" class="small" hidden></p>
<p id="summary" class="small">Loading clips…</p></section>
'''+html[end:]
    html=html.replace('Book 1 · Bucket labeling','Book 2 · Bucket labeling pilot')
    if full:
        html=html.replace('Book 2 · Bucket labeling pilot','Book 2 · Full-book labeling')
        html=html.replace('These are checked sentence clips from Chapters 1 and 2.',
            'These are independently checked sentence clips from the complete Book 2 audio. Some approximate alignments are for labeling only and remain held from training.')
    if args.book_label!='Book 2':
        html=html.replace('Book 2 · Full-book labeling',f'{args.book_label} · Full-book labeling')
        html=html.replace('Book 2 · Bucket labeling pilot',f'{args.book_label} · Bucket labeling')
        html=html.replace('the complete Book 2 audio',f'the complete {args.book_label} audio')
    js=(ROOT/'scripts/book1_bucket_review.js').read_text(encoding='utf-8')
    js=replace_once(js,'const decision=id=>store.decisions[id]||{};','let proposalMode=false;\nconst decision=id=>store.decisions[id]||{};')
    js=replace_once(js,'voice=aliases[voice]||voice;','if(proposalMode&&!d.voice_verdict)voice=row.base_voice_id_proposed;\n  voice=aliases[voice]||voice;')
    js=replace_once(js,'mood=canonicalMood(mood);','if(proposalMode&&!d.mood_verdict)mood=row.mood_proposed?.[0]||"mood_pending";\n  mood=canonicalMood(mood);')
    js=replace_once(js,"p?.status==='precision_pass'","(p?.status==='precision_pass'||proposalMode&&['pilot_asr_pass','review_only_asr_match'].includes(p?.status))")
    js=replace_once(js,"!d.boundary_start&&!d.boundary_end", "(proposalMode||!d.boundary_start&&!d.boundary_end)")
    js=replace_once(js,"return eligible(row)&&bucketReviewMatches", "return !decision(row.id).boundary_start&&!decision(row.id).boundary_end&&eligible(row)&&bucketReviewMatches")
    js=replace_once(js,"${rows.length-labeled} clips still need labels.","${proposalMode?rows.filter(r=>!verified(r)).length:rows.length-labeled} clips still need review.")
    js=replace_once(js,"${labeled-held} labeled clips in ${buckets.size} buckets.","${labeled-held} clips in ${buckets.size} ${proposalMode?'tentative ':''}buckets.")
    js=replace_once(js,"  updateSummary();\n}\nfunction filteredIds()", "  if(proposalMode){const checks=rows.filter(r=>voicePrefillReview(r.id)).length;$('#summary').textContent=`${rows.length} clips in ${buckets.size} tentative buckets. ${checks} strong voice disagreements are shown first. Move a clip only when its label is wrong.`;}\n  updateSummary();\n}\nfunction filteredIds()")
    js=replace_once(js,"  const stats=acousticState.stats.get(selected)", "  if(proposalMode){const checks=ids.filter(voicePrefillReview).length;$('#bucket-summary').textContent=selected?`${bucketLabel(selected)}: ${ids.length} clips${checks?` / ${checks} voice checks first`:''}`:'No clips in this bucket.';}\n  const stats=acousticState.stats.get(selected)")
    js=replace_once(js,"const row=byId.get(id),{voice,mood}=labels(row);if(!eligible(row)||!voice||!mood)return;","const row=byId.get(id),{voice,mood}=labels(row);if(!eligible(row)||!voice||!mood)return;\n  if(proposalMode&&(decision(id).boundary_start||decision(id).boundary_end)){updateCard(id,'Held for an audio-edge repair');return;}\n  if(proposalMode&&(voice==='voice_pending'||mood==='mood_pending')){updateCard(id,'Choose the voice and mood to save them.');return;}")
    js=replace_once(js,"const d=decision(id);store.decisions[id]={...d,bucket_review:","const d=decision(id);store.decisions[id]={...d,...(proposalMode&&!d.voice_verdict?{voice_verdict:'yes',confirmed_voice_id:voice}:{}),...(proposalMode&&!d.mood_verdict?{mood_verdict:'yes'}:{}),bucket_review:")
    js=replace_once(js,"Number(acousticState.warnings.has(b))-Number(acousticState.warnings.has(a))", "Number(hasReviewWarning(b))-Number(hasReviewWarning(a))||pilotReviewPriority(b)-pilotReviewPriority(a)")
    js=js.replace("filter(id=>acousticState.warnings.has(id)).length", "filter(id=>acousticState.warnings.has(id)||voicePrefillReview(id)).length")
    js=replace_once(js,"return acousticState.warnings.has(id)?", "if(precision[id]?.status==='review_only_asr_match'){const voice=voicePrefillReview(id)?` / Check voice: audio suggests ${escapeHTML(voiceLabel(byId.get(id).voice_prefill_alternative))}`:'';return `<span class=\"acoustic-warning\">Check audio alignment${voice}</span>`;}\n  if(openingMismatch(id))return '<span class=\"acoustic-warning\">Compare with Opening</span>';\n  if(voicePrefillReview(id)){const r=byId.get(id);return `<span class=\"acoustic-warning\">Check voice: audio suggests ${escapeHTML(voiceLabel(r.voice_prefill_alternative))}</span>`;}\n  if(pilotNeedsReview(id))return '<span class=\"acoustic-warning\">Compare model and audio hints</span>';\n  return acousticState.warnings.has(id)?")
    js=replace_once(js,"rebuildBuckets();readURL();rebuildBuckets();restorePosition(true);", "rebuildBuckets();if(proposalMode&&!new URL(location.href).searchParams.has('bucket'))selected=[...buckets.keys()].find(k=>k==='narrator_baseline|neutral')||selected;readURL();rebuildBuckets();restorePosition(true);")
    js=replace_once(js,"store=new Book1ReviewStore(summary);", "proposalMode=summary.review_mode==='proposal_buckets';\n  if(proposalMode){MOOD_GROUPS['Needs review']=['mood_pending'];MOODS.push('mood_pending');MOOD_LABELS.mood_pending='Needs mood review';}\n  store=new Book1ReviewStore(summary);")
    js=replace_once(js,"rows=raw.trim().split('\\n').map(JSON.parse).sort((a,b)=>a.recording_id.localeCompare(b.recording_id)||a.audio_start-b.audio_start);byId=new Map(rows.map(row=>[row.id,row]));", """rows=raw.trim().split('\\n').map(JSON.parse).sort((a,b)=>a.recording_id.localeCompare(b.recording_id)||a.audio_start-b.audio_start);byId=new Map(rows.map(row=>[row.id,row]));
  if(summary.candidate_model_proposals){
    const expected=summary.prediction_model_artifacts||{},tasks=['voice_family','mood'];
    if(tasks.some(task=>!expected[task]))throw Error('The suggestion model artifact hashes are missing.');
    for(const row of rows)for(const task of tasks){
      const proposal=row.prediction?.tasks?.[task];
      if(!proposal?.candidate_only||proposal.model_artifact_sha256!==expected[task]||proposal.next_pristine_gate!=='reality-benders-3'||
          JSON.stringify(proposal.training_books)!==JSON.stringify(['reality-benders-1','reality-benders-2-full']))
        throw Error(`Suggestion ${row.id}/${task} is not from the frozen Books 1+2 model.`);
      if(row.hybrid?.prediction_model_artifacts?.[task]!==expected[task])
        throw Error(`Mood suggestion ${row.id} is not bound to the frozen Books 1+2 ${task} model.`);
    }
    const modelStatus=$('#proposal-model-status');
    if(modelStatus){modelStatus.hidden=false;modelStatus.textContent=`Suggestions: frozen Books 1+2 model · voice ${expected.voice_family.slice(0,12)} · mood ${expected.mood.slice(0,12)}. Reviewed series priors override the default voice when stronger; model alternatives remain visible.`;}
  }""")
    js=replace_once(js,"<div class=\"acoustic-body\">${acousticWarningDetails(row.id)}</div>","<div class=\"acoustic-body\">${acousticWarningDetails(row.id)}</div>${visibleProposalHints(row)}")
    js=replace_once(js,"<p class=\"text\">${escapeHTML(row.source_text.trim())}</p>","<p class=\"text\">${escapeHTML(row.source_text.trim())}</p>${pilotHints(row)}")
    js=js.replace('book1-grouped-review.json','book2-grouped-review.json')
    js+='''
 function visibleProposalHints(row){
  if(!proposalMode||row.parent_id)return '';
  const current=labels(row),moods=[...new Set((row.hybrid?.top3||[]).map(p=>p.label).filter(Boolean))];
  const alternatives=moods.filter(mood=>mood!==current.mood).slice(0,2);
  const alignment=precision[row.id]?.status==='review_only_asr_match'?'<b>Audio warning:</b> check the beginning and ending; this alone does not mean the bucket is wrong. ':'';
  const voiceAlternative=row.voice_prefill_review&&row.voice_prefill_alternative&&row.voice_prefill_alternative!==current.voice?
    ` <b>Audio voice alternative:</b> ${escapeHTML(voiceLabel(row.voice_prefill_alternative))}.`:'';
  const otherMoods=alternatives.length?` <b>Other mood ideas:</b> ${escapeHTML(alternatives.map(mood=>MOOD_LABELS[mood]||mood).join(', '))}.`:'';
  return `<p class="small proposal-card-hints">${alignment}<b>Suggested label:</b> ${escapeHTML(voiceLabel(current.voice))} · ${escapeHTML(MOOD_LABELS[current.mood]||current.mood)}.${voiceAlternative}${otherMoods}</p>`;
}
function pilotHints(row){
  if(!proposalMode)return '';
  const show=(task,label)=>row.prediction.tasks[task].top3.map(p=>`${label==='Voice'?voiceLabel(p.label):MOOD_LABELS[p.label]||p.label} (${Math.round(p.score*100)} score)`).join(', ');
  const context=row.context_hint;
  const changed=labels(row).voice!==row.base_voice_id_proposed;
  const basis=row.base_voice_proposal_basis||'Audio-only Book 1 model.';
  return `${decision(row.id).boundary_start||decision(row.id).boundary_end?'<p class="acoustic-warning">Held for audio-edge repair</p>':''}${voicePrefillReview(row.id)?`<p class="acoustic-warning"><b>Voice check:</b> ${escapeHTML(row.voice_prefill_review_reason)}</p>`:''}<p class="small"><b>Current voice prefill:</b> ${escapeHTML(voiceLabel(row.base_voice_id_proposed))}. ${escapeHTML(basis)}</p><p class="small">${changed?'You changed the voice. The original mood comparisons below use the previous suggested family.':escapeHTML(row.hybrid.reason)+'. Proposed moods: '+escapeHTML(row.hybrid.top3.map(p=>MOOD_LABELS[p.label]||p.label).join(', '))+'. Listen before confirming.'}</p><details><summary>Audio hints and Book 1 comparisons</summary><p>Audio-only voice comparisons: ${escapeHTML(show('voice_family','Voice'))}. Model-only mood suggestions: ${escapeHTML(show('mood','Mood'))}. Scores are not measured accuracy.</p>`+
    (context?`<p>Text suggests: ${escapeHTML(MOOD_LABELS[context.mood]||context.mood)}. This describes the scene, not a confirmed audio label.</p>`:'')+
    '<h3>Nearest mood examples in this voice family</h3>'+row.mood_references.map(r=>`<p><b>${escapeHTML(voiceLabel(r.voice))} · ${escapeHTML(MOOD_LABELS[r.mood]||r.mood)}</b></p><p class="text">${escapeHTML(r.text)}</p><audio controls preload="none" src="/${escapeHTML(r.audio)}"></audio>`).join('')+
    '<h3>Voice family comparisons</h3>'+row.references.map(r=>`<p><b>${escapeHTML(voiceLabel(r.voice))} · ${escapeHTML(MOOD_LABELS[r.mood]||r.mood)}</b></p><p class="text">${escapeHTML(r.text)}</p><audio controls preload="none" src="/${escapeHTML(r.audio)}"></audio>`).join('')+'</details>'+
    `<details><summary>Full paragraph for context</summary><p class="text">${escapeHTML(row.paragraph_text)}</p></details><fieldset><legend>Audio edges</legend><label class="check"><input type="checkbox" data-edge="boundary_start" ${decision(row.id).boundary_start?'checked':''}> Beginning is cut or includes extra words</label><label class="check"><input type="checkbox" data-edge="boundary_end" ${decision(row.id).boundary_end?'checked':''}> Ending is cut or includes extra words</label><p class="small">Flagged clips stay available for review and are held from training.</p></fieldset>`;
}
function openingMismatch(id){const r=byId.get(id),d=decision(id);return proposalMode&&r?.base_voice_id_proposed==='narrator_opening'&&d.voice_verdict&&labels(r).voice!=='narrator_opening';}
function playbackAcceptedVoice(id){const r=byId.get(id),p=decision(id).playback_acceptance,l=r?labels(r):{};return !!r&&p?.audio_sha256===precision[id]?.audio_sha256&&p.voice_family_id===l.voice;}
function voicePrefillReview(id){const r=byId.get(id);return proposalMode&&r?.voice_prefill_review&&!decision(id).voice_verdict&&listened.get(id)!==fingerprint(r)&&!playbackAcceptedVoice(id);}
function pilotNeedsReview(id){const r=byId.get(id);return openingMismatch(id)||voicePrefillReview(id)||proposalMode&&!verified(r)&&(!r.hybrid?.agreement||r.base_voice_id_proposed==='voice_pending');}
function hasReviewWarning(id){const d=decision(id),p=precision[id];return !!(d.boundary_start||d.boundary_end||p?.alignment_hold||p?.status==='review_only_asr_match'||openingMismatch(id)||voicePrefillReview(id)||pilotNeedsReview(id)||acousticState.warnings.has(id));}
function pilotReviewPriority(id){const r=byId.get(id);return precision[id]?.status==='review_only_asr_match'?6:voicePrefillReview(id)?3+(r.voice_prefill_review_priority||0):acousticState.warnings.has(id)?2:pilotNeedsReview(id)?1:0;}
document.addEventListener('change',event=>{
  const field=event.target.dataset.edge;if(!field)return;
  const id=event.target.closest('article').dataset.id;
  store.decisions[id]={...decision(id),[field]:event.target.checked};delete store.decisions[id].bucket_review;
  queueSave();rebuildBuckets();updateCard(id,event.target.checked?'Held for an audio-edge repair':'Audio-edge flag cleared');
});
'''
    (board/'buckets.html').write_text(html,encoding='utf-8');(board/'index.html').write_text(html,encoding='utf-8');(board/'bucket-review.js').write_text(js,encoding='utf-8')
    from prepare_bucket_split_plan import build as build_split_plan
    from install_bucket_split_ui import install as install_split_ui
    build_split_plan(board,pilot,args.manual_book_id)
    install_split_ui(board)
    from build_book1_mixed_comparisons import build as build_book1_comparisons
    from install_book1_mixed_mode import install as install_mixed_mode
    build_book1_comparisons(board)
    install_mixed_mode(board)
    from install_bucket_hls_ui import install as install_hls
    install_hls(board)
    for page in (board/'buckets.html', board/'index.html'):
        page.write_text(page.read_text(encoding='utf-8').replace(
            'src="bucket-review.js"', 'src="bucket-review.js?v=6"'), encoding='utf-8')
    from prepare_book2_review_context import build as prepare_context
    manual=ROOT/'manual-labeling'/args.manual_book_id
    prepare_context(board,pilot/'source/book.json',manual)
    # Independent copies of all pilot media and metadata alongside human answers.
    media=manual/'audio';media.mkdir(exist_ok=True)
    audio_index={}
    for r in rows:
        dest=media/(r['exact_audio_sha256']+'.wav')
        if not dest.exists():shutil.copyfile(ROOT/r['exact_audio'],dest)
        if sha(dest)!=r['exact_audio_sha256']:raise ValueError('Pilot archive WAV mismatch')
        audio_index[r['id']]={'sha256':r['exact_audio_sha256'],'archive_path':str(dest.relative_to(manual)).replace('\\','/'),'source_path':r['exact_audio']}
    metadata=manual/'metadata';metadata.mkdir(exist_ok=True)
    for source,name in [(board/'candidates.jsonl','candidates.jsonl'),(board/'summary.json','summary.json'),(board/'voice-families.json','voice-families.json'),(board/'voice-proposal-overrides.json','voice-proposal-overrides.json'),(pilot/'clip-asr-audit.jsonl','clip-asr-audit.jsonl'),(pilot/'context.json','context.json'),(pilot/args.suggestions,'suggestions.jsonl'),(pilot/'hybrid/suggestions.json','hybrid-suggestions.json'),(pilot/'hybrid/report.json','hybrid-report.json')]:shutil.copyfile(source,metadata/name)
    shutil.copyfile(args.source_epub.resolve(),metadata/'source.epub')
    shutil.copyfile(pilot/'source/book.json',metadata/'source-book.json')
    save(manual/'audio-index.json',audio_index)
    save(manual/'archive-manifest.json',{'schema':1,'book_id':args.manual_book_id,'scope':summary['scope']+'; proposals separate from human labels',
        'candidates_sha256':digest,'metadata':{p.name:sha(p) for p in metadata.iterdir() if p.is_file()},'audio_index_sha256':sha(manual/'audio-index.json'),'audio_files':len(audio_index)})
    board_name=board.parent.name
    port=args.port or (1439 if full else 1438)
    (manual/'README.md').write_text(f'# {args.book_label} {"full" if full else "pilot"} labeling\n\nHuman answers: `review.json`; checksum and save metadata: `latest.json`.\n\nModel proposals live in `metadata/suggestions.jsonl`; they are not human decisions.\n`metadata/candidates.jsonl` binds sentence text to independent copies in `audio/`.\nAudio predictions and context have no automatic training approval.\n\nOpen http://127.0.0.1:{port}/{board_name}/fine-tune-all/ .\n',encoding='utf-8')
    # Large immutable board payloads otherwise make the first mobile load much
    # slower over Wi-Fi. The server uses these sidecars only when the browser
    # advertises gzip support; hashes and human review files remain unchanged.
    for path in [board/'candidates.jsonl',board/'audio-acoustics.json',board/'source-context.json',
                 board/'precision-v2/status.json',board/'quality-screen.json',board/'split-plan.json']:
        target=path.with_name(path.name+'.gz')
        with target.open('wb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,compresslevel=6,mtime=0) as packed:
                packed.write(path.read_bytes())
    print(f'Built {len(rows)} clips, {sum(r["base_voice_id_proposed"]!="voice_pending" for r in rows)} suggested voices above exploratory threshold; all proposed moods need human review.')

if __name__=='__main__':main()
