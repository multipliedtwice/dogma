import {memo,useEffect,useLayoutEffect,useRef,useState,type MouseEvent,type KeyboardEvent} from 'react';
import DOMPurify from 'dompurify';
import {asset} from '../lib/api';
import {hydrateBookAssets} from '../lib/bookAssets';
import {createImageCache,whenVisible,observeVisibility} from '../lib/imageCache';
const imageCache=createImageCache(asset);
import type {Block} from '../lib/types';
import {translator} from '../lib/i18n';
export const AssetImage=memo(function AssetImage({kind,id,book,alt='',className='',preview=false,eager=false,language}:{kind:string;id:string;book?:string;alt?:string;className?:string;preview?:boolean;eager?:boolean;language:'en'|'ru'}) {
  const [visible,setVisible]=useState(eager);
  const [url,setUrl]=useState('');const [failed,setFailed]=useState(false);const [attempt,setAttempt]=useState(0);const ref=useRef<HTMLSpanElement>(null);
  useEffect(()=>{const node=ref.current;if(!node||eager)return;return observeVisibility(node,setVisible);},[eager]);
  useEffect(()=>{const node=ref.current;if(!node||(!eager&&!visible)){setUrl('');return;}let live=true;let object='';let release=()=>{};setUrl('');setFailed(false);
    const load=()=>{const lease=imageCache.acquire(preview?kind+'_preview':kind,id,book);release=lease.release;lease.promise.then(blob=>{if(live){object=URL.createObjectURL(blob);setUrl(object);}}).catch(()=>{if(live)setFailed(true);});};
    load();
    return()=>{live=false;release();if(object)URL.revokeObjectURL(object);};
  },[kind,id,book,preview,eager,visible,attempt]);
  return <span ref={ref} className={'asset-image '+className}>{failed?<button type="button" onClick={event=>{event.stopPropagation();setAttempt(value=>value+1);}}>{translator(language)('imageUnavailable')}</button>:url?<img src={url} alt={alt} decoding="async" onError={()=>{imageCache.invalidate(preview?kind+'_preview':kind,id,book);setFailed(true);}}/>:<span className="image-placeholder" aria-label={alt}/>}</span>;
});
export const BookMarkup=memo(function BookMarkup({block,book,language,onNavigate}:{block:Block;book:string;language:'en'|'ru';onNavigate?:(sourceId:string)=>void}) {
  const ref=useRef<HTMLDivElement>(null);
  useLayoutEffect(()=>{
    const root=ref.current;if(!root)return;
    root.replaceChildren(DOMPurify.sanitize(block.html,{RETURN_DOM_FRAGMENT:true,USE_PROFILES:{html:true,svg:true,mathMl:true},ADD_ATTR:['data-asset','data-source-link'],FORBID_TAGS:['style','script','iframe','form','object','embed','audio','video'],FORBID_ATTR:['style','src','srcset','href','xlink:href']}));
    root.querySelectorAll<HTMLElement>('[data-source-link]').forEach(link=>{link.setAttribute('role','link');link.setAttribute('tabindex','0');});
    let dispose=()=>{};const cancel=whenVisible(root,()=>{dispose=hydrateBookAssets(root,id=>asset('book_preview',id,book),translator(language)('imageMissing'));});return()=>{cancel();dispose();};
  },[block.html,book,language]);
  const navigate=(event:MouseEvent<HTMLDivElement>|KeyboardEvent<HTMLDivElement>)=>{
    if('key' in event && event.key!=='Enter' && event.key!==' ')return;
    const target=event.target instanceof Element?event.target.closest<HTMLElement>('[data-source-link]'):null;
    if(target&&ref.current?.contains(target)&&target.dataset.sourceLink&&onNavigate){event.preventDefault();event.stopPropagation();onNavigate(target.dataset.sourceLink);}
  };
  return <div ref={ref} onClick={navigate} onKeyDown={navigate}/>;
});
