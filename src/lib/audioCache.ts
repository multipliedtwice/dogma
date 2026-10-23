export type AudioLoader = (hash: string, signal: AbortSignal, progress: () => void) => Promise<Blob>;
export type AudioLease = {ready: Promise<string>; release: () => void};
export type AudioCache = {
  /** Fetch (or reuse) a hash on behalf of a consumer. The shared fetch lives while any consumer or the prefetch window wants it. */
  acquire: (hash: string, onProgress?: () => void) => AudioLease;
  /** Replace the ordered prefetch window. Fetches start lowest priority after any consumer fetch, at most `concurrency` at once. */
  want: (hashes: string[]) => void;
  /** Hashes that should be evicted last (recently played, for instant replay). */
  keep: (hashes: Iterable<string>) => void;
  urlFor: (hash: string) => string | null;
  pin: (hash: string) => void;
  unpin: (hash: string) => void;
  subscribe: (listener: () => void) => () => void;
  stats: () => {bytes: number; count: number; hashes: string[]; loading: string[]};
  dispose: () => void;
};
type Entry = {
  hash: string; state: 'loading' | 'ready'; size: number; url: string; abort: AbortController;
  consumers: Map<symbol, () => void>; wanted: boolean; pins: number; touched: number; promise: Promise<string>;
};
type Options = {
  load: AudioLoader;
  createUrl?: (blob: Blob) => string;
  revokeUrl?: (url: string) => void;
  budgetBytes?: number;
  concurrency?: number;
  retryAfterMs?: number;
  now?: () => number;
};

const MB = 1024 * 1024;
export function defaultCacheBudget(): number {
  const nav = typeof navigator === 'undefined' ? undefined : navigator as Navigator & {deviceMemory?: number};
  const small = Boolean(nav && ((nav.deviceMemory !== undefined && nav.deviceMemory <= 2) || /Android|iPhone|iPad|iPod|Mobile/i.test(nav.userAgent || '')));
  return (small ? 60 : 120) * MB;
}
export function saveDataEnabled(): boolean {
  const nav = typeof navigator === 'undefined' ? undefined : navigator as Navigator & {connection?: {saveData?: boolean}};
  return Boolean(nav?.connection?.saveData);
}

export function createAudioCache(options: Options): AudioCache {
  const createUrl = options.createUrl ?? (blob => URL.createObjectURL(blob));
  const revokeUrl = options.revokeUrl ?? (value => URL.revokeObjectURL(value));
  const budget = options.budgetBytes ?? defaultCacheBudget();
  const concurrency = options.concurrency ?? 2;
  const retryAfter = options.retryAfterMs ?? 30000;
  const now = options.now ?? (() => Date.now());
  const entries = new Map<string, Entry>();
  const failed = new Map<string, number>();
  const listeners = new Set<() => void>();
  let kept = new Set<string>();
  let order: string[] = [];
  let tick = 0;
  let disposed = false;

  const notify = () => { for (const listener of [...listeners]) listener(); };
  const remove = (entry: Entry) => {
    if (entries.get(entry.hash) === entry) entries.delete(entry.hash);
    if (entry.url) revokeUrl(entry.url);
    entry.url = '';
  };
  const evict = () => {
    let total = 0;
    for (const entry of entries.values()) if (entry.state === 'ready') total += entry.size;
    if (total <= budget) return;
    const rank = (entry: Entry) => kept.has(entry.hash) ? 1 : entry.wanted ? 2 : 0;
    const candidates = [...entries.values()].filter(entry => entry.state === 'ready' && entry.pins === 0)
      .sort((a, b) => rank(a) - rank(b) || a.touched - b.touched);
    for (const entry of candidates) {
      if (total <= budget) break;
      total -= entry.size;
      remove(entry);
    }
  };
  const drop = (entry: Entry) => {
    if (entry.state === 'loading' && entry.consumers.size === 0 && !entry.wanted && entries.get(entry.hash) === entry) {
      entries.delete(entry.hash);
      entry.abort.abort();
    }
  };
  const pump = () => {
    if (disposed) return;
    let active = 0;
    for (const entry of entries.values()) {
      if (entry.state !== 'loading') continue;
      if (entry.consumers.size > 0) return;
      active++;
    }
    for (const hash of order) {
      if (active >= concurrency) break;
      if (entries.has(hash)) continue;
      const failedAt = failed.get(hash);
      if (failedAt !== undefined && now() - failedAt < retryAfter) continue;
      start(hash).wanted = true;
      active++;
    }
  };
  function start(hash: string): Entry {
    const entry: Entry = {hash, state: 'loading', size: 0, url: '', abort: new AbortController(),
      consumers: new Map(), wanted: false, pins: 0, touched: ++tick, promise: Promise.resolve('')};
    entries.set(hash, entry);
    entry.promise = options.load(hash, entry.abort.signal, () => { for (const progress of [...entry.consumers.values()]) progress(); }).then(blob => {
      if (disposed || entries.get(hash) !== entry) throw new Error('Audio download was cancelled.');
      entry.url = createUrl(blob);
      entry.size = blob.size;
      entry.state = 'ready';
      entry.touched = ++tick;
      failed.delete(hash);
      evict();
      queueMicrotask(() => { pump(); notify(); });
      return entry.url;
    }, reason => {
      if (entries.get(hash) === entry) {
        entries.delete(hash);
        if (entry.consumers.size === 0) failed.set(hash, now());
      }
      queueMicrotask(pump);
      throw reason;
    });
    entry.promise.catch(() => undefined);
    return entry;
  }
  return {
    acquire(hash, onProgress) {
      if (disposed) throw new Error('Audio cache is closed.');
      const entry = entries.get(hash) ?? start(hash);
      const id = Symbol();
      entry.consumers.set(id, onProgress ?? (() => undefined));
      entry.touched = ++tick;
      let released = false;
      return {
        ready: entry.state === 'ready' ? Promise.resolve(entry.url) : entry.promise,
        release() {
          if (released) return;
          released = true;
          entry.consumers.delete(id);
          drop(entry);
          queueMicrotask(pump);
        },
      };
    },
    want(hashes) {
      order = [...hashes];
      const wanted = new Set(order);
      for (const entry of [...entries.values()]) {
        entry.wanted = wanted.has(entry.hash);
        if (entry.wanted && entry.state === 'ready') entry.touched = ++tick;
        else drop(entry);
      }
      pump();
    },
    keep(hashes) { kept = new Set(hashes); },
    urlFor(hash) {
      const entry = entries.get(hash);
      if (!entry || entry.state !== 'ready') return null;
      entry.touched = ++tick;
      return entry.url;
    },
    pin(hash) { const entry = entries.get(hash); if (entry) entry.pins++; },
    unpin(hash) {
      const entry = entries.get(hash);
      if (entry && entry.pins > 0) { entry.pins--; evict(); }
    },
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    stats() {
      const all = [...entries.values()];
      return {bytes: all.reduce((sum, entry) => sum + entry.size, 0), count: all.filter(entry => entry.state === 'ready').length,
        hashes: all.filter(entry => entry.state === 'ready').map(entry => entry.hash), loading: all.filter(entry => entry.state === 'loading').map(entry => entry.hash)};
    },
    dispose() {
      disposed = true;
      for (const entry of [...entries.values()]) {
        if (entry.state === 'loading') entry.abort.abort();
        remove(entry);
      }
      entries.clear();
      listeners.clear();
      order = [];
    },
  };
}
