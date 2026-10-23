// Reuse the review UI's measured-audio comparisons for new-book suggestions.
import fs from 'node:fs';
import vm from 'node:vm';
const request=JSON.parse(fs.readFileSync(0,'utf8'));
const sandbox={};vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(new URL('./book1_bucket_acoustics.js',import.meta.url),'utf8')+'\nglobalThis.api={buildBucketAcoustics,acousticPeerProfile,acousticMatchDimensions,acousticMatchScore,nearestAcousticExample};',sandbox);
const {buildBucketAcoustics,acousticPeerProfile,acousticMatchDimensions,acousticMatchScore,nearestAcousticExample}=sandbox.api;
const rows=request.training,byId=new Map(rows.map(r=>[r.id,r]));
const precision=Object.fromEntries(rows.map(r=>[r.id,{audio_sha256:r.exact_audio_sha256}]));
const features=Object.fromEntries(rows.map(r=>[r.id,{...r.acoustics,audio_sha256:r.exact_audio_sha256}]));
const buckets=new Map(),voices=new Map();
for(const row of rows){
  const key=`${row.voice_family}|${row.mood}`;
  if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(row.id);
  if(!voices.has(row.voice_family))voices.set(row.voice_family,[]);voices.get(row.voice_family).push(row.id);
}
const state=buildBucketAcoustics(buckets,byId,precision,features);
const all=buildBucketAcoustics(voices,byId,precision,features);
const result=request.queries.map(q=>{
  const common=all.profiles.get(q.voice_family),matches=[];
  if(!common||q.acoustics.duration_seconds<3)return {id:q.id,matches,reason:'too_short_or_no_voice_references'};
  const dimensions=acousticMatchDimensions(q.acoustics,acousticPeerProfile(common,q.source_id));
  for(const [key,profile] of state.profiles){
    if(key.split('|')[0]!==q.voice_family||profile.items.length<8||profile.items.length/profile.total<.5)continue;
    const peers=acousticPeerProfile(profile,q.source_id);
    if(peers.clips<7||peers.paragraphs<6)continue;
    const score=acousticMatchScore(dimensions,peers);
    if(!score)continue;
    const example=nearestAcousticExample(profile,dimensions,q.source_id,state.warnings);
    if(!example)continue;
    matches.push({mood:key.split('|')[1],score:score.score,max_distance:score.max,strong_cues:score.strong,peer_clips:peers.clips,peer_paragraphs:peers.paragraphs,example,
      distances:Object.fromEntries(score.distances)});
  }
  matches.sort((a,b)=>a.score-b.score||a.mood.localeCompare(b.mood));
  return {id:q.id,matches,dimensions:dimensions.map(d=>d.key)};
});
process.stdout.write(JSON.stringify(result));
