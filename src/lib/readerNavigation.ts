const programmaticScrolls = new WeakMap<HTMLElement, ReturnType<typeof setTimeout>>();
type ReaderScrollAnimation = {frame: number; target: number};
const scrollAnimations = new WeakMap<HTMLElement, ReaderScrollAnimation>();
const AUTO_FOLLOW_SCROLL_MS = 3000;
const AUTO_FOLLOW_REFERENCE_WIDTH = 900;
const AUTO_FOLLOW_MIN_MS = 1200;
const AUTO_FOLLOW_FAR_MIN_MS = 320;
const CONTROLS = 'button,a,input,select,textarea,summary,[role="button"],[role="link"],[role="slider"]';

function withinControl(target: EventTarget | null): boolean {
  return Boolean(target && 'closest' in target && typeof target.closest === 'function' && target.closest(CONTROLS));
}

function finishProgrammaticScroll(root: HTMLElement): void {
  clearTimeout(programmaticScrolls.get(root));
  programmaticScrolls.delete(root);
}

function trackProgrammaticScroll(root: HTMLElement): void {
  clearTimeout(programmaticScrolls.get(root));
  programmaticScrolls.set(root, setTimeout(() => programmaticScrolls.delete(root), 200));
}

function cancelReaderAnimation(root: HTMLElement): void {
  const animation = scrollAnimations.get(root);
  if (!animation) return;
  root.ownerDocument.defaultView?.cancelAnimationFrame?.(animation.frame);
  scrollAnimations.delete(root);
}

export function autoFollowScrollDuration(root: HTMLElement, distance = 0): number {
  const width = root.getBoundingClientRect().width || root.clientWidth || AUTO_FOLLOW_REFERENCE_WIDTH;
  const widthScale = Math.max(AUTO_FOLLOW_MIN_MS / AUTO_FOLLOW_SCROLL_MS, Math.min(1, width / AUTO_FOLLOW_REFERENCE_WIDTH));
  const nearbyDuration = AUTO_FOLLOW_SCROLL_MS * widthScale;
  const viewportHeight = root.clientHeight || root.getBoundingClientRect().height || 1;
  const viewports = Math.abs(distance) / Math.max(1, viewportHeight);
  // Preserve the deliberately slow reading pace nearby. Once the destination
  // is more than one screen away, increase velocity with distance so a manual
  // return from another chapter never turns into a long page-by-page journey.
  return viewports <= 1 ? nearbyDuration : Math.max(AUTO_FOLLOW_FAR_MIN_MS, nearbyDuration / Math.sqrt(viewports));
}

export function scrollReaderTo(root: HTMLElement | null, top = 0, smooth = false, replace = true): boolean {
  if (!root) return false;
  const page = root.ownerDocument.defaultView;
  const reduce = page?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const animation = scrollAnimations.get(root);
  if (animation && smooth && !reduce && !replace) return true;
  cancelReaderAnimation(root);
  if (smooth && !reduce && page?.requestAnimationFrame) {
    const start = root.scrollTop;
    const destination = Math.max(0, top);
    if (Math.abs(destination - start) < .5) return true;
    const started = page.performance?.now?.() ?? Date.now();
    const duration = autoFollowScrollDuration(root, destination - start);
    const next: ReaderScrollAnimation = {frame: 0, target: destination};
    const step = (now: number) => {
      if (scrollAnimations.get(root) !== next) return;
      const progress = Math.max(0, Math.min(1, (now - started) / duration));
      const eased = progress * progress * (3 - 2 * progress);
      trackProgrammaticScroll(root);
      root.scrollTo({top: start + (next.target - start) * eased, behavior: 'instant'});
      if (progress < 1) next.frame = page.requestAnimationFrame(step);
      else scrollAnimations.delete(root);
    };
    scrollAnimations.set(root, next);
    next.frame = page.requestAnimationFrame(step);
    return true;
  }
  trackProgrammaticScroll(root);
  root.scrollTo({top, behavior: smooth && !reduce ? 'smooth' : 'instant'});
  return true;
}

export function scrollToSource(root: HTMLElement | null, sourceId: string, focus = true): boolean {
  const element = root?.ownerDocument.getElementById(sourceId);
  if (!root || !element || !root.contains(element)) return false;
  const reduce = root.ownerDocument.defaultView?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  trackProgrammaticScroll(root);
  element.scrollIntoView({block: 'start', behavior: focus && !reduce ? 'smooth' : 'instant'});
  if (focus) element.focus({preventScroll: true});
  return true;
}

// Put the point being spoken near the middle of the usable reader viewport.
// This is deliberately separate from scrollToReadingPosition: a saved manual
// reading position belongs near the reading inset, while playback benefits from
// context both before and after the current words.
export function scrollToPlaybackPosition(root: HTMLElement | null, sourceId: string, fraction = 0, smooth = false): boolean {
  const element = root?.ownerDocument.getElementById(sourceId);
  if (!root || !element || !root.contains(element) || !Number.isFinite(fraction)) return false;
  const viewport = root.getBoundingClientRect();
  const passage = element.getBoundingClientRect();
  const viewportHeight = viewport.height || root.clientHeight;
  if (viewportHeight <= 0 || passage.height <= 0) return false;
  const point = passage.top + Math.max(0, Math.min(1, fraction)) * passage.height;
  const top = root.scrollTop + point - viewport.top - viewportHeight * .5;
  return scrollReaderTo(root, Math.max(0, top), smooth, true);
}

// Follow the approximate spoken position inside a long EPUB paragraph. The
// reader only advances when that position leaves the visible reading area, so
// ordinary playback does not continually move the page under the listener.
export function followPlaybackProgress(root: HTMLElement | null, sourceId: string, fraction: number): boolean {
  const element = root?.ownerDocument.getElementById(sourceId);
  if (!root || !element || !root.contains(element) || !Number.isFinite(fraction)) return false;
  const viewport = root.getBoundingClientRect();
  const passage = element.getBoundingClientRect();
  if (viewport.height <= 0 || passage.height <= 0) return false;
  const line = passage.top + Math.max(0, Math.min(1, fraction)) * passage.height;
  const lower = viewport.bottom - Math.min(110, viewport.height * .22);
  const upper = viewport.top + Math.min(75, viewport.height * .15);
  if (line >= upper && line <= lower) return true;
  const top = root.scrollTop + line - viewport.top - viewport.height * .5;
  return scrollReaderTo(root, Math.max(0, top), true, false);
}

export function scrollToChapterStart(root: HTMLElement | null, sourceId: string, focus = true): boolean {
  const element = root?.ownerDocument.getElementById(sourceId);
  if (!root || !element || !root.contains(element)) return false;
  scrollReaderTo(root);
  if (focus) element.focus({preventScroll: true});
  return true;
}

function readingInset(root: HTMLElement): number {
  return Math.min(120, root.clientHeight * .2);
}

// Keep the legacy pixel offset as a fallback; the fraction survives paragraph reflow.
export function readingOffset(root: HTMLElement | null, sourceId: string): {offset: number; source_fraction?: number} {
  const offset = root?.scrollTop || 0;
  const element = root?.ownerDocument.getElementById(sourceId);
  if (!root || !element || !root.contains(element)) return {offset};
  const bounds = element.getBoundingClientRect?.();
  if (!bounds || !Number.isFinite(bounds.height) || bounds.height <= 0) return {offset};
  const line = root.getBoundingClientRect().top + readingInset(root);
  return {offset, source_fraction: Math.max(0, Math.min(1, (line - bounds.top) / bounds.height))};
}

export function scrollToReadingPosition(root: HTMLElement | null, sourceId: string, offset = 0, sourceFraction?: number): boolean {
  const element = root?.ownerDocument.getElementById(sourceId);
  if (root && element && root.contains(element) && sourceFraction !== undefined && Number.isFinite(sourceFraction) && sourceFraction >= 0 && sourceFraction <= 1) {
    const bounds = element.getBoundingClientRect();
    if (bounds.height > 0) {
      const top = root.scrollTop + bounds.top - root.getBoundingClientRect().top + sourceFraction * bounds.height - readingInset(root);
      return scrollReaderTo(root, Math.max(0, top));
    }
  }
  if (!root || !Number.isFinite(offset) || offset <= 0) return scrollToSource(root, sourceId, false);
  scrollReaderTo(root, offset);
  return readingSourceAtViewport(root) === sourceId || scrollToSource(root, sourceId, false);
}

export function bindReaderViewport(root: HTMLElement | null): () => void {
  if (!root) return () => {};
  let height = -1;
  const measure = () => {
    const next = root.clientHeight;
    if (next > 0 && next !== height) { height = next; root.style.setProperty('--reading-viewport', next + 'px'); }
  };
  measure();
  const Observer = root.ownerDocument.defaultView?.ResizeObserver;
  if (!Observer) return () => {};
  const observer = new Observer(measure);
  observer.observe(root);
  return () => observer.disconnect();
}

export function readingSourceAtViewport(root: HTMLElement | null): string | null {
  if (!root) return null;
  const top = root.getBoundingClientRect().top + readingInset(root);
  const sources = Array.from(root.querySelectorAll<HTMLElement>('[data-source-id]'));
  let current = sources[0] || null;
  for (const source of sources) {
    if (source.getBoundingClientRect().top > top) break;
    current = source;
  }
  return current?.dataset.sourceId || null;
}

export function bindReadingPosition(root: HTMLElement, onManual: () => void, onSave: (sourceId: string, offset: number, sourceFraction?: number) => void): {flush: () => void; dispose: () => void} {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: {sourceId: string; offset: number; source_fraction?: number} | undefined;
  const flush = () => {
    clearTimeout(timer);
    const position = pending;
    pending = undefined;
    if (position) onSave(position.sourceId, position.offset, position.source_fraction);
  };
  const markManual = () => { cancelReaderAnimation(root); finishProgrammaticScroll(root); onManual(); };
  const press = (event: Event) => { if (!withinControl(event.target)) markManual(); };
  const scroll = () => {
    if (programmaticScrolls.has(root)) { trackProgrammaticScroll(root); return; }
    markManual();
    const sourceId = readingSourceAtViewport(root);
    if (!sourceId) return;
    pending = {sourceId, ...readingOffset(root, sourceId)};
    clearTimeout(timer);
    timer = setTimeout(flush, 500);
  };
  const keyboard = (event: KeyboardEvent) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || !['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(event.key)) return;
    const target = event.target as HTMLElement | null;
    if (target && (target.closest('input,textarea,select,button,[contenteditable="true"],[role="slider"]') || (!root.contains(target) && target !== root.ownerDocument.body))) return;
    markManual();
  };
  const visibility = () => { if (root.ownerDocument.visibilityState === 'hidden') flush(); };
  const endScroll = () => { if (!scrollAnimations.has(root)) finishProgrammaticScroll(root); };
  const page = root.ownerDocument.defaultView;
  root.addEventListener('wheel', markManual, {passive: true});
  root.addEventListener('touchstart', press, {passive: true});
  root.addEventListener('pointerdown', press, {passive: true});
  root.addEventListener('scroll', scroll, {passive: true});
  root.addEventListener('scrollend', endScroll, {passive: true});
  page?.addEventListener('keydown', keyboard);
  page?.addEventListener('pagehide', flush);
  root.ownerDocument.addEventListener('visibilitychange', visibility);
  return {flush, dispose: () => {
    cancelReaderAnimation(root);
    root.removeEventListener('wheel', markManual);
    root.removeEventListener('touchstart', press);
    root.removeEventListener('pointerdown', press);
    root.removeEventListener('scroll', scroll);
    root.removeEventListener('scrollend', endScroll);
    page?.removeEventListener('keydown', keyboard);
    page?.removeEventListener('pagehide', flush);
    root.ownerDocument.removeEventListener('visibilitychange', visibility);
    flush();
  }};
}
