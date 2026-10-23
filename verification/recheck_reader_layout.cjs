const fs=require('node:fs');
const path=require('node:path');
const {sourceTree,html}=require('./layout_source.cjs');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const css=fs.readFileSync(process.argv[3],'utf8')+'\n'+fs.readFileSync(path.resolve(__dirname,'../src/player.css'),'utf8');
const output=path.resolve(process.argv[4]);
fs.mkdirSync(output,{recursive:true});
const source=fixture.states.selectedSource;
const original=fixture.states.book.chapters.find(chapter=>chapter.blocks.some(block=>block.id===source));
const next=fixture.states.book.chapters.find(chapter=>chapter.id!==original.id&&chapter.blocks.some(block=>block.kind==='paragraph'));
const record={book_id:fixture.states.book.id,created:'2026-09-23T10:00:00Z',style:'ink',error:'',items:[{source_id:source,status:'pending',error:'',visual_id:null}]};
fixture.states.modal='illustration';
fixture.states.chapterId=next.id;
fixture.states.settings={...fixture.states.settings,online_images:true};
fixture.states['src/components/IllustrationPanel.tsx:runs']=[
 {...record,id:'controlled-interrupted',status:'paused',error:'Controlled interrupted request. No online generation was run for this layout.',items:[{...record.items[0],status:'uncertain'}]},
 {...record,id:'controlled-finalize',status:'failed',error:'Controlled final-state write failure',items:[{...record.items[0],status:'ready',visual_id:'fixture-image-0'}]}
];
const tree=sourceTree('src/App.tsx',fixture).tree;
fs.writeFileSync(path.join(output,'illustration-recovery.html'),`<!doctype html><html data-theme="light" lang="en"><head><meta charset="utf-8"><style>${css}</style></head><body>${html(tree)}</body></html>`);
console.log(JSON.stringify({scope:'Actual JSX/CSS with controlled book/illustration state and simulated React/Radix. No generated artwork, full frontend or native acceptance.',frozen_source:source,visible_chapter:next.id}));
