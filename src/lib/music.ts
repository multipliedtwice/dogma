import type {Generation, Segment, Settings} from './types';
import type {PlaybackSnapshot} from './playback';

// Generated music is an overlay on the speech timeline: scene beds that follow
// each chapter's mood (short phrases separated by quiet gaps that ambience
// fills), crossfading from scene to scene, and ending music. It never moves
// speech; it follows the playback snapshot and is silent whenever the listener
// turns music off. The export mixes the same layout (runtime/rimsky/music.py).
export type MusicCue = {id: string; kind: 'bed' | 'ambience' | 'ending' | 'chapter_mix'; chapter_id: string; anchor_segment_id: string;
  start: number; mood: string; status: 'pending' | 'rendering' | 'ready' | 'failed'; audio_key: string | null; duration: number; error: string};
export type MusicScene = {id: string; chapter_id: string; anchor_segment_id: string; mood: string; bed: string; ambience: string;
  released: boolean; phrases: [number, number][]; cycle: number};
type TransitionPolicy = {lead_seconds:number; speech_gain:number; pause_gain:number; release_seconds:number};
type MusicTransition = {kind:'scene'|'chapter';anchor_segment_id:string;start:number;end:number};
export type MusicMix = {crossfade: number; ending_fade: number; variants: number;transition?:TransitionPolicy};
export type MusicStatus = {enabled: boolean; available: boolean; pack_error: string; active: boolean; error: string; cues: MusicCue[];
  scenes?: MusicScene[]; mix?: MusicMix;transitions?:MusicTransition[]};
/** One sounding take: `offset` seconds into it, at `gain` (0..1) before the listener's music volume. */
export type MusicLayer = {id: string; key: string; offset: number; gain: number; period: number | null};

const RESYNC_SECONDS = .6;
const DEFAULT_MIX: MusicMix = {crossfade: 6, ending_fade: 2, variants: 3};

export function musicTimeline(generation: Generation | null) {
  const ordered = [...(generation?.segments ?? [])].sort((a, b) => a.ordinal - b.ordinal);
  return {ordered, index: new Map(ordered.map((segment, position) => [segment.id, position]))};
}
type Timeline = ReturnType<typeof musicTimeline>;

/** Speech seconds from the start of `anchor` to this position, or null when unknown or before it. */
export function elapsedSince(anchor: string, timeline: Timeline, current: Segment, position: number): number | null {
  const from = timeline.index.get(anchor);
  const index = timeline.index.get(current.id);
  if (from === undefined || index === undefined || index < from) return null;
  let before = 0;
  for (let i = from; i < index; i++) {
    const segment = timeline.ordered[i];
    // Elapsed time is only known across ready audio.
    if (segment.status !== 'ready' || !(segment.duration > 0)) return null;
    before += segment.duration;
  }
  return before + position;
}

/** Seconds into `cue` for a speech position, or null when the cue is not sounding. */
export function cueOffset(cue: MusicCue, timeline: Timeline, current: Segment, position: number): number | null {
  const elapsed = elapsedSince(cue.anchor_segment_id, timeline, current, position);
  if (elapsed === null) return null;
  const offset = elapsed - cue.start;
  return offset >= 0 && offset < cue.duration ? offset : null;
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));
const TRANSITION:TransitionPolicy={lead_seconds:6,speech_gain:1.2,pause_gain:2,release_seconds:1.2};

export function transitionEnvelope(window:{start:number;end:number},t:number,policy=TRANSITION) {
  const smooth=(x:number)=>{const v=clamp(x);return v*v*(3-2*v);};
  const rise=policy.speech_gain*smooth((t-window.start+policy.lead_seconds)/policy.lead_seconds);
  const crest=policy.speech_gain+(policy.pause_gain-policy.speech_gain)*smooth((t-window.start)/.5);
  return (t<window.start?rise:crest)*smooth((window.end-t)/Math.min(policy.release_seconds,Math.max(.1,(window.end-window.start)/2)));
}

/** Every layer that should sound at this position: the current scene, the one it is replacing, and ending music. */
export function musicLayers(status: MusicStatus | null, timeline: Timeline, snapshot: PlaybackSnapshot): MusicLayer[] {
  // The branded opening already contains its own music and has no book time.
  if (snapshot.opening || snapshot.premixed) return [];
  const current = snapshot.segment;
  const index = current ? timeline.index.get(current.id) : undefined;
  if (!status || !current || index === undefined) return [];
  const mix = status.mix ?? DEFAULT_MIX;
  const ready = new Map(status.cues.filter(cue => cue.status === 'ready' && cue.audio_key).map(cue => [cue.id, cue]));
  const layers: MusicLayer[] = [];
  const ending = status.cues.find(cue => cue.kind === 'ending' && ready.has(cue.id));
  const endingOffset = ending ? cueOffset(ending, timeline, current, snapshot.position) : null;
  if (ending && endingOffset !== null) layers.push({id: ending.id, key: ending.audio_key as string, offset: endingOffset, gain: 1, period: null});
  // Imported Higgs previews already carry an independently verified, full-length
  // mix of YuE2 phrases plus procedural ambience.  Play that immutable mix as a
  // single timeline layer instead of rebuilding (and potentially doubling) it
  // from the production scene planner.
  const chapterMix = status.cues.find(cue => cue.kind === 'chapter_mix' && cue.chapter_id === current.chapter_id && ready.has(cue.id));
  const chapterMixOffset = chapterMix ? cueOffset(chapterMix, timeline, current, snapshot.position) : null;
  if (chapterMix && chapterMixOffset !== null) {
    layers.push({id: chapterMix.id, key: chapterMix.audio_key as string, offset: chapterMixOffset, gain: 1, period: null});
    return layers;
  }
  const scenes = (status.scenes ?? []).filter(scene => timeline.index.has(scene.anchor_segment_id))
    .sort((a, b) => (timeline.index.get(a.anchor_segment_id) as number) - (timeline.index.get(b.anchor_segment_id) as number));
  const policy=mix.transition??TRANSITION;
  let duck=1;
  for(const window of status.transitions??[]) {
    const anchorIndex=timeline.index.get(window.anchor_segment_id);
    if(anchorIndex===undefined)continue;
    const anchor=timeline.ordered[anchorIndex];
    const backwards=elapsedSince(current.id,timeline,anchor,0);
    const elapsed=elapsedSince(window.anchor_segment_id,timeline,current,snapshot.position)
      ??(backwards===null?null:snapshot.position-backwards);
    if(elapsed===null)continue;
    const gain=transitionEnvelope(window,elapsed,policy);
    if(gain<=0)continue;
    const scene=[...scenes].reverse().find(s=>s.released&&(timeline.index.get(s.anchor_segment_id) as number)<=anchorIndex);
    const cue=scene?Array.from({length:mix.variants},(_,v)=>ready.get(`bed:${scene.bed}:${v}`)).find(Boolean):undefined;
    if(!cue)continue;
    const offset=elapsed-window.start+policy.lead_seconds;
    if(offset<0||offset>=cue.duration)continue;
    layers.push({id:`transition:${window.anchor_segment_id}`,key:cue.audio_key as string,offset,gain,period:null});
    duck*=1-clamp(gain/policy.speech_gain);
  }
  let at = -1;
  scenes.forEach((scene, position) => { if ((timeline.index.get(scene.anchor_segment_id) as number) <= index) at = position; });
  if (at < 0) return layers;
  const sound = (scene: MusicScene, gain: number) => {
    const elapsed = elapsedSince(scene.anchor_segment_id, timeline, current, snapshot.position);
    if (!scene.released || elapsed === null || gain <= 0) return;
    gain *= clamp(elapsed / mix.crossfade)*duck;
    const variants = Array.from({length: mix.variants}, (_, v) => ready.get(`bed:${scene.bed}:${v}`)).filter(Boolean) as MusicCue[];
    const round = Math.floor(elapsed / scene.cycle);
    const within = elapsed - round * scene.cycle;
    scene.phrases.forEach(([variant, start], k) => {
      const take = variants[variant % Math.max(1, variants.length)];
      const offset = within - start;
      if (take && offset >= 0 && offset < take.duration) layers.push({id: `${scene.id}:${round}:${k}`, key: take.audio_key as string, offset, gain, period: null});
    });
    const ambience = ready.get(`ambience:${scene.ambience}`);
    if (ambience) layers.push({id: `${scene.id}:ambience`, key: ambience.audio_key as string, offset: elapsed % ambience.duration, gain, period: ambience.duration});
  };
  const last = at === scenes.length - 1;
  sound(scenes[at], last && endingOffset !== null ? clamp(1 - endingOffset / mix.ending_fade) : 1);
  if (at > 0) {
    const since = elapsedSince(scenes[at].anchor_segment_id, timeline, current, snapshot.position);
    if (since !== null && since < mix.crossfade) sound(scenes[at - 1], 1 - since / mix.crossfade);
  }
  return layers;
}

/** Narration reached the book's final words; ending music may play out on its own. */
export function narrationEnded(timeline: Timeline, snapshot: PlaybackSnapshot) {
  const last = timeline.ordered[timeline.ordered.length - 1];
  const current = snapshot.segment;
  return Boolean(!snapshot.opening && last && current && current.id === last.id && !snapshot.playing && !snapshot.error && snapshot.position >= current.duration - .3);
}

type Services = {
  loadMusic: (key: string, signal: AbortSignal) => Promise<Blob>;
  createAudio: () => HTMLAudioElement;
  createUrl?: (blob: Blob) => string;
  revokeUrl?: (url: string) => void;
};
type Voice = {audio: HTMLAudioElement; ready: boolean};

export function createMusicController(services: Services) {
  const createUrl = services.createUrl ?? (blob => URL.createObjectURL(blob));
  const revokeUrl = services.revokeUrl ?? (value => URL.revokeObjectURL(value));
  let settings: Settings | null = null;
  let status: MusicStatus | null = null;
  let timeline = musicTimeline(null);
  let last: PlaybackSnapshot | null = null;
  let disposed = false;
  const voices = new Map<string, Voice>();
  // One object URL per take; phrases of a scene reuse a handful of takes.
  const urls = new Map<string, string | null>();
  const downloads = new Map<string, AbortController>();

  const level = () => settings ? clamp(settings.volume * settings.music_volume) : 0;
  const release = (id: string) => {
    const voice = voices.get(id);
    if (!voice) return;
    voice.audio.pause();
    voice.audio.removeAttribute('src');
    voice.audio.load();
    voices.delete(id);
  };
  const url = (key: string) => {
    if (urls.has(key)) return urls.get(key) ?? null;
    urls.set(key, null);
    const download = new AbortController();
    downloads.set(key, download);
    services.loadMusic(key, download.signal).then(blob => {
      if (disposed || downloads.get(key) !== download) return;
      downloads.delete(key);
      urls.set(key, createUrl(blob));
      if (last) sync(last);
    }, () => { if (downloads.get(key) === download) { downloads.delete(key); urls.delete(key); } });
    return null;
  };
  const clearSources = () => {
    [...voices.keys()].forEach(release);
    for (const download of downloads.values()) download.abort();
    downloads.clear();
    for (const value of urls.values()) if (value) revokeUrl(value);
    urls.clear();
  };
  const drift = (voice: Voice, layer: MusicLayer) => {
    const gap = Math.abs(voice.audio.currentTime - layer.offset);
    return layer.period ? Math.min(gap, layer.period - gap) : gap;
  };
  const sync = (snapshot: PlaybackSnapshot) => {
    last = snapshot;
    if (disposed) return;
    if (snapshot.premixed) { clearSources(); return; }
    if (!settings?.music || !snapshot.segment || snapshot.previewing || snapshot.opening) { [...voices.keys()].forEach(release); return; }
    const ended = narrationEnded(timeline, snapshot);
    // At the exact end of narration an ending cue may not have existed on the
    // preceding playback tick.  Evaluate the final timeline position once so
    // it can be created, then let it play independently of the stopped speech.
    const layerSnapshot = ended ? {...snapshot, position: snapshot.segment.duration} : snapshot;
    const layers = musicLayers(status, timeline, layerSnapshot)
      .filter(layer => !ended || status?.cues.some(cue => cue.kind === 'ending' && cue.id === layer.id));
    const wanted = new Set(layers.map(layer => layer.id));
    [...voices.keys()].filter(id => !wanted.has(id)).forEach(release);
    for (const layer of layers) {
      const source = url(layer.key);
      if (!source) continue;
      let voice = voices.get(layer.id);
      if (!voice) {
        const audio = services.createAudio();
        (audio as HTMLAudioElement & {preservesPitch?: boolean}).preservesPitch = true;
        audio.loop = layer.period !== null;
        const created: Voice = voice = {audio, ready: false};
        voices.set(layer.id, created);
        audio.addEventListener('loadedmetadata', () => {
          if (voices.get(layer.id) !== created) return;
          created.ready = true;
          if (last) sync(last);
        }, {once: true});
        audio.src = source;
        continue;
      }
      if (!voice.ready) continue;
      voice.audio.volume = clamp(level() * layer.gain);
      voice.audio.playbackRate = settings.playback_rate;
      if (ended) {
        // Start a just-loaded ending once, but never seek or restart it on later
        // status/position updates after it has played out.
        if (voice.audio.paused && voice.audio.currentTime < .05) void voice.audio.play().catch(() => undefined);
        continue;
      }
      if (!snapshot.playing || snapshot.waiting) { voice.audio.pause(); continue; }
      if (drift(voice, layer) > RESYNC_SECONDS) voice.audio.currentTime = layer.offset;
      if (voice.audio.paused) void voice.audio.play().catch(() => undefined);
    }
  };
  return {
    update: sync,
    configure: (next: Settings) => {
      settings = next;
      if (last) sync(last);
    },
    setStatus: (generation: Generation | null, next: MusicStatus | null) => {
      timeline = musicTimeline(generation);
      status = next;
      const keys = new Set((next?.cues ?? []).filter(cue => cue.status === 'ready').map(cue => cue.audio_key));
      for (const [key, value] of urls) {
        if (keys.has(key)) continue;
        downloads.get(key)?.abort();
        downloads.delete(key);
        for (const [id, voice] of voices) if (voice.audio.src === value) release(id);
        if (value) revokeUrl(value);
        urls.delete(key);
      }
      if (last) sync(last);
    },
    dispose: () => {
      disposed = true;
      clearSources();
    },
  };
}
