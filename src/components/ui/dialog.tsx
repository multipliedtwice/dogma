import * as React from 'react';
import * as Primitive from '@radix-ui/react-dialog';
import {X} from 'lucide-react';
import {cn} from '../../lib/utils';
export const Dialog=Primitive.Root;
export const DialogTitle=Primitive.Title;
export const DialogDescription=Primitive.Description;
export function DialogContent({children,className,closeLabel='Close',...props}:React.ComponentPropsWithoutRef<typeof Primitive.Content>&{closeLabel?:string}){return <Primitive.Portal><Primitive.Overlay className="dialog-overlay"/><Primitive.Content className={cn('dialog-content',className)} {...props}>{children}<Primitive.Close className="dialog-close" aria-label={closeLabel}><X size={18}/></Primitive.Close></Primitive.Content></Primitive.Portal>}
export function DialogHeader({title,description}:{title:string;description?:string}){return <div className="dialog-header"><DialogTitle>{title}</DialogTitle>{description?<DialogDescription>{description}</DialogDescription>:<DialogDescription className="sr-only">{title}</DialogDescription>}</div>}
