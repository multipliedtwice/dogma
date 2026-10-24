import type {Book, Generation, Position, Segment, Settings, PlaybackChunk, PlaybackStreams} from './types';
import {createAudioCache, saveDataEnabled, type AudioCache, type AudioLease} from './audioCache';

import {canKeepHlsPrefix, playbackChunkFor, playbackFailureFor, playbackMediaTime, playbackSourceAt, validPlaybackChunk} from './playbackStreams';
import {chapterTimeline,timelineTarget} from './playerView';

type Anchor = Position & {start?: number; end?: number; duration?: number};

export type PlaybackSnapshot = {
  segment: Segment | null;
  position: number;
  playing: boolean;
  waiting: boolean;
  error: string;
  saveError: string;
  previewing: boolean;
  premixed?: boolean;
  streamKey?: string;
  bufferedSeconds?: number;
  buffered?: {ahead: number; loading: boolean};
  opening?: {key: string; position: number; duration: number};
};

type Binding = {bookId: string; generationId: string; chapterId: string; sourceId: string; segmentId: string};
type Services = {
  loadAudio: (hash: string, signal: AbortSignal, progress: () => void) => Promise<Blob>;
  loadPlayback?: (key: string, signal: AbortSignal, progress: () => void) => Promise<Blob>;
  playbackAssetUrl?: (key: string) => string | null;
  bindPlayback?: (audio: HTMLAudioElement, key: string, offset: number, signal: AbortSignal, onFatal: (reason: unknown) => void) => {destroy: () => void};
  savePosition: (bookId: string, value: Record<string, unknown>) => Promise<unknown>;
  notify: (state: PlaybackSnapshot) => void;
  createUrl?: (blob: Blob) => string;
  revokeUrl?: (url: string) => void;
  /** Stall timeout: loading fails only when no bytes arrive for this long. */
  loadTimeoutMs?: number;
  createAudio?: () => HTMLAudioElement;
  cache?: AudioCache;
  cacheBudgetBytes?: number;
  prefetchSeconds?: number;
  prefetchSegments?: number;
  keepPlayed?: number;
  saveData?: () => boolean;
};
type Standby = {chunk?: PlaybackChunk; attached?: boolean; segmentId: string; hash: string; element: HTMLAudioElement; ready: boolean; pinned: boolean; abort: AbortController};
const stalled = (status: string) => ['failed', 'paused', 'review', 'held'].includes(status);

export function createPlaybackController(initial: HTMLAudioElement, services: Services) {
  let audio = initial;
  let streams: PlaybackStreams | null = null;
  let streamsGeneration = '';
  let chunk: PlaybackChunk | null = null;
  let retainedHls = false;
  let playbackBinding: {destroy: () => void} | null = null;
  let buffering = false;
  let awaitingContinuation = false;
  const streaming = Boolean(services.loadPlayback);
  let book: Book | null = null;
  let generation: Generation | null = null;
  let selected: Segment | null = null;
  let binding: Binding | null = null;
  let loadedHash = '';
  let requestedHash = '';
  let lease: AudioLease | null = null;
  let pinned = '';
  let recent: string[] = [];
  let request = 0;
  let playRequest = 0;
  let playPending = false;
  let abort: AbortController | null = null;
  let wantsPlay = false;
  let loading = false;
  let position = 0;
  let error = '';
  let blockedError = '';
  let streamPreparationError = '';
  let failedSegment: {id: string; status: string; hash: string | null} | null = null;
  let provisional = false;
  let saveError = '';
  let rate = 1;
  let volume = .85;
  let disposed = false;
  let lastSave = 0;
  let loadTimer: ReturnType<typeof setTimeout> | undefined;
  let saves: Promise<unknown> = Promise.resolve();
  let opening: Position['opening'] = undefined;
  let openingActive = false;
  let previewing = false;
  let suppressPersist = false;
  let previewFrom: {id: string; position: number; provisional: boolean; openingActive: boolean} | null = null;
  let standby: Standby | null = null;
  let spare: HTMLAudioElement | null = null;
  const cache = services.cache ?? createAudioCache({
    load: (hash, signal, progress) => hash.startsWith('playback:') && services.loadPlayback ? services.loadPlayback(hash.slice(9), signal, progress) : services.loadAudio(hash, signal, progress), createUrl: services.createUrl, revokeUrl: services.revokeUrl, budgetBytes: services.cacheBudgetBytes ?? (streaming ? 24 * 1024 * 1024 : undefined),
  });
  const prefetchSeconds = services.prefetchSeconds ?? 180;
  const prefetchSegments = services.prefetchSegments ?? 12;
  const keepPlayed = services.keepPlayed ?? 10;
  const saveData = services.saveData ?? saveDataEnabled;
  const unsubscribe = cache.subscribe(() => attachStandby());
  const unpinMain = () => { if (pinned) { const hash = pinned; pinned = ''; cache.unpin(hash); } };
  const pinMain = (hash: string) => {
    if (pinned === hash) return;
    unpinMain();
    cache.pin(hash);
    pinned = hash;
  };
  const remember = (hash: string) => {
    recent = [...recent.filter(item => item !== hash), hash].slice(-(hash.startsWith('playback:') ? 2 : keepPlayed));
    cache.keep(recent);
  };

  const syncSource = () => {
    if (!chunk || !generation || !binding || openingActive || previewing) return;
    const current = playbackSourceAt(chunk, generation, audio.currentTime);
    if (!current) return;
    selected = current.segment;
    position = current.offset;
    binding = {...binding, chapterId: selected.chapter_id, sourceId: selected.source_id, segmentId: selected.id};
  };
  const sourcePosition = () => chunk ? position : binding ? audio.currentTime : position;
  const currentStreams = () => streamsGeneration === generation?.id ? streams : null;
  const selectedChunk = (segment: Segment) => generation && streaming && !openingActive && !previewing
    ? retainedHls && chunk?.parts.some(part => part.segment_id === segment.id) && validPlaybackChunk(chunk, generation) ? chunk : playbackChunkFor(currentStreams(), generation, segment)
    : null;
  const bufferedAhead = () => {
    try {
      for (let i = 0; i < (audio.buffered?.length ?? 0); i++) if (audio.buffered.start(i) <= audio.currentTime && audio.buffered.end(i) >= audio.currentTime) return audio.buffered.end(i) - audio.currentTime;
    } catch { /* A decoder can replace its TimeRanges between reads. */ }
    return 0;
  };
  const openingAsset = () => generation?.config?.opening?.variants.music;
  const emit = () => {
    if (!disposed) services.notify({segment: selected, position: openingActive ? 0 : position, ...(openingActive && opening && openingAsset() ? {opening: {key: opening.key, position, duration: openingAsset()!.duration}} : {}), playing: Boolean(binding && !audio.paused), waiting: wantsPlay && (loading || !binding || buffering), error, saveError, previewing, premixed: streaming && !openingActive && !previewing, ...(streaming ? {streamKey: chunk?.key, bufferedSeconds: bufferedAhead(), buffered: {ahead: bufferedAhead(), loading: buffering || loading || wantsPlay && !binding}} : {})});
  };
  const persist = () => {
    syncSource();
    // Analysis can publish the branded opening before any real book anchor exists.
    if (openingActive && !selected) return saves;
    const current = binding ?? (book && generation && selected ? {
      bookId: book.id, generationId: generation.id, chapterId: selected.chapter_id,
      sourceId: selected.source_id, segmentId: selected.id,
    } : null);
    if (!current || provisional || previewing || suppressPersist) return saves;
    const offset = openingActive ? 0 : sourcePosition();
    const limit = selected?.id === current.segmentId && selected.duration > 0 ? selected.duration : Infinity;
    const value = {generation_id: current.generationId, chapter_id: current.chapterId,
      source_id: current.sourceId, segment_id: current.segmentId,
      offset: Number.isFinite(offset) ? Math.min(Math.max(0, offset), limit) : 0,
      ...(opening ? {opening: {...opening, offset: openingActive && binding ? audio.currentTime : openingActive ? position : opening.offset}} : {})};
    saves = saves.then(() => services.savePosition(current.bookId, value)).then(() => {
      if (saveError) { saveError = ''; emit(); }
    }, reason => {
      saveError = 'Could not save playback position: ' + (reason instanceof Error ? reason.message : String(reason));
      emit();
    });
    return saves;
  };
  const fail = (message: string) => {
    unload();
    wantsPlay = false;
    error = message;
    failedSegment = selected && {id: selected.id, status: selected.status, hash: selected.audio_hash};
    emit();
  };
  const clearLoadTimer = () => { clearTimeout(loadTimer); loadTimer = undefined; };
  const invalidatePlay = () => { playRequest++; playPending = false; };
  const unload = () => {
    clearLoadTimer();
    request++;
    invalidatePlay();
    abort?.abort();
    abort = null;
    playbackBinding?.destroy();
    playbackBinding = null;
    lease?.release();
    lease = null;
    binding = null;
    chunk = null;
    retainedHls = false;
    buffering = false;
    loadedHash = '';
    requestedHash = '';
    loading = false;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    unpinMain();
  };
  const playLoaded = () => {
    if (playPending) return;
    const epoch = request;
    const intent = ++playRequest;
    playPending = true;
    const failed = (reason: unknown) => {
      if (disposed || epoch !== request || intent !== playRequest || !wantsPlay) return;
      playPending = false;
      wantsPlay = false;
      error = reason instanceof Error ? reason.message : String(reason);
      emit();
    };
    try {
      void audio.play().then(() => {
        if (epoch === request && intent === playRequest) playPending = false;
      }, failed);
    } catch (reason) { failed(reason); }
  };
  const ensureAudio = (startLoaded = false) => {
    const asset = openingActive ? openingAsset() : undefined;
    if (openingActive && (!asset?.audio_hash || generation?.config?.opening?.key !== opening?.key)) {
      fail('The Dogma intro is unavailable. Repair saved audio before playing this audiobook.');
      return;
    }
    const preparationFailure = streaming && !asset && !previewing ? playbackFailureFor(currentStreams(), selected) : '';
    if (preparationFailure && (!selected || !selectedChunk(selected))) {
      streamPreparationError = preparationFailure;
      fail(streamPreparationError);
      return;
    }
    if (awaitingContinuation && !openingActive && !previewing) {emit(); return;}
    // This private media target never enters the generation's source segments.
    const segment = asset ? {...selected, id: selected?.id ?? '', chapter_id: selected?.chapter_id ?? '',
      source_id: selected?.source_id ?? '', status: 'ready', audio_hash: asset.audio_hash, duration: asset.duration} : selected;
    if (!book || !generation || !segment || segment.status !== 'ready' || !segment.audio_hash) {
      if (binding || loading) unload();
      if (wantsPlay && generation && stalled(generation.status)) {
        wantsPlay = false;
        failedSegment = null;
        error = blockedError = segment?.status === 'review' ? 'This passage is held for review. Review it in Characters & narration to continue.'
          : generation.status === 'failed' ? generation.error || 'Preparation failed. Resume preparation to retry.'
          : generation.status === 'review' ? 'This passage is not ready. Review the cast and start narration to continue.'
          : 'This passage is not ready. Resume preparation to continue.';
      }
      emit();
      return;
    }
    const targetChunk = !asset && !previewing && selected ? selectedChunk(selected) : null;
    if (streaming && !asset && !previewing && !targetChunk) {
      if (binding || loading) unload();
      const failure = playbackFailureFor(currentStreams(), selected);
      if (failure) {
        wantsPlay = false;
        error = streamPreparationError = failure;
      }
      emit();
      return;
    }
    const targetHash = targetChunk ? 'playback:' + targetChunk.key : segment.audio_hash;
    if (targetChunk && error && error === streamPreparationError) {error = ''; streamPreparationError = '';}
    if (error) { emit(); return; }
    if (binding && (targetChunk || binding.segmentId === segment.id) && loadedHash === targetHash) {
      if (startLoaded && wantsPlay && audio.paused) playLoaded();
      emit();
      return;
    }
    if (loading && requestedHash === targetHash) { emit(); return; }
    unload();
    const epoch = request;
    const owner: Binding = {bookId: book.id, generationId: generation.id, chapterId: segment.chapter_id,
      sourceId: segment.source_id, segmentId: segment.id};
    const hash = targetHash;
    requestedHash = hash;
    loading = true;
    abort = new AbortController();
    const listenerAbort = abort;
    const arm = () => {
      clearLoadTimer();
      loadTimer = setTimeout(() => {
        if (disposed || epoch !== request || !loading) return;
        void persist();
        fail('Loading saved audio timed out. Press Play to retry, or check and repair saved audio.');
      }, services.loadTimeoutMs ?? 30000);
    };
    arm();
    emit();
    const hls = Boolean(targetChunk?.hls);
    const direct = targetChunk && !hls ? services.playbackAssetUrl?.(targetChunk.key) : null;
    const ready = () => {
      if (disposed || epoch !== request) return;
      clearLoadTimer();
      const mediaTime = targetChunk ? playbackMediaTime(targetChunk, segment.id, position) : position;
      audio.currentTime = Math.max(0, Math.min(mediaTime, Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : targetChunk?.duration ?? 0));
      if (!targetChunk) position = audio.currentTime;
      chunk = targetChunk;
      audio.playbackRate = openingActive ? 1 : rate;
      audio.volume = volume;
      binding = owner;
      loadedHash = hash;
      loading = false;
      audio.removeEventListener('loadedmetadata', ready);
      if (!hls && !direct) remember(hash);
      if (wantsPlay) playLoaded();
      emit();
      prefetch();
    };
    audio.addEventListener('loadedmetadata', ready, {once: true, signal: listenerAbort.signal});
    const fatal = (reason: unknown) => {
      if (disposed || epoch !== request) return;
      void persist();
      fail(reason instanceof Error ? reason.message : String(reason));
    };
    if (hls) {
      if (!services.bindPlayback || !targetChunk) {fatal('HLS playback is not supported in this player.'); return;}
      try {
        playbackBinding = services.bindPlayback(audio, targetChunk.key, playbackMediaTime(targetChunk, segment.id, position), listenerAbort.signal, fatal);
      } catch (reason) {fatal(reason);}
      return;
    }
    if (!direct) lease = cache.acquire(hash, () => { if (!disposed && epoch === request && loading) arm(); });
    void (direct ? Promise.resolve(direct) : lease!.ready).then(source => {
      if (disposed || epoch !== request) return;
      arm();
      if (!direct) pinMain(hash);
      audio.src = source;
      audio.load();
    }).catch(fatal);
  };
  const dropStandby = () => {
    const entry = standby;
    if (!entry) return;
    standby = null;
    entry.abort.abort();
    entry.element.removeAttribute('src');
    entry.element.load();
    if (entry.pinned) cache.unpin(entry.hash);
    spare = entry.element;
  };
  const afterHeld = (index: number, direction = 1) => {
    if (!generation) return -1;
    let next = index + direction;
    while (next >= 0 && next < generation.segments.length && generation.segments[next].status === 'review') next += direction;
    return next >= 0 && next < generation.segments.length ? next : -1;
  };
  const attachStandby = () => {
    const entry = standby;
    if (disposed || !entry || entry.attached) return;
    const direct = entry.chunk ? services.playbackAssetUrl?.(entry.chunk.key) : null;
    const source = direct || cache.urlFor(entry.hash);
    if (!source) return;
    entry.attached = true;
    if (!direct) {cache.pin(entry.hash); entry.pinned = true;}
    entry.element.addEventListener('loadedmetadata', () => { if (standby === entry) entry.ready = true; }, {once: true, signal: entry.abort.signal});
    entry.element.src = source;
    entry.element.load();
  };
  const upcoming = () => {
    const out: Segment[] = [];
    const start = generation && selected && binding && !previewing && !openingActive ? generation.segments.findIndex(item => item.id === selected?.id) : -1;
    if (!generation || start < 0) return out;
    const limit = saveData() ? 1 : prefetchSegments;
    let index = start, seconds = 0;
    while (out.length < limit && seconds < prefetchSeconds) {
      index = afterHeld(index);
      if (index < 0) break;
      const item = generation.segments[index];
      if (item.status !== 'ready' || !item.audio_hash) break;
      out.push(item);
      seconds += item.duration > 0 ? item.duration : 0;
    }
    return out;
  };
  const prefetch = () => {
    if (disposed) return;
    if(openingActive&&!previewing&&generation){
      const first=generation.segments[0];
      const firstChunk=streaming&&first?playbackChunkFor(currentStreams(),generation,first):null;
      // Warm the exact first speech asset during branding. HLS owns its MSE
      // fragment queue; it attaches only when the speech transport takes over.
      const hash=streaming?(firstChunk&&!firstChunk.hls&&!services.playbackAssetUrl?.(firstChunk.key)?'playback:'+firstChunk.key:'')
        :first?.status==='ready'?first.audio_hash||'':'';
      cache.keep(recent);cache.want(hash?[hash]:[]);return;
    }
    if (streaming && !openingActive && !previewing) {
      const manifest = currentStreams();
      // HLS owns its fragment queue and MSE buffer on the current media element.
      if (chunk?.hls || manifest?.chunks.some(item => item.hls)) {
        dropStandby();
        cache.want([]);
        return;
      }
      const index = chunk ? manifest?.chunks.findIndex(item => item.key === chunk?.key) ?? -1 : -1;
      const next = index >= 0 ? manifest?.chunks[index + 1] : undefined;
      const eligible = next && generation && validPlaybackChunk(next, generation) ? next : undefined;
      const hash = eligible ? 'playback:' + eligible.key : '';
      if (standby && standby.hash !== hash) dropStandby();
      cache.keep(recent);
      const direct = eligible ? services.playbackAssetUrl?.(eligible.key) : null;
      cache.want(eligible && !direct ? [hash] : []);
      if (services.createAudio && eligible && !standby) {
        const element = spare ?? services.createAudio();
        spare = null;
        element.preload = 'auto';
        standby = {chunk: eligible, segmentId: eligible.parts[0].segment_id, hash, element, ready: false, pinned: false, abort: new AbortController()};
      }
      attachStandby();
      return;
    }
    const window = upcoming();
    const next = window[0];
    if (standby && (standby.segmentId !== next?.id || standby.hash !== next.audio_hash)) dropStandby();
    cache.keep(recent);
    cache.want(window.map(item => item.audio_hash as string));
    if (services.createAudio && next && !standby) {
      const element = spare ?? services.createAudio();
      spare = null;
      element.preload = 'auto';
      standby = {segmentId: next.id, hash: next.audio_hash as string, element, ready: false, pinned: false, abort: new AbortController()};
    }
    attachStandby();
  };
  const activate = (entry: Standby, next: Segment, time: number, play: boolean) => {
    if (!book || !generation) return;
    standby = null;
    const previous = audio;
    for (const [name, listener] of Object.entries(listeners)) previous.removeEventListener(name, listener);
    clearLoadTimer();
    request++;
    invalidatePlay();
    abort?.abort();
    abort = null;
    lease?.release();
    lease = null;
    previous.pause();
    previous.removeAttribute('src');
    previous.load();
    unpinMain();
    spare = previous;
    audio = entry.element;
    for (const [name, listener] of Object.entries(listeners)) audio.addEventListener(name, listener);
    pinned = entry.pinned ? entry.hash : '';
    chunk = entry.chunk ?? null;
    buffering = false;
    selected = next;
    binding = {bookId: book.id, generationId: generation.id, chapterId: next.chapter_id, sourceId: next.source_id, segmentId: next.id};
    loadedHash = entry.hash;
    requestedHash = '';
    loading = false;
    provisional = false;
    error = '';
    failedSegment = null;
    wantsPlay = play;
    audio.playbackRate = openingActive ? 1 : rate;
    audio.volume = volume;
    const limit = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : chunk?.duration ?? time;
    position = Math.max(0, time);
    audio.currentTime = Math.max(0, Math.min(chunk ? playbackMediaTime(chunk, next.id, position) : position, limit));
    if (entry.pinned) remember(entry.hash);
    if (play) playLoaded(); else audio.pause();
    emit();
    prefetch();
  };
  const usableStandby = (next: Segment) => standby?.ready && standby.segmentId === next.id && standby.hash === (selectedChunk(next) ? 'playback:' + selectedChunk(next)!.key : next.audio_hash) && next.status === 'ready' ? standby : null;
  const choose = (id: string, play = true, time = 0, save = true) => {
    const next = generation?.segments.find(item => item.id === id);
    if (!next) return;
    awaitingContinuation = false;
    if (save) void persist();
    invalidatePlay();
    wantsPlay = play;
    error = '';
    position = Number.isFinite(time) ? Math.min(Math.max(0, time), (openingActive ? openingAsset()?.duration : next.duration) || Infinity) : 0;
    const nextChunk = selectedChunk(next);
    if (!openingActive && binding && (nextChunk ? loadedHash === 'playback:' + nextChunk.key : selected?.id === id && loadedHash === next.audio_hash)) {
      selected = next;
      binding = {...binding, chapterId: next.chapter_id, sourceId: next.source_id, segmentId: next.id};
      audio.currentTime = Math.min(nextChunk ? playbackMediaTime(nextChunk, next.id, position) : position, Number.isFinite(audio.duration) ? audio.duration : nextChunk?.duration ?? Infinity);
      if (!nextChunk) position = audio.currentTime;
      if (play) playLoaded(); else audio.pause();
      void persist();
      emit();
      return;
    }
    const warm = usableStandby(next);
    if (warm) {
      activate(warm, next, position, play);
      void persist();
      return;
    }
    unload();
    selected = next;
    ensureAudio();
    void persist();
  };
  const finishOpening = (play = wantsPlay) => {
    if (!openingActive) return;
    if (opening) opening = {...opening, offset: openingAsset()?.duration ?? position, complete: true};
    unload();
    openingActive = false;
    position = 0;
    wantsPlay = play;
    error = '';
    ensureAudio();
    void persist();
  };
  const select = (id: string, play = true, time = 0) => {
    if (!generation?.segments.some(item => item.id === id)) return;
    if (openingActive && !previewing && id === generation?.segments[0]?.id && time === 0) {
      selected = generation.segments[0];
      wantsPlay = play;
      error = '';
      failedSegment = null;
      provisional = false;
      if (!play) pause(); else ensureAudio(true);
      void persist();
      return;
    }
    const silent = previewing;
    if (openingActive) finishOpening(false);
    previewing = false;
    previewFrom = null;
    provisional = false;
    choose(id, play, time, !silent);
  };
  const seekChapterTrack = (chapterId:string,seconds:number,play=wantsPlay) => {
    if(!book||!generation||!Number.isFinite(seconds))return;
    const first=generation.segments[0];
    const firstChapter=first?.chapter_id||generation.config?.chapters?.[0];
    const configured=generation.config?.opening;
    const duration=chapterId===firstChapter?configured?.variants.music.duration||0:0;
    const time=Math.max(0,seconds);
    if(configured&&duration>0&&time<duration){
      // A track seek intentionally revisits its first eight seconds. Source
      // selection, restoration and correction adoption never enter this path.
      void persist();unload();dropStandby();
      selected=first??null;previewing=false;previewFrom=null;provisional=false;awaitingContinuation=false;
      opening={key:configured.key,offset:time,complete:false,variant:'music'};openingActive=true;
      position=time;wantsPlay=play;error='';failedSegment=null;
      ensureAudio();void persist();return;
    }
    const selectedChapter=generation.segments.find(item=>item.chapter_id===chapterId);
    if(!selectedChapter)return;
    const timeline=chapterTimeline(generation,selectedChapter,0);
    const target=timelineTarget(timeline.seekable,time-duration)??{id:selectedChapter.id,offset:0};
    if(configured){
      if(openingActive)finishOpening(false);
      opening={key:configured.key,offset:configured.variants.music.duration,complete:true,variant:'music'};
      openingActive=false;
    }
    select(target.id,play,target.offset);
  };
  const preview = (id: string) => {
    if (!generation?.segments.some(item => item.id === id)) return;
    if (!previewing) {
      void persist();
      previewFrom = selected && {id: selected.id, position: sourcePosition(), provisional, openingActive};
    }
    previewing = true;
    openingActive = false;
    choose(id, true, 0, false);
  };
  const endPreview = () => {
    const from = previewFrom;
    previewFrom = null;
    openingActive = from?.openingActive ?? false;
    previewing = false;
    suppressPersist = true;
    if (from && generation?.segments.some(item => item.id === from.id)) choose(from.id, false, from.position, false);
    else pause();
    suppressPersist = false;
    if (from) provisional = from.provisional;
    emit();
  };
  const listening = (): Anchor | null => {
    syncSource();
    const current = previewing ? generation?.segments.find(item => item.id === previewFrom?.id) : selected;
    const offset = (previewing ? previewFrom?.openingActive : openingActive) ? 0 : previewing ? previewFrom?.position ?? 0 : sourcePosition();
    return current && !(previewing ? previewFrom?.provisional : provisional) ? {chapter_id: current.chapter_id, source_id: current.source_id,
      segment_id: current.id, ...(opening ? {opening: {...opening, offset: openingActive && binding ? audio.currentTime : openingActive ? position : opening.offset}} : {}), start: current.start, end: current.end, duration: current.duration, offset} : null;
  };
  const update = (nextBook: Book | null, nextGeneration: Generation | null) => {
    syncSource();
    const changed = nextBook?.id !== book?.id || nextGeneration?.id !== generation?.id;
    const carried: Anchor | null = changed && generation && nextBook?.id === book?.id ? listening() : null;
    if (changed) {
      void persist();
      unload();
      dropStandby();
      wantsPlay = false;
      error = '';
      failedSegment = null;
      position = 0;
      opening = undefined;
      openingActive = false;
      provisional = false;
      previewing = false;
      previewFrom = null;
      awaitingContinuation = false;
      streams = null;
      streamsGeneration = '';
      recent = [];
      cache.keep(recent);
      cache.want([]);
    }
    book = nextBook;
    generation = nextGeneration?.book_id === nextBook?.id ? nextGeneration : null;
    if (!book || !generation) {
      unload();
      selected = null;
      wantsPlay = false;
      emit();
      return;
    }
    if (changed) {
      const saved: Anchor | null = carried ?? book.position;
      const matching = saved && generation.segments.find(item => item.id === saved.segment_id
        && (!saved.source_id || item.source_id === saved.source_id)
        && (saved.start === undefined || item.start === saved.start && item.end === saved.end));
      const related = matching
        ?? generation.segments.find(item => item.source_id === saved?.source_id && item.chapter_id === saved?.chapter_id)
        ?? generation.segments.find(item => item.chapter_id === saved?.chapter_id);
      const initial = related ?? generation.segments[0];
      const offset = matching && initial === matching && Number.isFinite(saved?.offset) ? (saved?.offset ?? 0)
        * (saved?.duration && matching.duration > 0 ? matching.duration / saved.duration : 1) : 0;
      selected = null;
      const intro = generation.config?.opening;
      if (intro) {
        const restored = saved?.opening?.key === intro.key ? saved.opening : undefined;
        const pastStart = Boolean(saved && (offset > 0 || initial !== generation.segments[0] || saved.offset > 0));
        opening = restored ? {...restored, variant: 'music'} : {key: intro.key, offset: 0, complete: pastStart, variant: 'music'};
        openingActive = !opening.complete;
      }
      provisional = Boolean(saved && !related);
      if (initial) choose(initial.id, false, openingActive ? opening!.offset : offset);
      else ensureAudio();
      return;
    }
    if (openingActive) {
      selected = generation.segments.find(item => item.id === selected?.id)
        ?? generation.segments.find(item => item.source_id === selected?.source_id) ?? generation.segments[0] ?? null;
      ensureAudio();
      return;
    }
    if (awaitingContinuation) {
      const index = generation.segments.findIndex(item => item.id === selected?.id);
      const nextIndex = index >= 0 ? afterHeld(index) : -1;
      if (nextIndex >= 0) {choose(generation.segments[nextIndex].id, wantsPlay); return;}
      if (generation.status === 'ready') {awaitingContinuation = false; pause(); return;}
      if (stalled(generation.status)) awaitingContinuation = false;
      else {emit(); return;}
    }
    const previous = selected;
    const exact = generation.segments.find(item => item.id === selected?.id);
    if (error && error === blockedError && (!stalled(generation.status) || exact?.status === 'ready' && exact.audio_hash)) error = '';
    if (error && failedSegment && exact && failedSegment.id === exact.id && (exact.status !== failedSegment.status || exact.audio_hash !== failedSegment.hash)) {
      error = '';
      failedSegment = null;
    }
    const replacement = exact ?? generation.segments.find(item => item.source_id === selected?.source_id
      && item.start <= (selected?.start ?? 0) && item.end > (selected?.start ?? 0))
      ?? generation.segments.find(item => item.chapter_id === selected?.chapter_id) ?? generation.segments[0];
    if (replacement?.id !== selected?.id) {
      if (replacement) choose(replacement.id, wantsPlay);
      else { unload(); selected = null; wantsPlay = false; emit(); }
      return;
    }
    const audioChanged = Boolean(previous && replacement && previous.id === replacement.id && previous.audio_hash !== replacement.audio_hash);
    selected = replacement ?? null;
    if (audioChanged) { position = 0; provisional = false; }
    ensureAudio();
    if (audioChanged) void persist();
    emit();
    prefetch();
  };
  const clearError = () => { if (!error) return; error = ''; failedSegment = null; ensureAudio(); };
  const pause = () => { wantsPlay = false; invalidatePlay(); audio.pause(); const saved = persist(); emit(); return saved; };
  const toggle = () => {
    if (!selected && !openingActive) return;
    if (wantsPlay || !audio.paused) { pause(); return; }
    error = '';
    failedSegment = null;
    provisional = false;
    wantsPlay = true;
    ensureAudio(true);
  };
  const seek = (seconds: number) => {
    if (!binding || !Number.isFinite(seconds)) return;
    if (openingActive) { finishOpening(wantsPlay); return; }
    provisional = false;
    audio.currentTime = Math.max(0, Math.min(chunk && selected ? playbackMediaTime(chunk, selected.id, seconds) : seconds, Number.isFinite(audio.duration) ? audio.duration : chunk?.duration ?? Infinity));
    position = chunk ? Math.max(0, Math.min(seconds, selected?.duration ?? seconds)) : audio.currentTime;
    void persist();
    emit();
  };
  const skip = (delta: number) => {
    if (!Number.isFinite(delta)) return;
    if (!generation) return;
    const play=wantsPlay;
    syncSource();
    const introDuration=openingAsset()?.duration||0;
    let target=(openingActive?(binding?audio.currentTime:position):sourcePosition())+delta;
    if(openingActive){
      if(target<introDuration){seekChapterTrack(generation.segments[0]?.chapter_id||generation.config?.chapters?.[0]||'',Math.max(0,target),play);return;}
      target-=introDuration;finishOpening(play);
      selected=generation.segments[0]??null;
    }
    if(!selected)return;
    let index = generation.segments.findIndex(item => item.id === selected!.id);
    while (target < 0 && index > 0) {
      const previous = afterHeld(index, -1);
      if (previous < 0 || generation.segments[previous].status !== 'ready') break;
      index = previous;
      target += generation.segments[index].duration;
    }
    if(target<0&&index===0&&introDuration){seekChapterTrack(generation.segments[0].chapter_id,Math.max(0,introDuration+target),play);return;}
    while (index < generation.segments.length - 1 && generation.segments[index].duration > 0 && target >= generation.segments[index].duration) {
      target -= generation.segments[index].duration;
      const next = afterHeld(index);
      if (next < 0) { target = generation.segments[index].duration; break; }
      index = next;
      if (generation.segments[index].status !== 'ready') { target = 0; break; }
    }
    select(generation.segments[index].id, play, generation.segments[index].status==='ready'?Math.max(0,target):0);
  };
  const onTime = () => {
    if (!binding) return;
    if (chunk) syncSource(); else position = audio.currentTime;
    if (Date.now() - lastSave > 4000) { lastSave = Date.now(); void persist(); }
    emit();
  };
  const onPause = () => {
    if (binding && audio.paused) {
      if (chunk) syncSource(); else position = audio.currentTime;
      if (!audio.ended) { wantsPlay = false; invalidatePlay(); }
      void persist();
    }
    emit();
  };
  const onEnd = () => {
    if (!wantsPlay || !binding || !audio.ended) return;
    if (openingActive) { finishOpening(true); return; }
    if (previewing) { endPreview(); return; }
    syncSource();
    const index = generation?.segments.findIndex(item => item.id === selected?.id) ?? -1;
    const nextIndex = index >= 0 ? afterHeld(index) : -1;
    const next = nextIndex >= 0 ? generation?.segments[nextIndex] : undefined;
    if (!next && streaming && generation && generation.status !== 'ready' && !stalled(generation.status)) {
      void persist();
      unload();
      awaitingContinuation = true;
      emit();
    } else if (!next) pause();
    else {
      const warm = usableStandby(next);
      if (warm) {
        void persist();
        activate(warm, next, 0, true);
        void persist();
      } else select(next.id, true);
    }
  };
  const onError = () => {
    if (!audio.hasAttribute('src') || !audio.error) return;
    void persist();
    fail('The saved audio could not be played. Press Play to retry.');
  };
  const onPlay = () => {
    if (binding) { wantsPlay = true; provisional = false; }
    emit();
    prefetch();
  };
  const onWaiting = () => {if (binding && wantsPlay) {buffering = true; emit();}};
  const onCanPlay = () => {buffering = false; emit();};
  const listeners = {waiting: onWaiting, stalled: onWaiting, playing: onCanPlay, canplay: onCanPlay, progress: emit, timeupdate: onTime, pause: onPause, play: onPlay, ended: onEnd, error: onError};
  for (const [name, listener] of Object.entries(listeners)) audio.addEventListener(name, listener);
  audio.preload = 'auto';
  return {
    setStreams: (generationId: string, manifest: PlaybackStreams | null) => {
      if (disposed || generation?.id !== generationId) return;
      syncSource();
      const keepPrefix = chunk && generation && canKeepHlsPrefix(chunk, currentStreams(), manifest, generation);
      streamsGeneration = generationId;
      streams = manifest;
      retainedHls = Boolean(keepPrefix && !manifest?.chunks.some(item => item.key === chunk?.key));
      if (chunk && !keepPrefix && (!manifest?.chunks.some(item => item.key === chunk?.key && generation && validPlaybackChunk(item, generation)))) {
        void persist();
        unload();
        dropStandby();
      }
      ensureAudio();
      prefetch();
    },
    adoptCorrection: (nextBook:Book,nextGeneration:Generation,affectedSegmentIds:string[],_affectedSourceIds:string[]) => {
      if(nextBook.id!==book?.id||nextGeneration.book_id!==book.id||!selected||previewing||openingActive)return false;
      const anchor=listening();
      if(!anchor)return false;
      const target=nextGeneration.segments.find(item=>item.id===anchor.segment_id&&item.source_id===anchor.source_id)
        ??nextGeneration.segments.find(item=>item.source_id===anchor.source_id&&item.start===anchor.start&&item.end===anchor.end);
      if(!target||target.status!=='ready'||!target.audio_hash)return false;
      const play=wantsPlay;
      const changed=affectedSegmentIds.includes(anchor.segment_id||'')||affectedSegmentIds.includes(target.id)
        ||target.audio_hash!==selected.audio_hash;
      const offset=changed?0:anchor.offset;
      update(nextBook,nextGeneration);
      // update() can activate a different generation's intro even though the
      // listener already finished this generation's opening before adoption.
      if(openingActive)finishOpening(false);
      select(target.id,play,offset);
      void persist();
      return true;
    },
    update, select, seekChapterTrack, preview, toggle, seek, skip, pause, persist, clearError, skipOpening: () => finishOpening(wantsPlay),
    configure: (settings: Settings) => { rate = settings.playback_rate; volume = settings.volume; audio.playbackRate = openingActive ? 1 : rate; audio.volume = volume; },
    dispose: () => {
      void persist();
      disposed = true;
      for (const [name, listener] of Object.entries(listeners)) audio.removeEventListener(name, listener);
      dropStandby();
      unload();
      unsubscribe();
      cache.dispose();
    },
    step: (direction: number) => {
      if (!generation || !selected) return;
      const index = generation.segments.findIndex(item => item.id === selected?.id);
      const target = index >= 0 ? afterHeld(index, direction < 0 ? -1 : 1) : -1;
      if (target >= 0 && generation.segments[target].status === 'ready') {
        if(target===0)seekChapterTrack(generation.segments[0].chapter_id,0,wantsPlay||!audio.paused);
        else select(generation.segments[target].id,wantsPlay||!audio.paused);
      }
    },
  };
}
