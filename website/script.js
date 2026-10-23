'use strict';

(() => {
  const audio = document.querySelector('#story-audio');
  const reader = document.querySelector('#story-reader');
  const follow = document.querySelector('#follow-audio');
  const note = document.querySelector('#playback-note');
  const base = new URL('.', document.currentScript.src);
  const MEDIA_TIME_EPSILON = .001;
  const russian = document.documentElement.lang === 'ru';
  const messages = russian ? {
    preparing: 'Готовится', ready: 'Можно слушать', complete: 'Рассказ целиком',
    preparingAudio: 'Английская запись готовится. Пожалуйста, загляните чуть позже.',
    unavailable: 'Английская запись временно недоступна. Пожалуйста, попробуйте чуть позже.',
    failedAudio: 'Не удалось загрузить запись. Обновите страницу или попробуйте чуть позже.',
    missingText: 'Текст пока недоступен. Аудио можно слушать.',
    hint: 'Выберите фрагмент текста, чтобы перейти к его началу в аудио.',
    resume: value => `Продолжаем с ${value}. Позиция сохраняется в этом браузере.`,
    seek: value => `Перейти к ${value}`,
    selected: value => `Выбран фрагмент с ${value}. Нажмите «Воспроизвести», чтобы слушать.`,
    ended: 'Конец. Спасибо, что слушали.', album: 'Озвучено в Dogma'
  } : {
    preparing: 'In preparation', ready: 'Ready to listen', complete: 'Complete story',
    preparingAudio: 'The English recording is being prepared. Please check back shortly.',
    unavailable: 'The English recording is temporarily unavailable. Please check back shortly.',
    failedAudio: 'The recording could not be loaded. Please refresh or try again shortly.',
    missingText: 'Read-along text is unavailable. The audio is still ready to listen.',
    hint: 'Select any passage to move the audio to its beginning.',
    resume: value => `Resumed at ${value}. Your place is saved in this browser.`,
    seek: value => `Seek to ${value}`,
    selected: value => `Ready at ${value}. Press play to listen.`,
    ended: 'The end. Thank you for listening.', album: 'Narrated with Dogma'
  };
  let book, captions = [], captionButtons = [], current = -1, lastSaved = 0;

  const time = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
  function localAsset(path) {
    if (typeof path !== 'string' || !path.startsWith('demos/')) throw new Error('Unexpected demo asset path');
    const url = new URL(path, base);
    if (url.origin !== base.origin || !url.pathname.startsWith(`${base.pathname}demos/`) || url.search || url.hash) throw new Error('Unexpected demo asset URL');
    return url.href;
  }
  async function json(url) {
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error('Demo data unavailable');
    return response.json();
  }
  function storage(value) {
    try {
      const key = `dogma-listen-${book.id}`;
      if (value !== undefined) localStorage.setItem(key, JSON.stringify(value));
      else return JSON.parse(localStorage.getItem(key) || 'null');
    } catch { /* Playback also works without browser storage. */ }
    return null;
  }
  function captionAt(position) {
    let low = 0, high = captions.length - 1, result = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (captions[middle].start <= position + MEDIA_TIME_EPSILON) { result = middle; low = middle + 1; }
      else high = middle - 1;
    }
    return result >= 0 && position <= captions[result].end + MEDIA_TIME_EPSILON ? result : -1;
  }
  function savePosition() {
    if (!book || !Number.isFinite(audio.currentTime)) return;
    let index = captionAt(audio.currentTime);
    if (index < 0 && audio.currentTime + MEDIA_TIME_EPSILON >= (captions[0]?.start ?? Infinity)) {
      index = captions.findLastIndex(caption => caption.start <= audio.currentTime + MEDIA_TIME_EPSILON);
    }
    const caption = captions[index];
    storage({ source: book.source, version: book.audio, unit_id: caption?.id ?? null, raw_audio_sha256: caption?.raw_audio_sha256 ?? null,
      offset: caption ? Math.max(0, Math.min(audio.currentTime - caption.start, caption.end - caption.start)) : audio.currentTime,
      rate: audio.playbackRate });
  }
  function restorePosition() {
    const saved = storage();
    if (saved && saved.source !== book.source) { audio.currentTime = 0; return; }
    if (!saved || !Number.isFinite(saved.offset) || saved.offset < 0) return;
    let position = 0;
    const caption = captions.find(row => row.id === saved.unit_id);
    if (caption) {
      const sameAudio = caption.raw_audio_sha256 ? caption.raw_audio_sha256 === saved.raw_audio_sha256 : saved.version === book.audio;
      position = caption.start + (sameAudio ? Math.min(saved.offset, caption.end - caption.start) : 0);
    } else if (!saved.unit_id && saved.version === book.audio) position = saved.offset;
    if (Number.isFinite(audio.duration)) position = Math.min(position, Math.max(0, audio.duration - .1));
    audio.currentTime = Math.max(0, position);
    if ([.75, 1, 1.25, 1.5, 2].includes(saved.rate)) {
      audio.playbackRate = saved.rate;
      document.querySelector('#playback-rate').value = String(saved.rate);
    }
    if (position > 1) note.textContent = messages.resume(time(position));
  }
  function updateReader() {
    const index = captionAt(audio.currentTime);
    if (index !== current) {
      if (current >= 0) captionButtons[current].removeAttribute('aria-current');
      current = index;
      if (index >= 0) {
        const target = captionButtons[index];
        target.setAttribute('aria-current', 'true');
        if (follow.checked) {
          const outer = reader.getBoundingClientRect(), inner = target.getBoundingClientRect();
          if (inner.top < outer.top + 40 || inner.bottom > outer.bottom - 40) {
            reader.scrollTo({ top: reader.scrollTop + inner.top - outer.top - reader.clientHeight * .35, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
          }
        }
      }
    }
    if (Date.now() - lastSaved > 2000) { savePosition(); lastSaved = Date.now(); }
  }
  function mountCaptions(rows) {
    if (!Array.isArray(rows) || !rows.length) throw new Error('No read-along text');
    let previousEnd = 0;
    const ids = new Set();
    captions = rows.map(row => {
      if (typeof row.id !== 'string' || ids.has(row.id) || typeof row.text !== 'string' || !row.text.trim() ||
          !Number.isFinite(row.start) || !Number.isFinite(row.end) || row.start < previousEnd || row.end <= row.start) throw new Error('Invalid read-along timing');
      ids.add(row.id); previousEnd = row.end;
      return row;
    });
    reader.replaceChildren();
    let paragraph, source;
    captions.forEach((caption, index) => {
      if (!paragraph || !caption.source_id || caption.source_id !== source) {
        paragraph = document.createElement('p'); paragraph.className = 'story-paragraph';
        reader.append(paragraph); source = caption.source_id;
      }
      const button = document.createElement('button');
      button.className = 'caption'; button.type = 'button'; button.textContent = caption.text.trim();
      button.title = messages.seek(time(caption.start));
      button.addEventListener('click', () => {
        audio.currentTime = caption.start;
        updateReader(); savePosition();
        if (audio.paused) note.textContent = messages.selected(time(caption.start));
      });
      paragraph.append(button, document.createTextNode(' ')); captionButtons[index] = button;
    });
    document.querySelector('#reader-hint').textContent = messages.hint;
    follow.disabled = false;
  }
  function unavailable(message) {
    document.querySelector('#demo-status').textContent = messages.preparing;
    document.querySelector('#audio-unavailable').textContent = message;
    document.querySelector('#audio-unavailable').hidden = false;
    document.querySelector('#player-region').hidden = true;
  }
  async function initialize() {
    follow.disabled = true;
    try {
      const catalog = await json(new URL('demos/catalog.json', base));
      if (!Array.isArray(catalog)) throw new Error('Invalid catalog');
      book = catalog.find(row => row.id === 'en' && row.status === 'ready');
      if (!book) { unavailable(messages.preparingAudio); return; }
      const audioURL = localAsset(book.audio);
      document.querySelector('#demo-title').textContent = book.title;
      document.querySelector('.demo-author').textContent = book.author;
      if (Number.isFinite(book.duration) && book.duration > 0) document.querySelector('#demo-duration').textContent = `${time(book.duration)} · ${messages.complete}`;
      document.querySelector('#demo-status').textContent = messages.ready;
      document.querySelector('#audio-unavailable').hidden = true;
      document.querySelector('#player-region').hidden = false;
      document.querySelector('#download-audio').href = audioURL;
      const source = new URL(book.source);
      if (source.protocol === 'https:') document.querySelector('#story-source').href = source.href;
      if ('mediaSession' in navigator && typeof MediaMetadata !== 'undefined') {
        navigator.mediaSession.metadata = new MediaMetadata({ title: book.title, artist: book.author, album: messages.album });
      }
      try {
        const data = await json(localAsset(book.captions));
        mountCaptions(Array.isArray(data) ? data : data.captions);
      } catch {
        document.querySelector('#reader-hint').textContent = messages.missingText;
      }
      audio.addEventListener('loadedmetadata', restorePosition, { once: true });
      audio.addEventListener('durationchange', () => { if (Number.isFinite(audio.duration)) document.querySelector('#demo-duration').textContent = `${time(audio.duration)} · ${messages.complete}`; });
      audio.addEventListener('timeupdate', updateReader);
      audio.addEventListener('pause', savePosition);
      audio.addEventListener('seeked', () => { updateReader(); savePosition(); });
      audio.addEventListener('error', () => unavailable(messages.failedAudio));
      audio.addEventListener('ended', () => { note.textContent = messages.ended; savePosition(); });
      document.querySelector('#playback-rate').addEventListener('change', event => { audio.playbackRate = Number(event.target.value); savePosition(); });
      document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(); });
      window.addEventListener('pagehide', savePosition);
      audio.src = audioURL;
    } catch { unavailable(messages.unavailable); }
  }
  initialize();
})();
