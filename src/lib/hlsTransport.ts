import Hls from 'hls.js';

/** HLS owns fragment loading, retry policies, buffering and decoder continuity. */
export function createHlsPlaybackAdapter(Engine: typeof Hls = Hls,
  preferNative = () => typeof navigator !== 'undefined' && navigator.vendor === 'Apple Computer, Inc.') {
  const native = (audio: HTMLAudioElement) => Boolean(audio.canPlayType?.('application/vnd.apple.mpegurl'));
  return {
    supported(audio?: HTMLAudioElement) {
      const media = audio ?? (typeof Audio === 'undefined' ? undefined : new Audio());
      return Boolean(media && native(media)) || Engine.isSupported();
    },
    bind(audio: HTMLAudioElement, url: string, offset: number, signal: AbortSignal, onFatal: (reason: Error) => void) {
      let engine: Hls | undefined;
      let destroyed = false;
      const destroy = () => {
        if (destroyed) return;
        destroyed = true;
        signal.removeEventListener('abort', destroy);
        engine?.destroy();
      };
      if (signal.aborted) return {destroy};
      signal.addEventListener('abort', destroy, {once: true});
      try {
        if (native(audio) && (preferNative() || !Engine.isSupported())) {
          audio.src = url;
          audio.load();
        } else if (Engine.isSupported()) {
          engine = new Engine({
            enableWorker: true,
            lowLatencyMode: false,
            startPosition: Number.isFinite(offset) ? Math.max(0, offset) : 0,
            maxBufferLength: 180,
            maxMaxBufferLength: 180,
            backBufferLength: 30,
            maxBufferSize: 12 * 1024 * 1024,
          });
          engine.on(Engine.Events.ERROR, (_event, data) => {
            if (!destroyed && data.fatal) onFatal(new Error(`Audio streaming failed (${data.details}). Press Play to retry.`));
          });
          engine.attachMedia(audio);
          engine.loadSource(url);
        } else {
          throw new Error('This browser does not support HLS audio playback.');
        }
      } catch (reason) {
        destroy();
        onFatal(reason instanceof Error ? reason : new Error(String(reason)));
      }
      return {destroy};
    },
  };
}

const adapter = createHlsPlaybackAdapter();
export const supportsPlaybackHls = adapter.supported;
export const bindPlaybackHls = adapter.bind;
