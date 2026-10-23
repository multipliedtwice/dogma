// Mixed delivery is annotated separately from sentence-sized synthesis inputs.
// The original candidate and human answer are retained; children are review
// ranges with independent labels, never automatically approved training audio.
function bucketSha256Fallback(input) {
  // WebCrypto is unavailable on ordinary http:// Wi-Fi origins in mobile
  // browsers. Keep the same source and child-audio integrity checks there.
  const bytes=input instanceof Uint8Array?input:new Uint8Array(input);
  const size=Math.ceil((bytes.length+9)/64)*64, padded=new Uint8Array(size);
  padded.set(bytes);padded[bytes.length]=0x80;
  const view=new DataView(padded.buffer),bits=bytes.length*8;
  view.setUint32(size-8,Math.floor(bits/0x100000000),false);
  view.setUint32(size-4,bits>>>0,false);
  const k=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const h=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19],w=new Uint32Array(64);
  const rotr=(value,count)=>(value>>>count)|(value<<(32-count));
  for(let offset=0;offset<size;offset+=64){
    for(let i=0;i<16;i++)w[i]=view.getUint32(offset+i*4,false);
    for(let i=16;i<64;i++){
      const s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>>3);
      const s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>>10);
      w[i]=(w[i-16]+s0+w[i-7]+s1)>>>0;
    }
    let [a,b,c,d,e,f,g,hh]=h;
    for(let i=0;i<64;i++){
      const s1=rotr(e,6)^rotr(e,11)^rotr(e,25),ch=(e&f)^(~e&g);
      const t1=(hh+s1+ch+k[i]+w[i])>>>0,s0=rotr(a,2)^rotr(a,13)^rotr(a,22),maj=(a&b)^(a&c)^(b&c),t2=(s0+maj)>>>0;
      hh=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;
    }
    h[0]=(h[0]+a)>>>0;h[1]=(h[1]+b)>>>0;h[2]=(h[2]+c)>>>0;h[3]=(h[3]+d)>>>0;
    h[4]=(h[4]+e)>>>0;h[5]=(h[5]+f)>>>0;h[6]=(h[6]+g)>>>0;h[7]=(h[7]+hh)>>>0;
  }
  return h.map(value=>value.toString(16).padStart(8,'0')).join('');
}
class BucketSplitReview {
  constructor(plan, parents, digest) {
    if(plan.schema!==1||plan.candidates_sha256!==digest)throw Error('Split boundaries do not match this corpus');
    this.plan=plan;this.digest=digest;this.parents=new Map(parents.map(r=>[r.id,r]));
    this.children=new Map();this.media=new Map();this.sources=new Map();
  }
  split(id,decisions){return decisions[id]?.split_review;}
  active(id,decisions){return this.split(id,decisions)?.enabled===true;}
  partId(id,start,end){return `${id}~${start}-${end}`;}
  ranges(id,cuts){
    const row=this.parents.get(id),plan=this.plan.clips[id];
    if(!row||!plan||plan.audio_sha256!==row.audio_sha256)throw Error('Split source changed');
    if(!Array.isArray(cuts)||!cuts.length||new Set(cuts).size!==cuts.length)throw Error('Choose at least one split point');
    const ordered=[...cuts].sort((a,b)=>a-b);
    if(ordered.some(c=>!plan.cuts.some(p=>p.offset===c)))throw Error('Split point is not a complete word boundary');
    const edges=[{offset:0,frame:0},...ordered.map(c=>plan.cuts.find(p=>p.offset===c)),{offset:row.source_text.length,frame:plan.frames}];
    return edges.slice(0,-1).map((a,i)=>({id:this.partId(id,a.offset,edges[i+1].offset),start:a.offset,end:edges[i+1].offset,first_frame:a.frame,last_frame:edges[i+1].frame}));
  }
  getDecision(id,decisions){const row=this.children.get(id);return row?decisions[row.parent_id]?.split_review?.parts?.[id]||{}:decisions[id]||{};}
  setDecision(id,value,decisions){
    const row=this.children.get(id);
    if(!row){decisions[id]=value;return;}
    const parent=decisions[row.parent_id],split=parent?.split_review;
    if(!split?.enabled)throw Error('This split is no longer active. Refresh saved labels.');
    decisions[row.parent_id]={...parent,split_review:{...split,parts:{...split.parts,[id]:{...value,split_source:row.split_source}}}};
  }
  voicePrefill(id,start,end){
    const parent=this.parents.get(id),plan=this.plan.clips[id];
    return (plan.voice_prefill_spans||[]).find(span=>{
      if(start>span.start||end<span.end)return false;
      const outside=parent.source_text.slice(start,span.start)+parent.source_text.slice(span.end,end);
      return /^[\s.,!?\u2026:;\u2014\u2013-]*$/.test(outside);
    })?.voice_family_id;
  }
  async hash(data){
    if(globalThis.crypto?.subtle)return [...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',data))].map(b=>b.toString(16).padStart(2,'0')).join('');
    return bucketSha256Fallback(data);
  }
  async source(id,url){
    if(!this.sources.has(id))this.sources.set(id,(async()=>{
      const response=await fetch(url);if(!response.ok)throw Error('Could not load original audio');
      const data=await response.arrayBuffer();
      if(await this.hash(data)!==this.plan.clips[id].audio_sha256)throw Error('Original audio changed');
      const view=new DataView(data),tag=p=>String.fromCharCode(...new Uint8Array(data,p,4));
      if(tag(0)!=='RIFF'||tag(8)!=='WAVE')throw Error('Expected PCM WAV');
      let pcm=null,format=null;
      for(let p=12;p+8<=data.byteLength;){const size=view.getUint32(p+4,true),name=tag(p);
        if(p+8+size>data.byteLength)throw Error('Truncated WAV');
        if(name==='fmt ')format={encoding:view.getUint16(p+8,true),channels:view.getUint16(p+10,true),rate:view.getUint32(p+12,true),bits:view.getUint16(p+22,true)};
        if(name==='data')pcm=data.slice(p+8,p+8+size);p+=8+size+(size%2);
      }
      const plan=this.plan.clips[id];
      if(!pcm||format?.encoding!==1||format.channels!==1||format.bits!==16||format.rate!==plan.sample_rate||pcm.byteLength!==plan.frames*2)throw Error('Split audio format changed');
      return {pcm,rate:format.rate};
    })());
    try{return await this.sources.get(id);}catch(error){this.sources.delete(id);throw error;}
  }
  async cut(id,range,url){
    if(this.media.has(range.id))return this.media.get(range.id);
    const source=await this.source(id,url),pcm=source.pcm.slice(range.first_frame*2,range.last_frame*2);
    const data=new ArrayBuffer(44+pcm.byteLength),view=new DataView(data),bytes=new Uint8Array(data);
    const text=(p,s)=>[...s].forEach((c,i)=>view.setUint8(p+i,c.charCodeAt(0)));
    text(0,'RIFF');view.setUint32(4,36+pcm.byteLength,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);
    view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,source.rate,true);view.setUint32(28,source.rate*2,true);
    view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,pcm.byteLength,true);bytes.set(new Uint8Array(pcm),44);
    const media={url:URL.createObjectURL(new Blob([data],{type:'audio/wav'})),sha256:await this.hash(data)};
    globalThis.BucketHls?.registerCut(media.url,{source:url,parent_audio_sha256:this.plan.clips[id].audio_sha256,first_frame:range.first_frame,last_frame:range.last_frame,sha256:media.sha256});
    this.media.set(range.id,media);return media;
  }
  async prepare(decisions,urlForParent){
    for(const [id,parent] of this.parents){const split=this.split(id,decisions);if(!split?.enabled)continue;
      if(split.schema!==1||split.parent_audio_sha256!==parent.audio_sha256||split.candidates_sha256!==this.digest)throw Error('Saved split belongs to a different source');
      for(const range of this.ranges(id,split.cuts)){
        const media=await this.cut(id,range,urlForParent(id));
        const source={parent_id:id,parent_audio_sha256:parent.audio_sha256,text_start:range.start,text_end:range.end,
          first_frame:range.first_frame,last_frame:range.last_frame,sample_rate:this.plan.clips[id].sample_rate,audio_sha256:media.sha256};
        const saved=split.parts?.[range.id]?.split_source;
        if(saved&&JSON.stringify(saved)!==JSON.stringify(source))throw Error('Saved split boundaries changed');
        this.children.set(range.id,{...parent,id:range.id,parent_id:id,source_text:parent.source_text.slice(range.start,range.end),
          base_voice_id_proposed:this.voicePrefill(id,range.start,range.end)||parent.base_voice_id_proposed,
          text_start:parent.text_start+range.start,text_end:parent.text_start+range.end,
          audio_start:parent.audio_start+range.first_frame/source.sample_rate,audio_end:parent.audio_start+range.last_frame/source.sample_rate,
          audio_sha256:media.sha256,split_source:source,training_approved:false});
      }
    }
  }
  activeRows(decisions){return [...this.parents.values()].flatMap(row=>this.active(row.id,decisions)?this.ranges(row.id,this.split(row.id,decisions).cuts).map(r=>{
    const child=this.children.get(r.id);if(!child)throw Error('Refresh to load split audio');return child;
  }):[row]);}
}
