"""One canonical stress per character name, carried into every inflected form.

Book names are not in any dictionary, so a stress model guesses each form
separately (Б+елар but Бел+ару). A character's profile instead records one
stressed nominative per name word (Бела́р, Ар-Хако́н); every case form that
keeps that stem receives the same stressed vowel: Бела́ра, Бела́ру, Бела́ром.
Explicit marks already in the text win. A form whose stem would lose the
stressed vowel (stress on a dropped final vowel) is left unmarked.
"""
from __future__ import annotations
import re
from .respelling import respelled_letters, substitute

ACUTE = '́'
VOWELS = 'аеёиоуыэюяАЕЁИОУЫЭЮЯ'
# Russian noun case endings for names (masculine/feminine/plural).
ENDINGS = ('', 'а', 'я', 'у', 'ю', 'ом', 'ем', 'ём', 'е', 'ы', 'и', 'ой', 'ою', 'ей', 'ею',
           'ов', 'ев', 'ам', 'ям', 'ами', 'ями', 'ах', 'ях')
_WORD = re.compile(r'[А-Яа-яЁё́]+')


def parse(stressed: str) -> tuple[str, int]:
    """'Бела́р' -> ('Белар', index of the stressed letter)."""
    if stressed.count(ACUTE) != 1:
        raise ValueError(f'Name needs exactly one stress mark: {stressed!r}')
    i = stressed.index(ACUTE)
    if i == 0 or stressed[i - 1] not in VOWELS:
        raise ValueError(f'Stress mark must follow a vowel: {stressed!r}')
    return stressed.replace(ACUTE, ''), i - 1


def _stems(word: str):
    """Stems a declined form can attach endings to: Белар, Маришк(а), Гуль(ь)."""
    yield word
    if word[-1].casefold() in 'аяоейь':
        yield word[:-1]


def _single_vowel(part: str) -> int | None:
    found = [i for i, ch in enumerate(part) if ch in VOWELS]
    return found[0] if len(found) == 1 else None


def _keys(form: str):
    """Lookup keys of a surface form: casefolded, ё as е; a form with э is also found by its printed е."""
    key = form.casefold().replace('ё', 'е')
    yield key
    if 'э' in key:
        yield key.replace('э', 'е')


def _put(forms, respell, form, index, letters):
    for key in _keys(form):
        if key not in forms:
            forms[key] = index
            if letters:
                respell[key] = letters


def _build(lexicon: dict[str, str], implicit_single: bool = False):
    """(forms, respellings): respellings maps a surface to the (index, 'э'|'ё') letters of its stem."""
    forms = {}; respell = {}
    for stressed in lexicon.values():
        for part in re.findall(r'[А-Яа-яЁё́]+', stressed):
            if ACUTE not in part and implicit_single and _single_vowel(part) is not None:
                part = part[:_single_vowel(part) + 1] + ACUTE + part[_single_vowel(part) + 1:]
            yo = [i for i, ch in enumerate(part) if ch in 'ёЁ']
            if ACUTE not in part and len(yo) != 1:
                # Protected from re-reading (Вел must never become Вёл), but unmarked.
                letters = respelled_letters(part)
                for stem in _stems(part) if len(part) > 1 else ():
                    for ending in ENDINGS:
                        _put(forms, respell, stem + ending, None, tuple(x for x in letters if x[0] < len(stem)))
                continue
            # ё is always stressed, so a single ё is the stress of an unmarked form (Мозёс).
            word, index = parse(part) if ACUTE in part else (part, yo[0])
            letters = respelled_letters(word)
            for stem in _stems(word):
                if index >= len(stem):
                    continue
                for ending in ENDINGS:
                    _put(forms, respell, stem + ending, index, tuple(x for x in letters if x[0] < len(stem)))
    return forms, respell


def build(lexicon: dict[str, str], implicit_single: bool = False) -> dict[str, int | None]:
    """Map every inflected surface (casefolded) to its stressed letter index (None: protect only).

    With implicit_single, a one-vowel name (Глас, Игв) is stressed on that vowel,
    so its longer forms keep it there (Гла́су, И́гва), as in the base form.
    A spoken respelling in the stressed name (Мо́зэс for Мозес) is found by the
    printed spelling too; name_spans carries it into every form that keeps the letter.
    """
    return _build(lexicon, implicit_single)[0]


_SENTENCE_START = re.compile(r'(?:^|[.!?…]\s*[«"„“—–-]?\s*|^[«"„“—–-]\s*)$')


def name_spans(text: str, lexicon: dict[str, str], common=frozenset(), implicit_single: bool = False) -> list[tuple[int, int, str]]:
    """(start, end, synthesis form) of every character-name occurrence.

    Only capitalised tokens are names: lowercase вел or атлас are ordinary
    words. A sentence-initial token whose name word is also a common word
    (Старый, Атлас) is ambiguous and left to ordinary reading.
    """
    forms, respell = _build(lexicon, implicit_single)
    # Invented words ('cw:' entries from rimsky.proper_noun_stress) are not
    # dictionary words, so their lowercase forms cannot be ordinary words.
    any_case = _build({k: v for k, v in lexicon.items() if k.startswith('cw:')}, implicit_single)[0]
    common = {w.casefold().replace('ё', 'е') for w in common}
    spans = []
    for match in _WORD.finditer(text):
        word = match[0]
        if not word[:1].isupper() and word.replace(ACUTE, '').casefold().replace('ё', 'е') not in any_case:
            continue
        key = word.replace(ACUTE, '').casefold().replace('ё', 'е')
        if key not in forms:
            continue
        if _SENTENCE_START.search(text[:match.start()]) and any(key.startswith(c[:max(3, len(c) - 1)]) for c in common):
            continue
        index = forms[key]
        # A one-vowel form needs no mark (and marks are noise to the model).
        if index is not None and sum(ch in VOWELS for ch in word) < 2:index = None
        if ACUTE in word:
            marked = word
        else:
            # A listener respelling (е->э/ё) rides along with the stress: Мозеса -> Мо́зэса.
            spoken = substitute(word, respell.get(key, ()))
            # ё is its own stress mark.
            marked = spoken if index is None or spoken[index] in 'ёЁ' else spoken[:index + 1] + ACUTE + spoken[index + 1:]
        spans.append((match.start(), match.end(), marked))
    return spans


def mark_names(text: str, lexicon: dict[str, str], common=frozenset()) -> tuple[str, list[dict]]:
    """Insert the character's stress into every name occurrence in text."""
    out = []; cursor = 0; edits = []
    for start, end, marked in name_spans(text, lexicon, common):
        out.append(text[cursor:start]); out.append(marked); cursor = end
        if marked != text[start:end]:
            edits.append({'start': start, 'word': text[start:end], 'marked': marked})
    out.append(text[cursor:])
    return ''.join(out), edits
