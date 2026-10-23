import type {Book, ExportFile} from './types';
let base='';
let token='';
let positionWrites:Promise<unknown>=Promise.resolve();
export function native() { return '__TAURI_INTERNALS__' in window; }

type ListenerLocation=Pick<Location,'href'|'pathname'|'search'>;
type ListenerDocument=Pick<Document,'querySelector'>;
export type HostedListenerConfig={enabled:boolean;base:string;target:{bookId:string;generationId:string}|null};
const BOOK_ID=/^[0-9a-f]{32}$/;
const GENERATION_ID=/^[0-9a-f]{24}$/;

/** Explicit same-origin mode for the production app behind a listener gateway. */
export function hostedListenerConfig(
  here:ListenerLocation=location,
  page:ListenerDocument|undefined=typeof document==='undefined'?undefined:document,
):HostedListenerConfig {
  const query=new URLSearchParams(here.search||'');
  const meta=(name:string)=>page?.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)?.content||'';
  const enabled=query.get('listener')==='1'||meta('rimsky-runtime-mode')==='hosted-listener';
  const bookId=query.get('book')||meta('rimsky-book-id');
  const generationId=query.get('generation')||meta('rimsky-generation-id');
  const target=BOOK_ID.test(bookId)&&GENERATION_ID.test(generationId)?{bookId,generationId}:null;
  return {enabled,base:enabled?new URL('./',here.href).href:'',target};
}

export async function connect() {
  if(native()) {
    const {invoke}=await import('@tauri-apps/api/core');
    const session=await invoke<{port:number;token:string}>('runtime_session');
    base=`http://127.0.0.1:${session.port}`;token=session.token;
  } else {
    const hosted=hostedListenerConfig();
    if(hosted.enabled){base=hosted.base;token='';return;}
    const query=new URLSearchParams(location.hash.slice(1));
    const port=Number(query.get('port')||sessionStorage.getItem('rimsky-port')||'1421');
    const savedPort=sessionStorage.getItem('rimsky-port');
    token=query.get('token')||(!savedPort||savedPort===String(port)?sessionStorage.getItem('rimsky-token'):'')||'';
    if(!token) throw new Error('Start the local runtime and open the URL printed in its terminal. No cloud service is used.');
    if(!Number.isInteger(port)||port<1||port>65535) throw new Error('Invalid local runtime port.');
    base=`http://127.0.0.1:${port}`;
    sessionStorage.setItem('rimsky-token',token);
    sessionStorage.setItem('rimsky-port',String(port));
    history.replaceState(null,'',location.pathname);
  }
}
const runtimeLoss=Symbol('runtime-loss');
const connectionListeners=new Set<()=>void>();
let runtimeLost=false;
function markRuntime(lost:boolean) {
  if(runtimeLost===lost)return;
  runtimeLost=lost;
  connectionListeners.forEach(listener=>listener());
}
export const runtimeConnection={
  subscribe:(listener:()=>void)=>{connectionListeners.add(listener);return()=>{connectionListeners.delete(listener);};},
  lost:()=>runtimeLost,
};
export class OperationError extends Error {
  code:string;details:Record<string,unknown>;
  constructor(message:string,code='',details:Record<string,unknown>={}){super(message);this.code=code;this.details=details;}
}
export function isRuntimeLoss(reason:unknown) { return reason instanceof Error&&reason.cause===runtimeLoss; }
async function request(path:string,init:RequestInit={}) {
  let response:Response;
  try {
    const url=token?base+path:base?new URL(path.replace(/^\//,''),base).href:path;
    const headers=new Headers(init.headers);
    if(token)headers.set('Authorization',`Bearer ${token}`);
    response=await fetch(url,{...init,headers});
  } catch(reason) {
    if(init.signal?.aborted) throw reason;
    markRuntime(true);
    throw new Error('The local runtime is not responding.',{cause:runtimeLoss});
  }
  if(response.status===401) {
    markRuntime(true);
    throw new Error('The local runtime session has ended.',{cause:runtimeLoss});
  }
  markRuntime(false);
  if(!response.ok) {
    const message=await response.json().catch(()=>({error:`Request failed (${response.status})`}));
    throw new OperationError(message.error||'Local operation failed.',typeof message.code==='string'?message.code:'',message.details&&typeof message.details==='object'?message.details:{});
  }
  return response;
}
export function api<T>(op:string,data:Record<string,unknown>={},signal?:AbortSignal):Promise<T> {
  const body=JSON.stringify({op,data});
  const positional=op==='save_position'||op==='save_reading_position';
  const send=async ():Promise<T> => (await request('/api',{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:positional,signal})).json();
  if(!positional)return send();
  const pending=positionWrites.then(send);
  positionWrites=pending.catch(()=>undefined);
  return pending;
}
export async function flushPositionWrites():Promise<void> {
  await positionWrites;
}
export async function uploadBook(file:File):Promise<Book> {
  if(!/\.(epub|fb2)$/i.test(file.name)||file.size>50*1024*1024) throw new Error('Choose an EPUB or FB2 of at most 50 MiB.');
  return (await request(`/import?name=${encodeURIComponent(file.name)}`,{method:'POST',body:file})).json();
}
export async function uploadVisual(file:File,book:string,source:string) {
  return (await request(`/visual?book=${encodeURIComponent(book)}&source=${encodeURIComponent(source)}`,{method:'POST',body:file})).json();
}
export async function asset(kind:string,id:string,book?:string,signal?:AbortSignal):Promise<Blob> {
  const query=new URLSearchParams({kind,id});if(book)query.set('book',book);
  return (await request('/asset?'+query,{signal})).blob();
}
export async function audioAsset(id:string,signal?:AbortSignal,progress?:()=>void):Promise<Blob> {
  return bufferedAudioAsset('audio',id,signal,progress);
}
/** Hosted playback lets the media element use HTTP ranges and its native buffer. */
export function playbackAssetUrl(id:string):string|null {
  if(token||!base)return null;
  return new URL('asset?'+new URLSearchParams({kind:'playback',id}),base).href;
}
export function playbackHlsUrl(id:string):string|null {
  if(token||!base)return null;
  return new URL('asset?'+new URLSearchParams({kind:'playback_hls',id}),base).href;
}
export async function playbackAsset(id:string,signal?:AbortSignal,progress?:()=>void):Promise<Blob> {
  return bufferedAudioAsset('playback',id,signal,progress);
}
async function bufferedAudioAsset(kind:string,id:string,signal?:AbortSignal,progress?:()=>void):Promise<Blob> {
  const response=await request('/asset?'+new URLSearchParams({kind,id}),{signal});
  progress?.();
  const reader=response.body?.getReader();
  if(!reader)return response.blob();
  const chunks:Uint8Array[]=[];
  for(;;) {
    const {done,value}=await reader.read();
    if(done)break;
    chunks.push(value);
    progress?.();
  }
  return new Blob(chunks as BlobPart[],{type:response.headers.get('Content-Type')||'audio/wav'});
}
export async function saveExport(item:ExportFile,title:string):Promise<boolean> {
  const extension=item.format==='chapters'?'zip':item.format;
  const filename=title.replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').slice(0,100)+'.'+extension;
  if(native()) {
    const [{save},{invoke}]=await Promise.all([import('@tauri-apps/plugin-dialog'),import('@tauri-apps/api/core')]);
    const destination=await save({defaultPath:filename,filters:[{name:extension.toUpperCase(),extensions:[extension]}]});
    if(!destination)return false;
    await invoke('save_export',{filename:item.file,destination});
  } else {
    const blob=await asset('export',item.id);
    const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
  }
  return true;
}
export async function choosePack():Promise<string|null> {
  if(!native())return null;
  const {open}=await import('@tauri-apps/plugin-dialog');
  const result=await open({directory:true,multiple:false,title:'Choose a local model pack'});
  return typeof result==='string'?result:null;
}
