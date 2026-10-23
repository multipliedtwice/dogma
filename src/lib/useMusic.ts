import {useCallback, useEffect, useRef, useState} from 'react';
import {api, asset} from './api';
import {createMusicController, type MusicStatus} from './music';
import type {createPlaybackClock} from './playbackClock';
import type {Generation, Settings} from './types';

export function useMusic(generation: Generation | null, settings: Settings | null, clock: ReturnType<typeof createPlaybackClock>) {
  const controller = useRef<ReturnType<typeof createMusicController> | null>(null);
  const [status, setStatus] = useState<MusicStatus | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const current = createMusicController({
      loadMusic: (key, signal) => asset('music', key, undefined, signal),
      createAudio: () => new Audio(),
    });
    controller.current = current;
    const unsubscribe = clock.subscribe(() => current.update(clock.getSnapshot()));
    return () => { unsubscribe(); current.dispose(); controller.current = null; };
  }, [clock]);
  useEffect(() => { if (settings) controller.current?.configure(settings); }, [settings]);
  const id = generation?.id;
  useEffect(() => {
    if (!id) { setStatus(null); return; }
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await api<MusicStatus>('music_cues', {generation_id: id});
        if (!live) return;
        setStatus(next);
        const busy = next.enabled && next.available && (next.active || next.cues.length === 0 || next.cues.some(cue => cue.status === 'pending' || cue.status === 'rendering'));
        timer = setTimeout(poll, busy ? 10000 : 60000);
      } catch {
        if (live) timer = setTimeout(poll, 30000);
      }
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [id, generation?.status, settings?.music, refresh]);
  useEffect(() => { controller.current?.setStatus(generation, status); }, [generation, status]);
  const retry = useCallback(async () => {
    if (!id) return;
    setStatus(await api<MusicStatus>('music_retry', {generation_id: id}));
    setRefresh(value => value + 1);
  }, [id]);
  return {status, retry};
}
