import * as React from 'react';
import {cn} from '../../lib/utils';
export const Input=React.forwardRef<HTMLInputElement,React.InputHTMLAttributes<HTMLInputElement>>(({className,...props},ref)=><input ref={ref} className={cn('flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',className)} {...props}/>);
Input.displayName='Input';
export function Textarea({className,...props}:React.TextareaHTMLAttributes<HTMLTextAreaElement>){return <textarea className={cn('w-full rounded-md border border-input bg-background p-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',className)} {...props}/>}
