"""Automatic pronunciation for the synthesis input (never the printed source).

Order of authority:
1. explicit stress marks already in the text;
2. each character's canonical name stress (rimsky.name_stress), proposed by the
   pinned analysis provider and validated here: same letters, one stress per
   multi-vowel word;
3. silero-stress for ё restoration and homograph stress only (по́том/пото́м,
   за́мок/замо́к). Ordinary words keep Higgs's own reading: marking every word
   lengthened a listener-approved take by 0.5 s with an extra pause.

Benchmark, 2026-10-15 (75 targeted cases): silero-stress 70/75 (homographs
41/43, ё 10/10), RUAccent 66/75 (homographs 35/43). silero also invents ё in
names (Вел -> Вёл), so every character-name form is excluded from it.
"""
from __future__ import annotations
import re
from functools import lru_cache
from .name_stress import ACUTE, VOWELS, name_spans, parse
from .util import digest

POLICY = 'auto-pronunciation-v1'
POLICY_ALL = 'stress-all-v1'  # experiment: every multi-vowel word stressed
# Higgs reads one mark on an unfamiliar word (по́том worked), but a mark on every
# word is unfamiliar input and made stress worse. This policy adds silero's
# stress only to the long tail: words Higgs has no reading habit for.
POLICY_RARE = 'stress-rare-v1'
# v2 (user rule, 2026-10-16): every form of a name keeps the base form's stress,
# including one-vowel names (Глас -> Гла́су) and non-character proper nouns
# (Игв -> И́гва) from rimsky.proper_noun_stress.
POLICY_RARE2 = 'stress-rare-v2'
# v3 (2026-10-17): v2 + homograph overrides adjudicated by a text-only model where silero and
# RUAccent disagree (rimsky.homograph_stress); 5 of 287 chapter-3 marks were wrong (плоти́, яйца́).
POLICY_RARE3 = 'stress-rare-v3'
# v4 (2026-10-21): v3 + ё decided only by rimsky.yo_restoration (silero's ё off) + close-mark guard, so no
# acute is forced on the е of a missed ё (мароде́рки) and no redundant mark sits beside a ё (тебе́ серьёзный).
POLICY_RARE4 = 'stress-rare-v4'
RARE_ZIPF = 2.0   # wordfreq Zipf; below ~1 occurrence per million words
RARE_LIMIT = 2    # at most this many added marks per synthesis unit, rarest first


def policy_options(policy: str, spec: dict | None = None) -> dict:
    options = {POLICY: {}, POLICY_ALL: {'every_word': True}, POLICY_RARE: {'rare_words': True},
               POLICY_RARE2: {'rare_words': True, 'implicit_single': True}}
    if policy == POLICY_RARE3:
        homograph = (spec or {}).get('homograph_stress')
        if not homograph:
            raise ValueError('Policy stress-rare-v3 needs the pronunciation spec homograph_stress answer')
        return {'rare_words': True, 'implicit_single': True, 'homograph': homograph}
    if policy == POLICY_RARE4:
        s = spec or {}
        if not s.get('homograph_stress') or not s.get('yo_restoration'):
            raise ValueError('Policy stress-rare-v4 needs the pronunciation spec homograph_stress and yo_restoration answers')
        return {'rare_words': True, 'implicit_single': True, 'homograph': s['homograph_stress'], 'yo': s['yo_restoration']}
    if policy not in options:
        raise ValueError('Unknown pronunciation policy')
    return options[policy]
_WORD = re.compile(r'[А-Яа-яЁё́]+')


@lru_cache(maxsize=1)
def _accentor():
    from silero_stress import load_accentor
    return load_accentor()


def validate_name_stress(name: str, stressed: str) -> None:
    """The proposal must spell the name exactly (a listener respelling е->э/ё aside) and stress each multi-vowel word once."""
    from .respelling import validate_respelling
    validate_respelling(name, stressed)  # ValueError on any other letter change
    for part in re.split(r'[\s\-(),]+', stressed):
        if not part:
            continue
        vowels = sum(ch in VOWELS for ch in part)
        marks = part.count(ACUTE)
        if vowels >= 2 and marks != 1 and not (marks == 0 and sum(ch in 'ёЁ' for ch in part) == 1):  # ё is its own stress
            raise ValueError(f'{part!r} needs exactly one stress mark')
        if marks:
            parse(part)


def _silero(text: str, ignore: list[str], every_word: bool = False, yo: bool = True) -> str:
    marked = _accentor()(text, put_stress=every_word, put_stress_homo=True, put_yo=yo, put_yo_homo=yo,
                         stress_single_vowel=False,
                         # silero matches ignored words in lower case only (Вел would become Вёл).
                         words_to_ignore=sorted({w.casefold() for w in ignore}) or None)
    # silero writes '+' before the stressed vowel; the synthesis input uses U+0301 after it.
    # ё is always stressed; a mark after it is redundant for the synthesis input.
    return re.sub(r'\+([' + VOWELS + '])', lambda m: m[1] + ('' if m[1] in 'ёЁ' else ACUTE), marked)


def _zipf(word: str) -> float | None:
    plain = word.replace(ACUTE, '').casefold()
    if sum(ch in VOWELS for ch in plain) < 2:
        return None
    from wordfreq import zipf_frequency
    return zipf_frequency(plain, 'ru')


def _yo_fold(word: str) -> str:
    return word.replace(ACUTE, '').replace('ё', 'е').replace('Ё', 'Е')


def _keep_layer_letters(source: str, marked: str) -> str:
    """v4: silero's word letters reverted to the ё layer's (an ё silero added on its own is dropped with its stress)."""
    a, b = list(_WORD.finditer(source)), list(_WORD.finditer(marked))
    if [_yo_fold(m[0]) for m in a] != [_yo_fold(m[0]) for m in b]:
        raise ValueError('Stress pass changed the words')
    out = marked
    for x, y in reversed(list(zip(a, b))):
        if x[0].replace(ACUTE, '') != y[0].replace(ACUTE, ''):
            out = out[:y.start()] + x[0] + out[y.end():]
    return out


def _long_tail(sparse: str, dense: str, exclude=None, yo_layer: bool = False) -> tuple[str, list[str]]:
    """sparse with dense's stress on its rarest unmarked words (at most RARE_LIMIT).

    With the ё layer (v4) silero runs with put_yo off, yet its every-word pass still writes ё
    (возней -> вознёй, черных -> чёрных; c0002 stopped 2026-10-21). Words then compare with ё
    folded, and a dense word whose letters differ is never transferred: ё is the layer's decision.
    """
    a, b = list(_WORD.finditer(sparse)), list(_WORD.finditer(dense))
    key = _yo_fold if yo_layer else (lambda w: w.replace(ACUTE, ''))
    if [key(m[0]) for m in a] != [key(m[0]) for m in b]:
        raise ValueError('Stress passes disagree on the words')
    rare = []
    for i, (x, y) in enumerate(zip(a, b)):
        if ACUTE in x[0] or ACUTE not in y[0]:
            continue
        if x[0].replace(ACUTE, '') != y[0].replace(ACUTE, ''):
            continue
        if exclude and exclude(sparse, x.start(), x[0]):
            continue
        zipf = _zipf(y[0])
        if zipf is not None and zipf < RARE_ZIPF:
            rare.append((zipf, i))
    chosen = sorted(i for _, i in sorted(rare)[:RARE_LIMIT])
    out = sparse
    for i in reversed(chosen):
        out = out[:a[i].start()] + b[i][0] + out[a[i].end():]
    return out, [b[i][0] for i in chosen]


def _is_name_like(text: str, start: int, word: str) -> bool:
    """v3: proper nouns and coined words are stressed only via the family lexicon, never per inflected form."""
    from .homograph_stress import is_proper
    from .proper_noun_stress import _morph
    return is_proper(text, start, word) or (word.islower() and not _morph().word_is_known(word.casefold()))


def _masked(text: str, name_stress: dict[str, str], common, implicit_single: bool):
    """text with names and explicitly stressed words swapped for placeholders: (pieces, restore, spans)."""
    spans = name_spans(text, name_stress, common, implicit_single)
    taken = {(s, e) for s, e, _ in spans}
    spans += [(m.start(), m.end(), m[0]) for m in _WORD.finditer(text)
              if ACUTE in m[0] and not any(s <= m.start() < e for s, e in taken)]
    spans.sort()
    pieces = []; cursor = 0; restore = {}
    for i, (start, end, marked) in enumerate(spans):
        token = f'Zq{i}z'
        pieces += [text[cursor:start], token]; restore[token] = marked; cursor = end
    pieces.append(text[cursor:])
    return pieces, restore, spans


def _repieces(text: str, spans) -> list[str]:
    """text with the given name/mark spans swapped for the placeholders _masked uses."""
    pieces = []; cursor = 0
    for i, (start, end, _) in enumerate(spans):
        pieces += [text[cursor:start], f'Zq{i}z']; cursor = end
    return pieces + [text[cursor:]]


def yo_witnesses(text: str, name_stress: dict[str, str], common=frozenset(), implicit_single: bool = True):
    """(spans, silero words with ё on, silero every-word stress words with ё off) for scripts/build_yo_restoration.py."""
    from .yo_restoration import normalize_marked_e
    text, _ = normalize_marked_e(text)
    pieces, restore, spans = _masked(text, name_stress, common, implicit_single)
    back = lambda t: re.sub(r'Zq\d+z', lambda m: restore[m[0]], t)
    words = lambda t: [m[0] for m in _WORD.finditer(t)]
    return spans, words(back(_silero(''.join(pieces), [], False))), words(back(_silero(''.join(pieces), [], True, False)))


def homograph_text(text: str, name_stress: dict[str, str], common=frozenset(), implicit_single: bool = True) -> str:
    """The text with silero's ё and homograph marks only (names masked, no long-tail marks)."""
    pieces, restore, _ = _masked(text, name_stress, common, implicit_single)
    return re.sub(r'Zq\d+z', lambda m: restore[m[0]], _silero(''.join(pieces), [], False))


def auto_pronunciation(text: str, name_stress: dict[str, str], common=frozenset(), every_word: bool = False,
                       rare_words: bool = False, implicit_single: bool = False,
                       homograph: dict | None = None, yo: dict | None = None) -> tuple[str, dict | None]:
    """Return (synthesis text, provenance or None when nothing changed).

    Name occurrences and explicitly stressed words are swapped for neutral
    placeholders before the stress model runs, so it can neither re-stress
    nor yofify them (Вел never becomes Вёл), while ordinary words (вёл, пёс)
    still get their ё and homograph fixes.
    """
    source_text = text
    marked_e_record = None
    if yo:
        from .yo_restoration import normalize_marked_e
        text, marked_e_record = normalize_marked_e(text)
    pieces, restore, spans = _masked(text, name_stress, common, implicit_single)
    yo_record = None
    if yo:
        from .yo_restoration import restore_yo
        yo_text, yo_record = restore_yo(text, spans, yo)
        pieces = _repieces(yo_text, spans)
    # Keep Silero's homograph pass identical to the one used by the frozen
    # homograph builder.  Passing put_yo=False looks as though it only controls
    # letters, but silero-stress also drops some homograph marks in that mode
    # (for example прода́л in c0001-u0470).  The v4 ё layer remains authoritative:
    # _keep_layer_letters immediately restores its exact е/ё spelling while
    # retaining homograph stress on words whose letters did not change.
    marked = _silero(''.join(pieces), [], every_word, True); rare = []
    if yo:marked = _keep_layer_letters(''.join(pieces), marked)
    if rare_words:
        marked, rare = _long_tail(marked, _silero(''.join(pieces), [], True, not yo), _is_name_like if homograph else None, bool(yo))
    result = re.sub(r'Zq\d+z', lambda m: restore[m[0]], marked)
    homograph_record = None
    if homograph:
        from .homograph_stress import apply, RULE
        if homograph.get('rule') != RULE:raise ValueError('Unknown homograph stress rule')
        result, applied, unbound = apply(result, homograph['overrides'], source_text)
        homograph_record = {'rule': RULE, 'answer_sha256': homograph['answer_sha256'], 'applied': applied, 'unbound': unbound}
    yo_extra = {}
    if yo:
        from .yo_restoration import drop_held_marks, close_mark_guard, GUARD_RULE
        keep = {(m_['word'].casefold().replace('ё', 'е'), m_['index']) for m_ in (homograph or {}).get('overrides', [])
                if m_['sha256'] == digest(source_text.encode('utf8'))}
        result, dropped_e = drop_held_marks(text, result, yo_record, keep)
        guarded = {(m_['word'].casefold().replace('ё', 'е'), m_['index']) for m_ in (homograph_record or {}).get('applied', [])} | keep
        result, close = close_mark_guard(text, result, spans, guarded)
        yo_extra = {'yo_restoration': {**yo_record, 'marked_e_normalization': marked_e_record,
                                       'dropped_e_marks': dropped_e},
                    'close_mark_guard': {'rule': GUARD_RULE, 'dropped': close}}
    name_edits = [{'word': text[s:e], 'marked': m} for s, e, m in spans if m != text[s:e]]
    source_words = [w.casefold().replace('ё', 'е') for w in _WORD.findall(source_text.replace(ACUTE, ''))]
    result_words = [w.casefold().replace('ё', 'е') for w in _WORD.findall(result.replace(ACUTE, ''))]
    if source_words != result_words:
        raise ValueError('Automatic pronunciation changed the words')
    if result == text and not yo:  # v4 always reports: the answer hash is part of the synthesis key
        return text, None
    if rare_words:
        import importlib.metadata as metadata
        return result, {'policy': POLICY_RARE4 if yo else POLICY_RARE3 if homograph else POLICY_RARE2 if implicit_single else POLICY_RARE, 'name_edits': name_edits, 'model': 'silero-stress',
                        'model_scope': 'yo+homographs+long-tail words', 'rare_words': rare,
                        'rare_rule': {'zipf_below': RARE_ZIPF, 'limit': RARE_LIMIT,
                                      'wordfreq': metadata.version('wordfreq')},
                        **({'homograph_overrides': homograph_record} if homograph_record else {}), **yo_extra}
    return result, {'policy': POLICY_ALL if every_word else POLICY, 'name_edits': name_edits,
                    'model': 'silero-stress', 'model_scope': 'every word' if every_word else 'yo+homographs only'}
