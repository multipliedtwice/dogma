type NativeBooksOptions = {
  listen: (notify: () => void) => Promise<() => void>;
  process: () => Promise<boolean>;
  error: (reason: unknown) => void;
  queued?: (waiting: boolean) => void;
};

export function bindNativeBooks(options: NativeBooksOptions): () => void {
  let live = true;
  let listening = false;
  let processing = false;
  let requested = true;
  let waiting = false;
  let dispose: (() => void) | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const schedule = (delay = 0) => {
    if (!live || !listening || processing || timer !== undefined) return;
    timer = setTimeout(() => {
      timer = undefined;
      void process();
    }, delay);
  };
  const process = async () => {
    if (!live || processing || !requested) return;
    requested = false;
    processing = true;
    let blocked = false;
    try {
      blocked = !await options.process();
      if (blocked) requested = true;
    } catch (reason) {
      if (live) options.error(reason);
    } finally {
      processing = false;
      if (live && blocked !== waiting) { waiting = blocked; options.queued?.(blocked); }
      if (requested) schedule(blocked ? 250 : 0);
    }
  };
  const notify = () => {
    requested = true;
    schedule();
  };
  void options.listen(notify).then(unlisten => {
    if (!live) {
      unlisten();
      return;
    }
    dispose = unlisten;
    listening = true;
    schedule();
  }, reason => {
    if (live) options.error(reason);
  });
  return () => {
    live = false;
    if (timer !== undefined) clearTimeout(timer);
    dispose?.();
  };
}
