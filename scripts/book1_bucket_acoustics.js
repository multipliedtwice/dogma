// Waveform-only comparisons. A warning requests listening; it never changes a label.
const ACOUSTIC_WARNING_THRESHOLDS = {joint:2.5,single:3};
const ACOUSTIC_METRICS = [
  {key:'median_pitch_hz',label:'Pitch',floor:8,unit:' Hz',digits:0},
  {key:'pitch_range_semitones',label:'Pitch range',floor:1.5,unit:' semitones',digits:1},
  {key:'pitch_start_offset_semitones',label:'Beginning pitch (relative)',floor:1.5,unit:' semitones',digits:1,group:'contour'},
  {key:'pitch_end_offset_semitones',label:'Ending pitch (relative)',floor:1.5,unit:' semitones',digits:1,group:'contour'},
  {key:'pitch_ending_change_semitones',label:'Rise / fall at the end',floor:1.5,unit:' semitones',digits:1,group:'contour'},
  {key:'active_level_dbfs',label:'Typical speech volume',floor:2,unit:' dBFS',digits:1},
  {key:'level_range_db',label:'Volume changes',floor:2,unit:' dB',digits:1},
  {key:'internal_pause_fraction',label:'Pauses',floor:.08,unit:'%',digits:0,display:100},
  {key:'envelope_peaks_per_second',label:'Pace estimate',floor:.6,unit:' sound peaks/s',digits:2}
];
// Visible averages include short clips; the warning population keeps its 3 s gate.
const ACOUSTIC_SUMMARY_METRICS = [
  {key:'envelope_peaks_per_second',label:'Pace estimate',unit:' sound peaks/s',digits:2},
  {key:'average_level_dbfs',label:'Average volume',unit:' dBFS',digits:1}
];
function trustedPitch(feature) {
  return Number.isFinite(feature.periodic_fraction)&&feature.periodic_fraction>=.45;
}
function pitchCurve(feature) {
  return feature.contour_schema===1&&trustedPitch(feature)&&
    Array.isArray(feature.relative_pitch_curve_semitones)&&feature.relative_pitch_curve_semitones.length===12?
    feature.relative_pitch_curve_semitones.map(value=>Number.isFinite(value)?value:null):Array(12).fill(null);
}
function checkedAcousticFeature(row,precision,features,minDuration=3) {
  const feature=features[row.id],audio=precision[row.id];
  return feature&&audio?.audio_sha256&&feature.audio_sha256===audio.audio_sha256&&
    Number.isFinite(feature.duration_seconds)&&feature.duration_seconds>0&&feature.duration_seconds>=minDuration ? feature : null;
}
function acousticTotals(items,metrics=ACOUSTIC_METRICS) {
  const scalar=Object.fromEntries(metrics.map(metric=>[metric.key,{sum:0,squares:0,count:0}])),
    curve=Array.from({length:12},()=>({sum:0,squares:0,count:0}));
  const add=(target,value)=>{if(Number.isFinite(value)){target.sum+=value;target.squares+=value*value;target.count++;}};
  for(const item of items){
    for(const metric of metrics)
      if(!metric.key.includes('pitch')||trustedPitch(item.feature))add(scalar[metric.key],item.feature[metric.key]);
    pitchCurve(item.feature).forEach((v,i)=>add(curve[i],v));
  }
  return {scalar,curve};
}
function averageFromTotals(total,exclude={sum:0,squares:0,count:0}) {
  const count=total.count-exclude.count,sum=total.sum-exclude.sum,squares=total.squares-exclude.squares;
  return {count,average:count?sum/count:null,deviation:count>1?Math.sqrt(Math.max(0,squares-sum*sum/count)/(count-1)):0};
}
function buildBucketAcoustics(buckets,byId,precision,features) {
  const stats=new Map(),warnings=new Map(),contours=new Map(),summaries=new Map(),profiles=new Map();
  for(const [key,ids] of buckets){
    const allItems=ids.map(id=>({row:byId.get(id),feature:checkedAcousticFeature(byId.get(id),precision,features,0)})).filter(item=>item.feature),
      summaryTotals=acousticTotals(allItems,ACOUSTIC_SUMMARY_METRICS),summaryParagraphs=new Map();
    for(const item of allItems){if(!summaryParagraphs.has(item.row.source_id))summaryParagraphs.set(item.row.source_id,[]);summaryParagraphs.get(item.row.source_id).push(item);}
    const summaryExclusions=new Map([...summaryParagraphs].map(([id,group])=>[id,acousticTotals(group,ACOUSTIC_SUMMARY_METRICS)]));
    for(const item of allItems){
      const excluded=summaryExclusions.get(item.row.source_id);
      summaries.set(item.row.id,ACOUSTIC_SUMMARY_METRICS.map(metric=>({...metric,value:item.feature[metric.key],
        ...averageFromTotals(summaryTotals.scalar[metric.key],excluded.scalar[metric.key])})));
    }
    const items=allItems.filter(item=>item.feature.duration_seconds>=3),
      paragraphs=new Map(),totals=acousticTotals(items);
    for(const item of items){if(!paragraphs.has(item.row.source_id))paragraphs.set(item.row.source_id,[]);paragraphs.get(item.row.source_id).push(item);}
    const averages=Object.fromEntries(ACOUSTIC_METRICS.map(metric=>[metric.key,averageFromTotals(totals.scalar[metric.key])])),
      exclusions=new Map([...paragraphs].map(([id,group])=>{
        const excluded=acousticTotals(group);
        return [id,{peerClips:items.length-group.length,
          scalar:Object.fromEntries(ACOUSTIC_METRICS.map(metric=>[metric.key,averageFromTotals(totals.scalar[metric.key],excluded.scalar[metric.key])])),
          curve:totals.curve.map((bin,i)=>averageFromTotals(bin,excluded.curve[i]))}];
      }));
    stats.set(key,{measured:items.length,total:ids.length,paragraphs:paragraphs.size,averages});
    profiles.set(key,{items,paragraphs,exclusions,averages,curve:totals.curve.map(bin=>averageFromTotals(bin)),total:ids.length});
    for(const item of items){
      const curve=pitchCurve(item.feature);
      if(curve.filter(Number.isFinite).length<6)continue;
      const average=exclusions.get(item.row.source_id).curve;
      contours.set(item.row.id,{curve,average:average.map(bin=>bin.count>=7?bin.average:null)});
    }
    // Short clips and tiny groups do not support stable tone comparisons.
    if(items.length<8||paragraphs.size<7||items.length/ids.length<.5)continue;
    for(const item of items){
      const excluded=exclusions.get(item.row.source_id);
      if(excluded.peerClips<7||paragraphs.size-1<6)continue;
      const peerStats=excluded.scalar,differences=[];
      for(const metric of ACOUSTIC_METRICS){
        const value=item.feature[metric.key],group=peerStats[metric.key];
        if(!Number.isFinite(value)||group.count<7||!Number.isFinite(group.average))continue;
        if(metric.key.includes('pitch')&&!trustedPitch(item.feature))continue;
        const distance=(value-group.average)/Math.max(group.deviation,metric.floor);
        if(Math.abs(distance)>ACOUSTIC_WARNING_THRESHOLDS.joint)differences.push({...metric,value,average:group.average,distance});
      }
      const curve=pitchCurve(item.feature),curveStats=excluded.curve,distances=[];
      for(let i=0;i<12;i++)if(Number.isFinite(curve[i])&&curveStats[i].count>=7)
        distances.push((curve[i]-curveStats[i].average)/Math.max(curveStats[i].deviation,1.5));
      if(distances.length>=8){
        const distance=Math.sqrt(distances.reduce((sum,v)=>sum+v*v,0)/distances.length);
        if(distance>ACOUSTIC_WARNING_THRESHOLDS.joint)
          differences.push({key:'pitch_contour_shape',label:'Pitch shape',group:'contour',distance,shape:true});
      }
      // Related contour measurements count as one cue, not multiple independent votes.
      if(new Set(differences.map(d=>d.group||d.key)).size>=2||differences.some(d=>Math.abs(d.distance)>ACOUSTIC_WARNING_THRESHOLDS.single))
        warnings.set(item.row.id,{bucket:key,differences,peerClips:excluded.peerClips});
    }
  }
  const {suggestions,currentReferences}=suggestAcousticBuckets(warnings,profiles,byId,precision,features);
  return {stats,warnings,contours,summaries,suggestions,currentReferences,profiles};
}
function acousticPeerProfile(profile,sourceId) {
  const excluded=profile.exclusions.get(sourceId);
  return {scalar:excluded?.scalar||profile.averages,curve:excluded?.curve||profile.curve,
    clips:excluded?.peerClips??profile.items.length,paragraphs:profile.paragraphs.size-(excluded?1:0)};
}
function acousticMatchDimensions(feature,own) {
  const dimensions=[];
  for(const metric of ACOUSTIC_METRICS){
    const group=own.scalar[metric.key],value=feature[metric.key];
    if(!Number.isFinite(value)||group.count<7||!Number.isFinite(group.average)||
      (metric.key.includes('pitch')&&!trustedPitch(feature)))continue;
    dimensions.push({...metric,value,scale:Math.max(group.deviation,metric.floor)});
  }
  const curve=pitchCurve(feature),bins=curve.flatMap((value,i)=>Number.isFinite(value)&&own.curve[i].count>=7?
    [{i,value,scale:Math.max(own.curve[i].deviation,1.5)}]:[]);
  if(bins.length>=8)dimensions.push({key:'pitch_contour_shape',label:'Pitch shape',group:'contour',shape:true,bins});
  return dimensions;
}
function acousticMatchScore(dimensions,profile) {
  const groups=new Map(),distances=new Map();
  for(const d of dimensions){
    let distance;
    if(d.shape){
      if(d.bins.some(bin=>profile.curve[bin.i].count<7||!Number.isFinite(profile.curve[bin.i].average)))return null;
      distance=Math.sqrt(d.bins.reduce((sum,b)=>sum+((b.value-profile.curve[b.i].average)/b.scale)**2,0)/d.bins.length);
    }else{
      const group=profile.scalar[d.key];
      if(group.count<7||!Number.isFinite(group.average))return null;
      distance=Math.abs((d.value-group.average)/d.scale);
    }
    distances.set(d.key,distance);
    const group=d.group||d.key;
    // One vote for related beginning, ending, and full-shape measurements.
    groups.set(group,Math.max(groups.get(group)||0,distance));
  }
  const values=[...groups.values()];if(values.length<4)return null;
  return {score:Math.sqrt(values.reduce((sum,v)=>sum+v*v,0)/values.length),max:Math.max(...values),
    strong:values.filter(v=>v>ACOUSTIC_WARNING_THRESHOLDS.joint).length,distances};
}
function nearestAcousticExample(profile,dimensions,sourceId,warnings) {
  let example='',exampleScore=Infinity;
  for(const item of profile.items){
    if(item.row.source_id===sourceId||warnings.has(item.row.id))continue;
    const scalar=Object.fromEntries(ACOUSTIC_METRICS.map(m=>[m.key,{count:7,average:
      m.key.includes('pitch')&&!trustedPitch(item.feature)?null:item.feature[m.key]}]));
    const comparison=acousticMatchScore(dimensions,{scalar,curve:pitchCurve(item.feature).map(v=>({count:7,average:v}))});
    if(comparison&&comparison.score<exampleScore){example=item.row.id;exampleScore=comparison.score;}
  }
  return example;
}
function suggestAcousticBuckets(warnings,profiles,byId,precision,features) {
  const suggestions=new Map(),currentReferences=new Map();
  for(const [id,warning] of warnings){
    const row=byId.get(id),feature=checkedAcousticFeature(row,precision,features),own=acousticPeerProfile(profiles.get(warning.bucket),row.source_id);
    if(!feature)continue;
    const dimensions=acousticMatchDimensions(feature,own),baseline=acousticMatchScore(dimensions,own);
    if(!baseline)continue;
    const currentExample=nearestAcousticExample(profiles.get(warning.bucket),dimensions,row.source_id,warnings);
    if(currentExample)currentReferences.set(id,{bucket:warning.bucket,example:currentExample,peerClips:own.clips});
    const matches=[];
    for(const [key,profile] of profiles){
      // Pitch, pace and level cannot establish timbre identity. Keep these mood
      // comparisons within the listener's selected voice family.
      if(key===warning.bucket||key.split('|')[0]!==warning.bucket.split('|')[0]||
        profile.items.length<8||profile.items.length/profile.total<.5)continue;
      const peers=acousticPeerProfile(profile,row.source_id);
      if(peers.clips<7||peers.paragraphs<6)continue;
      const match=acousticMatchScore(dimensions,peers);
      // Use the same dimensions and source scales for all destinations. A broad
      // destination cannot win simply by having a larger standard deviation.
      if(!match)continue;
      const clearer=match.score<=baseline.score*.7&&match.max<=ACOUSTIC_WARNING_THRESHOLDS.single&&match.strong<2&&
        warning.differences.some(d=>match.distances.has(d.key)&&match.distances.get(d.key)<Math.abs(d.distance)*.7);
      const reasons=warning.differences.filter(d=>match.distances.has(d.key)).slice(0,3).map(d=>
        d.shape?{...d,closer:match.distances.get(d.key)<Math.abs(d.distance)}:{...d,average:peers.scalar[d.key].average});
      // Pick an actual measured example near this clip, excluding its paragraph.
      const example=nearestAcousticExample(profile,dimensions,row.source_id,warnings);
      if(!example)continue;
      matches.push({bucket:key,score:match.score,clearer,closer:match.score<baseline.score*.95,
        peerClips:peers.clips,reasons,example});
    }
    matches.sort((a,b)=>a.score-b.score||a.bucket.localeCompare(b.bucket));
    if(matches.length)suggestions.set(id,matches.slice(0,3));
  }
  return {suggestions,currentReferences};
}
function acousticClipSummary(id,state) {
  const metrics=state.summaries?.get(id);if(!metrics)return '';
  const format=(v,m)=>v.toFixed(m.digits)+m.unit;
  return '<div class="acoustic-averages">'+metrics.map(m=>
    `<div><b>${m.label}</b>: ${Number.isFinite(m.value)?format(m.value,m):'not measured'}<span class="small">`+
    (Number.isFinite(m.average)?`Other clips average: ${format(m.average,m)} (${m.count} clips)`:'No other measured clips in this bucket')+'</span></div>').join('')+
    '<p class="small">Pace is estimated from sound peaks, not words. Volume is whole-clip RMS, including pauses; closer to 0 dBFS is louder. Averages use other paragraphs in this voice + mood bucket.</p></div>';
}
function acousticDifferenceText(difference) {
  if(difference.shape)return 'The pitch rises and falls differently from the average shape of other clips in this bucket.';
  const factor=difference.display||1,format=value=>(value*factor).toFixed(difference.digits)+difference.unit;
  return `${difference.label}: ${format(difference.value)}; other clips average ${format(difference.average)}.`;
}
function acousticPitchPlot(id,state) {
  const contour=state.contours?.get(id);if(!contour)return '';
  const combined=[...contour.curve,...contour.average].filter(Number.isFinite),limit=Math.max(6,Math.ceil(Math.max(...combined.map(Math.abs)))),
    point=(v,i)=>`${(22+i*276/11).toFixed(1)},${(43-v*30/limit).toFixed(1)}`;
  function paths(values,color){let run=[],out='';
    const flush=()=>{if(run.length>1)out+=`<polyline points="${run.join(' ')}" fill="none" stroke="${color}" stroke-width="2"/>`;run=[];};
    values.forEach((v,i)=>{if(Number.isFinite(v)){run.push(point(v,i));out+=`<circle cx="${(22+i*276/11).toFixed(1)}" cy="${(43-v*30/limit).toFixed(1)}" r="2" fill="${color}"/>`;}else flush();});flush();return out;
  }
  const hasAverage=contour.average.some(Number.isFinite);
  return '<div class="pitch-plot"><svg viewBox="0 0 320 90" role="img" aria-label="Pitch shape from beginning to end. Green is this clip; gray is the bucket average.">'+
    `<line x1="22" y1="43" x2="298" y2="43" stroke="#d5ded7" stroke-dasharray="3 3"/>`+
    `<text x="2" y="17">+${limit}</text><text x="2" y="74">−${limit}</text>`+
    paths(contour.average,'#909a94')+paths(contour.curve,'#28664b')+
    '<text x="22" y="87">Beginning</text><text x="275" y="87">End</text></svg>'+
    `<p class="small">Relative pitch: green = this clip${hasAverage?', gray = bucket average':''}. Each clip is centered on its own usual pitch. Gaps have no reliable pitch.</p></div>`;
}
