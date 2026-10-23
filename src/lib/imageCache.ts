type Entry = {refs: number; controller: AbortController; promise: Promise<Blob>; size: number; used: number; ready: boolean};

export function createImageCache(load: (kind: string, id: string, book: string | undefined, signal: AbortSignal) => Promise<Blob>, maxBytes = 32 * 1024 * 1024) {
  const entries = new Map<string, Entry>();
  let clock = 0;
  function trim() {
    let total = [...entries.values()].reduce((bytes, entry) => bytes + entry.size, 0);
    for (const [key, entry] of [...entries].sort((a, b) => a[1].used - b[1].used)) {
      if (total <= maxBytes && entries.size <= 96) break;
      if (entry.refs) continue;
      entry.controller.abort(); entries.delete(key); total -= entry.size;
    }
  }
  function acquire(kind: string, id: string, book?: string) {
    const key = JSON.stringify([kind, id, book]);
    let entry = entries.get(key);
    if (!entry) {
      const controller = new AbortController();
      entry = {refs: 0, controller, size: 0, used: 0, ready: false, promise: Promise.resolve(new Blob())};
      const owned = entry;
      entry.promise = Promise.resolve().then(() => load(kind, id, book, controller.signal)).then(blob => {
        owned.size = blob.size; owned.ready = true; trim(); return blob;
      }, error => { if (entries.get(key) === owned) entries.delete(key); throw error; });
      entries.set(key, entry);
    }
    entry.refs++; entry.used = ++clock;
    let released = false;
    const owned = entry;
    return {promise: entry.promise, release: () => {
      if (released) return; released = true;
      owned.refs--;
      if (!owned.refs && !owned.ready) { owned.controller.abort(); if (entries.get(key) === owned) entries.delete(key); }
      trim();
    }};
  }
  return {acquire, invalidate: (kind:string,id:string,book?:string) => {const key=JSON.stringify([kind,id,book]);const entry=entries.get(key);entry?.controller.abort();entries.delete(key);}, clear: () => { for (const entry of entries.values()) entry.controller.abort(); entries.clear(); }, size: () => entries.size};
}

export function whenVisible(element: Element, callback: () => void): () => void {
  const Observer = element.ownerDocument.defaultView?.IntersectionObserver;
  if (!Observer) { callback(); return () => {}; }
  const observer = new Observer(entries => {
    if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); callback(); }
  }, {rootMargin: '400px'});
  observer.observe(element);
  return () => observer.disconnect();
}

export function observeVisibility(element: Element, callback: (visible: boolean) => void): () => void {
  const Observer = element.ownerDocument.defaultView?.IntersectionObserver;
  if (!Observer) { callback(true); return () => {}; }
  const observer = new Observer(entries => callback(entries.some(entry => entry.isIntersecting)), {
    root: element.closest('.reading-scroll'), rootMargin: '400px',
  });
  observer.observe(element);
  return () => observer.disconnect();
}
