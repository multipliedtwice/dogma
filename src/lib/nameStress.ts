// Name-stress choices for the cast panel. The automatic guess is always made
// by the pipeline; these helpers only let a listener pick another syllable.

export const ACUTE = '́';
const VOWELS = 'аеёиоуыэюяАЕЁИОУЫЭЮЯ';
const WORD = /[А-Яа-яЁё́]+/g;

export type StressWord = {index: number; start: number; plain: string; chosen: number; options: number[]};

// Letter index of the stressed vowel in a marked word, or -1.
export function stressedIndex(word: string): number {
  const at = word.indexOf(ACUTE);
  return at > 0 ? at - 1 : -1;
}

// Words of a name that have a real choice (two or more vowels).
export function stressWords(stressed: string): StressWord[] {
  const words: StressWord[] = [];
  let index = 0;
  for (const match of stressed.matchAll(WORD)) {
    const marked = match[0];
    const plain = marked.split(ACUTE).join('');
    const options = [...plain].flatMap((ch, i) => VOWELS.includes(ch) ? [i] : []);
    if (options.length >= 2) words.push({index, start: match.index ?? 0, plain, chosen: stressedIndex(marked), options});
    index++;
  }
  return words;
}

export function markWord(plain: string, letter: number): string {
  return plain.slice(0, letter + 1) + ACUTE + plain.slice(letter + 1);
}

// Replace one word's stress, keeping every other character of the name.
export function withStress(stressed: string, wordIndex: number, letter: number): string {
  let index = 0;
  return stressed.replace(WORD, marked => index++ === wordIndex ? markWord(marked.split(ACUTE).join(''), letter) : marked);
}

// A few case forms so the listener hears the consequence: Бела́р · Бела́ра · Бела́ру.
export function formsPreview(plain: string, letter: number): string[] {
  const last = plain.slice(-1).toLowerCase();
  const stem = 'аяоеь'.includes(last) ? plain.slice(0, -1) : plain;
  if (letter >= stem.length) return [markWord(plain, letter)];
  const endings = 'ая'.includes(last) ? ['ы', 'е', 'у'] : 'ь'.includes(last) ? ['я', 'ю', 'ем'] : 'ое'.includes(last) ? [] : ['а', 'у', 'ом'];
  return [markWord(plain, letter), ...endings.map(ending => markWord(stem + ending, letter))];
}
