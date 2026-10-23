const generatedPrefixes: Array<[string, string]> = [
  ['Codex illustration · ', 'Иллюстрация Codex · '],
  ['Generated illustration · ', 'Созданная иллюстрация · '],
];

export function visualCaption(caption: string, language: 'en' | 'ru'): string {
  const match = generatedPrefixes.find(([english]) => caption.startsWith(english));
  return match ? (language === 'ru' ? match[1] : match[0]) + caption.slice(match[0].length) : caption;
}
