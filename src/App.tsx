import {invoke} from '@tauri-apps/api/core';
import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState,useSyncExternalStore,type ReactNode} from 'react';
import {ArrowLeft,ArrowRight,BookOpen,Bookmark as BookmarkIcon,Check,CheckCircle2,ChevronDown,ChevronLeft,ChevronRight,Download,FileAudio,FolderOpen,Headphones,History,Image as ImageIcon,Library,LoaderCircle,MoreHorizontal,Pause,Play,Plus,RotateCcw,Search,Settings2,ShieldCheck,SkipBack,SkipForward,Sparkles,Trash2,Type,Users,Volume2,X,AlertCircle,Moon,PanelLeftClose,PanelLeftOpen,GraduationCap,Pencil,CheckCheck,HardDrive,Upload,Timer} from 'lucide-react';
import {Button} from './components/ui/button';
import {Dialog,DialogContent,DialogHeader} from './components/ui/dialog';
import {Input,Textarea} from './components/ui/input';
import {Toggle} from './components/ui/toggle';
import {BookTools} from './components/BookTools';
import {PlayerBar} from './components/PlayerBar';
import {PlaybackSettings} from './components/PlaybackSettings';
import {ReadingPreferences} from './components/ReadingPreferences';
import {ExportPanel} from './components/ExportPanel';
import {Column} from './components/Column';
import {AssetImage,BookMarkup} from './components/Media';
import {api,choosePack,connect,hostedListenerConfig,isRuntimeLoss,native,runtimeConnection,saveExport,uploadBook} from './lib/api';
import type {Book,Chapter,Character,DeletionResult,ExportFile,Generation,GenerationSummary,Health,LibraryBook,Position,Segment,Settings,StressReport} from './lib/types';
import {active,bytes,cn,duration,errorText} from './lib/utils';
import {blockKindLabel,localMessage,preview,characterName,count,deliveryLabel,passageNote,passageProgress,importNotice,jobElsewhere,locale,operationMessage,playerMessage,statusLabel,translator} from './lib/i18n';
import {confirmPartRequest,confirmPresentationRequest,confirmSpeakerRequest,heldCount,heldLabel,holdQueue,mixedSegment,partRequests,partVerdicts,presentationLine,withPartSpeaker,preparationPercent,preparationStatus,segmentHeld,speakerConfirmationAvailable,speakerLabels,stageProgress} from './lib/castReview';
import {usePlayback} from './lib/usePlayback';
import {createCorrectionAdoption,type CorrectionPublication} from './lib/correctionAdoption';
import {useMusic} from './lib/useMusic';
import {playable,spaceToggles} from './lib/playerView';
import {acceptVisualRefresh,acceptVisualPoll,preserveVisualRevision,bookIndex,segmentIndex,paragraphImages,titleSource} from './lib/readerIndexes';
import {bindReaderViewport,bindReadingPosition,followPlaybackProgress,readingOffset,scrollReaderTo,scrollToChapterStart,scrollToPlaybackPosition,scrollToReadingPosition,scrollToSource} from './lib/readerNavigation';
import {bindBookDrop,bindDropGuard,importBooks} from './lib/importFiles';
import {bindNativeBooks} from './lib/nativeBooks';
import {ReaderChapter,type PassageAction} from './components/ReaderChapter';
import {stressCorrection,stressVariants,type StressSelection} from './lib/stressSelection';
import {IllustratedPanel} from './components/IllustratedPanel';
import {IllustrationPanel} from './components/IllustrationPanel';
import type {Visual} from './lib/types';
import {BookPreparation} from './components/BookPreparation';
import {AskDialog,type AskOptions,type AskRequest,type RemoveAction} from './components/AskDialog';
import {focusIfLost,removalFocus} from './lib/removalFocus';
import {acceptGenerationRefresh,acceptGenerationPoll,afterPlaybackSaved,playbackStart,generationHistory,visibleChapters,playbackShortcut,deletionNotice,defaultGeneration,repairOutcome,correctionChanged,correctionRequest,resumesChapter,generationRename,searchKey} from './lib/readerSelection';
import {createDialogFocus} from './lib/dialogFocus';
import {createPreparationPause,healthSettling} from './lib/preparationPause';
import {CastPanel} from './components/CastPanel';
import {HomeRecommendations} from './components/HomeRecommendations';
import {ExternalJobs} from './components/ExternalJobs';
import {addSample,formatRemaining,remainingSeconds,type RateSample} from './lib/eta';
import {defaultNarrationChapters,hasNarratableText,narrationSelection,sourceChapter,speakable} from './lib/preparation';

type Modal='bookTools'|'playback'|'appearance'|'settings'|'generations'|'narration'|'export'|'generate'|'correct'|'illustration'|'preparation'|'stressReports'|null;
type NarrationFeedbackSelection=StressSelection&{segmentId:string;generationId:string;audioHash:string|null;audioRevision:number|null};
const deliveries=['neutral','soft','whisper','firm','excited','sad','angry','questioning','enumeration','stunned'];
const FOLLOW_RETURN_DELAY=3*60*1000;

export default function App(){
  const listener=useRef(hostedListenerConfig()).current;
  const listenerMode=listener.enabled;
  const launchSelection=useRef(listener.target);
  const [connected,setConnected]=useState(false);
  const [bootFailure,setBootFailure]=useState<{reason:unknown}|null>(null);
  const [bootRetries,setBootRetries]=useState(0);
  const runtimeLost=useSyncExternalStore(runtimeConnection.subscribe,runtimeConnection.lost);
  const [runtimeRetries,setRuntimeRetries]=useState(0);
  const [retryingRuntime,setRetryingRuntime]=useState(false);
  const recoveringRuntime=useRef(false);
  const [settings,setSettings]=useState<Settings|null>(null);
  const [health,setHealth]=useState<Health|null>(null);
  const [pollErrors,setPollErrors]=useState<Record<'health'|'generation'|'visuals',string>>({health:'',generation:'',visuals:''});
  const pollResult=useCallback((key:'health'|'generation'|'visuals',message='')=>setPollErrors(current=>current[key]===message?current:{...current,[key]:message}),[]);
  const [library,setLibrary]=useState<LibraryBook[]>([]);
  const [book,setBook]=useState<Book|null>(null);
  const [generation,setGeneration]=useState<Generation|null>(null);
  const [chapterId,setChapterId]=useState('');
  const [query,setQuery]=useState('');
  const [chapterQuery,setChapterQuery]=useState('');
  const [modal,setModalState]=useState<Modal>(null);
  const modalRevision=useRef(0);
  const dialogFocus=useMemo(()=>createDialogFocus<HTMLElement>(),[]);
  const pointerTarget=useRef<{element:HTMLElement;at:number}|null>(null);
  const setModal=useCallback((value:Modal)=>{modalRevision.current++;const focused=document.activeElement;const recent=pointerTarget.current;dialogFocus.change(Boolean(value),focused instanceof HTMLElement&&focused!==document.body?focused:recent&&Date.now()-recent.at<2000?recent.element:null);if(value){setNotice('');setError('');}setModalState(value);},[dialogFocus]);
  const closeModal=(revision:number)=>{if(modalRevision.current===revision)setModal(null);};
  const [mode,setMode]=useState<'read'|'illustrated'|'study'>('read');
  const [navTab,setNavTab]=useState<'chapters'|'bookmarks'>('chapters');
  const [sidebar,setSidebar]=useState(()=>typeof window==='undefined'||typeof window.matchMedia!=='function'||!window.matchMedia('(max-width: 700px)').matches);
  const [busy,setBusy]=useState('');
  const [exporting,setExporting]=useState(false);
  const [pausing,setPausing]=useState<string|null>(null);
  const healthRefresh=useRef(0);
  const [browsing,setBrowsing]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [selectedSource,setSelectedSource]=useState('');
  const [navigation,setNavigation]=useState<{bookId:string;sourceId:string;focus:boolean;offset?:number;sourceFraction?:number;playback?:boolean;smooth?:boolean}|null>(null);
  const [dragging,setDragging]=useState(false);
  const [scope,setScope]=useState('all');
  const [generationName,setGenerationName]=useState('');
  const [generationSource,setGenerationSource]=useState('');
  const [generationChapter,setGenerationChapter]=useState('');
  const [playFrom,setPlayFrom]=useState<{generationId:string;sourceId:string}|null>(null);
  const [selectedChapters,setSelectedChapters]=useState<string[]>([]);
  const [packPath,setPackPath]=useState('');
  const [editSegment,setEditSegment]=useState<Segment|null>(null);
  const [stressMenu,setStressMenu]=useState<(NarrationFeedbackSelection&{x:number;y:number})|null>(null);
  const [stressMenuKind,setStressMenuKind]=useState<'pronunciation'|'pacing'|'voice'|'other'>('other');
  const [stressMenuComment,setStressMenuComment]=useState('');
  const [respelling,setRespelling]=useState('');
  const [stressReports,setStressReports]=useState<StressReport[]>([]);
  const [correctionOffer,setCorrectionOffer]=useState<CorrectionPublication|null>(null);
  const correctionAdoption=useRef(createCorrectionAdoption());
  const [retake,setRetake]=useState(false);
  const [queued,setQueued]=useState(0);
  const [nativeQueued,setNativeQueued]=useState(false);
  const importQueue=useRef<File[]>([]);
  const pendingRepair=useRef('');
  const closeFocus=useRef('');
  const fileInput=useRef<HTMLInputElement>(null);
  const viewRef=useRef<HTMLDivElement>(null);
  const flushReading=useRef<()=>void>(()=>{});
  const manualReading=useRef(false);
  const chapterStartIntent=useRef('');
  const restoringPlayback=useRef('');
  const followReturnTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const selectionEpoch=useRef(0);
  const runningAction=useRef(false);
  const settingsRequests=useRef<Promise<unknown>>(Promise.resolve());
  const settingsChanges=useRef<Partial<Settings>>({});
  const libraryRefresh=useRef(0);
  const bookRefresh=useRef(0);
  const t=translator(settings?.language||'en');
  const ru=settings?.language==='ru';
  const language=settings?.language||'en';
  const pollError=Object.values(pollErrors).filter(Boolean).map(message=>localMessage(language,message)).join('\n');
  const [ask,setAsk]=useState<AskRequest|null>(null);
  const currentOpener=()=>{const focused=document.activeElement;const recent=pointerTarget.current;return focused instanceof HTMLElement&&focused!==document.body?focused:recent&&Date.now()-recent.at<2000?recent.element:null;};
  const request=(options:AskOptions,opener=currentOpener())=>new Promise<string|null>(resolve=>{setAsk({...options,cancel:t('cancel'),close:t('close'),opener,fallback:viewRef.current,resolve});});
  const confirmed=(options:AskOptions)=>request(options).then(value=>value!==null);
  const afterCommit=()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
  const removeWith:RemoveAction=async(options,row,fallbacks,remove)=>{const opener=currentOpener();if(options&&await request(options,opener)===null)return;const restore=removalFocus(opener,row,fallbacks);await remove();await afterCommit();restore();};
  const passageLabel=(sourceId:string)=>{const block=readerIndex.sources.get(sourceId)?.block;return block?.text?preview(block.text,90):blockKindLabel(language,block?.kind||'paragraph');};
  const removeNoteText=(note:string)=>ru?`Заметка «${preview(note)}» тоже будет удалена.`:`Its note “${preview(note)}” will also be deleted.`;
  const bookmarked=useMemo(()=>new Set(book?.bookmarks.map(mark=>mark.source_id)),[book?.bookmarks]);
  const player=usePlayback(book,generation,settings);
  const feedbackContext=useRef({book,generation,player,epoch:selectionEpoch.current});
  feedbackContext.current={book,generation,player,epoch:selectionEpoch.current};
  const feedbackServices={
    selection:()=>{const current=feedbackContext.current;return {bookId:current.book?.id||'',generationId:current.generation?.id||'',epoch:selectionEpoch.current};},
    load:async(publication:CorrectionPublication)=>{const [nextBook,nextGeneration]=await Promise.all([
      api<Book>('book',{id:publication.bookId}),api<Generation>('generation',{id:publication.generationId})]);return {book:nextBook,generation:nextGeneration};},
    adopt:(nextBook:Book,nextGeneration:Generation,publication:CorrectionPublication)=>{
      if(!feedbackContext.current.player.adoptCorrection(nextBook,nextGeneration,publication.affectedSegmentIds,publication.affectedSourceIds))return false;
      setBook(current=>preserveVisualRevision(current,nextBook));setGeneration(nextGeneration);setCorrectionOffer(null);return true;
    },
    offer:setCorrectionOffer,
  };
  const feedbackServicesRef=useRef(feedbackServices);feedbackServicesRef.current=feedbackServices;
  const music=useMusic(generation,settings,player.clock);
  const cancelFollowingReturn=()=>{if(followReturnTimer.current!==null){clearTimeout(followReturnTimer.current);followReturnTimer.current=null;}};
  const scheduleFollowingReturn=()=>{cancelFollowingReturn();followReturnTimer.current=setTimeout(()=>{followReturnTimer.current=null;manualReading.current=false;setBrowsing(false);setSelectedSource('');},FOLLOW_RETURN_DELAY);};
  const resumeFollowing=()=>{chapterStartIntent.current='';cancelFollowingReturn();flushReading.current();manualReading.current=false;setBrowsing(false);setSelectedSource('');};
  const togglePlayback=()=>{
    if(!player.playing&&!player.waiting){
      if(browsing&&chapter?.id&&(chapterStartIntent.current===chapter.id||chapter.id!==player.segment?.chapter_id)){
        const start=chapterSegments.find(playable);
        const intro=generation?.config?.opening?.variants.music;
        const firstChapter=generation?.segments[0]?.chapter_id||generation?.config?.chapters?.[0];
        if(!start&&!(chapter.id===firstChapter&&intro?.audio_hash&&intro.duration>0)){setNotice(t('generationMissingChapter'));return;}
        resumeFollowing();player.seekChapterTrack(chapter.id,0,true);return;
      }
      resumeFollowing();
    }
    player.toggle();
  };
  const selectForPlayback=(id:string,play:boolean,offset=0)=>{if(play)resumeFollowing();player.select(id,play,offset);};
  const seekTrackForPlayback=(chapterId:string,seconds:number,play:boolean)=>{if(play)resumeFollowing();player.seekChapterTrack(chapterId,seconds,play);};
  const listenTo=(id:string)=>{flushReading.current();chapterStartIntent.current='';manualReading.current=false;setBrowsing(false);setSelectedSource('');player.select(id,true);};
  const previewSegment=(id:string)=>{if(player.previewing&&player.playing&&player.segment?.id===id)player.toggle();else player.preview(id);};
  const readerIndex=useMemo(()=>bookIndex(book),[book?.chapters]);
  useEffect(()=>{chapterStartIntent.current='';},[book?.id,generation?.id]);
  const segments=useMemo(()=>segmentIndex(generation),[generation?.segments]);
  const imageIndex=useMemo(()=>paragraphImages(book?.visuals||[]),[book?.visuals]);
  const chapter=readerIndex.chapters.get(chapterId)||book?.chapters[0];
  const chapterSegments=segments.byChapter.get(chapter?.id||'')||[];
  const completed=generation?.preparation_progress?.ready??segments.ready;
  const total=generation?.preparation_progress?.total??generation?.segments.length??0;
  const rateSamples=useRef<RateSample[]>([]);
  const [remaining,setRemaining]=useState<number|null>(null);
  useEffect(()=>{
    if(!generation||!active(generation.status)){rateSamples.current=[];setRemaining(null);return;}
    rateSamples.current=addSample(rateSamples.current,completed);
    setRemaining(remainingSeconds(rateSamples.current,total));
  },[generation?.id,generation?.status,completed,total]);
  const remainingLabel=remaining!=null?` · ${formatRemaining(language,remaining)}`:'';
  const selectedBlock=(modal==='illustration'?readerIndex.sources.get(selectedSource)?.block:undefined)||chapter?.blocks.find(b=>b.id===(selectedSource||player.segment?.source_id))||chapter?.blocks.find(b=>b.kind==='paragraph')||chapter?.blocks[0];
  const generationChapterTitle=readerIndex.chapters.get(generationChapter)?.title||'';
  const savedGenerations=book?generationHistory(book,generation):[];
  const defaultGenerationName=book?.title||(ru?'Аудиокнига':'Audiobook');
  const filtered=library.filter(b=>searchKey(b.title+' '+b.author).includes(searchKey(query)));
  const libraryKey=useMemo(()=>library.map(item=>item.id).join(','),[library]);
  const run=async(label:string,fn:()=>Promise<void>)=>{if(runningAction.current){setNotice(`${t('busyWait')}${busy?`: ${busy}`:''}…`);return;}runningAction.current=true;setBusy(label);setError('');try{await fn();}catch(e){if(!isRuntimeLoss(e))setError(operationMessage(e,language));}finally{runningAction.current=false;setBusy('');}};
  const refreshHealth=useCallback(async(signal?:AbortSignal)=>{
    const revision=++healthRefresh.current;
    try{
      const next=await api<Health>('health',{},signal);
      if(!signal?.aborted&&revision===healthRefresh.current){setHealth(next);pollResult('health');}
      return next;
    }catch(reason){
      if(!signal?.aborted&&revision===healthRefresh.current)pollResult('health',errorText(reason));
      throw reason;
    }
  },[pollResult]);
  const pauseRequest=useMemo(()=>createPreparationPause({
    pause:(id,signal)=>api('pause',{id},signal),
    refresh:async(id,signal)=>{
      const [next,latestHealth]=await Promise.all([api<Generation>('generation',{id},signal),refreshHealth(signal)]);
      if(!signal.aborted){setGeneration(current=>acceptGenerationRefresh(current,next));pollResult('generation');}
      return {generation:next,health:latestHealth};
    },
    pending:setPausing,
    refreshError:reason=>pollResult('generation',errorText(reason)),
  }),[refreshHealth,pollResult]);
  useEffect(()=>()=>pauseRequest.dispose(),[pauseRequest]);
  const refreshLibrary=useCallback(async()=>{const revision=++libraryRefresh.current;const next=await api<LibraryBook[]>('library');if(revision===libraryRefresh.current)setLibrary(next);},[]);
  const refreshBook=async()=>{if(!book)return;const epoch=selectionEpoch.current;const revision=++bookRefresh.current;const next=await api<Book>('book',{id:book.id});if(epoch===selectionEpoch.current&&revision===bookRefresh.current)setBook(current=>current?.id===next.id?preserveVisualRevision(current,next):current);return next;};
  const selectGeneration=async(id:string)=>{if(!book)return;flushReading.current();const epoch=++selectionEpoch.current;const [next,currentBook]=await afterPlaybackSaved(player.stop,()=>Promise.all([api<Generation>('generation',{id}),api<Book>('book',{id:book.id})]));if(epoch===selectionEpoch.current){manualReading.current=false;setBook(current=>preserveVisualRevision(current,currentBook));setGeneration(current=>current?.id===next.id&&current.book_id===next.book_id?acceptGenerationRefresh(current,next):next);setBrowsing(false);}};
  const open=async(id:string,generationId='')=>{
    flushReading.current();
    const epoch=++selectionEpoch.current;
    const next=await afterPlaybackSaved(player.stop,()=>api<Book>('book',{id}));if(epoch!==selectionEpoch.current)return;
    manualReading.current=false;setModal(null);setNavigation(null);setGeneration(null);setSelectedSource('');setGenerationSource('');setPlayFrom(null);setChapterQuery('');setNavTab('chapters');setBrowsing(false);
    const saved=defaultGeneration(next)??next.generations.find(g=>g.id===generationId);
    const listening=next.position?.generation_id&&next.position.source_id?next.position:null;
    restoringPlayback.current=listening?`${next.id}:${listening.source_id}`:'';
    const reading=listening||next.reading_position||(!next.position?.generation_id?next.position:null);
    setBook(next);setSelectedChapters(defaultNarrationChapters(next));setChapterId(reading?.chapter_id||next.position?.chapter_id||next.chapters[0]?.id||'');setQuery('');
    if(!listening&&reading?.chapter_id&&(reading.source_id!==next.position?.source_id||reading.chapter_id!==next.position?.chapter_id)){manualReading.current=true;setBrowsing(true);}
    if(reading?.source_id){setSelectedSource(listening?'':reading.source_id);setNavigation(listening
      ?{bookId:next.id,sourceId:reading.source_id,focus:false,playback:true}
      :{bookId:next.id,sourceId:reading.source_id,focus:false,offset:reading.offset,sourceFraction:reading.source_fraction});}
    else setNavigation({bookId:next.id,sourceId:'',focus:false});
    if(saved){const g=await api<Generation>('generation',{id:saved.id});if(epoch===selectionEpoch.current)setGeneration(g);}
    focusIfLost(()=>viewRef.current);
  };
  useEffect(()=>{
    const target=launchSelection.current;
    if(!connected||!target||!library.some(item=>item.id===target.bookId))return;
    launchSelection.current=null;
    void open(target.bookId,target.generationId).catch(reason=>setError(errorText(reason)));
  },[connected,library]);
  const openNativePaths=async(paths:string[])=>{const failures:string[]=[];let first:string|null=null;for(const path of paths){try{const bytes=await invoke<ArrayBuffer>('read_opened_book',{path});const imported=await uploadBook(new File([new Uint8Array(bytes)],path.split(/[\\/]/).pop()||'book.epub'));first??=imported.id;}catch(reason){failures.push(`${path}: ${operationMessage(reason,settings?.language||'en')}`);}}await refreshLibrary();if(first)await open(first);if(failures.length)setError(failures.join('\n'));};
  const update=(change:Partial<Settings>)=>{setSettings(current=>current&&{...current,...change});settingsChanges.current={...settingsChanges.current,...change};const pending=settingsRequests.current.catch(()=>undefined).then(()=>{const next=settingsChanges.current;settingsChanges.current={};return Object.keys(next).length?api<Settings>('update_settings',next):null;});settingsRequests.current=pending;return pending.then(saved=>{if(saved&&!Object.keys(settingsChanges.current).length)setSettings(saved);},e=>{setError(operationMessage(e,language));if(!Object.keys(settingsChanges.current).length)void api<Settings>('settings').then(setSettings,()=>undefined);});};
  const boot=useCallback((live:()=>boolean)=>{
    setBootFailure(null);
    return connect().then(async()=>{const [s,l]=await Promise.all([api<Settings>('settings'),api<LibraryBook[]>('library')]);if(live()){setSettings(s);setPackPath(s.pack_path);setLibrary(l);setConnected(true);void refreshHealth().catch(()=>undefined);}}).catch(reason=>{if(live())setBootFailure({reason});});
  },[refreshHealth]);
  useEffect(()=>{let live=true;void boot(()=>live);return()=>{live=false;};},[boot]);
  useEffect(()=>{
    if(runtimeLost){recoveringRuntime.current=true;return;}
    if(!recoveringRuntime.current)return;
    recoveringRuntime.current=false;setRuntimeRetries(0);setError('');setPollErrors({health:'',generation:'',visuals:''});
  },[runtimeLost]);
  useEffect(()=>{
    const record=(event:PointerEvent)=>{const element=event.target instanceof Element?event.target.closest<HTMLElement>('button,a[href],input,select,textarea,summary,[tabindex]'):null;pointerTarget.current=element&&{element,at:Date.now()};};
    window.addEventListener('pointerdown',record,true);return()=>window.removeEventListener('pointerdown',record,true);
  },[]);
  useEffect(()=>{
    if(!pendingRepair.current)return;
    const outcome=repairOutcome(pendingRepair.current,generation);
    if(outcome==='wait')return;
    pendingRepair.current='';
    if(outcome==='clear')player.clearError();
  },[generation?.id,generation?.status]);
  const nativeAction=useRef<()=>Promise<boolean>>(async()=>false);
  nativeAction.current=async()=>{
    if(runningAction.current)return false;
    await run(t('openingBook'),async()=>{
      const paths=await invoke<string[]>('startup_books');
      if(paths.length)await openNativePaths(paths);
    });
    return true;
  };
  useEffect(()=>{
    if(!connected||!native())return;
    return bindNativeBooks({
      listen:async notify=>{const {listen}=await import('@tauri-apps/api/event');return listen('open-books',notify);},
      process:()=>nativeAction.current(),
      error:reason=>setError(t('fileAssociationFailed')+': '+errorText(reason)),
      queued:setNativeQueued,
    });
  },[connected]);
  useEffect(()=>{document.documentElement.dataset.theme=settings?.theme||'light';document.documentElement.lang=settings?.language||'en';},[settings?.theme,settings?.language]);
  useEffect(()=>{
    if(typeof window==='undefined'||typeof window.matchMedia!=='function')return;
    const narrow=window.matchMedia('(max-width: 700px)');
    const resize=(event:MediaQueryListEvent)=>setSidebar(!event.matches);
    narrow.addEventListener('change',resize);
    return()=>narrow.removeEventListener('change',resize);
  },[]);
  useEffect(()=>{
    if(!generation)return;
    let live=true;const gid=generation.id;let revision:number|undefined=generation.revision;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{const epoch=selectionEpoch.current;try{
      const result=await api<{id:string;revision:number;generation:Generation|null;full:boolean;removed_segment_ids:string[]}>('generation_poll',{id:gid,revision});
      if(live&&epoch===selectionEpoch.current&&result.id===gid){revision=result.revision;const next=result.generation;if(next)setGeneration(current=>acceptGenerationPoll(current,next,result.full,result.removed_segment_ids));}
      if(live)pollResult('generation');
    }catch(reason){if(live)pollResult('generation',t('generationRefreshFailed')+': '+errorText(reason));}finally{if(live)timer=setTimeout(poll,active(generation.status)?3000:6500);}};
    timer=setTimeout(poll,active(generation.status)?3000:6500);
    return()=>{live=false;clearTimeout(timer);pollResult('generation');};
  },[generation?.id,generation?.status]);
  useEffect(()=>{
    if(!connected||pausing)return;let pending=false;const timer=setInterval(()=>{if(pending)return;pending=true;void refreshHealth().catch(()=>undefined).finally(()=>{pending=false;});},10000);return()=>clearInterval(timer);
  },[connected,pausing,refreshHealth]);
  useEffect(()=>{
    if(!connected||!generation?.status||pausing)return;
    let live=true;let timer:ReturnType<typeof setTimeout>|undefined;const current={id:generation.id,status:generation.status};
    const check=(attempt:number)=>void refreshHealth().then(next=>{if(live&&attempt<5&&healthSettling(current,next))timer=setTimeout(()=>check(attempt+1),1000);},()=>undefined);
    check(0);return()=>{live=false;clearTimeout(timer);};
  },[connected,generation?.id,generation?.status,pausing,refreshHealth]);
  useEffect(()=>{if(modal==='settings')setPackPath(settings?.pack_path||'');},[modal]);
  useEffect(()=>{if(modal==='illustration')void refreshHealth().catch(()=>undefined);},[modal,refreshHealth]);
  useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),4500);return()=>clearTimeout(timer);},[notice]);
  useEffect(()=>{
    cancelFollowingReturn();
    if(browsing&&generation&&settings?.follow)scheduleFollowingReturn();
    return cancelFollowingReturn;
  },[browsing,generation?.id,settings?.follow,book?.reading_position?.chapter_id,book?.reading_position?.source_id,book?.reading_position?.offset,book?.reading_position?.source_fraction]);
  useEffect(()=>{
    if(player.opening||player.previewing||!player.segment||!settings?.follow||browsing||manualReading.current||!generation||generation.book_id!==book?.id||segments.byId.get(player.segment.id)?.source_id!==player.segment.source_id)return;
    setChapterId(player.segment.chapter_id);
    if(!book||mode==='illustrated')return;
    const source=readerIndex.sources.get(player.segment.source_id)?.block;
    const progress=player.segment.duration>0?Math.max(0,Math.min(1,player.position/player.segment.duration)):0;
    const sourceFraction=source?.text.length?Math.max(0,Math.min(1,(player.segment.start+(player.segment.end-player.segment.start)*progress)/source.text.length)):undefined;
    const restoreKey=`${book.id}:${player.segment.source_id}`;
    const restoring=restoringPlayback.current===restoreKey;
    if(restoring)restoringPlayback.current='';
    setNavigation({bookId:book.id,sourceId:player.segment.source_id,focus:false,playback:true,smooth:!restoring,...(sourceFraction===undefined?{}:{offset:0,sourceFraction})});
    if(book.reading_position?.source_id!==player.segment.source_id)storeReading(book.id,{chapter_id:player.segment.chapter_id,source_id:player.segment.source_id,offset:0});
  },[book?.id,generation?.id,player.segment?.id,player.opening?.key,settings?.follow,browsing,mode,readerIndex]);
  useEffect(()=>{
    if(player.previewing||!settings?.follow||browsing||mode==='illustrated'||!book||!generation)return;
    return player.clock.subscribe(()=>{
      if(manualReading.current)return;
      const state=player.clock.getSnapshot();
      const segment=state.segment;
      if(!state.playing||state.opening||state.previewing||!segment||segment.chapter_id!==chapter?.id||!segments.byId.has(segment.id))return;
      const source=readerIndex.sources.get(segment.source_id)?.block;
      if(!source?.text.length)return;
      const progress=segment.duration>0?Math.max(0,Math.min(1,state.position/segment.duration)):0;
      const fraction=(segment.start+(segment.end-segment.start)*progress)/source.text.length;
      followPlaybackProgress(viewRef.current,segment.source_id,fraction);
    });
  },[book?.id,generation?.id,chapter?.id,readerIndex,segments,settings?.follow,browsing,mode,player.clock,player.previewing]);
  useLayoutEffect(()=>bindReaderViewport(viewRef.current),[book?.id]);
  useLayoutEffect(()=>{
    const root=viewRef.current;if(!root||!book)return;
    const binding=bindReadingPosition(root,()=>{chapterStartIntent.current='';manualReading.current=true;setNavigation(null);setBrowsing(true);if(settings?.follow)scheduleFollowingReturn();},(sourceId,offset,sourceFraction)=>{
      const destination=readerIndex.sources.get(sourceId);if(!destination)return;
      const position={chapter_id:destination.chapter.id,source_id:sourceId,offset,...(sourceFraction===undefined?{}:{source_fraction:sourceFraction})};
      storeReading(book.id,position);
    });
    flushReading.current=binding.flush;
    return()=>{binding.dispose();if(flushReading.current===binding.flush)flushReading.current=()=>{};};
  },[book?.id,readerIndex,settings?.follow]);
  useLayoutEffect(()=>{
    if(!navigation||navigation.bookId!==book?.id)return;
    if(navigation.sourceId?navigation.playback?scrollToPlaybackPosition(viewRef.current,navigation.sourceId,navigation.sourceFraction,navigation.smooth):navigation.sourceId===titleSource(chapter)?scrollToChapterStart(viewRef.current,navigation.sourceId,navigation.focus):(navigation.offset!==undefined||navigation.sourceFraction!==undefined)?scrollToReadingPosition(viewRef.current,navigation.sourceId,navigation.offset,navigation.sourceFraction):scrollToSource(viewRef.current,navigation.sourceId,navigation.focus):scrollReaderTo(viewRef.current))setNavigation(null);
  },[navigation,book?.id,chapter?.id,mode]);
  useEffect(()=>{
    if(!book)return;
    const keyboard=(event:KeyboardEvent)=>{if(modal||ask)return;const action=playbackShortcut(event,spaceToggles(player.playing||player.waiting,player.error,player.segment&&segments.byId.get(player.segment.id)));if(!action)return;event.preventDefault();if(action==='toggle')togglePlayback();else player.skip(action==='back'?-15:15);};
    window.addEventListener('keydown',keyboard);return()=>window.removeEventListener('keydown',keyboard);
  });
  useEffect(()=>{if(!playFrom)return;if(book&&generation?.book_id===book.id&&generation.id===playFrom.generationId){const segment=playbackStart(generation,playFrom.sourceId);if(segment)listenTo(segment.id);else setError(t('passageMissing'));}setPlayFrom(null);},[book?.id,generation?.id,playFrom]);
  const importFiles=(files:FileList|File[]|null)=>{if(!files?.length)return;const selected=Array.from(files);if(runningAction.current){importQueue.current.push(...selected);setQueued(importQueue.current.length);return;}void run(t('importingBooks'),async()=>{
    const epoch=selectionEpoch.current;const known=new Set(library.map(item=>item.id));const result=await importBooks(selected,uploadBook,reason=>operationMessage(reason,language),t('importRejected'));await refreshLibrary();
    if(epoch===selectionEpoch.current&&result.imported.length)await open(result.imported[0].id);
    if(result.errors.length)setError(result.errors.join('\n'));
    if(result.imported.length){const added=new Set(result.imported.map(item=>item.id).filter(id=>!known.has(id))).size;setNotice(importNotice(language,added,new Set(result.imported.map(item=>item.id)).size-added));}
  });};
  const storeReading=(bookId:string,position:Position)=>{setBook(current=>current?.id===bookId?{...current,reading_position:position}:current);void api('save_reading_position',{book_id:bookId,position}).catch(reason=>setError(t('readingSaveFailed')+': '+errorText(reason)));};
  const saveReading=(sourceId:string)=>{if(!book)return;flushReading.current();const destination=readerIndex.sources.get(sourceId);if(!destination)return;const position={chapter_id:destination.chapter.id,source_id:sourceId,...readingOffset(viewRef.current,sourceId)};storeReading(book.id,position);};
  const selectChapter=(id:string)=>{flushReading.current();chapterStartIntent.current=id;manualReading.current=true;setNavigation(null);setBrowsing(true);setChapterId(id);setSelectedSource('');if(typeof window!=='undefined'&&typeof window.matchMedia==='function'&&window.matchMedia('(max-width: 700px)').matches)setSidebar(false);scrollReaderTo(viewRef.current);focusIfLost(()=>viewRef.current?.querySelector<HTMLElement>('.chapter-navigation button:not(:disabled)')??viewRef.current);if(book){const position={chapter_id:id,offset:0};storeReading(book.id,position);}};
  const verifyPack=async()=>{
    const pending=settingsRequests.current.catch(()=>undefined).then(()=>api<{settings:Settings}>('select_pack',{path:packPath}));
    settingsRequests.current=pending;
    const checked=await pending.catch(reason=>{setPackPath(settings?.pack_path||'');throw reason;});
    setSettings(checked.settings);setPackPath(checked.settings.pack_path);
    // Pack verification can overlap a pause. Do not apply its older bundled
    // health snapshot after the worker's inactive confirmation.
    void refreshHealth().catch(()=>undefined);
    setNotice(ru?'Файлы комплекта и эталонное аудио проверены локально. Модели не запускались.':'Pack files and reference audio verified locally. No inference was run.');
  };
  const openActive=()=>run(t('openingBook'),async()=>{const target=health?.active_book;const id=health?.active;if(!target||!id)return;if(book?.id===target.id)await selectGeneration(id);else await open(target.id,id);});
  const elsewhere=health?.active?jobElsewhere(settings?.language||'en','audio',health.active_book?.title||''):'';
  const resumePreparation=(id:string)=>run(t('resumingPreparation'),async()=>{const epoch=selectionEpoch.current;const next=await api<Generation>('resume',{id});if(epoch===selectionEpoch.current)setGeneration(current=>acceptGenerationRefresh(current,next));});
  const pausePreparation=async(id:string)=>{try{if(await pauseRequest.request(id))setNotice(t('pauseRequested'));}catch(e){setError(operationMessage(e,language));}};
  const repairGeneration=(id:string)=>run(t('repairingAudio'),async()=>{const epoch=selectionEpoch.current;if(generation?.id===id)await player.stop();const next=await api<Generation>('repair_audio',{id});if(epoch===selectionEpoch.current){setGeneration(current=>acceptGenerationRefresh(current,next));if(generation?.id===id){const outcome=repairOutcome(id,next);if(outcome==='clear')player.clearError();pendingRepair.current=outcome==='wait'?id:'';}}await refreshBook();await refreshLibrary();setNotice(next.status==='ready'?t('audioVerified'):t('repairStarted'));});
  const exportAudio=(format:string,chapters:string[])=>run(t('exportingAudio'),async()=>{if(!generation||!book)return;if(!chapters.length)throw new Error(t('selectCompletedChapter'));const epoch=selectionEpoch.current;setExporting(true);const result=await api<ExportFile>('export',{id:generation.id,format,chapters}).catch(reason=>{if(reason instanceof Error&&'code' in reason&&reason.code==='export_cancelled'){setNotice(localMessage(language,reason.message));return null;}throw reason;}).finally(()=>setExporting(false));if(!result)return;const saved=await saveExport(result,book.title);if(!saved){const deleted=await api<DeletionResult>('delete_export',{id:result.id});setNotice(deleted.cleanup_warnings.length?t('exportCancelledCleanup'):t('exportCancelled'));}else setNotice(result.pending_updates?(ru?'Экспорт сохранён из доступного аудио; ожидающие замены отрывки будут обновлены позднее.':'Export saved from currently playable audio; pending replacements will be published later.'):t('exportSaved'));const next=await api<Generation>('generation',{id:generation.id});if(epoch===selectionEpoch.current)setGeneration(current=>acceptGenerationRefresh(current,next));});
  const reviseSegment=(label:string,changes:Record<string,unknown>[])=>run(label,async()=>{if(!generation)return;const epoch=selectionEpoch.current;const panel=modalRevision.current;const next=await api<Generation>('revise',{id:generation.id,name:generation.name,changes});if(epoch===selectionEpoch.current){setGeneration(next);closeModal(panel);}await refreshBook();setNotice(ru?`Правка сохранена. Продолжите озвучку: изменённый отрывок заменится, когда будет готов.`:`Correction saved. Resume narration; the changed passage will be replaced when ready.`);});
  const cancelExport=()=>void api('cancel_export',{}).catch(reason=>setError(operationMessage(reason,language)));
  const startGeneration=()=>run(t('preparingAudiobook'),async()=>{if(!book)return;const epoch=selectionEpoch.current;const panel=modalRevision.current;const chapters=narrationSelection(book,scope,generationChapter,selectedChapters);if(!chapters.length)throw new Error(t('selectNarratable'));if(generationSource&&!chapters.includes(sourceChapter(book,generationSource)?.id||''))throw new Error(t('includePassageChapter'));const next=await api<Generation>('generate',{book_id:book.id,name:generationName.trim()||defaultGenerationName,chapters});if(epoch===selectionEpoch.current){setGeneration(next);setPlayFrom(generationSource?{generationId:next.id,sourceId:generationSource}:null);setGenerationSource('');closeModal(panel);setGenerationName('');}await refreshBook();await refreshLibrary();});
  const navigateSource=useCallback((id:string)=>{if(!book)return;flushReading.current();chapterStartIntent.current='';const destination=readerIndex.sources.get(id);if(!destination)return;manualReading.current=true;setBrowsing(true);setChapterId(destination.chapter.id);setSelectedSource(id);setNavigation({bookId:book.id,sourceId:id,focus:true});const position={chapter_id:destination.chapter.id,source_id:id,offset:0};storeReading(book.id,position);},[book?.id,readerIndex]);
  const refreshGeneration=async(id:string)=>{const epoch=selectionEpoch.current;const next=await api<Generation>('generation',{id});if(epoch===selectionEpoch.current)setGeneration(current=>acceptGenerationRefresh(current,next));};
  const castChange=(label:string,op:string,data:Record<string,unknown>={},close=false)=>run(label,async()=>{if(!generation)return;const panel=modalRevision.current;await api(op,{id:generation.id,...data});await refreshGeneration(generation.id);if(close)closeModal(panel);});
  const renameCharacter=(character:Character)=>void request({title:t('renameCharacter'),message:characterName(language,character),confirm:t('save'),input:{label:t('characterLabel'),value:character.name,maxLength:200,required:true}}).then(value=>{const name=value?.trim();if(name&&name!==character.name)void castChange(t('updatingCast'),'cast_rename',{character_id:character.id,name});});
  const mergeCharacter=(source:Character,target:Character)=>void confirmed({title:t('mergeCharactersTitle'),message:ru?`Реплики «${preview(characterName(language,source))}» будут приписаны персонажу «${preview(characterName(language,target))}» и озвучены его голосом.`:`Lines of “${preview(characterName(language,source))}” will be attributed to “${preview(characterName(language,target))}” and use that voice.`,confirm:t('merge')}).then(merge=>{if(merge)void castChange(t('updatingCast'),'cast_merge',{source_id:source.id,target_id:target.id});});
  const updateVisuals=useCallback((bookId:string,visuals:Visual[],revision:number)=>{setBook(current=>acceptVisualRefresh(current,{id:bookId,visuals,revision}));},[]);
  useEffect(()=>{
    if(!book)return;
    const id=book.id;let live=true;let revision:number|undefined;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const result=await api<{id:string;revision:number;visuals:Visual[]|null;full:boolean;removed_visual_ids:string[]}>('visuals_poll',{book_id:id,revision});if(live&&result.id===id){revision=result.revision;const changed=result.visuals;if(changed)setBook(current=>acceptVisualPoll(current,{...result,visuals:changed}));}if(live)pollResult('visuals');}catch(reason){if(live)pollResult('visuals',t('illustrationRefreshFailed')+': '+errorText(reason));}finally{if(live)timer=setTimeout(poll,health?.illustration_active?3000:6500);}};
    timer=setTimeout(poll,1200);return()=>{live=false;clearTimeout(timer);pollResult('visuals');};
  },[book?.id,health?.illustration_active,updateVisuals]);
  const openGenerate=(targetChapter:string,targetScope:string,source='')=>{setGenerationChapter(targetChapter);setScope(targetScope);setGenerationName('');setGenerationSource(source);if(book)setSelectedChapters(defaultNarrationChapters(book));setModal('generate');};
  const passageActionRef=useRef<(id:string,action:PassageAction)=>void>(()=>{});
  passageActionRef.current=(id,action)=>{
    const first=segments.bySource.get(id)?.[0];
    if(action==='listen'){if(first)listenTo(first.id);else openGenerate(readerIndex.sources.get(id)?.chapter.id||'','chapter',id);}
    else if(action==='bookmark')void toggleBookmark(id);
    else if(action==='correct'){if(first){setEditSegment({...first});setRetake(false);setModal('correct');}else setNotice(t(generation?'generationMissingChapter':'createBeforeCorrect'));}
    else {setSelectedSource(id);setModal('illustration');}
  };
  const passageAction=useCallback((id:string,action:PassageAction)=>passageActionRef.current(id,action),[]);
  const openStressMenu=(selection:StressSelection,point:{x:number;y:number})=>{
    if(!generation)return;
    const candidates=segments.bySource.get(selection.sourceId)||[];
    const offset=selection.textOffset;
    const segment=offset!==null
      ?candidates.find(s=>s.status==='ready'&&s.start<=offset&&offset+Array.from(selection.text).length<=s.end)
      :candidates.find(s=>s.status==='ready'&&s.id===player.segment?.id&&s.text.includes(selection.text))
        ||candidates.find(s=>s.status==='ready'&&s.text.includes(selection.text));
    if(!segment){setNotice(ru?'Для этого текста ещё нет готовой озвучки.':'No generated audio is ready for this text.');return;}
    setError('');setStressMenuKind('other');setStressMenuComment('');setRespelling('');
    const popupWidth=316,popupMaxHeight=430,edge=8;
    setStressMenu({...selection,segmentId:segment.id,generationId:generation.id,
      audioHash:player.segment?.id===segment.id?player.segment.audio_hash:segment.audio_hash,audioRevision:generation.revision??null,
      x:Math.max(edge,Math.min(point.x||24,window.innerWidth-popupWidth-edge)),
      y:Math.max(edge,Math.min(point.y||24,window.innerHeight-popupMaxHeight-edge))});
  };
  const saveNarrationFeedback=(selection:NarrationFeedbackSelection,kind:string,comment='',intendedStress:string|null=null)=>{
    void run(ru?'Сохраняем замечание':'Saving narration feedback',async()=>{
      const feedbackOrigin=feedbackServicesRef.current.selection();
      const audioOffset=player.segment?.id===selection.segmentId&&player.segment.audio_hash===selection.audioHash?player.position:null;
      const saved=await api<StressReport&{retake_queue?:{id:string;status:string;error?:string;targets?:number;queued?:number;character?:string|null;family_status?:string};character_state?:{status:string;canonical_name?:string;family_status?:string}}>('report_stress',{generation_id:selection.generationId,segment_id:selection.segmentId,source_id:selection.sourceId,
        selected_text:selection.text,text_offset:selection.textOffset,audio_offset:audioOffset,audio_hash:selection.audioHash,audio_revision:selection.audioRevision,kind,comment,intended_stress:intendedStress});
      correctionAdoption.current.watch(saved,feedbackOrigin,true);
      void correctionAdoption.current.receive([saved],feedbackServicesRef.current).catch(()=>undefined);
      setStressMenu(null);setStressMenuComment('');
      const propagated=(saved.retake_queue?.targets||0)>1;
      setNotice(saved.retake_queue?.status==='held'
        ?(ru?`Правка «${selection.text}» сохранена; повторная озвучка ожидает разбора.`:`Correction saved; the retake for “${selection.text}” needs attention.`)
        :saved.retake_queue?.phase==='awaiting_render_completion'
          ?(ru?'Правка сохранена и применится автоматически после завершения текущей озвучки. Повторять замечание не нужно.':'Correction saved. It will run automatically after the current render finishes; no need to report it again.')
        :saved.character_state?.family_status==='unresolved'
          ?(ru?'Правка сохранена для этого места; определение всех форм имени требует разбора.':'Correction saved for this occurrence; the name family needs review before applying it throughout the book.')
        :saved.retake_queue?.status==='applied'
          ?(ru?'Правка сохранена для следующих озвучек; новых фрагментов для замены нет.':'Correction saved for future generations; no additional passages need replacement.')
        :saved.retake_queue?(propagated
          ?(ru?`Ударение имени «${saved.retake_queue.character||selection.text}» сохранено для серии; ${saved.retake_queue.targets} фрагм. поставлено в очередь.`:`Series name stress saved; ${saved.retake_queue.targets} passages queued.`)
          :(ru?`Правка «${selection.text}» в очереди на повторную озвучку. Продолжайте слушать.`:`Correction queued for “${selection.text}”. Keep listening.`))
          :(ru?`Замечание сохранено: «${selection.text}». Продолжайте слушать.`:`Feedback saved: “${selection.text}”. Keep listening.`));
    });
  };
  const retryRuntime=async()=>{setRetryingRuntime(true);try{await refreshHealth();}catch{setRuntimeRetries(count=>count+1);}finally{setRetryingRuntime(false);}};
  const deleteGeneration=(saved:GenerationSummary)=>run(t('deletingGeneration'),async()=>{
    const epoch=++selectionEpoch.current;const selected=generation?.id===saved.id;
    if(selected)await player.stop();
    const result=await api<DeletionResult>('delete_generation',{id:saved.id});pauseRequest.removed(saved.id);void refreshHealth().catch(()=>undefined);
    const nextBook=await refreshBook();
    if(selected&&epoch===selectionEpoch.current){const fallback=nextBook&&defaultGeneration(nextBook);const next=fallback?await api<Generation>('generation',{id:fallback.id}):null;if(epoch===selectionEpoch.current)setGeneration(current=>current?.id===saved.id?next:current);}
    await refreshLibrary();setNotice(deletionNotice(result,'Generation',settings?.language));
  });
  const toggleBookmark=async(source:string)=>{const existing=book?.bookmarks.find(mark=>mark.source_id===source);if(existing?.note.trim()&&!await confirmed({title:t('removeBookmarkTitle'),message:removeNoteText(existing.note),confirm:t('removeBookmark'),destructive:true}))return;await run(t('updatingBookmark'),async()=>{if(!book)return;if(existing){await api('delete_bookmark',{id:existing.id});setNotice(t('bookmarkRemoved'));}else{await api('bookmark',{book_id:book.id,source_id:source});setNotice(t('bookmarkSaved'));}await refreshBook();});};
  const progressStatus=(g:GenerationSummary|Generation)=>statusLabel(language,g.status);
  const failedSegment=generation?.segments.find(segment=>segment.status==='failed');
  const playerNote=passageNote(language,player.segment);
  const editNote=passageNote(language,editSegment);
  const speakerNames=useMemo(()=>speakerLabels(language,generation?.cast||[]),[generation?.cast,language]);
  const failedSources=useMemo(()=>new Set(generation?.segments.filter(segment=>segment.status==='failed').map(segment=>segment.source_id)),[generation?.segments]);
  const editOriginal=editSegment?generation?.segments.find(s=>s.id===editSegment.id):undefined;
  const editHold=generation&&editOriginal?holdQueue(generation).find(item=>item.segment?.id===editOriginal.id):undefined;
  const editHeld=Boolean(generation&&editOriginal&&segmentHeld(generation,editOriginal));
  const editSpeakerHeld=editHeld&&speakerConfirmationAvailable(editHold);
  const editMixed=mixedSegment(editOriginal);
  const confirmChange=editSegment&&editSpeakerHeld?confirmSpeakerRequest(editOriginal,editSegment):null;
  const editParts=editSpeakerHeld&&partVerdicts(editOriginal);
  const editPresentation=modal==='correct'&&editHeld?editHold?.presentation??[]:[];
  const partChanges=editSegment?partRequests(editOriginal,editSegment):[];
  const wholeChange=editSegment?correctionRequest(editOriginal,editSegment,retake):null;
  const saveChanges=partChanges.length?(wholeChange?null:partChanges):wholeChange?[wholeChange]:null;
  const heldTotal=generation?heldCount(generation):0;
  const castBlocked=health?.active&&health.active!==generation?.id?elsewhere:'';
  const openModal=(value:Modal,sourceId?:string)=>{setError('');if(value==='illustration'){const target=sourceId?readerIndex.sources.get(sourceId)?.block:selectedBlock?.kind==='paragraph'?selectedBlock:chapter?.blocks.find(block=>block.kind==='paragraph');if(!target||target.kind!=='paragraph'){setError(t('noParagraph'));return;}setSelectedSource(target.id);}if(value==='generate'){openGenerate(chapter?.id||'','all');return;}setModal(value);if(value==='settings')void refreshHealth().catch(()=>undefined);if(value==='generations')void refreshBook().catch(e=>setError(operationMessage(e,language)));if(value==='narration'&&generation)void refreshGeneration(generation.id).catch(e=>setError(operationMessage(e,language)));};
  useEffect(()=>{let live=true;let pending=false;
    const refresh=async()=>{if(pending)return;pending=true;try{
      const ids=[...new Set([book?.id,...correctionAdoption.current.bookIds()].filter((id):id is string=>Boolean(id)))];
      for(const id of ids){const rows=await api<StressReport[]>('stress_reports',{book_id:id});if(!live)return;
        if(feedbackContext.current.book?.id===id)setStressReports(rows);
        await correctionAdoption.current.receive(rows,feedbackServicesRef.current);}
    }catch(e){if(live&&modal==='stressReports')setError(operationMessage(e,language));}finally{pending=false;}};
    void refresh();const timer=window.setInterval(()=>void refresh(),4000);return()=>{live=false;window.clearInterval(timer);};},[book?.id]);

  const importRef=useRef(importFiles);importRef.current=importFiles;
  useEffect(()=>bindDropGuard(window),[]);
  useEffect(()=>{if(busy||!importQueue.current.length)return;const files=importQueue.current.splice(0);setQueued(0);importRef.current(files);},[busy]);
  useEffect(()=>{
    if(!connected||listenerMode)return;
    return bindBookDrop(window,{blocked:()=>Boolean(modal),hover:setDragging,
      drop:files=>importRef.current(files),rejected:()=>setError(t('closePanelDrop'))});
  },[connected,modal,listenerMode]);

  const restartRuntime=native()?t('runtimeRestartDesktop'):t('runtimeRestartBrowser');
  const runtimeMessage=runtimeRetries?restartRuntime:t('runtimeLost');
  if(!connected||listenerMode&&!book)return <div className="boot"><Column size={62}/><h1>Dogma</h1>{bootFailure?<><p className="max-w-lg text-center" role="alert">{bootRetries?restartRuntime:isRuntimeLoss(bootFailure.reason)?t('runtimeLost'):errorText(bootFailure.reason)}</p><Button onClick={()=>{setBootRetries(count=>count+1);void boot(()=>true);}} variant="outline"><RotateCcw/>{t('retry')}</Button></>:<><LoaderCircle className="spin"/><p>{t('openingLibrary')}</p></>}</div>;
  const runtimeRetry=<Button variant="outline" size="sm" disabled={retryingRuntime} onClick={()=>void retryRuntime()}>{retryingRuntime?<LoaderCircle className="spin"/>:<RotateCcw/>}{t('retry')}</Button>;
  const stressOptions=stressMenu?stressVariants(stressMenu.text):[];

  return <div className={cn('app',listenerMode&&'hosted-listener')}>
    {dragging&&<div className="book-drop-overlay" role="status">{modal?t('closePanelFirst'):t('dropBooks')}</div>}
    {!listenerMode&&<input type="file" ref={fileInput} accept=".epub,.fb2" multiple hidden onChange={e=>{importFiles(e.target.files);e.currentTarget.value='';}}/>}
    {stressMenu&&<><div className="stress-menu-dismiss" onClick={()=>setStressMenu(null)}/><div className="stress-menu" role="dialog" aria-labelledby="stress-feedback-title" style={{left:stressMenu.x,top:stressMenu.y}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();setStressMenu(null);}}}>
      <header className="stress-menu-header"><div><h2 id="stress-feedback-title">{ru?'Замечание об озвучке':'Narration feedback'}</h2><p><span>{ru?'Выделено':'Selected'}</span><strong lang={ru?'ru':undefined} dir="auto" title={stressMenu.text}>{preview(stressMenu.text,50)}</strong></p></div><button className="stress-menu-close" type="button" aria-label={ru?'Закрыть':'Close'} onClick={()=>setStressMenu(null)}><X size={16}/></button></header>
      {error&&<p className="stress-menu-error" role="alert">{error}</p>}
      {stressOptions.length?<fieldset className="stress-section"><legend>{ru?'Выберите правильное ударение':'Choose the correct stress'}</legend><div className="stress-variants">{stressOptions.map(variant=><button type="button" key={variant} aria-label={ru?`Ударение: ${variant}`:`Stress: ${variant}`} aria-pressed={respelling.normalize('NFC')===variant.normalize('NFC')} disabled={Boolean(busy)} onClick={()=>setRespelling(variant.normalize('NFC'))}><span className="stress-variant-word" lang="ru">{variant.normalize('NFC')}</span></button>)}</div></fieldset>:<p className="stress-menu-hint">{ru?'Для ударения выделите одно русское слово.':'Select one Russian word to report its stress.'}</p>}
      {stressOptions.length>0&&<form className="stress-respell" onSubmit={event=>{event.preventDefault();const form=stressCorrection(stressMenu.text,respelling);if(form)saveNarrationFeedback(stressMenu,'stress','',form);}}>
        <label><span>{ru?'Ударение или свой вариант: е → э / ё':'Stress or your own form: е → э / ё'}</span><input type="text" lang="ru" value={respelling} maxLength={200} placeholder={ru?'Выберите вариант выше или введите свой':'Choose above or type your form'} aria-label={ru?'Правильное ударение или произношение':'Correct stress or pronunciation'} onChange={event=>setRespelling(event.target.value)}/></label>
        {respelling.trim()&&!stressCorrection(stressMenu.text,respelling)&&<p className="stress-menu-hint" role="status">{ru?'Выберите ударение выше или укажите одно ударение в том же слове. Можно заменить е на э или ё.':'Choose a stress above or mark one stressed vowel in the same word. You may replace е with э or ё.'}</p>}
        <Button type="submit" size="sm" disabled={Boolean(busy)||!stressCorrection(stressMenu.text,respelling)}>{ru?'Сохранить ударение':'Save stress'}</Button>
      </form>}
      <form className="stress-other" onSubmit={event=>{event.preventDefault();if(stressMenuComment.trim())saveNarrationFeedback(stressMenu,stressMenuKind,stressMenuComment);}}>
        <div className="stress-other-heading"><strong>{ru?'Другая проблема':'Another issue'}</strong><span>{ru?'Если дело не в ударении':'If it is not a stress error'}</span></div>
        <label><span>{ru?'Тип проблемы':'Issue type'}</span><select value={stressMenuKind} onChange={event=>setStressMenuKind(event.target.value as typeof stressMenuKind)}><option value="pronunciation">{ru?'Произношение':'Pronunciation'}</option><option value="pacing">{ru?'Темп или паузы':'Tempo or pauses'}</option><option value="voice">{ru?'Голос или интонация':'Voice or intonation'}</option><option value="other">{ru?'Другое':'Other'}</option></select></label>
        <label><span>{ru?'Комментарий':'Comment'}</span><Textarea rows={2} maxLength={2000} value={stressMenuComment} onChange={event=>setStressMenuComment(event.target.value)} placeholder={ru?'Что прозвучало не так?':'What sounded wrong?'}/></label>
        <Button type="submit" size="sm" disabled={Boolean(busy)||!stressMenuComment.trim()}>{ru?'Сохранить':'Submit'}</Button>
      </form>
    </div></>}

    {sidebar&&<button className="sidebar-scrim" type="button" aria-label={t('hideSidebar')} onClick={()=>setSidebar(false)}/>}
    {sidebar&&<aside className="sidebar">
      <button className="brand" disabled={listenerMode} onClick={()=>{selectionEpoch.current++;player.stop();setBook(null);setGeneration(null);void refreshLibrary();}} aria-label={t('library')}><Column/><span>Dogma</span></button>
      <button className={cn('nav-item',!book&&'selected')} disabled={listenerMode} onClick={()=>{selectionEpoch.current++;player.stop();setBook(null);setGeneration(null);void refreshLibrary();}}><Library size={18}/>{t('library')}<span className="count">{library.length}</span></button>
      {!listenerMode&&<Button variant="outline" className="open-book-button" onClick={()=>fileInput.current?.click()}><Plus/>{t('openBook')}</Button>}
      {book?<div className="book-navigation">
        <div className="sidebar-book"><span className="eyebrow">{t('nowReading')}</span><h3>{book.title}</h3><p>{book.author}</p></div>
        <div className="nav-tabs"><button className={navTab==='chapters'?'active':''} aria-pressed={navTab==='chapters'} onClick={()=>setNavTab('chapters')}>{t('chapters')}</button>{!listenerMode&&<button className={navTab==='bookmarks'?'active':''} aria-pressed={navTab==='bookmarks'} onClick={()=>setNavTab('bookmarks')}>{t('bookmarks')}</button>}</div>
        {navTab==='chapters'?<><div className="chapter-search"><Search size={14}/><input aria-label={t('findChapterLabel')} value={chapterQuery} onChange={e=>setChapterQuery(e.target.value)} placeholder={t('findChapter')}/></div><div className="chapter-list">{!visibleChapters(book.chapters,chapterQuery).length&&<p className="small-empty">{settings?.language==='ru'?'Главы не найдены':'No matching chapters'}</p>}{visibleChapters(book.chapters,chapterQuery).map(({chapter:c,number})=>{const state=segments.progress.get(c.id);const done=Boolean(state?.total&&state.ready===state.total);return <button key={c.id} className={cn('chapter-item',chapter?.id===c.id&&'active')} aria-current={chapter?.id===c.id?'true':undefined} onClick={()=>selectChapter(c.id)}><span className="chapter-number">{String(number).padStart(2,'0')}</span><span>{c.title}</span>{done?<Check size={14} className="text-primary"/>:state?.rendering?<LoaderCircle size={14} className="spin"/>:null}</button>})}</div></>:<div className="chapter-list">{book.bookmarks.length?book.bookmarks.map(mark=>{const label=passageLabel(mark.source_id);const name=mark.note?preview(mark.note,60):label;return <div className={cn('bookmark-item',selectedSource===mark.source_id&&'active')} key={mark.id}><button onClick={()=>navigateSource(mark.source_id)} aria-current={selectedSource===mark.source_id?'true':undefined} aria-label={mark.note?preview(mark.note):label} aria-describedby={mark.note?`bookmark-${mark.id}`:undefined}><BookmarkIcon size={14}/><span>{mark.note?preview(mark.note):label}{mark.note&&<small id={`bookmark-${mark.id}`}>{label}</small>}</span></button><button aria-label={`${t('editBookmarkNote')}: ${name}`} disabled={Boolean(busy)} onClick={()=>void request({title:t('editBookmarkNote'),message:label,confirm:t('save'),input:{label:t('bookmarkNote'),value:mark.note,maxLength:2000}}).then(note=>{if(note!==null)void run(t('updatingBookmark'),async()=>{await api('update_bookmark',{id:mark.id,note});await refreshBook();});})}><Pencil size={13}/></button><button aria-label={`${t('removeBookmark')}: ${name}`} disabled={Boolean(busy)} onClick={()=>void removeWith(mark.note.trim()?{title:t('removeBookmarkTitle'),message:removeNoteText(mark.note),confirm:t('removeBookmark'),destructive:true}:null,'.bookmark-item',()=>[document.querySelector<HTMLElement>('.nav-tabs button[aria-pressed="true"]')],()=>run(t('removingBookmark'),async()=>{await api('delete_bookmark',{id:mark.id});await refreshBook();}))}><X size={13}/></button></div>;}):<p className="small-empty">{t('noBookmarks')}</p>}</div>}
      </div>:<div className="sidebar-note"><Headphones size={22}/><h3>{t('sidebarTitle')}</h3><p>{t('sidebarText')}</p></div>}
      <div className="sidebar-bottom">{!listenerMode&&<button className="nav-item" onClick={()=>openModal('settings')}><Settings2 size={17}/>{t('settings')}{health&&!health.ready&&<span className="setup-dot"/>}</button>}<div className="local-label"><ShieldCheck size={14}/>{listenerMode?(ru?'РЕЖИМ ПРОСЛУШИВАНИЯ':'LISTENING REVIEW'):t('local')}</div></div>
    </aside>}
    <main className={cn('main',book&&'has-player')}>
      <header className="topbar"><div className="flex items-center gap-3">{listenerMode&&<a href="/" target="_blank" rel="noopener" className="listener-home" title={ru?"Главная в новой вкладке":"Open Home in another tab and keep listening"}><ArrowLeft size={15}/>{ru?"Главная":"Home"}</a>}<Button variant="ghost" size="icon" title={sidebar?t('hideSidebar'):t('showSidebar')} aria-label={sidebar?t('hideSidebar'):t('showSidebar')} aria-expanded={sidebar} onClick={()=>setSidebar(!sidebar)}>{sidebar?<PanelLeftClose/>:<PanelLeftOpen/>}</Button><span className="breadcrumb">{book?<><span>{t('library')}</span><ChevronRight size={13}/><strong>{book.title}</strong></>:t('library')}</span></div><div className="flex items-center gap-2">{!runtimeLost&&health?.active&&health.active!==generation?.id&&<Button variant="ghost" size="sm" title={elsewhere} onClick={()=>void openActive()}><LoaderCircle size={14} className="spin"/>{t('preparingAudio')}{health.active_book?` · ${health.active_book.title}`:''}</Button>}<span className={cn('offline-badge',runtimeLost&&'disconnected')}><span/>{runtimeLost?t('runtimeDisconnected'):health?.illustration_active?`${t('illustratingOnline')}${health.illustration_book&&health.illustration_book.id!==book?.id?` · ${health.illustration_book.title}`:''}`:t('local')}</span>{!listenerMode&&health?.illustration_active&&<Button variant="ghost" size="sm" onClick={()=>void api('illustration_pause',{id:health.illustration_active}).then(()=>setNotice(t('illustrationStopRequested')),reason=>setError(operationMessage(reason,language)))}><Pause size={14}/>{t('stopIllustrations')}</Button>}{!listenerMode&&book&&<><Button variant="ghost" size="icon" title={t('generations')} aria-label={t('generations')} onClick={()=>openModal('generations')}><History/></Button><Button variant="outline" size="sm" onClick={()=>openModal('export')} disabled={!generation}><Download/>{t('export')}</Button></>}</div></header>
      {runtimeLost?!modal&&<div className="error-banner" role="alert"><AlertCircle size={17}/><span>{runtimeMessage}</span>{runtimeRetry}</div>:<>
      {pollError&&!error&&<div className="error-banner" role="alert"><AlertCircle size={17}/><span>{pollError}</span><button aria-label={t('dismissError')} onClick={()=>setPollErrors({health:'',generation:'',visuals:''})}><X size={16}/></button></div>}
      {error&&!modal&&<div className="error-banner" role="alert"><AlertCircle size={17}/><span>{error}</span><button aria-label={t('dismissError')} onClick={()=>setError('')}><X size={16}/></button></div>}</>}
      {notice&&!modal&&<div className="toast" role="status"><CheckCircle2 size={16}/>{notice}</div>}
      {Boolean(generation?.pending_replacements)&&!modal&&<p className="helper current-audio-updates" role="status">{ru?'Аудиокнига обновляется. Прежние отрывки остаются доступны до готовности замены.':'Audiobook updating. Existing passages remain playable until their replacements are ready.'}</p>}
      {correctionOffer&&!modal&&<div className="toast" role="status"><CheckCircle2 size={16}/>{ru?'Исправленная озвучка готова.':'Corrected narration is ready.'}<Button variant="outline" size="sm" onClick={()=>void run(t('openingGeneration'),async()=>{const target=correctionOffer;await open(target.bookId,target.generationId);setCorrectionOffer(null);})}>{ru?'Открыть новую версию':'Open updated version'}</Button><button aria-label={t('close')} onClick={()=>setCorrectionOffer(null)}><X size={16}/></button></div>}
      {!listenerMode&&!runtimeLost&&<ExternalJobs language={language}/>}
      {!book?<div className="library-page">
        <div className="page-heading"><div><span className="eyebrow">{t('localLibrary')}</span><h1>{t('library')}</h1><p>{library.length?`${count(language,library.length,'books')} · ${t('savedOnComputer')}`:t('privacy')}</p></div><Button onClick={()=>fileInput.current?.click()}><Plus/>{t('openBook')}</Button></div>
        {library.length>0&&<div className="library-tools"><div className="search-field"><Search size={17}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder={t('search')} aria-label={t('search')}/>{query&&<button onClick={()=>setQuery('')} aria-label={t('clearSearch')}><X size={15}/></button>}</div><span>{count(language,filtered.length,'books')}</span></div>}
        {!library.length?<div className="empty-library"><div className="empty-art"><div className="paper one"/><div className="paper two"/><div className="paper main-paper"><Column size={48}/><span/><span/><span/><Headphones size={23}/></div></div><h2>{t('emptyTitle')}</h2><p>{t('emptyText')}</p><Button size="lg" onClick={()=>fileInput.current?.click()}><FolderOpen/>{t('openBook')}</Button><small>{t('importHint')}</small></div>:filtered.length?<div className="book-grid">{filtered.map((item,index)=><article className="book-card" key={item.id}><button className="book-card-main" onClick={()=>void(runningAction.current?open(item.id).catch(reason=>{if(!isRuntimeLoss(reason))setError(operationMessage(reason,language));}):run(t('openingBook'),()=>open(item.id)))}><div className={cn('book-cover','cover-'+index%4)} aria-hidden="true">{item.cover?<AssetImage kind="book" id={item.cover} book={item.id} alt="" language={language}/>:<><div className="cover-rule"/><small>{item.author}</small><h3>{item.title}</h3><Column size={30}/><div className="cover-rule"/></>}</div><div className="book-card-meta"><h3>{item.title}</h3><p>{item.author}</p><div><span>{count(language,item.chapter_count,'chapters')}</span><span>{item.format.toUpperCase()}</span></div></div></button><div className="book-card-foot"><span>{item.generations.some(g=>g.id===health?.active)?`${t('preparingAudio')}…`:item.generations.length?(ru?`${t('savedGenerations')}: ${item.generations.length}`:`${item.generations.length} saved generation${item.generations.length===1?'':'s'}`):t('readyToRead')}</span><Button variant="ghost" size="icon" title={t('removeFromLibrary')} aria-label={`${t('delete')} ${item.title}`} onClick={()=>void removeWith({title:t('deleteBookTitle'),message:ru?`Удалить «${preview(item.title)}» и созданное для неё аудио из приложения? Исходный файл не будет удалён.`:`Delete “${preview(item.title)}” and its generated audio from the app? Your original file will not be deleted.`,confirm:t('delete'),destructive:true},'.book-card',()=>[document.querySelector<HTMLElement>('.page-heading button'),document.querySelector<HTMLElement>('.sidebar .nav-item')],()=>run(t('deletingBook'),async()=>{const result=await api<DeletionResult>('delete_book',{id:item.id});item.generations.forEach(g=>pauseRequest.removed(g.id));void refreshHealth().catch(()=>undefined);await refreshLibrary();setNotice(deletionNotice(result,'Book',settings?.language));}))}><Trash2/></Button></div></article>)}</div>:<div className="small-empty">{t('noResults')}</div>}
        {health&&!health.ready&&<button className="setup-callout" onClick={()=>openModal('settings')}><div className="setup-icon"><HardDrive size={20}/></div><div><strong>{t('setupNarration')}</strong><p>{t('readOnly')}</p></div><ArrowRight size={18}/></button>}
        {settings&&<HomeRecommendations settings={settings} libraryKey={libraryKey} update={update}/>}
      </div>:<>
        <div className="reader-toolbar"><div className="view-tabs">{(['read','illustrated','study'] as const).map(value=><button key={value} className={mode===value?'active':''} aria-pressed={mode===value} onClick={()=>setMode(value)}>{value==='read'?<BookOpen size={15}/>:value==='illustrated'?<ImageIcon size={15}/>:<GraduationCap size={16}/>}{t(value)}</button>)}</div><div className="flex items-center gap-1"><Button variant="ghost" size="icon" title={ru?'Оформление чтения':'Reading appearance'} aria-label={ru?'Оформление чтения':'Reading appearance'} onClick={()=>openModal('appearance')}><Type/></Button><Button variant="ghost" size="icon" title={t('bookTools')} aria-label={t('bookTools')} onClick={()=>openModal('bookTools')}><MoreHorizontal/></Button></div></div>
        <div ref={viewRef} tabIndex={0} aria-label={ru?'Текст книги':'Book text'} className={cn('reading-scroll',mode==='study'&&'study-layout',mode==='illustrated'&&'illustrated-layout')}>
          <article className="reading-column" style={{fontSize:settings?.font_size,lineHeight:settings?.line_height}}>
            <div className="chapter-kicker">{String((book.chapters.findIndex(c=>c.id===chapter?.id)??0)+1).padStart(2,'0')} <span>/ {count(language,book.chapters.length,'chapters').toLocaleUpperCase(locale(language))}</span></div>
            <h1 className={cn('chapter-title',titleSource(chapter)&&selectedSource===titleSource(chapter)&&'title-selected',titleSource(chapter)&&!player.opening&&player.segment?.source_id===titleSource(chapter)&&'title-speaking')}>{chapter?.title}</h1>
            <div className="chapter-meta"><span>{chapterSegments.length?passageProgress(language,chapterSegments.filter(s=>s.status==='ready').length,chapterSegments.length,'prepared'):t('noAudio')}</span>{generation?.status==='review'&&chapterSegments.length?<><span>·</span><button onClick={()=>openModal('narration')}>{t('reviewCast')}</button></>:generation?.status==='held'&&chapterSegments.some(s=>s.status==='review')?<><span>·</span><button onClick={()=>openModal('narration')}>{t('reviewHolds')}</button></>:generation&&resumesChapter(generation,chapterSegments)?<><span>·</span><button disabled={Boolean(busy)||Boolean(pausing)||Boolean(health?.active)} title={elsewhere||undefined} onClick={()=>void resumePreparation(generation.id)}>{t('resume')}</button></>:!(chapterSegments.length&&chapterSegments.every(s=>s.status==='ready'))&&<><span>·</span><button onClick={()=>openGenerate(chapter?.id||'','chapter')}>{t('prepareChapter')}</button></>}</div>
            {book.warnings.length>0&&<details className="import-warnings"><summary>{t('importNotes')} ({book.warnings.length})</summary>{book.warnings.map(w=><p key={w}>{localMessage(language,w)}</p>)}</details>}
            {chapter&&<ReaderChapter chapter={chapter} bookId={book.id} speaking={player.opening ? undefined : player.segment?.source_id} selected={selectedSource}
              images={imageIndex} bookmarks={bookmarked} failed={failedSources} language={settings?.language||'en'} onSelect={id=>{chapterStartIntent.current='';manualReading.current=true;setBrowsing(true);setSelectedSource(id);saveReading(id);}} onNavigate={navigateSource} onAction={passageAction} onStressSelection={openStressMenu} listener={listenerMode}/>}
            <div className="chapter-navigation"><Button variant="outline" disabled={book.chapters[0]?.id===chapter?.id} onClick={()=>{const i=book.chapters.findIndex(c=>c.id===chapter?.id);if(i>0)selectChapter(book.chapters[i-1].id);}}><ChevronLeft/>{t('previousChapter')}</Button><Button variant="outline" disabled={book.chapters.at(-1)?.id===chapter?.id} onClick={()=>{const i=book.chapters.findIndex(c=>c.id===chapter?.id);if(i<book.chapters.length-1)selectChapter(book.chapters[i+1].id);}}>{t('nextChapter')}<ChevronRight/></Button></div>
          </article>
          {mode==='illustrated'&&chapter&&<IllustratedPanel chapter={chapter} bookId={book.id} sourceId={!player.opening&&!browsing&&player.segment?.chapter_id===chapter.id?player.segment.source_id:selectedBlock?.id} images={imageIndex} language={settings?.language||'en'} onNavigate={navigateSource} onIllustrate={id=>openModal('illustration',id)}/>}
          {mode==='study'&&<aside className="study-panel"><span className="eyebrow">{ru?'ИЗУЧЕНИЕ ПО ИСХОДНИКУ':'STUDY WITH THE SOURCE'}</span><h3>{ru?'По одной идее за раз.':'One idea at a time.'}</h3><p>{ru?'Формулы, таблицы и рисунки остаются рядом с исходным текстом. Повторите отрывок или сохраните закладку.':'Equations, tables, and figures stay attached to their original text. Replay a passage or save a bookmark.'}</p><Button variant="outline" disabled={!selectedBlock||!speakable(selectedBlock)} onClick={()=>{const s=chapterSegments.find(s=>s.source_id===selectedBlock?.id);if(s)listenTo(s.id);else setNotice(ru?'Подготовьте эту главу, чтобы прослушать объяснение.':'Prepare this chapter to replay its explanation.');}}><RotateCcw/>{ru?'Повторить выбранный отрывок':'Replay selected passage'}</Button>{chapter?.blocks.filter(b=>['figure','equation','table'].includes(b.kind)).map(block=><div key={block.id} className="study-figure"><BookMarkup block={block} book={book.id} language={language} onNavigate={navigateSource}/><small>{ru?'Исходный материал':'Original source'} · {blockKindLabel(language,block.kind)}</small></div>)}<div className="study-note"><ShieldCheck size={17}/><span>{ru?'Созданные иллюстрации не являются проверенными физическими схемами. Этот режим не переписывает учебник.':'Generated illustrations are not verified physics diagrams. This view does not rewrite the textbook.'}</span></div></aside>}
        </div>
        {generation?<PlayerBar book={book} generation={generation} player={player} settings={settings}
          status={`${preparationStatus(language,generation,completed,total)}${remainingLabel}${generation.status!=='ready'&&!active(generation.status)&&health?.active&&health.active!==generation.id?` · ${t('preparingAudio')}${health.active_book?`: ${health.active_book.title}`:''}`:''}`}
          error={playerMessage(language,player.error||player.saveError||generation.error||'')} warning={playerNote.text} warningDetail={playerNote.detail}
          statusAction={generation.config.engine?.startsWith('higgs_audio_tts_q8_external')&&generation.status==='rendering'?<span><LoaderCircle size={12} className="spin"/>{ru?`Создано ${completed} из ${total}`:`Rendered ${completed} of ${total}`}</span>:<>{generation.status==='review'&&pausing!==generation.id?<button disabled={Boolean(busy)} onClick={()=>openModal('narration')}><Users size={12}/>{t('reviewCast')}</button>:pausing!==generation.id&&!active(generation.status)&&generation.analysis.complete&&player.error&&player.segment?.status==='ready'?<button disabled={Boolean(busy)||Boolean(health?.active)} onClick={()=>void repairGeneration(generation.id)}><RotateCcw size={12}/>{t('repairAudio')}</button>:(pausing===generation.id||active(generation.status))?<button disabled={Boolean(pausing)} onClick={()=>void pausePreparation(generation.id)}><Pause size={12}/>{pausing===generation.id?(settings?.language==='ru'?'Запрос паузы…':'Requesting pause…'):(settings?.language==='ru'?'Приостановить озвучку':'Pause preparation')}</button>:generation.status!=='ready'?<>{generation.status==='held'&&<button disabled={Boolean(busy)} onClick={()=>openModal('narration')}><Users size={12}/>{t('reviewHolds')}</button>}{failedSegment&&<button onClick={()=>navigateSource(failedSegment.source_id)}><ArrowRight size={12}/>{t('goToPassage')}</button>}<button disabled={Boolean(pausing)||Boolean(health?.active)} title={elsewhere||undefined} onClick={()=>void resumePreparation(generation.id)}><Play size={12}/>{t('resume')}</button></>:<span><Check size={12}/>{t('savedLocally')}</span>}</>}
          onToggle={togglePlayback} onSeekTrack={seekTrackForPlayback} onSkip={player.skip} onSelect={selectForPlayback} onSettings={()=>openModal('playback')} onHistory={()=>openModal('generations')} onUpdate={update}
          showFollow={browsing&&Boolean(player.segment)} onFollow={()=>{if(!settings?.follow)void update({follow:true});resumeFollowing();focusIfLost(()=>viewRef.current);}} listener={listenerMode}/>:<footer className="no-generation"><div><Headphones size={23}/><div><strong>{t('noAudio')}</strong><p>{t('noGeneration')}</p></div></div><Button onClick={()=>openModal('generate')}><Sparkles/>{t('prepare')}</Button></footer>}
      </>}
    </main>
    <Dialog open={Boolean(modal)} onOpenChange={value=>{if(!value){setModal(null);setGenerationSource('');setError('');}}}>
      <DialogContent key={modal||'closed'} closeLabel={t('close')} onCloseAutoFocus={event=>{event.preventDefault();const preferred=closeFocus.current?document.getElementById(closeFocus.current):null;closeFocus.current='';dialogFocus.restore(viewRef.current,()=>document.activeElement,preferred);}} className={cn(modal==='settings'&&'settings-dialog',(modal==='generations'||modal==='preparation'||modal==='narration')&&'wide-dialog')}>
        {modal==='preparation'&&book&&<BookPreparation book={book} language={settings?.language||'en'} onChapter={id=>{selectChapter(id);setModal(null);}}/>}
        {modal==='bookTools'&&<BookTools language={settings?.language||'en'} onAction={openModal}/>}
        {modal==='stressReports'&&<><DialogHeader title={ru?'Замечания об озвучке':'Narration feedback'} description={ru?'Правки ударения сохраняются для всех последующих озвучек. Для имён обновляются все падежные формы; исходное замечание и аудио остаются в истории.':'Stress corrections carry into future generations. Name corrections cover every case form; original reports and audio stay in history.'}/><div className="modal-scroll stress-reports">
          {stressReports.length?stressReports.map(report=><article key={report.id}><strong>«{report.selected_text}»</strong><small>{readerIndex.chapters.get(report.chapter_id)?.title||report.chapter_id} · {new Date(report.created).toLocaleString(language)}</small>
            {report.retake_queue&&<p role={report.retake_queue.status==='held'?'alert':'status'}>{ru?'Повторная озвучка':'Correction audio'}: {report.retake_queue.phase==='awaiting_render_completion'?(ru?'ожидает завершения текущей озвучки; затем запустится автоматически':'waiting for current render; correction starts automatically afterwards'):({queued:ru?'в очереди':'queued',running:ru?'создаётся':'rendering',applied:ru?'опубликована':'published',held:ru?'остановлена — требуется исправление':'held — needs attention'} as Record<string,string>)[report.retake_queue.status]||report.retake_queue.status}{report.retake_queue.targets?` · ${report.retake_queue.applied||0}/${report.retake_queue.targets}`:''}{report.retake_queue.error&&` · ${report.retake_queue.error}`}</p>}
            {report.intended_stress&&<p>{ru?'Ожидаемое ударение':'Expected stress'}: <strong>{report.intended_stress}</strong></p>}<p>{preview(report.passage_text,180)}</p>{report.comment&&<p>{report.comment}</p>}<small>{report.kind||'stress'} · {ru?'Озвучка':'Generation'}: {book?.generations.find(g=>g.id===report.generation_id)?.name||report.generation_id} · {report.audio_offset===null?(ru?'время не зафиксировано':'time not captured'):`${report.audio_offset.toFixed(1)} s`}</small>
            <div><Button variant="outline" size="sm" disabled={!report.source_id} onClick={()=>{setModal(null);navigateSource(report.source_id);if(generation?.id===report.generation_id)player.select(report.segment_id,true,report.audio_offset||0);}}>{ru?'Открыть место':'Open passage'}</Button></div></article>):<p className="helper">{ru?'Замечаний пока нет. Выделите слово в тексте и нажмите правой кнопкой мыши.':'No reports yet. Select words in the book and right-click.'}</p>}
        </div></>}
        {modal==='playback'&&<PlaybackSettings settings={settings} sleepAt={player.sleepAt} onSleep={player.setSleepAt} update={update} music={music.status} onRetryMusic={()=>void music.retry()} opening={Boolean(generation?.config.opening)} attribution={generation?.config.opening?.attribution||health?.music?.attribution||''}/>}
        {modal==='appearance'&&<><DialogHeader title={t('appearance')} description={ru?'Размер текста, тема и следование за озвучкой.':'Text size, theme, and following narration.'}/><div className="modal-scroll"><ReadingPreferences settings={settings} update={update} showHeading={false}/></div></>}
        {modal==='settings'&&<><DialogHeader title={t('settings')} description={ru?'Настройки чтения и локальная среда. Для локальной озвучки не нужна учётная запись.':'Reading preferences and local runtime. Local narration needs no account.'}/><div className="modal-scroll"><ReadingPreferences settings={settings} update={update}/><div className="settings-section"><h3>{t('models')}</h3><p>{ru?'Выберите запечатанный автономный комплект с локальной текстовой моделью, речевой моделью Higgs и профилями голосов. Ничего не загружается автоматически.':'Choose a sealed offline pack containing a local text model, Higgs speech model, and voice profiles. Nothing downloads automatically.'}</p><div className="flex gap-2"><Input value={packPath} onChange={e=>setPackPath(e.target.value)} placeholder={ru?'Путь к папке с pack.json':'Path to folder containing pack.json'} aria-label={ru?'Папка комплекта моделей':'Model pack folder'}/>{native()&&<Button variant="outline" size="icon" title={t('chooseFolder')} onClick={()=>void run(t('selectingPack'),async()=>{const path=await choosePack();if(path)setPackPath(path);})}><FolderOpen/></Button>}</div><Button variant="outline" onClick={()=>void run(t('checkingPack'),verifyPack)} disabled={Boolean(busy)}><CheckCheck/>{t('verify')}</Button><div className={cn('runtime-status',health?.ready?'healthy':'')}><span className="flex gap-2 items-center">{health?.ready?<CheckCircle2 size={16}/>:<AlertCircle size={16}/>}<strong>{!health?`${t('checkingPack')}…`:health.ready?health.pack?.name:(ru?'Среда озвучки не готова':'Narration runtime is not ready')}</strong></span>{health?.pack_error&&<p>{localMessage(language,health.pack_error)}</p>}{health?.runtime_error?<p>{localMessage(language,health.runtime_error)}</p>:Boolean(health?.missing.length)&&<p>{ru?'Отсутствуют модули среды: ':'Missing runtime modules: '}{health?.missing.join(', ')}</p>}<small>FFmpeg: {health?.ffmpeg?(ru?'доступен':'available'):(ru?'отсутствует':'missing')} · FFprobe: {health?.ffprobe?(ru?'доступен':'available'):(ru?'отсутствует':'missing')} · {ru?'Выравнивание слов':'Word aligner'}: {health?.word_alignment?(ru?'доступно':'available'):(ru?'не установлено':'not installed')}</small></div><label className="setting-row"><span>{ru?'Устройство вычислений':'Compute device'}</span><select value={settings?.device} onChange={e=>void update({device:e.target.value as Settings['device']})}><option value="auto">{ru?'Автоматически':'Automatic'}</option><option value="cuda">NVIDIA CUDA</option><option value="mps">Apple MPS · {ru?'не проверено':'unverified'}</option><option value="cpu">CPU</option></select></label><label className="setting-row"><span>{t('characterAnalysis')}</span><select value={settings?.character_analysis||'auto'} onChange={e=>void update({character_analysis:e.target.value as Settings['character_analysis']})}><option value="auto">{t('analysisAuto')}</option><option value="local">{t('analysisLocal')}</option><option value="codex_cli">{t('analysisCodex')}</option><option value="claude_cli">{t('analysisClaude')}</option></select></label><p className="text-xs">{t('characterAnalysisHelp')}</p><label className="setting-row"><span>{t('tieBreaker')}</span><select value={settings?.speaker_tie_breaker||'none'} onChange={e=>void update({speaker_tie_breaker:e.target.value as Settings['speaker_tie_breaker']})}><option value="none">{t('tieBreakerNone')}</option><option value="codex_cli">{t('tieBreakerCodex')}</option><option value="claude_cli">{t('tieBreakerClaude')}</option></select></label><p className="text-xs">{t('tieBreakerHelp')}</p><label className="setting-row"><span>{t('unresolvedPolicy')}</span><select value={settings?.unresolved_policy||'hold_flagged_segments'} onChange={e=>void update({unresolved_policy:e.target.value as Settings['unresolved_policy']})}><option value="hold_flagged_segments">{t('unresolvedHold')}</option><option value="render_with_warning">{t('unresolvedPreview')}</option></select></label><p className="text-xs">{t('unresolvedPolicyHelp')}</p><Toggle label={ru?'Выравнивание по словам':'Word-level alignment'} checked={settings?.align_words||false} disabled={Boolean(health&&!health.word_alignment&&!settings?.align_words)} onChange={align_words=>void update({align_words})}/><p className="text-xs">{health&&!health.word_alignment?t('alignmentUnavailable'):ru?'Требуется отдельно упакованный модуль выравнивания. Субтитры по отрывкам доступны и без него.':'Requires a separately packaged aligner. Passage subtitles remain available without it.'}</p><Toggle label={t('castReviewSetting')} checked={settings?.cast_review!==false} onChange={cast_review=>void update({cast_review})}/><p className="text-xs">{t('castReviewHelp')}</p></div><div className="settings-section"><h3>{ru?'Необязательные сетевые иллюстрации':'Optional online illustrations'}</h3><Toggle label={ru?'Разрешить явные запросы на создание изображений':'Allow explicit image-generation requests'} checked={settings?.online_images||false} onChange={online_images=>void update({online_images})}/><p>{ru?'По умолчанию выключено. Codex использует существующий вход CLI, а отдельный Images API — ключ только для одного запроса. Абзацы отправляются лишь после подтверждения. Озвучка остаётся локальной.':'Off by default. Codex uses your existing CLI sign-in; the separate Images API uses a key only for its request. Selected paragraphs are sent only after approval. Narration stays local.'}</p></div><div className="settings-section"><h3>{t('recommendationsSettingTitle')}</h3><Toggle label={t('recommendationsSetting')} checked={settings?.online_recommendations||false} onChange={online_recommendations=>void update({online_recommendations})}/><p>{t('recommendationsConsent')}</p></div><div className="storage-row"><HardDrive size={20}/><div><strong>{t('storage')}</strong><p>{bytes(health?.storage_bytes||0,language)} · {ru?'Без автоматического удаления аудио':'No automatic audio deletion'}</p></div></div></div></>}
        {modal==='generate'&&book&&<><DialogHeader title={t('newGeneration')} description={ru?'Проанализируйте всю книгу для согласованного подбора голосов, затем подготовьте речь для выбранных глав.':'Analyse the whole book for consistent casting, then prepare speech for the selected chapters.'}/><div className="modal-body"><label>{ru?'Название озвучки':'Generation name'}<Input value={generationName} onChange={e=>setGenerationName(e.target.value)} placeholder={defaultGenerationName}/></label><label>{t('chapters')}<select value={scope} onChange={e=>setScope(e.target.value)}><option value="all">{ru?'Основной порядок чтения':'Main reading order'} · {defaultNarrationChapters(book).length}</option><option value="chapter">{t('thisChapter')} · {generationChapterTitle}</option><option value="everything">{ru?'Все озвучиваемые разделы, включая примечания и оглавление':'All narratable sections, including notes and contents'}</option><option value="selected">{ru?'Выбрать разделы…':'Choose sections…'}</option></select></label>{scope==='selected'&&<fieldset className="chapter-selection"><legend>{ru?'Разделы для озвучки':'Sections to narrate'}</legend>{book.chapters.map(c=><label key={c.id}><input type="checkbox" checked={selectedChapters.includes(c.id)} disabled={!hasNarratableText(c)} onChange={event=>setSelectedChapters(current=>event.target.checked?[...current,c.id]:current.filter(id=>id!==c.id))}/><span>{c.title}{c.default_narration===false&&<small> · {ru?'по умолчанию только чтение':'reading only by default'}</small>}</span></label>)}</fieldset>}<p className="helper">{ru?`Выбрано разделов: ${narrationSelection(book,scope,generationChapter,selectedChapters).length}. Оглавление и нелинейные примечания остаются доступными для чтения и озвучиваются только при выборе.`:`${count(language,narrationSelection(book,scope,generationChapter,selectedChapters).length,'sections')} selected. Contents and non-linear notes stay readable; they are not narrated unless selected.`}</p><div className="info-box"><Users size={20}/><div><strong>{health?.pack?.name||(ru?'Локальный комплект не выбран':'No local pack selected')}</strong><p>{ru?'Голоса персонажей назначаются автоматически. Готовые отрывки остаются доступны, пока новые части заменяют их по мере готовности.':'Character voices are assigned automatically. Existing passages remain playable while ready replacements update this audiobook.'}{settings?.cast_review!==false?(ru?' Перед озвучкой вы сможете проверить роли.':' You can review the cast before narration starts.'):''}</p></div></div>{elsewhere&&<p className="helper">{elsewhere}</p>}{!health&&<p className="helper">{t('checkingPack')}…</p>}{health&&!health.ready&&<p className="validation-error">{ru?'Перед созданием настройте локальный комплект моделей. Чтение и существующее аудио продолжат работать.':'Set up the local model pack in Settings before generating. Reading and existing saved audio still work.'}</p>}<div className="dialog-actions"><Button variant="outline" onClick={()=>setModal('settings')}><Settings2/>{t('settings')}</Button><Button disabled={!health?.ready||Boolean(busy)||Boolean(health?.active)||!narrationSelection(book,scope,generationChapter,selectedChapters).length} onClick={()=>void startGeneration()}>{busy?<LoaderCircle className="spin"/>:<Sparkles/>}{t('prepare')}</Button></div></div></>}
        {modal==='generations'&&book&&<><DialogHeader title={t('generations')} description={ru?'Текущая аудиокнига. Изменённые отрывки заменяются по мере готовности.':'The current audiobook. Changed passages are replaced as they become ready.'}/><div className="modal-scroll generation-list">{book.generations.length?savedGenerations.map(g=><div key={g.id} className={cn('generation-card',generation?.id===g.id&&'current')}><div className="generation-card-heading"><div><h3>{g.name}{generation?.id===g.id&&<span className="tag">{ru?'Выбрано':'Selected'}</span>}</h3><p>{new Date(g.created).toLocaleString(locale(language))} · {g.config.pack_name}</p></div><span className={cn('status-pill',g.status)}>{progressStatus(g)}</span></div><div className="generation-stats"><span>{generation?.id===g.id&&stageProgress(generation)?preparationStatus(language,generation,completed,total):`${g.progress.ready}/${count(language,g.progress.total,'passages')}`}</span>{(generation?.id===g.id?heldTotal:g.progress.held??0)>0&&<span>{heldLabel(language,generation?.id===g.id?heldTotal:g.progress.held??0)}</span>}<span>{duration(g.progress.duration)} {ru?'сохранённого аудио':'saved audio'}</span><span>{count(language,g.config.chapters.length,'chapters')}</span></div><div className="thin-progress"><span style={{width:`${generation?.id===g.id?preparationPercent(generation):g.progress.total?g.progress.ready/g.progress.total*100:0}%`}}/></div>{g.error&&<p className="validation-error">{localMessage(language,g.error)}</p>}<div className="generation-actions"><Button size="sm" variant="outline" onClick={()=>generation?.id===g.id?setModal(null):void run(t('openingGeneration'),async()=>{const panel=modalRevision.current;await selectGeneration(g.id);closeModal(panel);})}><Headphones/>{ru?'Открыть':'Open'}</Button><Button size="icon" variant="ghost" aria-label={`${ru?'Проверить и восстановить':'Check and repair'} ${g.name}`} disabled={active(g.status)||g.status==='review'||Boolean(busy)||Boolean(health?.active)} title={elsewhere||(ru?'Проверить и восстановить аудио':'Check and repair audio')} onClick={()=>void repairGeneration(g.id)}><RotateCcw/></Button><Button size="icon" variant="ghost" title={t('rename')} aria-label={`${t('rename')} ${g.name}`} disabled={Boolean(busy)} onClick={()=>void request({title:t('renameGenerationTitle'),message:g.name,confirm:t('save'),input:{label:t('generationName'),value:g.name,maxLength:200,required:true}}).then(value=>{const rename=generationRename(value,g.name);if(!rename)return;if('invalid' in rename){setError(t('generationNameRequired'));return;}void run(t('renamingGeneration'),async()=>{const next=await api<Generation>('rename_generation',{id:g.id,name:rename.name});setGeneration(current=>acceptGenerationRefresh(current,next));await refreshBook();});})}><Pencil/></Button><Button size="icon" variant="ghost" title={t('delete')} aria-label={`${t('delete')} ${g.name}`} disabled={active(g.status)} onClick={()=>void removeWith({title:t('deleteGenerationTitle'),message:ru?`Удалить «${preview(g.name)}»? Аудио, используемое другими озвучками, сохранится.`:`Delete “${preview(g.name)}”? Audio shared with other generations will be kept.`,confirm:t('delete'),destructive:true},'.generation-card',()=>[document.querySelector<HTMLElement>('[role="dialog"] .dialog-actions button'),document.querySelector<HTMLElement>('[role="dialog"] .dialog-close')],()=>deleteGeneration(g))}><Trash2/></Button></div></div>):<div className="small-empty"><History size={30}/><p>{ru?'Сохранённых озвучек пока нет.':'No saved generations yet.'}</p></div>}</div><div className="dialog-actions"><Button onClick={()=>openModal('generate')}><Plus/>{t('newGeneration')}</Button></div></>}
        {modal==='narration'&&book&&(generation?<CastPanel generation={generation} chapters={book.chapters} passages={readerIndex.sources} language={language} busy={Boolean(busy)} blocked={castBlocked}
          previewing={player.previewing&&player.playing?player.segment?.id||'':''} onPreview={previewSegment} onNavigate={id=>{closeFocus.current=id;setModal(null);navigateSource(id);}} onRename={renameCharacter} onMerge={mergeCharacter}
          onVoice={(character,voice_id)=>void castChange(t('updatingCast'),'cast_voice',{character_id:character.id,voice_id})} onNameStress={(character,stressed)=>void castChange(t('updatingCast'),'cast_name_stress',{character_id:character.id,stressed})} onKind={(character,kind)=>void castChange(t('updatingCast'),'cast_kind',{character_id:character.id,kind})} onNarrator={(chapter_id,voice_id)=>void castChange(t('updatingCast'),'cast_narrator',{chapter_id,voice_id})}
          onStart={()=>void castChange(t('startingNarration'),'start_narration',{},true)} onReview={segment=>{setEditSegment({...segment});setRetake(false);openModal('correct');}}/>:<><DialogHeader title={t('narration')}/><div className="small-empty"><Users size={32}/><p>{t('noGeneration')}</p></div></>)}
        {modal==='export'&&generation&&book&&<><ExportPanel key={generation.id} book={book} generation={generation} removeAction={removeWith} chapterId={chapter?.id||''} busy={Boolean(busy)} exporting={exporting} onCancel={cancelExport} audioTools={Boolean(health?.ffmpeg&&health?.ffprobe)} language={settings?.language||'en'} onExport={(format,chapters)=>void exportAudio(format,chapters)} onSave={item=>void run(t('savingExport'),async()=>{await saveExport(item,book.title);})} onDelete={item=>run(t('deletingExport'),async()=>{const result=await api<DeletionResult>('delete_export',{id:item.id});const next=await api<Generation>('generation',{id:generation.id});setGeneration(current=>acceptGenerationRefresh(current,next));setNotice(deletionNotice(result,'Export',settings?.language));})}/>{heldTotal>0&&<p className="validation-error" role="note">{t('exportHeld')}</p>}</>}
        {modal==='correct'&&editSegment&&generation&&<><DialogHeader title={t('correct')} description={ru?'Правка обновляет эту аудиокнигу. Прежний отрывок доступен до готовности замены.':'Updates this audiobook. The previous passage stays playable until its replacement is ready.'}/><div className="modal-body"><label>{ru?'Отрывок':'Passage'}<select value={editSegment.id} onChange={e=>{const s=generation.segments.find(s=>s.id===e.target.value);if(s){setEditSegment({...s});setRetake(false);}}}>{generation.segments.filter(s=>s.source_id===editSegment.source_id).map(s=><option key={s.id} value={s.id}>{s.text.slice(0,80)}</option>)}</select></label><div className="original-quote">{editSegment.text}</div>{editNote.text&&<p className="passage-warning" role="note" title={editNote.detail||undefined}>{editNote.text}</p>}{editMixed&&<div className="analysis-parts-box"><small>{t('analysisParts')}</small><ul className="analysis-parts">{(editOriginal?.performance?.analysis_parts||[]).map((part,index)=>{const shown=editSegment.performance?.analysis_parts?.[index]?.speaker??part.speaker;const confirmPart=confirmPartRequest(editOriginal,editSegment,index);return <li key={`${part.start}:${index}`}>{part.text}<small>{part.speaker==='unknown'?(ru?'Не определён':'Unresolved'):speakerNames.get(part.speaker)??part.speaker} · {deliveryLabel(language,part.delivery)}</small>{editParts&&<div className="part-verdict"><select aria-label={`${t('partSpeaker')} ${index+1}`} value={shown} disabled={Boolean(busy)} onChange={e=>setEditSegment(withPartSpeaker(editSegment,index,e.target.value))}>{generation.cast.map(c=><option key={c.id} value={c.id}>{speakerNames.get(c.id)??characterName(language,c)}</option>)}<option value="unknown">{ru?'Не определён':'Unresolved'}</option></select><Button size="sm" variant="outline" aria-label={`${t('confirmPart')} ${index+1}`} disabled={Boolean(busy)||active(generation.status)||!generation.analysis.complete||!confirmPart} onClick={()=>{if(confirmPart)void reviseSegment(t('confirmingSpeaker'),[confirmPart]);}}><CheckCircle2/>{t('confirmPart')}</Button></div>}</li>;})}</ul>{editParts&&<p className="helper">{t('partVerdictHelp')}</p>}</div>}{editPresentation.length>0&&<div className="analysis-parts-box presentation-review"><small>{t('presentationReview')}</small><ul className="analysis-parts">{editPresentation.map((binding,index)=>{const part=binding.part??null;const confirm=confirmPresentationRequest(editOriginal,editSegment,part);return <li key={`presentation:${part}:${index}`}>{part===null?(ru?'Весь отрывок':'Whole passage'):`${ru?'Часть':'Part'} ${part+1}`}<small>{presentationLine(language,binding,generation.cast)}</small><div className="part-verdict"><Button size="sm" variant="outline" aria-label={part===null?t('confirmPresentation'):`${t('confirmPresentation')} ${part+1}`} disabled={Boolean(busy)||active(generation.status)||!generation.analysis.complete||!confirm} onClick={()=>{if(confirm)void reviseSegment(t('confirmingPresentation'),[confirm]);}}><CheckCircle2/>{t('confirmPresentation')}</Button></div></li>;})}</ul><p className="helper">{t('verdictsSeparate')}</p></div>}<label>{ru?'Произносимый текст / произношение':'Spoken text / pronunciation'}<Textarea rows={4} value={editSegment.spoken} disabled={editMixed} onChange={e=>setEditSegment({...editSegment,spoken:e.target.value})}/></label>{editMixed&&<p className="helper">{t('mixedSpokenLocked')}</p>}<div className="two-fields"><label>{ru?'Говорящий':'Speaker'}<select value={editSegment.speaker} onChange={e=>setEditSegment({...editSegment,speaker:e.target.value})}>{generation.cast.map(c=><option key={c.id} value={c.id}>{speakerNames.get(c.id)??characterName(language,c)}</option>)}<option value="unknown">{ru?'Не определён':'Unresolved'}</option></select></label><label>{ru?'Подача':'Delivery'}<select value={editSegment.delivery} disabled={editMixed} onChange={e=>setEditSegment({...editSegment,delivery:e.target.value})}>{deliveries.map(d=><option value={d} key={d}>{deliveryLabel(language,d)}</option>)}</select></label></div><label className="consent"><input type="checkbox" checked={retake} onChange={e=>setRetake(e.target.checked)}/><span>{t('newTake')}</span></label><div className="dialog-actions">{editSpeakerHeld&&<Button variant="outline" disabled={Boolean(busy)||active(generation.status)||!generation.analysis.complete||!confirmChange} onClick={()=>{if(confirmChange)void reviseSegment(t('confirmingSpeaker'),[confirmChange]);}}><CheckCircle2/>{t('confirmSpeaker')}</Button>}<Button disabled={Boolean(busy)||active(generation.status)||!generation.analysis.complete||!saveChanges} onClick={()=>{if(saveChanges)void reviseSegment(t('savingCorrection'),saveChanges);}}><Check/>{ru?'Сохранить правку':'Save correction'}</Button></div>{(editSpeakerHeld||editHeld&&editOriginal?.speaker==='unknown')&&<p className="helper">{!editOriginal||editOriginal.speaker==='unknown'?t('confirmSpeakerUnknown'):confirmChange?t(editMixed?'confirmSpeakerMixed':'confirmSpeakerHelp'):t('confirmSpeakerChanged')}</p>}{partChanges.length>0&&Boolean(wholeChange)&&<p className="validation-error">{t('partAndWholeMixed')}</p>}{active(generation.status)?<p className="helper">{ru?'Приостановите подготовку перед исправлением.':'Pause preparation before making corrections.'}</p>:!generation.analysis.complete?<p className="helper">{t('correctionNeedsAnalysis')}</p>:!editSegment.spoken.trim()?<p className="validation-error">{t('spokenRequired')}</p>:!retake&&!partChanges.length&&!correctionChanged(generation.segments.find(s=>s.id===editSegment.id),editSegment)&&<p className="helper">{t('correctionUnchanged')}</p>}</div></>}
        {modal==='illustration'&&book&&selectedBlock&&<IllustrationPanel key={book.id+':'+selectedBlock.id} book={book} removeAction={removeWith} sourceId={selectedBlock.id} enabled={settings?.online_images||false} codex={health?.codex} language={settings?.language||'en'} onVisuals={updateVisuals} onNavigate={id=>{closeFocus.current=id;setModal(null);navigateSource(id);}} onSettings={()=>setModal('settings')} activeRun={health?.illustration_active||null} activeTitle={health?.illustration_book?.title||''} onRunChange={()=>void refreshHealth().catch(()=>undefined)}/>}

        {notice&&<div className="modal-notice" role="status"><CheckCircle2 size={16}/>{notice}</div>}
        {busy&&<div className="modal-busy" role="status"><LoaderCircle size={14} className="spin"/>{busy}… {t('panelContinues')}{queued||nativeQueued?` ${t('booksQueued')}`:''}</div>}
        {runtimeLost?<div className="modal-error" role="alert"><AlertCircle size={16}/><span>{runtimeMessage}</span>{runtimeRetry}</div>:error&&<div className="modal-error" role="alert"><AlertCircle size={16}/>{error}</div>}
      </DialogContent>
    </Dialog>
    <AskDialog request={ask} onDone={value=>{ask?.resolve(value);setAsk(null);}}/>
    {busy&&!modal&&<div className="busy-pill" role="status"><LoaderCircle size={15} className="spin"/>{busy}…{queued||nativeQueued?` ${t('booksQueued')}`:''}{exporting&&<button onClick={cancelExport}>{t('cancelExport')}</button>}</div>}
  </div>;
}
