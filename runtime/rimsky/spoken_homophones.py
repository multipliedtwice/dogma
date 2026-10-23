"""Russian spellings that ordinary speech cannot tell apart (recognizer orthography).

A recognizer must choose one spelling for sounds that standard Russian
pronunciation merges: word spacing (не правда/неправда), geminate letters
(цеппелины/цепелины), iotated vowels (ямарайаху/ямараяху) and unstressed
vowel reduction (пугала/пугало). Those choices are orthography, not evidence
that the audio differs. This module recognises exactly those differences and
nothing else:

* consonants are compared after espeak-ng grapheme-to-phoneme conversion, so
  softness, devoicing and assimilation must be identical;
* vowels may differ only inside the akanye (а/о/ə) and ikanye (и/е/э) classes;
* wherever either spelling is stressed the vowel letters must agree, and two
  dictionary words must share their stress positions (было/была differ);
* dictionary-word pairs that differ in a vowel letter are accepted only when the
  pair is listed in ``REVIEWED_LEXICAL_PAIRS`` after a stress review, because
  espeak's stress dictionary is occasionally wrong (могла/могло, слова/слова).

Calibration (2026-10-15, docs/CORE_NARRATION_SPEC.md, "Recognizer orthography"):
985/988 injected word substitutions on verified takes were rejected; the three
accepted are true homophones (великий/великей, готовит/готовет, хранилище/хранилища).
"""
from __future__ import annotations
import difflib
import re
from functools import lru_cache

RULE = 'russian-reduction-homophone-v1'
VOWEL_PHONES = set('aɑʌəoɐiɪeɛɨuʊy')
CLASS = {'a': 'A', 'ɑ': 'A', 'ʌ': 'A', 'ə': 'A', 'o': 'A', 'ɐ': 'A',
         'i': 'I', 'ɪ': 'I', 'e': 'I', 'ɛ': 'I', 'ɨ': 'Y', 'y': 'Y', 'u': 'U', 'ʊ': 'U'}
VOWEL_LETTERS = 'аеёиоуыэюя'
FAMILY = {'а': 'a', 'я': 'a', 'о': 'o', 'ё': 'o', 'у': 'u', 'ю': 'u', 'е': 'e', 'э': 'e', 'и': 'i', 'ы': 'y'}
CLITICS = frozenset({'не', 'ни', 'на', 'за', 'в', 'во', 'с', 'со', 'к', 'ко', 'по', 'до', 'от', 'из', 'у', 'о',
                     'об', 'при', 'без', 'над', 'под', 'про', 'для', 'а', 'и', 'но', 'да', 'же', 'ли', 'бы', 'то', 'что'})
# (source form, recognizer form): both are dictionary words whose stress was
# checked by hand to fall outside the differing vowel. Extend only after review.
REVIEWED_LEXICAL_PAIRS = frozenset({
    ('мяса', 'мясо'), ('вышла', 'вышло'), ('пугала', 'пугало'), ('брюхо', 'брюха'),
    ('покачивающееся', 'покачивающиеся'), ('озеро', 'озера'),
})
# Unstressed ни and не are both [nʲɪ]; не takes stress before forms of быть
# and in не + preposition + pronoun (не́ было, не́ за что), where they differ.
PARTICLES = frozenset({('ни', 'не'), ('не', 'ни')})
STRESSED_NE_NEXT = frozenset({'был', 'было', 'была', 'были'})
PRONOUNS = frozenset({'кем', 'чем', 'что', 'кого', 'чего', 'кому', 'чему', 'ком', 'чем'})


@lru_cache(maxsize=1)
def _phonemizer():
    from espeak_phonemizer import Phonemizer
    return Phonemizer(default_voice='ru')


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def dependencies():
    import importlib.metadata as metadata
    return {name: metadata.version(name) for name in ('espeak-phonemizer-windows', 'pymorphy3', 'pymorphy3-dicts-ru')}


@lru_cache(maxsize=None)
def phrase(text):
    """(kind, phone, stressed) tuples with word boundaries and geminates removed."""
    raw = re.sub(r'\([a-z\-]+\)', ' ', _phonemizer().phonemize(text, phoneme_separator='_', word_separator=' '))
    out = []
    for word in raw.split():
        for token in word.split('_'):
            stressed = False
            while token and token[0] in 'ˈˌ':
                stressed = stressed or token[0] == 'ˈ'; token = token[1:]
            token = token.replace('ː', '')
            if not token: continue
            if len(token) == 2 and token[0] == 'j' and token[1] in VOWEL_PHONES:
                out.append(('C', 'j', False)); token = token[1]
            out.append(('V', token, stressed) if token[0] in VOWEL_PHONES else ('C', token, False))
    merged = []
    for item in out:
        if merged and item[0] == 'C' and merged[-1][0] == 'C' and merged[-1][1].rstrip('ʲ') == item[1].rstrip('ʲ'):
            merged[-1] = item; continue
        merged.append(item)
    # Between a vowel and и/е, [j] is not articulated distinctly (мои/мойи).
    return tuple(t for i, t in enumerate(merged)
                 if not (t == ('C', 'j', False) and 0 < i < len(merged) - 1 and merged[i - 1][0] == 'V'
                         and merged[i + 1][0] == 'V' and CLASS.get(merged[i + 1][1]) == 'I'))


def _letters(words):
    return [c for c in ''.join(words) if c in VOWEL_LETTERS]


def _clitic_vowels(words):
    result = set(); i = 0
    for word in words:
        n = len(_letters([word]))
        if word in CLITICS: result.update(range(i, i + n))
        i += n
    return result


def _span(source_words, heard_words, stress=None):
    # Letter-based post-tonic endings first: espeak keeps [j] in -ная but drops it in
    # -ное, so phone counts differ for two endings that both sound -нəjə (iteration 5).
    if (len(source_words) == len(heard_words) == 1 and source_words != heard_words
            and all(_morph().word_is_known(w) for w in source_words + heard_words)
            and _post_tonic(source_words, heard_words, stress)):
        return 'post_tonic_reduction'
    a = phrase(' '.join(source_words)); b = phrase(' '.join(heard_words))
    if len(a) != len(b): return None
    for x, y in zip(a, b):
        if x[0] != y[0] or (x[0] == 'C' and x[1] != y[1]): return None
        if x[0] == 'V' and CLASS.get(x[1], x[1]) != CLASS.get(y[1], y[1]): return None
    sl, hl = _letters(source_words), _letters(heard_words)
    sv = [t for t in a if t[0] == 'V']; hv = [t for t in b if t[0] == 'V']
    if not (len(sl) == len(sv) == len(hl) == len(hv)): return None
    clitic = _clitic_vowels(source_words) | _clitic_vowels(heard_words)
    lexical = all(_morph().word_is_known(w) for w in source_words + heard_words)
    vowels_differ = sl != hl
    reviewed = (tuple(source_words) + tuple(heard_words)) in {(x, y) for x, y in REVIEWED_LEXICAL_PAIRS}
    if lexical and vowels_differ and not reviewed:
        return 'post_tonic_reduction' if _post_tonic(source_words, heard_words, stress) else None
    if reviewed: return 'lexical_reviewed_reduction'  # stress checked by hand, not by espeak
    # A misspelling's espeak stress is a guess about a non-word. When the source
    # is one dictionary word whose stress espeak and silero-stress agree on,
    # only that stress matters (ольхи́ heard as альхи); otherwise any stressed
    # position on either side must keep its vowel.
    trusted = (len(source_words) == 1 and _morph().word_is_known(source_words[0])
               and not any(_morph().word_is_known(w) for w in heard_words)
               and _model_stress(source_words[0]) == next((k for k, t in enumerate(sv) if t[2]), None))
    # A non-dictionary source word whose synthesis input carried an explicit
    # stress mark: Higgs was told that stress, so espeak's guess is irrelevant
    # (Затоли́се heard as Заталисе). Only that one position must keep its vowel.
    marked = (stress or {}).get(source_words[0]) if len(source_words) == 1 else None
    if marked is not None and (_morph().word_is_known(source_words[0]) or lexical
                               or any(_morph().word_is_known(w) for w in heard_words) or marked >= len(sv)):
        marked = None
    for k in range(len(sv)):
        if k in clitic: continue
        stressed = (k == marked) if marked is not None else sv[k][2] if trusted else (sv[k][2] or hv[k][2])
        if stressed and FAMILY[sl[k]] != FAMILY[hl[k]]: return None
        if lexical and sv[k][2] != hv[k][2]: return None
    if marked is not None and vowels_differ: return 'unstressed_reduction_marked_stress'
    return 'unstressed_reduction' if vowels_differ else 'spelling_or_spacing'


# After the stressed syllable а/о both sound as [ə] and е/и/я as [ɪ] (уходи́ла /
# уходи́ло, похо́ду / похо́да): no listener can hear which was meant. Two
# dictionary words qualify only with identical consonants, every differing vowel
# after the stress and in one of these classes, and the same stressed syllable
# by silero-stress for both (было/была and городом/городам move the stress and
# stay flagged). An explicit synthesis mark replaces silero for the source.
_POST_TONIC = ('ао', 'еия')


def _post_tonic(source_words, heard_words, stress=None):
    if len(source_words) != 1 or len(heard_words) != 1: return False
    s, h = source_words[0], heard_words[0]
    if len(s) != len(h): return False
    diffs = [i for i, (x, y) in enumerate(zip(s, h)) if x != y]
    if not diffs: return False
    ks = (stress or {}).get(s, _model_stress(s)); kh = _model_stress(h)
    if ks is None or kh is None or ks != kh: return False
    def reduced(word, i):
        # In the last syllable unstressed я sounds as [ə] (like а/о), е/и as [ɪ]:
        # шея/шеи, капля/капле, давит/давят stay apart (iteration 5 calibration).
        last = not any(ch in VOWEL_LETTERS for ch in word[i + 1:])
        if last and (word[i] in 'ая' or (word[i] == 'е' and i == len(word) - 1 and word[i - 1] in VOWEL_LETTERS)): return 'ə'  # -ая/-ое/-ее
        if last and word[i] == 'о': return 'ə'
        if last and word[i] in 'еи': return 'ɪ'
        return next((c for c in _POST_TONIC if word[i] in c), None)
    for i in diffs:
        if reduced(s, i) is None or reduced(s, i) != reduced(h, i): return False
        if sum(ch in VOWEL_LETTERS for ch in s[:i]) <= ks: return False
    return True


def synthesis_stress(text):
    """{source word: stressed vowel index} for words the synthesis input marks with U+0301."""
    marks = {}
    for word in re.findall('[А-Яа-яЁё́]+', text):
        if '́' not in word: continue
        plain = word.replace('́', '').casefold().replace('ё', 'е')
        marks[plain] = sum(c in VOWEL_LETTERS for c in word[:word.index('́')].casefold()) - 1
    return marks


@lru_cache(maxsize=None)
def _model_stress(word):
    """Vowel index silero-stress stresses in an isolated word, or None when unavailable."""
    try:
        from silero_stress import load_accentor
    except ImportError:
        return None
    marked = _accentor_cached()(word, put_yo=False, put_yo_homo=False, stress_single_vowel=False)
    if '+' not in marked: return None
    return sum(c in VOWEL_LETTERS for c in marked[:marked.index('+')].casefold())


@lru_cache(maxsize=1)
def _accentor_cached():
    from silero_stress import load_accentor
    return load_accentor()


def homophone_span(source_words, heard_words, following=(), stress=None):
    """Return None, or how a source span and a heard span are speech-identical."""
    if not source_words or not heard_words or source_words == heard_words: return None
    latin = all(re.fullmatch('[a-z]+', w) for w in source_words + heard_words)
    if not latin and any(not re.fullmatch('[а-яё]+', w) for w in source_words + heard_words): return None
    if latin:
        return ({'source_tokens': list(source_words), 'heard_tokens': list(heard_words),
                 'kind': 'english_letter_homophone', 'rule': RULE}
                if _latin_letter_homophone(source_words, heard_words) else None)
    if len(source_words) == len(heard_words) > 1:
        kinds = []
        for i, (x, y) in enumerate(zip(source_words, heard_words)):
            if x == y: continue
            change = homophone_span([x], [y], tuple(source_words[i + 1:]) + tuple(following), stress)
            if not change: break
            kinds.append(change['kind'])
        else:
            return {'source_tokens': list(source_words), 'heard_tokens': list(heard_words),
                    'kind': '+'.join(sorted(set(kinds))), 'rule': RULE}
    if (tuple(source_words), tuple(heard_words)) in {((x,), (y,)) for x, y in PARTICLES}:
        nxt = list(following[:2])
        if not nxt or nxt[0] in STRESSED_NE_NEXT or (len(nxt) == 2 and nxt[1] in PRONOUNS): return None
        kind = 'unstressed_particle'
    elif len(source_words) == len(heard_words) == 1 and _final_neutralization(source_words[0], heard_words[0]):
        kind = 'unstressed_final_neutralization'
    elif _latin_letter_homophone(source_words, heard_words):
        kind = 'english_letter_homophone'
    else:
        kind = _span(source_words, heard_words, stress)
        if not kind:
            # Russian spelling writes е for a hard [ɛ] in foreign names (Готэн
            # is heard-spelled Готен, as отель/тест). Only non-dictionary words.
            hard = [re.sub('(?<=[бвгджзклмнпрстфхцчшщ])э', 'е', w) if not _morph().word_is_known(w) else w
                    for w in source_words]
            if hard == list(source_words): return None
            if not (hard == list(heard_words) or _span(hard, heard_words)): return None
            kind = 'loanword_e_spelling'
    return {'source_tokens': list(source_words), 'heard_tokens': list(heard_words), 'kind': kind, 'rule': RULE}


# Word-final -ие/-ия and -ище/-ища are never stressed and both end in [ə]
# (здание [ˈzdanʲɪjə], армия [ˈarmʲɪjə]); recognizers pick the likelier case.
# Only inflections of one lemma; -ье/-ья can be stressed (статья́/статье́).
FINAL_PAIRS = ({'ие', 'ия'}, {'ище', 'ища'})


def _final_neutralization(source, heard):
    if source[:-1] != heard[:-1] or not any({source[-len(next(iter(p))):], heard[-len(next(iter(p))):]} == p for p in FINAL_PAIRS):
        return False
    if not (_morph().word_is_known(source) and _morph().word_is_known(heard)): return False
    lemmas = lambda w: {parse.normal_form for parse in _morph().parse(w) if parse.is_known}
    return bool(lemmas(source) & lemmas(heard))


def _latin_letter_homophone(source_words, heard_words):
    """English eye and the letter I are one sound (NS-Eye/NSI); Latin spans only."""
    if not all(re.fullmatch('[a-z]+', w) for w in source_words + heard_words): return False
    return ''.join('i' if w == 'eye' else w for w in source_words) == ''.join(heard_words)


def homophone_equivalence(expected_words, heard_words, stress=None):
    """Replace only speech-identical heard spans by the source spelling."""
    corrected = []; changes = []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=expected_words, b=heard_words, autojunk=False).get_opcodes():
        change = (homophone_span(expected_words[i:j], heard_words[k:l], tuple(expected_words[j:j + 2]), stress)
                  if tag == 'replace' else None)
        if change:
            corrected.extend(expected_words[i:j]); changes.append({**change, 'source_index': i})
        else:
            corrected.extend(heard_words[k:l])
    return corrected, changes


ELONGATION_RULE = 'stylized-sustained-sound-letter-count-v1'
_STYLIZED = re.compile(r'(?<!\w)[А-Яа-яЁё]+(?:[-‐‑–][А-Яа-яЁё]+)*(?!\w)')


def _collapse(text):
    return re.sub(r'(.)\1+', r'\1', text)


def elongation_equivalence(expected_text, expected_words, heard_words):
    """How many letters spell a sustained sound is not a speech difference.

    Only a source word written as a stylized sustained sound qualifies: a
    hyphenated word with repeated letters or single-letter parts (Не-е-ет,
    У-х-х, Гун-да-хааа-ррр, М-м-м), or a non-dictionary word with a repeated
    letter (Шшш). The heard span must collapse to exactly the same letters.
    Real compounds (Ар-Хакон, кто-то) and different vowels (О-оу/О) stay flagged.
    """
    from .speech_quality import spoken_word_tokens
    spans = {}
    for match in _STYLIZED.finditer(expected_text):
        word = match[0]; parts = re.split('[-‐‑–]', word)
        letters = ''.join(parts).casefold().replace('ё', 'е')
        stylized = (len(parts) > 1 and (letters != _collapse(letters) or any(len(p) == 1 for p in parts))) or \
                   (len(parts) == 1 and letters != _collapse(letters) and not _morph().word_is_known(letters))
        if not stylized: continue
        start = len(spoken_word_tokens(expected_text[:match.start()]))
        spans[(start, start + len(spoken_word_tokens(word)))] = (word, _collapse(letters))
    # Map each source position to the heard tokens aligned with it, so a span
    # the recognizer merged (О-оу heard as Оу: a delete plus an equal) or split
    # is compared as a whole.
    opcodes = difflib.SequenceMatcher(a=expected_words, b=heard_words, autojunk=False).get_opcodes()
    corrected = list(heard_words); changes = []; offset = 0
    for (i, j), (word, letters) in sorted(spans.items()):
        lo = hi = None
        for tag, a, b, c, d in opcodes:
            if b <= i or a >= j:
                if tag == 'insert' and i < a < j:
                    lo = c if lo is None else lo; hi = d
                continue
            if tag == 'equal':
                c, d = c + max(i - a, 0), d - max(b - j, 0)
            elif a < i or b > j:
                lo = None; break  # a replace reaching outside the span: not this rule
            lo = c if lo is None else min(lo, c); hi = d if hi is None else max(hi, d)
        if lo is None or hi is None or hi <= lo:
            continue
        heard = heard_words[lo:hi]
        if heard == expected_words[i:j] or _collapse(''.join(heard)) != letters:
            continue
        corrected[lo + offset:hi + offset] = expected_words[i:j]
        offset += (j - i) - (hi - lo)
        changes.append({'source_word': word, 'heard_tokens': heard, 'source_index': i, 'rule': ELONGATION_RULE})
    return corrected, changes
