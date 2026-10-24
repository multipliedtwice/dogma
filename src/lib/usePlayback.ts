import {useEffect, useRef, useState} from 'react';
import {api, audioAsset, playbackAsset, playbackAssetUrl, playbackHlsUrl, flushPositionWrites, OperationError} from './api';
import {createPlaybackController, type PlaybackSnapshot} from './playback';
import type {Book, Generation, PlaybackStreams, Settings} from './types';
import {createPlaybackClock, samePlaybackStructure} from './playbackClock';
import {bindMediaSession, describeMediaSession} from './mediaSession';
import {bindPlaybackHls, supportsPlaybackHls} from './hlsTransport';

export function usePlayback(book: Book | null, generation: Generation | null, settings: Settings | null) {
  const controller = useRef<ReturnType<typeof createPlaybackController> | null>(null);
  const [state, setState] = useState<PlaybackSnapshot>({segment: null, position: 0, playing: false, waiting: false, error: '', saveError: '', previewing: false});
  const stateRef = useRef(state);
  stateRef.current = state;
  const [sleepAt, setSleepAt] = useState(0);
  const [clock] = useState(createPlaybackClock);
  const [streamVolume, setStreamVolume] = useState(settings?.music_volume);
  const requestStreams = useRef<(() => void) | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setStreamVolume(settings?.music_volume), 250);
    return () => clearTimeout(timer);
  }, [settings?.music_volume]);
  useEffect(() => {
    const current = createPlaybackController(new Audio(), {
      loadAudio: (hash, signal, progress) => audioAsset(hash, signal, progress),
      loadPlayback: (key, signal, progress) => playbackAsset(key, signal, progress),
      playbackAssetUrl,
      bindPlayback: (audio, key, offset, signal, onFatal) => {
        const url = playbackHlsUrl(key);
        if (!url) throw new Error('HLS playback requires the hosted listener.');
        return bindPlaybackHls(audio, url, offset, signal, onFatal);
      },
      savePosition: (bookId, position) => api('save_position', {book_id: bookId, position}),
      notify: next => { clock.publish(next); setState(previous => samePlaybackStructure(previous, next) ? previous : next); },
      createAudio: () => new Audio(),
    });
    controller.current = current;
    const save = () => { void current.persist(); };
    window.addEventListener('beforeunload', save);
    window.addEventListener('pagehide', save);
    const visibility=()=>{if(document.visibilityState==='hidden')save();};
    document.addEventListener('visibilitychange',visibility);
    return () => { window.removeEventListener('beforeunload', save);window.removeEventListener('pagehide',save);document.removeEventListener('visibilitychange',visibility);current.dispose(); controller.current = null; };
  }, []);
  useEffect(() => bindMediaSession({
    play: () => { if (!stateRef.current.playing) controller.current?.toggle(); },
    pause: () => { void controller.current?.pause(); },
    skip: seconds => controller.current?.skip(seconds),
    step: direction => controller.current?.step(direction),
  }), []);
  const chapterTitle = book?.chapters.find(item => item.id === state.segment?.chapter_id)?.title ?? '';
  useEffect(() => {
    describeMediaSession(book && (state.segment||state.opening) ? {title: chapterTitle || book.chapters[0]?.title || book.title, artist: book.author || book.title, album: book.title} : null, state.playing);
  }, [book, state.segment?.id, state.opening?.key, chapterTitle, state.playing, settings?.language]);
  useEffect(() => { controller.current?.update(book, generation); }, [book, generation]);
  useEffect(() => { if (settings) controller.current?.configure(settings); }, [settings]);
  useEffect(() => {
    const id = generation?.id;
    if (!id || !settings || streamVolume === undefined) return;
    // Connection setup has completed before a generation is available. Bearer-only
    // desktop sessions retain compressed playback until HLS has signed asset URLs.
    const transport = playbackHlsUrl('0'.repeat(64)) && supportsPlaybackHls() ? 'hls' : 'mp3';
    let live = true, fetching = false, queued = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort: AbortController | undefined;
    controller.current?.setStreams(id, null);
    const poll = async () => {
      if (!live) return;
      if (fetching) {queued = true; return;}
      clearTimeout(timer);
      fetching = true;
      abort = new AbortController();
      let delay = 1000;
      try {
        const manifest = await api<PlaybackStreams>('playback_streams', {generation_id: id, transport, music: settings.music,
          music_volume: streamVolume, priority_segment_id: stateRef.current.segment?.id || book?.position?.segment_id || ''}, abort.signal);
        if (!live) return;
        controller.current?.setStreams(id, manifest);
        delay = manifest.status === 'pending' || manifest.chunks.some(chunk => chunk.status === 'pending') ? 1000 : 15000;
      } catch (reason) {
        if (!live || abort.signal.aborted) return;
        if (reason instanceof OperationError) {
          controller.current?.setStreams(id, {schema: 1, key: '', status: 'failed', music: settings.music,
            music_volume: streamVolume, chunks: [], holds: [], error: reason.message});
        }
        // Connection failures already surface through the shared runtime status.
        // Keep the listening anchor and retry without falling back to sentence playback.
        delay = reason instanceof OperationError ? 15000 : 3000;
      } finally {
        fetching = false;
        if (live) {const soon = queued; queued = false; timer = setTimeout(poll, soon ? 0 : delay);}
      }
    };
    requestStreams.current = () => {void poll();};
    void poll();
    return () => {live = false; clearTimeout(timer); abort?.abort(); requestStreams.current = null;};
  }, [generation?.id, settings?.music, streamVolume]);
  useEffect(() => {
    if (state.premixed && state.waiting && !state.streamKey) requestStreams.current?.();
  }, [state.segment?.id, state.premixed, state.waiting, state.streamKey]);
  useEffect(() => {
    if (!sleepAt) return;
    const timer = setInterval(() => {
      if (Date.now() >= sleepAt) { controller.current?.pause(); setSleepAt(0); }
    }, 500);
    return () => clearInterval(timer);
  }, [sleepAt]);
  return {...state, clock, sleepAt, setSleepAt,
    adoptCorrection: (nextBook:Book,nextGeneration:Generation,affectedSegmentIds:string[],affectedSourceIds:string[]) =>
      controller.current?.adoptCorrection(nextBook,nextGeneration,affectedSegmentIds,affectedSourceIds)??false,
    toggle: () => controller.current?.toggle(),
    select: (id: string, play = true, time = 0) => controller.current?.select(id, play, time),
    seekChapterTrack: (chapterId:string,seconds:number,play=true)=>controller.current?.seekChapterTrack(chapterId,seconds,play),
    preview: (id: string) => controller.current?.preview(id),
    seek: (seconds: number) => controller.current?.seek(seconds),
    skipOpening: () => controller.current?.skipOpening(),
    skip: (delta: number) => controller.current?.skip(delta),
    clearError: () => controller.current?.clearError(),
    stop: async () => { await controller.current?.pause(); await flushPositionWrites(); },
  };
}
