const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const {sourceTree}=require('./layout_source.cjs');

const compiled=file=>ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'..',file),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const load=file=>{const module={exports:{}};new Function('module','exports',compiled(file))(module,module.exports);return module.exports;};
const runs=load('src/lib/illustrationRuns.ts');
const i18n=load('src/lib/i18n.ts');
const t=i18n.translator('en');
const panelFile=path.resolve(__dirname,'../src/components/IllustrationPanel.tsx');
const panel=ts.createSourceFile(panelFile,fs.readFileSync(panelFile,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const nextTurn=()=>new Promise(resolve=>setImmediate(resolve));
const walk=(value,predicate)=>Array.isArray(value)?value.flatMap(item=>walk(item,predicate)):!value||typeof value!=='object'?[]:[...(predicate(value)?[value]:[]),...walk(value.props?.children,predicate)];
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):String(value??'');
function find(predicate){let result;const visit=node=>{if(result)return;if(predicate(node)){result=node;return;}ts.forEachChild(node,visit);};visit(panel);assert.ok(result,'Source node is present');return result;}
function evaluate(node,environment){const js=ts.transpileModule('const result = '+node.getText(panel)+';',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;return new Function(...Object.keys(environment),js+'\nreturn result;')(...Object.values(environment));}
const onClickOf=fragment=>{const button=find(node=>ts.isJsxElement(node)&&node.openingElement.tagName.getText(panel)==='Button'&&node.openingElement.getText(panel).includes(fragment));return button.openingElement.attributes.properties.find(item=>item.name?.getText(panel)==='onClick').initializer.expression;};
const visual=(id,caption='',kind='generated-codex')=>({id,source_id:'p',caption,file:id+'.png',kind,created:'2026-09-25T10:00:00Z'});
const baseRun={id:'r',book_id:'book',status:'paused',created:'2026-09-25T10:00:00Z',style:'ink',error:'',items:[{source_id:'p',status:'pending',error:'',visual_id:null}]};
function render(states={},visuals=[],language='en',props={}){
  return sourceTree('src/components/IllustrationPanel.tsx',{states:{runs:[],...states}},{component:'IllustrationPanel',language,book:{id:'book',chapters:[{id:'chapter',blocks:[{id:'p',kind:'paragraph',text:'Paragraph text'}]}],visuals},sourceId:'p',enabled:true,removeAction:async()=>{},onVisuals:()=>{},onNavigate:()=>{},onSettings:()=>{},...props}).tree;
}

test('the resume control follows the runtime resume plan instead of a client-side count',()=>{
  assert.deepEqual(runs.illustrationResume({...baseRun,resume:{remaining:2,retry_failed:true,local_only:false}}),{remaining:2,retryFailed:true,localOnly:false});
  assert.deepEqual(runs.illustrationResume({...baseRun,resume:{remaining:0,retry_failed:false,local_only:true}}),{remaining:0,retryFailed:false,localOnly:true});
  assert.equal(runs.illustrationResume({...baseRun,resume:null}),null);
  assert.equal(runs.illustrationResume(baseRun),null);
  assert.equal(runs.illustrationResume({...baseRun,status:'running',resume:{remaining:1,retry_failed:false,local_only:false}}),null);
  const missing={...baseRun,items:[{...baseRun.items[0],status:'ready',visual_id:'gone'},{source_id:'q',status:'pending',error:'',visual_id:null}],resume:{remaining:2,retry_failed:false,local_only:false}};
  const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):String(value??'');
  assert.match(text(render({runs:[missing]},[visual('gone','','imported')])),/Allow the 2 unfinished paragraphs/);
});

test('several saved runs covering one paragraph all become locally finishable',()=>{
  const local={remaining:0,retry_failed:false,local_only:true};
  const tree=render({runs:[{...baseRun,id:'a',resume:local},{...baseRun,id:'b',style:'literary',resume:local}],resumeConsent:null},[visual('manual','','imported')]);
  const buttons=walk(tree,node=>node.tag==='button'&&text(node)==='Finish saved run');
  assert.equal(buttons.length,2);assert.ok(buttons.every(button=>button.props.disabled===false));
  assert.equal(walk(tree,node=>node.tag==='button'&&text(node)==='Resume saved run').length,0);
});

test('every stored run error has a Russian rendering and English passes through',()=>{
  const read=file=>fs.readFileSync(path.resolve(__dirname,'..',file),'utf8');
  const source=read('runtime/rimsky/illustrations.py');
  const section=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
  const raised=[section('def codex_executable','class IllustrationJobs'),section('def _work','def pause')].join('\n');
  const stored=[...raised.matchAll(/(?:AppError|NotDispatched)\('([^']+)'/g),...source.matchAll(/error='([^']+)'/g),...source.matchAll(/\['error'\] = '([^']+)'/g)].map(match=>match[1]);
  const visuals=read('runtime/rimsky/visuals.py');
  stored.push(visuals.match(/AppError\('(Unknown illustration passage\.)'\)/)[1],visuals.match(/AppError\('(Choose a valid PNG[^']+)'\)/)[1]);
  assert.ok(stored.length>=18);
  for(const message of new Set(stored)){
    assert.notEqual(runs.runError(message,'ru'),message,message);
    assert.equal(runs.runError(message,'en'),message);
  }
  assert.equal(runs.runError('Codex image generation unavailable: no image tool','ru'),'Создание изображений в Codex недоступно: no image tool');
  assert.equal(runs.runError('Controlled illustration failure.','ru'),'Controlled illustration failure.');
  const tree=render({runs:[{...baseRun,error:'A saved illustration was deleted. Resume the run to regenerate it.'}]},[],'ru');
  assert.match(text(tree),/Сохранённая иллюстрация удалена\. Продолжите задачу/);
});

test('feedback renders outside the scrolling panel so it stays in view',()=>{
  const tree=render({message:'Image saved beside this paragraph.',error:'Controlled failure'});
  const body=walk(tree,node=>typeof node.props?.className==='string'&&node.props.className.includes('paragraph-image-panel'))[0];
  assert.ok(body);
  assert.equal(walk(body,node=>node.props?.role==='status'||node.props?.role==='alert').length,0);
  const top=tree.filter(node=>node&&typeof node==='object');
  const bodyIndex=top.indexOf(body),status=top.findIndex(node=>node.props?.role==='status'),alert=top.findIndex(node=>node.props?.role==='alert');
  assert.ok(bodyIndex>=0&&status>bodyIndex&&alert>bodyIndex);
  assert.equal(text(top[status]),'Image saved beside this paragraph.');
  assert.match(text(top[alert]),/Controlled failure/);
  assert.equal(walk(render({pollError:'Runtime unavailable'}),node=>node.props?.role==='alert').length,1);
});

test('stop notice shows only while the stopped run is still active',()=>{
  const active=render({runs:[{...baseRun,status:'running'}],stopping:'r'});
  assert.match(text(walk(active,node=>node.props?.role==='status')[0]),/Stop requested\. Completed images are kept\./);
  const stopped=render({runs:[{...baseRun,status:'paused'}],stopping:'r'});
  assert.equal(walk(stopped,node=>node.props?.role==='status').length,0);
});

test('Delete stays available during an active run and the backend rejection is shown in the panel language',()=>{
  const visuals=[visual('v1','Codex illustration · Chapter'),visual('v2','Codex illustration · Chapter')];
  const tree=render({runs:[{...baseRun,status:'running',items:[{...baseRun.items[0],status:'ready',visual_id:'v1'}]}]},visuals);
  const deletes=walk(tree,node=>node.tag==='button'&&/^Delete illustration/.test(node.props['aria-label']||''));
  assert.equal(deletes.length,2);
  assert.ok(deletes.every(button=>button.props.disabled===false&&button.props.title===undefined));
  assert.equal(walk(tree,node=>node.tag==='p'&&text(node)===t('stopBeforeDelete')).length,0);
  const rejection=Object.assign(new Error('raw'),{code:'illustration_run_active'});
  assert.equal(i18n.operationMessage(rejection,'ru'),i18n.translator('ru')('stopBeforeDelete'));
  assert.match(fs.readFileSync(panelFile,'utf8'),/catch\(reason\)\{setError\(operationMessage\(reason,language\)\);\}/,'A rejected deletion is reported through the panel error slot');
});

test('version images get unique localized alt text',()=>{
  const visuals=[visual('v1','Codex illustration · Глава'),visual('v2','Codex illustration · Глава'),visual('v3','','imported')];
  const alts=walk(render({},visuals,'ru'),node=>node.tag==='img').map(node=>node.props.alt);
  assert.deepEqual(alts,['Иллюстрация Codex · Глава · 1','Иллюстрация Codex · Глава · 2','Прикреплённое изображение · 3']);
  const chapter={id:'c',blocks:[{id:'p',kind:'paragraph',text:'Paragraph text'}]};
  const stage=sourceTree('src/components/IllustratedPanel.tsx',{}, {component:'IllustratedPanel',chapter,bookId:'book',sourceId:'p',images:new Map([['p',[visual('v1','Codex illustration · Глава')]]]),language:'ru',onNavigate:()=>{},onIllustrate:()=>{}}).tree;
  assert.equal(walk(stage,node=>node.tag==='img')[0].props.alt,'Иллюстрация Codex · Глава');
  assert.ok(walk(stage,node=>node.tag==='p'&&text(node)==='Иллюстрация Codex · Глава').length===1);
});

test('poll errors clear on the next successful poll and a finished run notifies the host',async()=>{
  const effect=find(node=>ts.isCallExpression(node)&&node.expression.getText(panel)==='useEffect'&&node.arguments[0]?.getText(panel).includes("'illustration_status'"));
  const responses=[()=>{throw new Error('Runtime unavailable');},()=>({id:'r',status:'running',error:'',complete:1,total:2,protected_visual_ids:['v1']}),()=>({id:'r',status:'paused',error:'',complete:1,total:2,protected_visual_ids:[]})];
  const timers=[],pollErrors=[];let state=[{...baseRun,status:'running'}],changes=0,refreshes=0;
  evaluate(effect.arguments[0],{active:state[0],api:async()=>responses.shift()(),setRuns:update=>{state=update(state);},setPollError:value=>pollErrors.push(value),
    running:runs.illustrationRunning,refresh:async()=>{refreshes++;},onRunChange:()=>{changes++;},operationMessage:i18n.operationMessage,language:'en',
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}})();
  await timers[0]();assert.deepEqual(pollErrors,['Runtime unavailable']);
  await timers[1]();assert.deepEqual(pollErrors,['Runtime unavailable','']);assert.equal(state[0].progress_complete,1);assert.equal(changes,0);
  await timers[2]();assert.equal(state[0].status,'paused');assert.equal(changes,1);assert.equal(refreshes,1);
});

test('stop is acknowledged, reported to the host and errors are readable',async()=>{
  const stops=[],errors=[];let changes=0;
  const environment=api=>({run:{id:'r'},api,setStopping:value=>stops.push(value),onRunChange:()=>{changes++;},setError:value=>errors.push(value),setMessage:()=>{},operationMessage:i18n.operationMessage,language:'en'});
  evaluate(onClickOf("'illustration_pause'"),environment(async()=>({})))();await nextTurn();
  assert.deepEqual(stops,['r']);assert.equal(changes,1);
  evaluate(onClickOf("'illustration_pause'"),environment(async()=>{throw new Error('Controlled pause failure');}))();await nextTurn();
  assert.deepEqual(stops,['r']);assert.equal(changes,1);assert.deepEqual(errors,['','','Controlled pause failure']);
});

test('start and resume notify the host only after the request succeeds',async()=>{
  const flags=[];const action=(task,flag)=>{flags.push(flag);return task();};
  evaluate(onClickOf("'illustration_start'"),{action,api:async()=>({}),book:{id:'book'},sources:['p'],style:'ink',scope:'paragraph',newVersion:false,setConsent:()=>{},setNewVersion:()=>{}})();
  evaluate(onClickOf("'illustration_resume'"),{action,api:async()=>({}),run:baseRun,resume:{localOnly:true,retryFailed:false},setResumeConsent:()=>{}})();
  assert.deepEqual(flags,[true,true]);
  const actionNode=find(node=>ts.isVariableDeclaration(node)&&node.name.getText(panel)==='action').initializer;
  let changes=0;const noop=()=>{};
  const run=evaluate(actionNode,{requestActive:{current:false},setBusy:noop,setError:noop,setPollError:noop,setMessage:noop,setStopping:noop,onRunChange:()=>{changes++;},
    api:async()=>({id:'book',revision:1,visuals:null}),onVisuals:noop,refresh:async()=>{},book:{id:'book'},operationMessage:i18n.operationMessage,language:'en'});
  await run(async()=>{},true);assert.equal(changes,1);
  await run(async()=>{throw new Error('refused');},true);assert.equal(changes,1);
  await run(async()=>{});assert.equal(changes,1);
});

test('API image requests are tracked outside the dialog and success is reported',async()=>{
  let notified=0;const unsubscribe=runs.subscribeImageRequests(()=>{notified++;});
  let release;const first=runs.trackImageRequest('book:p',()=>new Promise(resolve=>{release=resolve;}));
  assert.equal(runs.imageRequestPending('book:p'),true);assert.equal(runs.imageRequestPending('book:q'),false);
  await assert.rejects(runs.trackImageRequest('book:p',async()=>'second'),/still running/);
  release('saved');assert.equal(await first,'saved');assert.equal(runs.imageRequestPending('book:p'),false);assert.equal(notified,2);
  await assert.rejects(runs.trackImageRequest('book:p',async()=>{throw new Error('provider');}),/provider/);assert.equal(runs.imageRequestPending('book:p'),false);
  unsubscribe();
  const generate=find(node=>ts.isJsxElement(node)&&node.openingElement.tagName.getText(panel)==='Button'&&node.openingElement.getText(panel).includes("'generate_visual'")).openingElement;
  assert.match(generate.attributes.properties.find(item=>item.name?.getText(panel)==='disabled').getText(panel),/apiPending/);
  const messages=[],keys=[];
  evaluate(onClickOf("'generate_visual'"),{action:task=>task(),trackImageRequest:(key,task)=>{keys.push(key);return task();},requestKey:'book:p',api:async()=>({}),book:{id:'book'},sourceId:'p',apiKey:'key',
    setMessage:value=>messages.push(value),setApiKey:()=>{},setApiConsent:()=>{},ru:false})();
  await nextTurn();assert.deepEqual(keys,['book:p']);assert.deepEqual(messages,['API image saved beside this paragraph.']);
});

test('a missing Codex CLI is explained before consent and blocks only actions that start Codex',()=>{
  const hint='Codex CLI was not found. Install it and sign in, then reopen this panel.';
  const button=(tree,label)=>walk(tree,node=>node.tag==='button'&&text(node)===label)[0];
  const ready={consent:true,apiKey:'sk-test',apiConsent:true};
  const missing=render(ready,[],'en',{codex:false});
  assert.equal(walk(missing,node=>node.tag==='p'&&text(node)===hint).length,1);
  assert.equal(button(missing,'Prepare paragraph illustrations').props.disabled,true);
  assert.equal(button(missing,'Generate one API image').props.disabled,false);
  for(const codex of [true,undefined]){
    const tree=render(ready,[],'en',{codex});
    assert.equal(walk(tree,node=>text(node)===hint).length,0);
    assert.equal(button(tree,'Prepare paragraph illustrations').props.disabled,false);
  }
  assert.equal(walk(render({},[],'ru',{codex:false}),node=>node.tag==='p'&&text(node)==='Codex CLI не найден. Установите его, войдите в учётную запись и снова откройте эту панель.').length,1);
  const saved=[{...baseRun,id:'a',resume:{remaining:1,retry_failed:false,local_only:false}},{...baseRun,id:'b',resume:{remaining:0,retry_failed:false,local_only:true}}];
  const blocked=render({runs:saved,resumeConsent:'a'},[],'en',{codex:false});
  assert.equal(button(blocked,'Resume saved run').props.disabled,true);
  assert.equal(button(blocked,'Finish saved run').props.disabled,false);
  assert.equal(button(render({runs:saved,resumeConsent:'a'},[],'en',{codex:true}),'Resume saved run').props.disabled,false);
});
