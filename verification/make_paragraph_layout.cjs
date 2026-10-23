const fs=require('node:fs');
const path=require('node:path');
const {sourceTree,html}=require('./layout_source.cjs');
const prepared=path.resolve(process.argv[2]);const output=path.resolve(process.argv[3]);const css=fs.readFileSync(process.argv[4],'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../src/player.css'),'utf8');
fs.mkdirSync(output,{recursive:true});
const book=JSON.parse(fs.readFileSync(path.join(prepared,'book.json'),'utf8'));
const inputs=fs.readFileSync(path.join(prepared,'speech-inputs.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
const chapters=book.chapters;const chapter=chapters.find(c=>c.blocks.filter(b=>b.kind==='paragraph').length>=3);
const paragraphs=chapter.blocks.filter(b=>b.kind==='paragraph');
const generation={id:'fixture-generation',book_id:book.id,name:'Saved narration · layout fixture',status:'ready',created:'2026-09-23',updated:'2026-09-23',error:'',parent_id:null,config:{chapters:chapters.map(c=>c.id),pack_name:'Controlled layout fixture',pack_fingerprint:'fixture',align_words:false},analysis:{complete:true},cast:[],exports:[],segments:inputs.map((input,index)=>({...input,id:'s'+index,ordinal:index,status:'ready',duration:10,audio_hash:'fixture'+index,spoken:input.text,speaker:'narrator',delivery:'neutral',cues:[],warning:''}))};
const segment=generation.segments.find(item=>item.source_id===paragraphs[0].id);
const assetPath=path.join(prepared,'books',book.id,'assets',book.cover);const cover='data:image/png;base64,'+fs.readFileSync(assetPath).toString('base64');
const assets={[book.cover]:cover};
book.visuals=paragraphs.slice(0,2).map((block,index)=>{assets['fixture-image-'+index]=cover;return{id:'fixture-image-'+index,source_id:block.id,file:'fixture.png',kind:'layout-fixture',caption:'Layout fixture · paragraph '+(index+1)+' · not generated artwork',created:String(index)};});
book.visual_revision=3;book.generations=[{...generation,progress:{total:generation.segments.length,ready:generation.segments.length,duration:generation.segments.length*10}}];book.bookmarks=[];book.position=null;
const settings={theme:'light',language:'en',font_size:20,line_height:1.9,playback_rate:1,volume:.85,follow:true,online_images:false,align_words:false,device:'auto',pack_path:'',image_model:'gpt-image-1'};
const summary={...book,added:'2026-09-23',chapter_count:book.chapters.length};delete summary.chapters;
const baseline={connected:true,book,generation,chapterId:chapter.id,selectedSource:paragraphs[0].id,browsing:false,mode:'read',sidebar:true,modal:null,library:[summary],settings,health:{ready:false,missing:[],pack_error:'Runtime not exercised in this layout fixture',pack:null,ffmpeg:true,word_alignment:false,active:null,illustration_active:null,storage_bytes:0,offline:true,version:'0.1.14'}};
for(const view of ['reader','dark','library','playback-settings','export','generations','illustrated','illustration']){
 const states={...baseline,settings:{...settings,theme:view==='dark'?'dark':'light'}};
 if(view==='library'){states.book=null;states.generation=null;}
 if(view==='illustrated')states.mode='illustrated';
 if(['playback-settings','export','generations','illustration'].includes(view))states.modal=view==='playback-settings'?'playback':view;
 const fixture={states,assets,player:{segment,position:2,playing:false,waiting:false,error:'',sleepAt:0}};
 if(view==='reader')fs.writeFileSync(path.join(output,'private-fixture.json'),JSON.stringify(fixture));
 const tree=sourceTree('src/App.tsx',fixture).tree;
 fs.writeFileSync(path.join(output,view+'.html'),`<!doctype html><html data-theme="${states.settings.theme}" lang="en"><head><meta charset="utf-8"><style>${css}</style></head><body>${html(tree)}</body></html>`);
}
console.log(JSON.stringify({scope:'Actual JSX/styles with simulated React/Radix/media boundaries; book text and cover are private user assets. Attached image and audio state are explicit layout fixtures, not generated narration or art.',sections:book.chapters.length,paragraph:paragraphs[0].id}));
