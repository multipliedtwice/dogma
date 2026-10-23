import {useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import {AlertCircle, LoaderCircle, Pause, Sparkles, Trash2, Upload} from 'lucide-react';
import type {Book, DeletionResult, Visual} from '../lib/types';
import {api, uploadVisual} from '../lib/api';
import {illustrationSources} from '../lib/readerIndexes';
import {illustrationElsewhere, illustrationRunning as running, illustrationResume, imageRequestPending, runError, subscribeImageRequests, trackImageRequest, type IllustrationRun as Run} from '../lib/illustrationRuns';
import {copyWhenReady} from '../lib/clipboard';
import {visualCaption} from '../lib/visualCaption';
import {AssetImage} from './Media';
import {Button} from './ui/button';
import {DialogHeader} from './ui/dialog';
import {jobElsewhere,locale,operationMessage,translator} from '../lib/i18n';
import type {RemoveAction} from './AskDialog';
const runStatuses:Record<string,[string,string]>={queued:['Queued','В очереди'],running:['Running','Выполняется'],ready:['Ready','Готово'],paused:['Stopped','Остановлено'],failed:['Failed','Ошибка']};
const styleNames:Record<string,[string,string]>={literary:['Literary','Литературный'],ink:['Ink','Тушь'],painted:['Painted','Живопись']};
const visualKinds:Record<string,[string,string]>={imported:['Attached image','Прикреплённое изображение'],'generated-codex':['Codex image','Изображение Codex'],'generated-online':['Images API image','Изображение Images API']};
const label=(names:Record<string,[string,string]>,value:string,ru:boolean)=>names[value]?.[ru?1:0]??value;


export function IllustrationPanel({book, sourceId, enabled, codex, language='en', activeRun=null, activeTitle='', removeAction, onVisuals, onNavigate, onSettings, onRunChange}:
  {book: Book; sourceId: string; enabled: boolean; codex?: boolean; removeAction: RemoveAction; onVisuals: (bookId: string, visuals: Visual[], revision: number) => void;
   language?: 'en'|'ru'; activeRun?: string|null; activeTitle?: string; onNavigate: (sourceId: string) => void; onSettings: () => void; onRunChange?: () => void}) {
  const ru=language==='ru';
  const [scope,setScope]=useState<'paragraph'|'chapter'|'book'>('paragraph');
  const [style,setStyle]=useState('literary');
  const [newVersion,setNewVersion]=useState(false);
  const [consent,setConsent]=useState(false);
  const [resumeConsent,setResumeConsent]=useState<string|null>(null);
  const refreshRevision=useRef(0);
  const mounted=useRef(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [pollError,setPollError]=useState('');
  const [message,setMessage]=useState('');
  const [stopping,setStopping]=useState<string|null>(null);
  const requestActive=useRef(false);
  const [apiKey,setApiKey]=useState('');
  const [apiConsent,setApiConsent]=useState(false);
  const [runs,setRuns]=useState<Run[]>([]);
  const chapter=book.chapters.find(item=>item.blocks.some(block=>block.id===sourceId));
  const block=chapter?.blocks.find(item=>item.id===sourceId);
  const sources=useMemo(()=>illustrationSources(scope==='book'?book.chapters.filter(chapter=>chapter.default_narration!==false):chapter?[chapter]:[],sourceId,scope),[book.chapters,chapter,sourceId,scope]);
  const refresh=useCallback(async()=>{
    const revision=++refreshRevision.current;
    try {
      const history=await api<Run[]>('illustration_history',{book_id:book.id});
      if(mounted.current&&revision===refreshRevision.current)setRuns(history);
    } catch(reason) {
      if(mounted.current&&revision===refreshRevision.current)throw reason;
    }
  },[book.id]);
  useEffect(()=>{
    mounted.current=true;
    void refresh().catch(reason=>{if(mounted.current)setPollError(operationMessage(reason,language));});
    return()=>{mounted.current=false;refreshRevision.current++;};
  },[refresh]);
  useEffect(()=>{if(!enabled){setConsent(false);setResumeConsent(null);setApiConsent(false);setApiKey('');}},[enabled]);
  const active=runs.find(running);
  const elsewhere=illustrationElsewhere(activeRun,runs);
  const t=translator(language);
  const showParagraph=useRef<HTMLButtonElement>(null);
  const paragraphVisuals=book.visuals.filter(visual=>visual.source_id===sourceId);
  const requestKey=book.id+':'+sourceId;
  const apiPending=useSyncExternalStore(subscribeImageRequests,()=>imageRequestPending(requestKey));
  const notices=[message,stopping&&runs.some(run=>run.id===stopping&&running(run))?(ru?'Остановка запрошена. Готовые изображения сохранены.':'Stop requested. Completed images are kept.'):'',apiPending?(ru?'Запрос изображения через API для этого абзаца ещё выполняется.':'An API image request for this paragraph is still running.'):''].filter(Boolean);
  const failure=error||pollError;
  const noCodex=codex===false;
  useEffect(()=>{
    if(!active)return;
    let live=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const status=await api<{id:string;status:string;error:string;complete:number;total:number;resume?:Run['resume']}>('illustration_status',{id:active.id});if(live){setPollError('');setRuns(current=>current.map(run=>run.id===status.id?{...run,status:status.status,error:status.error,progress_complete:status.complete,progress_total:status.total,resume:status.resume}:run));if(!running({...active,status:status.status})){onRunChange?.();await refresh();}}}catch(reason){if(live)setPollError(operationMessage(reason,language));}finally{if(live)timer=setTimeout(poll,3000);}};
    timer=setTimeout(poll,1000);return()=>{live=false;clearTimeout(timer);};
  },[active?.id,refresh]);
  const action=async(task:()=>Promise<unknown>,runChange=false)=>{if(requestActive.current)return;requestActive.current=true;setBusy(true);setError('');setPollError('');setMessage('');setStopping(null);try{await task();if(runChange)onRunChange?.();const visuals=await api<{id:string;revision:number;visuals:Visual[]|null}>('visuals_poll',{book_id:book.id});if(visuals.visuals)onVisuals(visuals.id,visuals.visuals,visuals.revision);await refresh();}catch(reason){setError(operationMessage(reason,language));}finally{requestActive.current=false;setBusy(false);}};
  return <><DialogHeader title={ru?'Иллюстрации к абзацам':'Paragraph illustrations'} description={ru?'Одно изображение для каждого выбранного абзаца. Исходная книга и озвучка не меняются.':'One image per selected paragraph, saved beside its text. Original books and narration stay unchanged.'}/>
    <div className="modal-body paragraph-image-panel">
      <div className="original-quote">{block?.text || (ru?'Выберите текстовый абзац.':'Select a text paragraph.')}</div>
      <Button ref={showParagraph} variant="ghost" onClick={()=>onNavigate(sourceId)}>{ru?'Показать абзац в тексте':'Show this paragraph in text'}</Button>
      <label className="image-upload"><Upload size={16}/>{ru?'Добавить изображение к абзацу':'Attach an image to this paragraph'}<input type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" disabled={busy||block?.kind!=='paragraph'} onChange={event=>{
        const file=event.currentTarget.files?.[0];event.currentTarget.value='';
        if(file)void action(async()=>{await uploadVisual(file,book.id,sourceId);setMessage(ru?'Изображение сохранено рядом с абзацем.':'Image saved beside this paragraph.');});
      }}/></label>
      {paragraphVisuals.map((visual,index)=><div className="visual-item" key={visual.id}><AssetImage kind="visual" id={visual.id} preview language={language} alt={`${visual.caption?visualCaption(visual.caption,language):label(visualKinds,visual.kind,ru)} · ${index+1}`}/>
        <span>{label(visualKinds,visual.kind,ru)} · {new Date(visual.created).toLocaleString(locale(language))}</span><Button variant="ghost" size="icon" disabled={busy} aria-label={`${ru?'Удалить иллюстрацию':'Delete illustration'} ${index+1}`} onClick={()=>void removeAction({title:t('deleteIllustrationTitle'),message:t('deleteIllustrationConfirm'),confirm:t('delete'),destructive:true},'.visual-item',()=>[showParagraph.current],()=>action(async()=>{const result=await api<DeletionResult>('delete_visual',{id:visual.id});setMessage(result.cleanup_warnings.length?(ru?'Иллюстрация удалена; часть файлов требует очистки.':'Illustration removed; some files still need cleanup.'):(ru?'Иллюстрация удалена.':'Illustration removed.'));}))}><Trash2/></Button></div>)}
      <Button variant="ghost" onClick={()=>{if(requestActive.current)return;const copied=copyWhenReady(api('visual_brief',{book_id:book.id,source_id:sourceId}).then(brief=>JSON.stringify(brief,null,2)));void action(async()=>{await copied;setMessage(ru?'Описание абзаца скопировано.':'Paragraph brief copied.');});}}>{ru?'Копировать описание для Codex':'Copy paragraph brief for Codex'}</Button>
      <hr/><h3>{ru?'Создать с помощью Codex':'Generate with Codex'}</h3>
      <p className="helper">{ru?'Используется установленный Codex CLI с выполненным входом и встроенным инструментом изображений. Выбранные абзацы покидают компьютер; действуют лимиты или тарифы Codex. Автоматического перехода на Images API нет. Доступность изображений в CLI нужно проверить в вашей установке.':'Uses your installed, signed-in Codex CLI and its built-in image tool. Selected paragraphs leave this computer; Codex usage limits or charges apply. No Images API fallback. CLI image availability still needs verification on your installation.'}</p>
      {noCodex&&<p className="validation-error">{t('codexMissing')}</p>}
      <div className="two-fields"><label>{ru?'Абзацы':'Paragraphs'}<select value={scope} onChange={event=>{setScope(event.target.value as typeof scope);setNewVersion(false);setConsent(false);}}><option value="paragraph">{ru?'Этот абзац':'This paragraph'}</option><option value="chapter">{ru?'Все абзацы этой главы':'Every paragraph in this chapter'}</option><option value="book">{ru?'Все абзацы основной книги':'Every paragraph in the primary book'}</option></select></label>
        <label>{ru?'Стиль':'Style'}<select value={style} onChange={event=>{setStyle(event.target.value);setConsent(false);}}><option value="literary">{ru?'Литературный':'Literary'}</option><option value="ink">{ru?'Тушь':'Ink'}</option><option value="painted">{ru?'Живопись':'Painted'}</option></select></label></div>
      {scope==='paragraph'&&paragraphVisuals.length>0&&<label className="consent"><input type="checkbox" checked={newVersion} onChange={event=>{setNewVersion(event.target.checked);setConsent(false);}}/><span>{ru?'Создать новую версию для абзаца. Существующие изображения сохранятся; это новый запрос.':'Create a new version for this paragraph. Keep existing images; this makes a new request.'}</span></label>}
      <p className="helper">{ru?`Выбрано абзацев: ${sources.length}. Сначала проверяются сохранённые файлы; исправные иллюстрации используются без нового запроса, если явно не выбрана новая версия.`:`${sources.length} paragraph${sources.length===1?'':'s'} selected. Saved image files are checked first; healthy illustrations are kept without a request unless a new version was explicitly selected. No scene grouping or silent text truncation.`}</p>
      {sources.length>5000&&<p className="validation-error">{ru?'Выбирайте по одной главе: не более 5 000 абзацев на запрос.':'Select one chapter at a time: at most 5,000 paragraphs per request.'}</p>}
      {!enabled?<Button variant="outline" onClick={onSettings}>{ru?'Включить сетевые иллюстрации в настройках':'Enable optional online illustrations in Settings'}</Button>:<label className="consent"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/><span>{ru?'Разрешить отправку выбранных абзацев в Codex. Повторные запросы могут расходовать дополнительный лимит.':'Allow the selected paragraphs to be sent to Codex. Explicit retries can use additional allowance.'}</span></label>}
      {elsewhere&&<p className="helper">{jobElsewhere(language,'illustrations',activeTitle)}</p>}
      <Button disabled={!enabled||!consent||busy||!!active||elsewhere||noCodex||!sources.length||sources.length>5000} onClick={()=>void action(async()=>{await api('illustration_start',{book_id:book.id,source_ids:sources,style,consent:true,replace_existing:scope==='paragraph'&&newVersion});setConsent(false);setNewVersion(false);},true)}>{busy?<LoaderCircle className="spin"/>:<Sparkles/>}{ru?'Подготовить иллюстрации':'Prepare paragraph illustrations'}</Button>
      {runs.length>0&&<details open className="illustration-runs"><summary>{ru?'Подготовка иллюстраций':'Illustration preparation'}</summary>{runs.map(run=>{
        const resume=illustrationResume(run);
        return <div className="illustration-run" key={run.id}><strong>{label(runStatuses,run.status,ru)} · {run.progress_complete??run.items.filter(item=>['ready','skipped'].includes(item.status)).length}/{run.progress_total??run.items.length}</strong>
          <p className="helper">{label(styleNames,run.style,ru)} · {new Date(run.created).toLocaleString(locale(language))}</p>
          {run.error&&<p className="validation-error">{runError(run.error,language)}</p>}
          {Boolean(run.excluded?.length)&&<details className="illustration-excluded"><summary>{ru?`Пропущено абзацев: ${run.excluded?.length}`:`${run.excluded?.length} paragraph${run.excluded?.length===1?'':'s'} skipped`}</summary>{run.excluded?.map(item=><p key={item.source_id}>{runError(item.reason,language)} <button type="button" onClick={()=>onNavigate(item.source_id)}>{ru?`«${item.opening}…»`:`“${item.opening}…”`} · {item.chapter}</button></p>)}</details>}
          {running(run)?<Button variant="outline" onClick={()=>{setError('');setMessage('');void api('illustration_pause',{id:run.id}).then(()=>{setStopping(run.id);onRunChange?.();},reason=>setError(operationMessage(reason,language)));}}><Pause/>{ru?'Остановить иллюстрации':'Stop illustrations'}</Button>:resume&&<>
            {resume.localOnly?<p className="helper">{ru?'У всех абзацев есть сохранённые результаты. Завершите задачу локально: текст не будет отправлен, изображения не будут созданы заново.':'All paragraphs have recorded results. Finish the saved run locally; no text will be sent and no images regenerated.'}</p>:<label className="consent"><input type="checkbox" disabled={!enabled||busy||!!active||elsewhere} checked={resumeConsent===run.id} onChange={event=>setResumeConsent(event.target.checked?run.id:null)}/><span>{ru?`Разрешить отправку в Codex незавершённых абзацев (${resume.remaining}) из сохранённой задачи «${label(styleNames,run.style,ru)}», а не выбранных выше.${resume.retryFailed?' Неудачные или неясные запросы будут повторены и могут расходовать дополнительный лимит.':' Ранее сохранённые изображения остаются.'}`:<>Allow the {resume.remaining} unfinished paragraph{resume.remaining===1?'':'s'} from this saved {label(styleNames,run.style,ru).toLocaleLowerCase()} run to be sent to Codex, not the selection above.{resume.retryFailed?' This explicitly retries failed or uncertain requests and may use additional allowance.':' Previously saved images are kept.'}</>}</span></label>}
            <Button variant="outline" disabled={busy||!!active||elsewhere||(!resume.localOnly&&(!enabled||noCodex||resumeConsent!==run.id))} onClick={()=>void action(async()=>{setResumeConsent(null);await api('illustration_resume',{id:run.id,consent:!resume.localOnly,retry_failed:resume.retryFailed});},true)}>{resume.localOnly?(ru?'Завершить сохранённую задачу':'Finish saved run'):resume.retryFailed?(ru?'Повторить незавершённые изображения':'Retry unfinished images'):(ru?'Продолжить сохранённую задачу':'Resume saved run')}</Button>
          </>}
        </div>;
      })}</details>}
      {enabled&&<details className="image-api-details"><summary>{ru?'Images API (отдельная оплата)':'Images API instead (separate billing)'}</summary><p className="helper">{ru?'Необязательный API-маршрут только для этого абзаца. Он не используется автоматически вместо Codex.':'Optional existing API route for this paragraph only. It is not used as a Codex fallback.'}</p>
        <input type="password" aria-label={ru?'Ключ OpenAI API для одного изображения':'OpenAI API key for one image'} autoComplete="off" value={apiKey} onChange={event=>setApiKey(event.target.value)} placeholder={ru?'Ключ API только для этого запроса':'API key for this request only'}/>
        <label className="consent"><input type="checkbox" checked={apiConsent} onChange={event=>setApiConsent(event.target.checked)}/><span>{ru?'Разрешить отправку этого абзаца для создания одного оплачиваемого изображения через API.':'Allow this paragraph to be sent for one billed API image.'}</span></label>
        <Button disabled={!apiKey||!apiConsent||busy||apiPending} onClick={()=>void action(async()=>{try{await trackImageRequest(requestKey,()=>api('generate_visual',{book_id:book.id,source_id:sourceId,api_key:apiKey,consent:true}));setMessage(ru?'Изображение из Images API сохранено рядом с абзацем.':'API image saved beside this paragraph.');}finally{setApiKey('');setApiConsent(false);}})}>{ru?'Создать одно изображение через API':'Generate one API image'}</Button></details>}
    </div>
    {notices.length>0&&<div role="status" className="helper illustration-feedback" style={{marginTop:14,flexShrink:0}}>{notices.map(notice=><p key={notice}>{notice}</p>)}</div>}
    {failure&&<div role="alert" className="modal-error"><AlertCircle size={16}/><span>{failure}</span></div>}</>;
}
