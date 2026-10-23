import type {PlaybackSnapshot} from './playback';
const initial: PlaybackSnapshot = {segment: null, position: 0, playing: false, waiting: false, error: '', saveError: '', previewing: false};
export function createPlaybackClock() {
  let snapshot = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    publish: (next: PlaybackSnapshot) => { snapshot = next; for (const listener of listeners) listener(); },
  };
}
export function samePlaybackStructure(a: PlaybackSnapshot, b: PlaybackSnapshot) {
  return a.premixed === b.premixed && a.streamKey === b.streamKey && a.opening?.key === b.opening?.key && a.segment === b.segment && a.playing === b.playing && a.waiting === b.waiting && a.error === b.error && a.saveError === b.saveError && a.previewing === b.previewing;
}
