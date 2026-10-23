import type {Generation, PlaybackChunk, PlaybackStreams, Segment} from './types';

/** Encoded media timing is private; book positions always refer to these source parts. */
export function validPlaybackChunk(chunk: PlaybackChunk, generation: Generation) {
  const indices = chunk.parts.map(part => generation.segments.findIndex(item => item.id === part.segment_id));
  return chunk.status === 'ready' && Boolean(chunk.key) && Number.isFinite(chunk.start) && chunk.start >= 0
    && Number.isFinite(chunk.duration) && chunk.duration > 0 && chunk.parts.length > 0
    && chunk.parts.every((part, index) => {
      const segment = generation.segments.find(item => item.id === part.segment_id);
      return segment?.status === 'ready' && segment.audio_hash === part.audio_hash && segment.chapter_id === chunk.chapter_id
        && Math.abs(segment.duration - part.duration) <= .05 && (!index || indices[index] > indices[index - 1])
        && Number.isFinite(part.start) && part.start >= 0 && Number.isFinite(part.duration) && part.duration > 0
        && part.start + part.duration <= chunk.duration + .1 && (!index || part.start >= chunk.parts[index - 1].start + chunk.parts[index - 1].duration - .01);
    });
}

export function playbackChunkFor(streams: PlaybackStreams | null, generation: Generation, segment: Segment) {
  return streams?.chunks.find(chunk => chunk.parts.some(part => part.segment_id === segment.id) && validPlaybackChunk(chunk, generation)) ?? null;
}

export function playbackSourceAt(chunk: PlaybackChunk, generation: Generation, time: number) {
  const part = [...chunk.parts].reverse().find(item => item.start <= time) ?? chunk.parts[0];
  const segment = generation.segments.find(item => item.id === part?.segment_id);
  return segment && part ? {segment, offset: Math.max(0, Math.min(time - part.start, part.duration))} : null;
}

export function playbackMediaTime(chunk: PlaybackChunk, segmentId: string, offset: number) {
  const part = chunk.parts.find(item => item.segment_id === segmentId);
  return part ? part.start + Math.max(0, Math.min(offset, part.duration)) : 0;
}

export function playbackFailureFor(streams: PlaybackStreams | null, segment: Segment | null) {
  if (!streams) return '';
  if (streams.status === 'failed') return streams.error || 'Continuous playback preparation failed. Press Play to retry.';
  if (!segment) return '';
  const failed = streams.chunks.find(chunk => chunk.status === 'failed' && chunk.parts.some(part => part.segment_id === segment.id));
  const hold = streams.holds.find(value => {
    if (!value || typeof value !== 'object') return false;
    const item = value as {id?: string; segment_id?: string; status?: string};
    return item.status === 'failed' && (item.id === segment.id || item.segment_id === segment.id || item.id === 'music:' + segment.chapter_id);
  }) as {error?: string} | undefined;
  return failed || hold ? failed?.error || hold?.error || 'Continuous playback source is unavailable. Check and repair saved audio.' : '';
}

/** A newly extended chapter must not restart an unaffected program already playing. */
export function canKeepHlsPrefix(chunk: PlaybackChunk, previous: PlaybackStreams | null, next: PlaybackStreams | null, generation: Generation) {
  return Boolean(chunk.hls && previous && next && previous.music === next.music && previous.music_volume === next.music_volume
    && validPlaybackChunk(chunk, generation) && !chunk.parts.some(part => playbackFailureFor(next, generation.segments.find(item => item.id === part.segment_id) ?? null))
    && next.chunks.some(program => program.hls && program.chapter_id === chunk.chapter_id && program.start <= chunk.start
      && chunk.parts.every(part => program.parts.some(item => item.segment_id === part.segment_id && item.audio_hash === part.audio_hash && Math.abs(item.duration - part.duration) <= .05))));
}
