/* Source-bound bookmarks survive progressive publication and selective retakes. */
(function () {
  'use strict';

  function capturePosition(time, captions, version) {
    if (!Number.isFinite(time) || time < 0 || !captions.length) return null;
    const unit = [...captions].reverse().find(c => time >= c.start);
    if (!unit) return null;
    return {
      unit_id: unit.id,
      offset: Math.min(Math.max(0, time - unit.start), unit.end - unit.start),
      raw_audio_sha256: unit.raw_audio_sha256,
      version: version
    };
  }

  function resumePosition(saved, captions) {
    if (!saved || typeof saved !== 'object' || !Number.isFinite(saved.offset) || saved.offset < 0) return 0;
    const unit = captions.find(c => c.id === saved.unit_id);
    if (!unit) return 0;
    if (saved.raw_audio_sha256 !== unit.raw_audio_sha256) return unit.start;
    return unit.start + Math.min(saved.offset, Math.max(0, unit.end - unit.start));
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {capturePosition, resumePosition};
  if (typeof document === 'undefined') return;

  const config = JSON.parse(document.getElementById('playback-config').textContent);
  const audio = document.querySelector('audio');
  const paragraphs = [...document.querySelectorAll('p[data-start]')];
  const captions = [{id: '__opening__', start: 0, end: config.opening_seconds,
                     raw_audio_sha256: config.opening_sha256}, ...config.captions];
  const storageKey = 'dogma-' + config.generation;
  let restored = false;

  function save() {
    if (!restored) return;
    const position = capturePosition(audio.currentTime, captions, config.version);
    if (position) {
      try { localStorage.setItem(storageKey, JSON.stringify(position)); } catch (_) {}
    }
  }

  function restore() {
    if (restored) return;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) {}
    audio.currentTime = Math.min(resumePosition(saved, captions), Math.max(0, audio.duration || 0));
    restored = true;
    // A reload preserves the source position without starting playback.
  }

  paragraphs.forEach(p => p.onclick = () => {
    audio.currentTime = +p.dataset.start;
    audio.play();
  });
  audio.addEventListener('loadedmetadata', restore);
  if (audio.readyState >= 1) restore();
  audio.addEventListener('timeupdate', () => {
    save();
    paragraphs.forEach(p => p.classList.toggle('active', audio.currentTime >= +p.dataset.start && audio.currentTime < +p.dataset.end));
  });
  audio.addEventListener('pause', save);
  window.addEventListener('pagehide', save);
})();
