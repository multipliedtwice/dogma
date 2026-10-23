"""Progressive audiobook player with audio-bound listener feedback."""
PAGE_HTML='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Элирм IV — прослушивание</title><style>
body{font:19px system-ui;max-width:800px;margin:35px auto;padding:20px;background:#f7f8f4;color:#172a25}select,audio{width:100%;margin:18px 0}select{font:inherit;min-height:58px;padding:12px;border:1px solid #789;border-radius:12px}small{color:#59645f}a{color:#245d46}#status{min-height:2em}
#reader{margin:28px 0;padding-top:8px;border-top:1px solid #cad2cd}#reader-text{font-family:Georgia,serif;font-size:1.08em;line-height:1.75}.source{margin:0 0 1em}.unit{border-radius:.25em;transition:background .15s}.unit.active{background:#fff0a8;box-shadow:0 0 0 .15em #fff0a8}.word{border-radius:.2em;cursor:context-menu}.word:hover,.word:focus{outline:2px solid #5b806f;outline-offset:1px}.word.reported{background:#ccebd8}.hint{font-size:.88em;color:#59645f}
dialog{width:min(92vw,520px);border:0;border-radius:16px;padding:22px;box-shadow:0 18px 60px #0006}dialog::backdrop{background:#172a2588}#stress-options{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}button,textarea{font:inherit}button{padding:8px 13px;border:1px solid #789;border-radius:9px;background:#fff;cursor:pointer}button:hover{background:#edf4ef}textarea{box-sizing:border-box;width:100%;min-height:90px;padding:9px}.dialog-actions{display:flex;justify-content:space-between;gap:10px;margin-top:10px}#report-status{min-height:1.5em;color:#245d46}
</style></head><body>
<h1>Элирм IV</h1><p>Главы 1–3 · голоса и настроения из записей Влада. Без эффектов Higgs.</p>
<p id="status" role="status">Загружается состояние генерации…</p><label for="chapter">Глава</label>
<select id="chapter" aria-label="Выбрать главу"></select><audio id="player" controls preload="metadata"></audio>
<p><a id="download">Скачать доступную часть MP3</a></p><p id="checks"></p>
<p><label><input type="checkbox" id="music-enabled" checked> Музыка</label>
<label>Громкость музыки <input id="music-volume" type="range" min="0" max="1" step="0.05" value="1"></label></p>
<small id="music-credit"></small>
<small>Прослушивание MVP. Аудио добавляется частями; голосовые назначения ещё оцениваются на слух. Это не утверждённый релиз.</small>
<section id="reader"><h2>Текст главы</h2><p class="hint">Текст следует за аудио. Выделите слово правой кнопкой (на телефоне — долгим нажатием), чтобы отметить неверное ударение или другую проблему.</p><p id="reader-status" role="status">Текст загружается…</p><div id="reader-text"></div></section>
<dialog id="report-dialog"><form method="dialog"><button value="cancel" aria-label="Закрыть" style="float:right">×</button></form><h3 id="report-title">Отметить слово</h3><p>Правильное ударение:</p><div id="stress-options"></div><label for="report-comment">Другая проблема</label><textarea id="report-comment" maxlength="2000" placeholder="Что прозвучало или отображается неправильно?"></textarea><p id="report-status" role="status"></p><div class="dialog-actions"><button id="report-other" type="button">Отправить комментарий</button><form method="dialog"><button value="cancel">Отмена</button></form></div></dialog>
<script>
const s=document.querySelector('#chapter'),a=document.querySelector('#player'),d=document.querySelector('#download'),status=document.querySelector('#status'),checks=document.querySelector('#checks');
const readerText=document.querySelector('#reader-text'),readerStatus=document.querySelector('#reader-status'),reportDialog=document.querySelector('#report-dialog'),stressOptions=document.querySelector('#stress-options'),reportStatus=document.querySelector('#report-status'),reportComment=document.querySelector('#report-comment');
let state=null,active='c0001',version=null,generation=null,waiting=false,wantedPlay=false,changing=false,epoch=0,timeline=null,timelineKey=null,timelineEpoch=0,activeUnit=null,reportTarget=null;
let stored=null;try{stored=JSON.parse(localStorage.getItem('elirm-palette-position')||'null')}catch{}
if(stored&&['c0001','c0002','c0003'].includes(stored.chapter))active=stored.chapter;
function save(){if(state)localStorage.setItem('elirm-palette-position',JSON.stringify({generation:state.generation,chapter:active,time:a.currentTime||0}));}
function heldCount(id){return Object.keys(state?.held_sections||{}).filter(key=>key.startsWith(id+'/')).length;}
function codePointOffset(text,utf16Offset){return Array.from(text.slice(0,utf16Offset)).length;}
function stressVariants(word){
 const base=word.replaceAll('\u0301',''),letters=Array.from(base),vowels=new Set(Array.from('аеёиоуыэюяАЕЁИОУЫЭЮЯ')),result=[];
 letters.forEach((letter,index)=>{if(vowels.has(letter))result.push(letters.slice(0,index+1).join('')+'\u0301'+letters.slice(index+1).join(''));});return result;
}
function openReport(target){
 reportTarget=target;reportStatus.textContent='';reportComment.value='';stressOptions.replaceChildren();
 document.querySelector('#report-title').textContent=`Отметить «${target.word}»`;
 for(const variant of stressVariants(target.word)){const button=document.createElement('button');button.type='button';button.textContent=variant;button.onclick=()=>sendReport('stress',variant);stressOptions.append(button);}
 if(!stressOptions.children.length)stressOptions.textContent='Для этого слова варианты ударения недоступны.';
 reportDialog.showModal();
}
function renderTimeline(){
 readerText.replaceChildren();activeUnit=null;
 if(!timeline?.units?.length){readerStatus.textContent='Для этой главы текст пока недоступен.';return;}
 let paragraph=null,lastSource=null;
 const wordPattern=/[\\p{Script=Cyrillic}\u0401\u0451\u0301]+(?:-[\\p{Script=Cyrillic}\u0401\u0451\u0301]+)*/gu;
 for(const row of timeline.units){
  if(row.source_id!==lastSource){paragraph=document.createElement('p');paragraph.className='source';readerText.append(paragraph);lastSource=row.source_id;}
  const unit=document.createElement('span');unit.className='unit';unit.id='unit-'+row.id;unit.dataset.unitId=row.id;
  let position=0;
  for(const match of row.text.matchAll(wordPattern)){
   unit.append(document.createTextNode(row.text.slice(position,match.index)));
   const word=document.createElement('span');word.className='word';word.tabIndex=0;word.textContent=match[0];
   const target={element:word,unit:row,word:match[0],start:codePointOffset(row.text,match.index),end:codePointOffset(row.text,match.index+match[0].length)};
   word.oncontextmenu=event=>{event.preventDefault();openReport(target);};word.ondblclick=()=>openReport(target);
   word.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openReport(target);}};
   unit.append(word);position=match.index+match[0].length;
  }
  unit.append(document.createTextNode(row.text.slice(position)));paragraph.append(unit);
 }
 readerStatus.textContent='Выберите слово, если услышите ошибку.';updateActiveUnit(true);
}
async function loadTimeline(){
 const row=state?.chapters?.[active];
 if(!row){timeline=null;timelineKey=null;readerText.replaceChildren();readerStatus.textContent='Текст появится вместе с опубликованным аудио.';return;}
 const key=`${state.generation}:${active}:${row.wav_sha256}:${row.timeline_asset?.sha256||''}`;
 if(key===timelineKey)return;
 const token=++timelineEpoch;readerStatus.textContent='Текст загружается…';
 try{
  const response=await fetch(`timeline?chapter=${encodeURIComponent(active)}`,{cache:'no-store'});if(!response.ok)throw Error('timeline unavailable');
  const next=await response.json();if(token!==timelineEpoch)return;
  if(next.generation!==state.generation||next.chapter!==active||next.audio_sha256!==row.wav_sha256)throw Error('timeline changed');
  timeline=next;timelineKey=key;renderTimeline();
 }catch{if(token===timelineEpoch){timeline=null;timelineKey=null;readerText.replaceChildren();readerStatus.textContent='Не удалось загрузить текст этой версии.';}}
}
function updateActiveUnit(force=false){
 if(!timeline?.units?.length)return;const time=a.currentTime||0;
 let low=0,high=timeline.units.length-1,found=null;
 while(low<=high){const mid=(low+high)>>1,row=timeline.units[mid];if(time<row.start)high=mid-1;else if(time>row.end)low=mid+1;else{found=row;break;}}
 if(!found)found=timeline.units[Math.min(low,timeline.units.length-1)];
 if(!force&&found?.id===activeUnit)return;
 if(activeUnit)document.querySelector('#unit-'+CSS.escape(activeUnit))?.classList.remove('active');activeUnit=found?.id||null;
 const element=activeUnit&&document.querySelector('#unit-'+CSS.escape(activeUnit));if(element){element.classList.add('active');if(!reportDialog.open)element.scrollIntoView({block:'center',behavior:force?'auto':'smooth'});}
}
async function sendReport(kind,intendedStress=null){
 if(!reportTarget||!timeline)return;const comment=reportComment.value;
 if(kind==='other'&&!comment.trim()){reportStatus.textContent='Опишите проблему.';return;}
 const payload={kind,generation:timeline.generation,chapter:timeline.chapter,audio_sha256:timeline.audio_sha256,timeline_sha256:timeline.timeline_sha256,
  unit_id:reportTarget.unit.id,source_id:reportTarget.unit.source_id,unit_text_sha256:reportTarget.unit.text_sha256,
  selected_text:reportTarget.word,word_start:reportTarget.start,word_end:reportTarget.end,intended_stress:intendedStress,comment,audio_offset:a.currentTime||0};
 reportStatus.textContent='Сохраняю…';for(const button of reportDialog.querySelectorAll('button'))button.disabled=true;
 try{const response=await fetch('feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok||!result.saved)throw Error(result.error||'save failed');reportTarget.element.classList.add('reported');reportStatus.textContent='Сохранено для проверки.';setTimeout(()=>reportDialog.close(),500);}
 catch(error){reportStatus.textContent='Не удалось сохранить: '+error.message;}
 finally{for(const button of reportDialog.querySelectorAll('button'))button.disabled=false;}
}
document.querySelector('#report-other').onclick=()=>sendReport('other');
function display(){
 const current=state.chapters[active];
 status.textContent=state.status==='failed'?'Генерация остановилась. Готовую часть можно слушать.':current?.complete?'Глава готова. Следующие главы продолжают генерироваться.':waiting?'Дошли до готовой части. Ждём продолжение…':'Аудио добавляется по мере готовности.';
 if(state.status==='complete'&&['c0001','c0002','c0003'].every(id=>state.chapters[id]?.complete&&state.chapters[id].word_flags===0))status.textContent='Все три главы готовы.';
 if(state.status==='held')status.textContent='Готовые части доступны. Некоторые фрагменты удержаны после автоматической проверки.';
 if(current?.word_flags)status.textContent='Доступна оценочная версия главы. Проверки и исправления ещё не завершены.';
 if(!current&&heldCount(active))status.textContent=`Глава ${Number(active.slice(1))} пока не опубликована: ${heldCount(active)} частей ожидают исправления после проверки текста или аудио. Поздние части могут генерироваться независимо.`;
 if(state.background_job?.kind==='scene_music'&&state.background_job.status==='rendering')status.textContent=`Подготовка музыки: ${state.background_job.verified_takes}/${state.background_job.planned_takes}. Готовые части можно слушать; озвучивание продолжится автоматически.`;
 if(state.pending_generation&&state.pending_generation!==state.generation)status.textContent='Готовится новое озвучивание глав 1–3. Пока доступна предыдущая версия; новая появится после проверки первой части.';
 checks.textContent=current?`Доступно ${(current.seconds/60).toFixed(1)} мин · ${current.word_flags} расхождений распознавания · ${current.assignment_flags} назначений для дальнейшей оценки.`:'Эта глава ещё не готова.';
 if(current){d.href=current.audio;d.download=current.audio;d.hidden=false;}else d.hidden=true;
}
function swap(row,pos,resume){
 const token=++epoch;changing=true;version=row.wav_sha256;a.src=row.stream;a.load();
 a.addEventListener('loadedmetadata',()=>{if(token!==epoch)return;a.currentTime=Math.min(pos,Number.isFinite(a.duration)?a.duration:pos);changing=false;save();if(resume)a.play().catch(()=>{wantedPlay=false;waiting=false;display();});},{once:true});
}
function update(next){
 const oldGeneration=generation,oldPosition=a.currentTime||0;state=next;generation=next.generation;
 const oldRow=next.chapters[active];
 if(oldGeneration&&oldGeneration!==generation){version=null;stored=null;waiting=false;}
 const focused=document.activeElement===s;
 if(!focused){const selected=active;s.replaceChildren();for(const id of ['c0001','c0002','c0003']){const row=next.chapters[id],option=document.createElement('option');option.value=id;option.textContent=row?`Глава ${Number(id.slice(1))} · ${(row.seconds/60).toFixed(1)} мин${row.complete?'':' · продолжение готовится'}`:`Глава ${Number(id.slice(1))} · ${heldCount(id)?'исправляются проверки':'готовится'}`;s.append(option);}s.value=selected;}
 if(oldRow&&oldRow.wav_sha256!==version){
  const pos=version?a.currentTime:(stored?.generation===generation&&stored.chapter===active?stored.time:0);
  swap(oldRow,pos,wantedPlay||waiting);stored=null;
 }
 if(waiting&&oldRow?.complete&&oldPosition>=oldRow.seconds-.25){const index=['c0001','c0002','c0003'].indexOf(active),nextId=['c0001','c0002','c0003'][index+1];if(nextId&&next.chapters[nextId]){active=nextId;s.value=active;swap(next.chapters[active],0,wantedPlay);waiting=false;}}
 display();loadTimeline();
}
s.onchange=()=>{wantedPlay=false;waiting=false;a.pause();active=s.value;version=null;stored=null;epoch++;timeline=null;timelineKey=null;timelineEpoch++;if(state.chapters[active])swap(state.chapters[active],0,false);else {changing=true;a.removeAttribute('src');a.load();changing=false;}save();display();loadTimeline();};
a.addEventListener('play',()=>{wantedPlay=true;waiting=false;display();});
a.addEventListener('pause',()=>{if(!changing&&!a.ended){wantedPlay=false;waiting=false;display();}});
a.addEventListener('timeupdate',()=>{save();updateActiveUnit();});a.addEventListener('seeking',()=>updateActiveUnit(true));
a.addEventListener('ended',()=>{waiting=wantedPlay;display();refresh();});
async function refresh(){try{const response=await fetch('progress.json',{cache:'no-store'});if(!response.ok)throw Error('state unavailable');update(await response.json());}catch{if(!state)status.textContent='Генерация запускается. Эта страница обновится автоматически.';}}
refresh();setInterval(refresh,15000);
</script>
<script>
const musicToggle=document.querySelector('#music-enabled'),musicVolume=document.querySelector('#music-volume');
let musicSaved=null;try{musicSaved=JSON.parse(localStorage.getItem('elirm-music')||'null')}catch{}
if(musicSaved){musicToggle.checked=musicSaved.enabled;musicVolume.value=musicSaved.volume;}
let musicPlayers=new Map(),musicBusy=false;
function musicSettings(){localStorage.setItem('elirm-music',JSON.stringify({enabled:musicToggle.checked,volume:Number(musicVolume.value)}));syncMusic();}
function syncMusic(){
 if(musicBusy)return;musicBusy=true;
 const row=state?.chapters[active],cues=row?.music||[],live=new Set();
 if(row)d.href=musicToggle.checked&&row.audio_with_music?row.audio_with_music:row.audio;
 document.querySelector('#music-credit').textContent=(row?.music_pending?row.music_pending+' ':state?.music?.status==='more_variants_pending'?'Дополнительные варианты музыки готовятся. ':'')+(state?.music?.attribution||'Музыка готовится.');
 for(const cue of cues){
  const key=active+':'+cue.kind+':'+cue.sha256;live.add(key);
  let player=musicPlayers.get(key);if(!player){player=new Audio(cue.stream);player.preload='auto';musicPlayers.set(key,player);}
  const ending=cue.kind==='ending'&&a.ended&&wantedPlay,pos=ending?Math.max(0,a.currentTime-cue.start):a.currentTime-cue.start;
  player.volume=(typeof a.volume==='number'?a.volume:1)*Number(musicVolume.value);
  if(!musicToggle.checked||pos<0||pos>=cue.seconds||(!wantedPlay&&!ending)||(!ending&&a.paused)||(ending&&player.ended))player.pause();
  else {if(!ending&&Math.abs(player.currentTime-pos)>.35)player.currentTime=pos;if(player.paused)player.play().catch(()=>{});}
 }
 for(const [key,player] of musicPlayers)if(!live.has(key)){player.pause();musicPlayers.delete(key);}
 musicBusy=false;
}
musicToggle.onchange=musicSettings;musicVolume.oninput=musicSettings;
for(const event of ['play','pause','seeking','timeupdate','ended','loadedmetadata','volumechange'])a.addEventListener(event,syncMusic);
s.addEventListener('change',syncMusic);setInterval(syncMusic,1000);
</script></body></html>'''
