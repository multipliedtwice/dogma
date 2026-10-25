"""One stress per proper-noun family, taken from its base form (user rule, 2026-10-16).

"For all names the stress should stay in the same place as in original form of
name." Character names get their stress from the analysis provider
(rimsky.name_stress); other proper nouns of the book (races, places, titles:
Игв, Затолис, Умфанг) otherwise get a separate stress-model guess per inflected
form (Игва́). Here every capitalized, non-dictionary word that is not a
character name is grouped with its inflections; the shortest form is the base.
A one-vowel base is stressed on that vowel; a longer base gets one silero-stress
guess in isolation. The result is a lexicon in the character-name format, so
rimsky.name_stress carries the base stress into every capitalized form.
"""
from __future__ import annotations
import re
from functools import lru_cache
from .name_stress import ACUTE, ENDINGS, VOWELS, build

RULE = 'proper-noun-base-stress-v1'
# v2 (2026-10-17, stress-rare-v3): a base seen only sentence-initially (Нимрас) is a candidate, and an
# oblique-only family (Нимраса) takes its stress from the derived nominative stem instead of staying
# unmarked for the rare-word pass (which stressed the oblique form itself: Нимраса́).
RULE_V2 = 'proper-noun-base-stress-v2'
RULE_SOURCE = 'proper-noun-explicit-source-stress-v3'
OBLIQUE = ('ами', 'ями', 'ой', 'ом', 'ем', 'ам', 'ям', 'ах', 'ях', 'а', 'я', 'у', 'ю', 'е', 'и', 'ы')


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def _coinages(texts) -> set[str]:
    """Lowercase invented words of >=3 letters (игв, кроколиск): not dictionary words."""
    return {m[0] for text in texts for m in re.finditer(r'(?<![А-Яа-яЁё])[а-яё]{3,}(?![А-Яа-яЁё])', text)
            if not _morph().word_is_known(m[0])}


def _candidates(texts, initial: bool = False, *, acute_transparent: bool = False) -> set[str]:
    """Capitalized mid-sentence words of >=3 letters that are not dictionary words."""
    found = set()
    for text in texts:
        start = True
        pattern=r'[А-Яа-яЁё́]+|[.!?…]'if acute_transparent else r'[А-Яа-яЁё]+|[.!?…]'
        for match in re.finditer(pattern, text):
            token = match[0].replace(ACUTE,'')if acute_transparent else match[0]
            if token in '.!?…':start = True; continue
            # All-caps tokens are shouting (ХАААРРР), not names.
            if ((initial or not start) and token[0].isupper() and not token.isupper() and len(token) >= 3
                    and not _morph().word_is_known(token.casefold())):found.add(token)
            start = False
    return found


def _base_of(word: str, bases: list[str]) -> str | None:
    w = word.casefold()
    for base in bases:
        b = base.casefold()
        stems = [b] + ([b[:-1]] if b[-1] in 'аяоейь' else [])
        if any(w == stem + ending for stem in stems for ending in ENDINGS):return base
    return None


def _stress(base: str, derive: bool = False) -> str | None:
    vowels = [i for i, ch in enumerate(base) if ch in VOWELS]
    if len(vowels) <= 1:return base  # implicit: build(implicit_single=True) stresses the only vowel
    from .auto_pronunciation import _accentor
    marked = _accentor()(base, put_stress=True, put_stress_homo=True, put_yo=False, put_yo_homo=False, stress_single_vowel=False)
    if marked.count('+') != 1:return None
    i = marked.index('+')
    if i + 1 >= len(marked) or marked[i + 1] not in VOWELS:return None
    if marked[i + 1] in 'ёЁ':return base  # ё is always stressed; protect the family unmarked
    # A base seen only in an oblique case (Солтмира, Равви́ром) whose guessed
    # stress falls on the case ending would spread a likely wrong stress to the
    # stem; leave such a family to per-form reading instead.
    oblique = next((e for e in OBLIQUE if base.casefold().endswith(e)), None)
    if oblique and i >= len(base) - len(oblique):
        # v2: stress the nominative stem (Нимраса -> Нимрас) and carry its position to the family.
        stem = base[:-len(oblique)] if derive else ''
        if not stem or not any(ch in VOWELS for ch in stem):return None
        stressed = _stress(stem, False)
        if stressed and ACUTE not in stressed and 'ё' not in stressed.casefold():
            v = next(k for k, ch in enumerate(stressed) if ch in VOWELS); stressed = stressed[:v + 1] + ACUTE + stressed[v + 1:]
        return stressed + base[len(stem):] if stressed else None
    return marked[:i] + marked[i + 1] + ACUTE + marked[i + 2:]


def proper_noun_lexicon(texts, character_lexicon: dict[str, str], v2: bool = False,
                        *, explicit_stress: list[dict] | None = None,
                        source_evidence: list[dict] | None = None) -> dict[str, str]:
    """Stressed base forms: 'pn:' proper nouns (capitalized forms only) and
    'cw:' invented lowercase words (any case; they cannot clash with ordinary words)."""
    texts = list(texts)
    known = build(character_lexicon, implicit_single=True)
    lexicon = {}
    source_marks=explicit_stress is not None
    families = (('pn:', _candidates(texts, v2,acute_transparent=source_marks)),
                ('cw:', _coinages([text.replace(ACUTE,'')for text in texts]if source_marks else texts)))
    # v2: sentence-initial candidates overlap the coined words; the any-case 'cw:' entry must win (игв/Игв).
    for prefix, found in (families[::-1] if v2 else families):
        words = sorted((w for w in found if w.casefold().replace('ё', 'е') not in known), key=lambda w: (len(w), w))
        bases: list[str] = []
        for word in words:
            if _base_of(word, bases) is None:bases.append(word)
        for base in bases:
            choices=set();sources=[]
            if source_marks:
                from .name_stress import parse,aligned_stress_index
                for evidence in explicit_stress:
                    plain,index=parse(evidence['word'])
                    if evidence.get('plain')!=plain or evidence.get('stress')!=index:
                        raise ValueError('Explicit source-name stress evidence changed')
                    if plain.casefold().replace('ё','е')in known:continue
                    matching=[candidate for candidate in bases if _base_of(plain,[candidate])is not None]
                    if base not in matching:continue
                    if len(matching)!=1:raise ValueError('Ambiguous explicit source-name family: '+plain)
                    mapped=aligned_stress_index(plain,base,index)
                    if mapped is None:raise ValueError('Explicit source-name stress loses its vowel: '+plain)
                    choices.add(mapped);sources.append(evidence)
                if len(choices)>1:raise ValueError('Conflicting explicit source-name stresses: '+base)
            if choices:
                index=next(iter(choices));stressed=base[:index+1]+ACUTE+base[index+1:]
            else:stressed = _stress(base, v2)
            if stressed and not any(k[3:].casefold() == base.casefold() for k in lexicon):
                lexicon[prefix + base] = stressed
                if choices and source_evidence is not None:
                    source_evidence.append({'family':prefix+base,'stressed':stressed,'sources':sources})
    return lexicon
