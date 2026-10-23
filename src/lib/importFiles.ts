export async function importBooks<T>(files: File[], upload: (file: File) => Promise<T>, describe: (reason: unknown) => string = reason => reason instanceof Error ? reason.message : String(reason), rejected = 'choose a nonempty EPUB or FB2 of at most 50 MiB.') {
  const imported: T[] = [];
  const errors: string[] = [];
  for (const file of files) {
    if (!/\.(epub|fb2)$/i.test(file.name) || !file.size || file.size > 50 * 1024 * 1024) {
      errors.push(`${file.name}: ${rejected}`); continue;
    }
    try { imported.push(await upload(file)); }
    catch (error) { errors.push(`${file.name}: ${describe(error)}`); }
  }
  return {imported, errors};
}

const isFile = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes('Files');
const editable = (target: EventTarget | null) => typeof Element !== 'undefined' && target instanceof Element
  && Boolean(target.closest('input:not([type=file]),textarea,[contenteditable=""],[contenteditable="true"]'));

export function bindDropGuard(target: Window) {
  const guard = (event: DragEvent) => {
    if (!isFile(event) && editable(event.target)) return;
    event.preventDefault();
    if (event.type === 'dragover' && event.dataTransfer) event.dataTransfer.dropEffect = 'none';
  };
  target.addEventListener('dragover', guard); target.addEventListener('drop', guard);
  return () => { target.removeEventListener('dragover', guard); target.removeEventListener('drop', guard); };
}

export function bindBookDrop(target: Window, handlers: {blocked: () => boolean; hover: (value: boolean) => void; drop: (files: File[]) => void; rejected: () => void}) {
  let depth = 0;
  const reset = () => { depth = 0; handlers.hover(false); };
  const enter = (event: DragEvent) => { if (isFile(event)) { event.preventDefault(); depth++; handlers.hover(true); } };
  const over = (event: DragEvent) => { if (isFile(event)) { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = handlers.blocked() ? 'none' : 'copy'; } };
  const leave = () => { depth = Math.max(0, depth - 1); if (!depth) handlers.hover(false); };
  const drop = (event: DragEvent) => {
    if (!isFile(event)) return;
    event.preventDefault(); reset();
    if (handlers.blocked()) handlers.rejected();
    else handlers.drop(Array.from(event.dataTransfer?.files || []));
  };
  target.addEventListener('dragenter', enter); target.addEventListener('dragover', over);
  target.addEventListener('dragleave', leave); target.addEventListener('drop', drop);
  target.addEventListener('dragend', reset); target.addEventListener('blur', reset);
  return () => {
    target.removeEventListener('dragenter', enter); target.removeEventListener('dragover', over);
    target.removeEventListener('dragleave', leave); target.removeEventListener('drop', drop);
    target.removeEventListener('dragend', reset); target.removeEventListener('blur', reset);
  };
}
