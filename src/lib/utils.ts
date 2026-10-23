import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
export function cn(...inputs:ClassValue[]) { return twMerge(clsx(inputs)); }
export function duration(value:number) { const n=Math.max(0,Math.floor(value)); return n>=3600?`${Math.floor(n/3600)}:${String(Math.floor(n%3600/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`; }
export function bytes(value:number,language:'en'|'ru'='en') { const unit=value>=1024**3?'gigabyte':value>=1024**2?'megabyte':'kilobyte'; const digits=unit==='kilobyte'?0:1; return new Intl.NumberFormat(language==='ru'?'ru-RU':'en-US',{style:'unit',unit,minimumFractionDigits:digits,maximumFractionDigits:digits}).format(value/1024**(unit==='gigabyte'?3:unit==='megabyte'?2:1)); }
export function errorText(value:unknown) { return value instanceof Error?value.message:String(value); }
export function active(status:string) { return ['queued','analyzing','casting','rendering','aligning'].includes(status); }
