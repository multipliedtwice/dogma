// Remaining-time estimates from observed progress, never from a fixed guess.

export function formatRemaining(language: 'en' | 'ru', seconds: number): string {
  const ru = language === 'ru';
  if (seconds < 60) return ru ? 'меньше минуты' : 'under a minute';
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  if (!hours) return ru ? `≈${minutes} мин` : `≈${minutes} min left`;
  return ru ? `≈${hours} ч ${rest} мин` : `≈${hours} h ${rest} min left`;
}

export type RateSample = {at: number; done: number};

// Keep samples from the last window and extrapolate the recent rate.
export function addSample(samples: RateSample[], done: number, now = Date.now(), windowMs = 15 * 60 * 1000): RateSample[] {
  // A stalled count still adds a sample, so a stall lowers the rate.
  return [...samples.filter(s => now - s.at <= windowMs && s.done <= done), {at: now, done}];
}

export function remainingSeconds(samples: RateSample[], total: number): number | null {
  if (samples.length < 2) return null;
  const first = samples[0], last = samples[samples.length - 1];
  const rate = (last.done - first.done) / ((last.at - first.at) / 1000);
  if (!(rate > 0) || last.at - first.at < 60_000) return null;
  return Math.max(0, (total - last.done) / rate);
}
