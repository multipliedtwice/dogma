import {useState, type FormEvent} from 'react';
import {Dialog, DialogContent, DialogHeader} from './ui/dialog';
import {Button} from './ui/button';
import {Input} from './ui/input';

export type AskOptions = {title: string; message: string; confirm: string; input?: {label: string; value: string; maxLength?: number; required?: boolean}; destructive?: boolean};
export type RemoveAction = (options: AskOptions | null, row: string, fallbacks: () => Array<HTMLElement | null>, remove: () => Promise<unknown>) => Promise<void>;
export type AskRequest = AskOptions & {cancel: string; close: string; opener: HTMLElement | null; fallback: HTMLElement | null; resolve: (value: string | null) => void};

function AskContent({request, onDone}: {request: AskRequest; onDone: (value: string | null) => void}) {
  const [value, setValue] = useState(request.input?.value ?? '');
  const blank = Boolean(request.input?.required && !value.trim());
  const submit = (event: FormEvent) => { event.preventDefault(); if (!blank) onDone(request.input ? value : ''); };
  return <DialogContent closeLabel={request.close} className="ask-dialog" onCloseAutoFocus={event => {
    event.preventDefault();
    const target = request.opener?.isConnected ? request.opener : request.fallback;
    if (target?.isConnected) target.focus({preventScroll: true});
  }}>
    <DialogHeader title={request.title} description={request.message}/>
    <form className="modal-body" onSubmit={submit}>
      {request.input && <label>{request.input.label}<Input autoFocus value={value} maxLength={request.input.maxLength} required={request.input.required} onChange={event => setValue(event.target.value)}/></label>}
      <div className="dialog-actions">
        <Button type="button" variant="outline" autoFocus={Boolean(request.destructive)} onClick={() => onDone(null)}>{request.cancel}</Button>
        <Button type="submit" disabled={blank} variant={request.destructive ? 'destructive' : 'default'} autoFocus={!request.input && !request.destructive}>{request.confirm}</Button>
      </div>
    </form>
  </DialogContent>;
}

export function AskDialog({request, onDone}: {request: AskRequest | null; onDone: (value: string | null) => void}) {
  return <Dialog open={Boolean(request)} onOpenChange={open => { if (!open) onDone(null); }}>
    {request && <AskContent request={request} onDone={onDone}/>}
  </Dialog>;
}
