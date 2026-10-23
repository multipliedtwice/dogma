import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {chromium} from 'playwright';

const root='training-work/character-rb1/fine-tune-all/';
const summary=JSON.parse(await readFile(root+'summary.json','utf8'));
const rows=(await readFile(root+'candidates.jsonl','utf8')).trim().split('\n').map(JSON.parse);
const p=JSON.parse(await readFile(root+'precision-v2/status.json','utf8')).clips;
const q=JSON.parse(await readFile(root+'quality-screen.json','utf8')).clips;
const seen=new Set();
const chosen=rows.filter(row=>{
  if(seen.has(row.source_id)||p[row.id]?.status!=='precision_pass'||row.role==='mixed'||
    p[row.id].audio_end-p[row.id].audio_start<3||
    q[row.id]?.reasons?.some(reason=>['missing_or_changed_audio','unexpected_pcm_format','empty_audio','duration_mismatch','clipping','level_outlier'].includes(reason)))return false;
  seen.add(row.source_id);return true;
}).slice(0,14);
assert.equal(chosen.length,14);
const ids=chosen.map(row=>row.id),target=ids.at(-1),bucket='narrator_baseline|neutral';
const features={schema:1,candidates_sha256:summary.candidates_sha256,clips:Object.fromEntries(chosen.map(row=>[row.id,{
  audio_sha256:p[row.id].audio_sha256,duration_seconds:4,median_pitch_hz:row.id===target?280:100,
  pitch_range_semitones:6,active_level_dbfs:-25,average_level_dbfs:row.id===target?-20:-26,level_range_db:10,internal_pause_fraction:.1,
  envelope_peaks_per_second:4,periodic_fraction:.8,contour_schema:1,
  pitch_start_offset_semitones:0,pitch_end_offset_semitones:0,pitch_ending_change_semitones:0,
  relative_pitch_curve_semitones:Array(12).fill(0)}]))};
const sandbox=vm.createContext({});
vm.runInContext(await readFile('scripts/book1_bucket_acoustics.js','utf8'),sandbox);
sandbox.buckets=new Map([[bucket,ids]]);sandbox.byId=new Map(chosen.map(row=>[row.id,row]));sandbox.precision=p;sandbox.features=features.clips;
let stats=vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox);
assert.equal(stats.stats.get(bucket).averages.median_pitch_hz.average,100+180/chosen.length,'Plain bucket average includes all measurements');
assert.equal(stats.warnings.size,1);assert.ok(stats.warnings.has(target));
assert.equal(stats.warnings.get(target).differences[0].average,100,'The warning compares other paragraphs');
assert.equal(stats.summaries.get(target)[1].value,-20,'Whole-clip volume is distinct from active-frame median');
assert.equal(stats.summaries.get(target)[1].average,-26,'Visible averages exclude this paragraph');
const short=structuredClone(features.clips);short[target].duration_seconds=1;sandbox.features=short;
const shortState=vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox);
assert.ok(shortState.summaries.has(target),'Short clips still display pace and volume');
assert.equal(shortState.warnings.size,0,'Short clips do not trigger statistical warnings');
assert.equal(shortState.summaries.get(target)[1].average,-26);
const moderate=structuredClone(features.clips);moderate[target].median_pitch_hz=125;sandbox.features=moderate;
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),1,'A single moderately unusual measurement now warns');
moderate[target].median_pitch_hz=123;
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),0,'One measurement below three deviations does not warn');
moderate[target].median_pitch_hz=122;moderate[target].active_level_dbfs=-19.5;
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),1,'Two measurements above 2.5 deviations now warn');
const shaped=structuredClone(features.clips);
for(const feature of Object.values(shaped)){feature.median_pitch_hz=100;feature.relative_pitch_curve_semitones=Array.from({length:12},(_,i)=>-6+12*i/11);}
shaped[target].relative_pitch_curve_semitones.reverse();sandbox.features=shaped;
const shapeState=vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox);
assert.ok(shapeState.warnings.get(target).differences.some(d=>d.key==='pitch_contour_shape'),'Opposite contour shapes warn even with identical median pitch and range');
assert.equal(shapeState.stats.get(bucket).averages.median_pitch_hz.average,100);
assert.match(vm.runInContext(`acousticPitchPlot('${target}',buildBucketAcoustics(buckets,byId,precision,features))`,sandbox),/polyline/);
const edges=structuredClone(features.clips);edges[target].median_pitch_hz=100;edges[target].pitch_end_offset_semitones=6;sandbox.features=edges;
assert.ok(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox).warnings.has(target),'Ending pitch can warn independently');
edges[target].pitch_end_offset_semitones=4;edges[target].pitch_start_offset_semitones=4;
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),0,'Related contour measures do not count as two independent cues');
const stale=structuredClone(features.clips);stale[target].audio_sha256='stale';sandbox.features=stale;
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),0,'Changed WAVs cannot trigger stale warnings');
assert.ok(!vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox).summaries.has(target),'Changed WAVs cannot show stale averages');
sandbox.features=features.clips;sandbox.buckets=new Map([[bucket,ids.slice(0,7)]]);
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features).warnings.size',sandbox),0,'Small groups have no warning');
sandbox.buckets=new Map([[bucket,ids]]);

// Suggestions use only audio, with the same scales and dimensions for every bucket.
const hintBucket='narrator_baseline|excited',otherVoiceBucket='masculine_2|neutral';
const hintRows=[],hintPrecision={...p},hintFeatures=structuredClone(features.clips),hintBuckets=new Map([[bucket,ids]]);
for(const [name,key] of [['mood',hintBucket],['voice',otherVoiceBucket]]){
  const group=[];
  for(let i=0;i<8;i++){
    const id=`${name}_${i}`,row={...chosen[0],id,source_id:`${name}_paragraph_${i}`,source_text:'Text does not enter the score'};
    hintRows.push(row);group.push(id);hintPrecision[id]={audio_sha256:`wav_${id}`};
    hintFeatures[id]={...features.clips[target],audio_sha256:`wav_${id}`};
  }
  hintBuckets.set(key,group);
}
sandbox.buckets=hintBuckets;sandbox.byId=new Map([...chosen,...hintRows].map(row=>[row.id,row]));sandbox.precision=hintPrecision;sandbox.features=hintFeatures;
const hintState=vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox);
assert.equal(hintState.suggestions.get(target)[0].bucket,hintBucket,'Hints compare moods within the selected voice family');
assert.equal(hintState.suggestions.get(target).length,1,'Matching pitch cannot suggest a different voice family');
assert.ok(hintState.suggestions.get(target)[0].example.startsWith('mood_'));
assert.equal(hintState.suggestions.get(target)[0].clearer,true,'A strong measured improvement is marked explicitly');
assert.ok(hintState.currentReferences.get(target).example!==target,'A warning also gets a current-bucket example');
assert.ok(!hintState.suggestions.has(ids[0]),'No hints for unflagged clips');
const textChanged=new Map([...sandbox.byId].map(([id,row])=>[id,{...row,source_text:'An unrelated scene with a completely different intended emotion'}]));
sandbox.byId=textChanged;
assert.deepEqual(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox).suggestions.get(target),hintState.suggestions.get(target),'Text cannot change audio hints');
sandbox.buckets=new Map([[bucket,ids],[hintBucket,hintBuckets.get(hintBucket).slice(0,7)]]);
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox).suggestions.size,0,'Sparse destinations produce no guesses');
sandbox.buckets=hintBuckets;
const distantFeatures=structuredClone(hintFeatures);
for(const id of hintBuckets.get(hintBucket))distantFeatures[id].median_pitch_hz=100;
sandbox.features=distantFeatures;
const uncertainState=vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox);
assert.equal(uncertainState.suggestions.get(target)[0].clearer,false,'An alternative that is not better is not called a stronger match');
assert.equal(uncertainState.suggestions.get(target)[0].closer,false);
assert.ok(uncertainState.suggestions.has(target),'Inconclusive warnings still offer listening comparisons');
sandbox.features=hintFeatures;
const nearbyTextGroup=hintBuckets.get(hintBucket);
for(const id of nearbyTextGroup)textChanged.get(id).source_id=chosen.at(-1).source_id;
sandbox.buckets=new Map([[bucket,ids],[hintBucket,nearbyTextGroup]]);
assert.equal(vm.runInContext('buildBucketAcoustics(buckets,byId,precision,features)',sandbox).suggestions.size,0,'The target paragraph cannot supply its own destination evidence');

// Browser hints use real WAV paths, isolated labels, and mocked writes.
const hintSeen=new Set(chosen.map(row=>row.source_id));
const browserHintRows=rows.filter(row=>{
  if(hintSeen.has(row.source_id)||p[row.id]?.status!=='precision_pass'||row.role==='mixed'||q[row.id]?.audio_sha256!==row.audio_sha256||
    q[row.id]?.reasons?.some(reason=>['missing_or_changed_audio','unexpected_pcm_format','empty_audio','duration_mismatch','clipping','level_outlier'].includes(reason)))return false;
  hintSeen.add(row.source_id);return true;
}).slice(0,8);
assert.equal(browserHintRows.length,8);
const browserFeatures=structuredClone(features);
for(const row of browserHintRows)browserFeatures.clips[row.id]={...features.clips[target],audio_sha256:p[row.id].audio_sha256};
let draft={schema:1,candidates_sha256:summary.candidates_sha256,reviewer:'test-only',
  decisions:Object.fromEntries([...ids.map(id=>[id,{voice_verdict:'no',corrected_voice_id:'narrator_baseline',mood_verdict:'no',corrected_mood:'neutral'}]),
    ...browserHintRows.map(row=>[row.id,{voice_verdict:'no',corrected_voice_id:'narrator_baseline',mood_verdict:'no',corrected_mood:'excited'}])])};
let servedFeatures=browserFeatures;
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1250,height:900}});
  await context.route('**/review-draft.json',route=>route.fulfill({json:draft}));
  await context.route('**/audio-acoustics.json',route=>route.fulfill({json:servedFeatures}));
  await context.route('**/save-review',route=>route.abort());
  await context.route('**/patch-review',route=>{
    const {changes}=route.request().postDataJSON();
    for(const [id,change] of Object.entries(changes)){
      const next={...draft.decisions[id]};
      for(const field of new Set([...Object.keys(change.before||{}),...Object.keys(change.after||{})])){
        if(JSON.stringify(change.before?.[field])===JSON.stringify(change.after?.[field]))continue;
        if(Object.hasOwn(change.after||{},field))next[field]=change.after[field];else delete next[field];
      }
      draft.decisions[id]={...next,_revision:(draft.decisions[id]?._revision||0)+1};
    }
    return route.fulfill({json:{saved:true,review:draft}});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const url='http://127.0.0.1:1437/character-rb1/fine-tune-all/buckets.html?bucket='+encodeURIComponent(bucket);
  await page.goto(url);const card=page.locator(`article[data-id="${target}"]`);await card.waitFor();
  assert.equal(await page.locator('article').first().getAttribute('data-id'),target,'A warning at the end of the source list appears first, before pagination');
  const order=await page.evaluate(()=>filteredIds());
  const sourceOrder=await page.evaluate(()=>buckets.get(selected));
  assert.deepEqual(order,[target,...sourceOrder.filter(id=>id!==target)],'Unflagged clips retain source order');
  await page.locator('#next').click();
  assert.equal(await page.locator('.acoustic-warning').count(),0,'The first page contains the warning rather than leaving it on page two');
  await page.locator('#prev').click();await card.waitFor();
  assert.equal(await page.locator('.acoustic-warning').count(),1);
  assert.match(await card.locator('.acoustic-warning').textContent(),/Unusual for this bucket/);
  assert.match(await card.locator('.acoustic-averages').textContent(),/Average volume: -20.0 dBFS/);
  assert.match(await card.locator('.acoustic-averages').textContent(),/Other clips average: -26.0 dBFS/);
  const normalCard=page.locator(`article[data-id="${ids[0]}"]`);
  assert.equal(await normalCard.locator('.acoustic-warning').count(),0);
  assert.match(await normalCard.locator('.acoustic-averages').textContent(),/Pace estimate: 4.00 sound peaks\/s/,'Unflagged clips show average pace too');
  await card.locator('.acoustic-details summary').click();
  assert.match(await card.locator('.acoustic-details').textContent(),/Pitch: 280 Hz; other clips average 100 Hz/);
  assert.equal(await card.locator('.pitch-plot svg').count(),1,'Clip and bucket pitch curves are visible');
  assert.equal(draft.decisions[target].corrected_mood,'neutral','Warnings never relabel a clip');
  await card.locator('.acoustic-hints>summary').click();
  assert.match(await card.locator('.acoustic-hints').textContent(),/Narrator baseline.*Excited/);
  assert.match(await card.locator('.acoustic-hints').textContent(),/Current bucket: Narrator baseline/,'Users can compare before deciding to keep the current label');
  await card.locator('[data-action=hint]').first().click();
  assert.equal(await card.locator('[data-field=mood]').inputValue(),'excited');
  assert.equal(draft.decisions[target].corrected_mood,'neutral','Using a hint only stages choices; it does not move or approve');
  assert.ok(!draft.decisions[target].bucket_review);
  assert.equal(await card.locator('.hint-example audio').first().getAttribute('preload'),'none');
  assert.equal(await card.locator('.hint-example audio').first().getAttribute('data-clip'),null,'Examples do not count as listening to the target');
  await card.locator('[data-field=mood]').selectOption('energetic');
  assert.equal(await card.locator('.acoustic-warning').count(),1,'Staged choices do not change the saved bucket');
  await card.locator('[data-action=move]').click();
  assert.equal(await card.locator('.acoustic-warning').count(),0,'Moving recalculates the target bucket immediately');
  assert.equal(await card.locator('.acoustic-hints').count(),0,'Moving removes stale hints');
  assert.match(await card.locator('.acoustic-averages').textContent(),/No other measured clips in this bucket/,'Moved clip no longer compares with old bucket');
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  await page.locator('#undo').click();assert.equal(await card.locator('.acoustic-warning').count(),1,'Undo restores the warning');
  assert.match(await card.locator('.acoustic-averages').textContent(),/Other clips average: -26.0 dBFS/,'Undo restores averages');
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  await page.reload();await card.waitFor();assert.equal(await card.locator('.acoustic-warning').count(),1);
  assert.equal(await page.locator('article').first().getAttribute('data-id'),target,'Refresh keeps warnings first');
  await card.scrollIntoViewIfNeeded();await card.locator('.acoustic-details summary').click();
  await page.screenshot({path:'training-work/character-rb1/label-audit-20260929/warning-ui-check.png',fullPage:false});
  servedFeatures={...features,candidates_sha256:'wrong-corpus'};
  await page.reload();await page.locator('article').first().waitFor();assert.equal(await page.locator('.acoustic-warning').count(),0);
  assert.deepEqual(await page.evaluate(()=>filteredIds()),sourceOrder,'Without valid warning data, clips retain source order');
  assert.equal(await page.locator('.acoustic-averages').count(),0,'Stale corpus measurements are hidden');
  assert.match(await page.locator('#acoustic-summary').textContent(),/unavailable.*do not match/i);
  assert.ok(await page.locator('#play-bucket').isEnabled(),'Missing measurements do not block playback');
  assert.deepEqual(errors,[]);
  const liveContext=await browser.newContext({viewport:{width:1250,height:1000}});
  await liveContext.route('**/save-review',route=>route.abort());
  await liveContext.route('**/patch-review',route=>route.abort());
  const live=await liveContext.newPage();
  await live.goto('http://127.0.0.1:1437/character-rb1/fine-tune-all/buckets.html');
  await live.locator('article').first().waitFor();
  const flaggedBucket=await live.evaluate(()=>acousticState.warnings.values().next().value?.bucket);
  assert.ok(flaggedBucket,'Expected live audio warnings missing');
  await live.locator('#bucket').selectOption(flaggedBucket);
  await live.evaluate(()=>{const i=filteredIds().findIndex(id=>acousticState.warnings.has(id));
    if(i<0)throw Error('Expected live audio warning missing');pageIndex=Math.floor(i/PAGE_SIZE);render();});
  const liveCard=live.locator('article').filter({has:live.locator('.acoustic-warning')}).first();
  await liveCard.scrollIntoViewIfNeeded();await liveCard.locator('.acoustic-details summary').click();
  await live.screenshot({path:'training-work/character-rb1/label-audit-20260929/live-warning-ui-check.png',fullPage:false});
  assert.match(await liveCard.locator('.acoustic-details').textContent(),/other clips average/);
  console.log('PASS: averages, warnings first, audio-only mood hints, sparse/unrelated destination gates, staged choices, move/undo/refresh, playback and stale-data fallback.');
}finally{await browser.close();}
