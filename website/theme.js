'use strict';

(() => {
  const root = document.documentElement;
  const preference = matchMedia('(prefers-color-scheme: light)');
  let explicit = null;
  try {
    const stored = localStorage.getItem('dogma-color-theme');
    if (stored === 'dark' || stored === 'light') explicit = stored;
  } catch { /* Use the system preference when storage is unavailable. */ }

  function apply(theme) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'light' ? '#f6f3ea' : '#161b18';
    const button = document.querySelector('.theme-toggle');
    if (!button) return;
    const russian = root.lang === 'ru';
    const label = theme === 'dark'
      ? (russian ? 'Включить светлую тему' : 'Switch to light theme')
      : (russian ? 'Включить тёмную тему' : 'Switch to dark theme');
    button.setAttribute('aria-label', label);
    button.title = label;
    button.dataset.currentTheme = theme;
  }

  apply(explicit ?? (preference.matches ? 'light' : 'dark'));
  preference.addEventListener('change', () => {
    if (!explicit) apply(preference.matches ? 'light' : 'dark');
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply(root.dataset.theme);
    document.querySelector('.theme-toggle')?.addEventListener('click', () => {
      explicit = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('dogma-color-theme', explicit); } catch { /* The toggle still works for this page. */ }
      apply(explicit);
    });
  });
})();
