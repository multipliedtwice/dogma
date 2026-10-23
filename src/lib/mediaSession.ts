type Controls = {play: () => void; pause: () => void; skip: (seconds: number) => void; step: (direction: number) => void};
type Details = {title: string; artist: string; album: string};

/** Phone lock-screen / headset controls. A no-op where the Media Session API is missing. */
export function bindMediaSession(controls: Controls, target: Navigator | undefined = typeof navigator === 'undefined' ? undefined : navigator): () => void {
  const session = target && 'mediaSession' in target ? (target as Navigator & {mediaSession: MediaSession}).mediaSession : undefined;
  if (!session) return () => undefined;
  const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
    ['play', () => controls.play()],
    ['pause', () => controls.pause()],
    ['seekbackward', details => controls.skip(-(details.seekOffset || 15))],
    ['seekforward', details => controls.skip(details.seekOffset || 15)],
    ['previoustrack', () => controls.step(-1)],
    ['nexttrack', () => controls.step(1)],
  ];
  for (const [action, handler] of handlers) {
    try { session.setActionHandler(action, handler); } catch { /* unsupported action */ }
  }
  return () => {
    for (const [action] of handlers) {
      try { session.setActionHandler(action, null); } catch { /* ignore */ }
    }
  };
}

export function describeMediaSession(details: Details | null, playing: boolean, target: Navigator | undefined = typeof navigator === 'undefined' ? undefined : navigator) {
  const session = target && 'mediaSession' in target ? (target as Navigator & {mediaSession: MediaSession}).mediaSession : undefined;
  if (!session) return;
  try {
    session.playbackState = details ? (playing ? 'playing' : 'paused') : 'none';
    if (!details) { session.metadata = null; return; }
    const current = session.metadata;
    if (!current || current.title !== details.title || current.artist !== details.artist || current.album !== details.album) {
      session.metadata = new MediaMetadata(details);
    }
  } catch { /* metadata is optional */ }
}
