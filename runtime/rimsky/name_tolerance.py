"""Proper nouns are not spelling-checked (user decisions, 2026-10-16).

A recognizer never sees the text, so for an invented name it can only guess a
spelling, and its vocabulary pulls the guess toward real words (Мозес heard as
Моисей). The listener judged small pronunciation slips in names not worth
retakes, unlike wrong stress, and asked to skip the recognizer for proper
nouns. This is a disclosed tolerance, not a spoken equivalence: every skipped
name is recorded with the instruction. Two guards keep a name from silently
disappearing: the heard slot must not be empty, and it must have about the
same number of syllables. Every other word of the sentence still has to match.
"""
from __future__ import annotations
import difflib
import re
from functools import lru_cache

RULE = 'proper-noun-slot-v1'
INSTRUCTIONS = ('such errors where voiceless consonant is mispronounced - is not a big deal I think. '
                'Small pronounciation errors is not as bad as incorrect stress marks.',
                'maybe just skip recognizer for proper nouns?')
VOWELS = frozenset('аеёиоуыэюя')
_WORD = re.compile(r'[^\W\d_]+', re.UNICODE)


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def _vowels(words) -> int:
    return sum(ch in VOWELS for w in words for ch in w.casefold())


def proper_nouns(text: str) -> set[str]:
    """Lower-cased capitalized words: mid-sentence, or sentence-initial and not a dictionary word."""
    found = set(); start = True
    # A hyphenated name (Ар-Хакон) counts as a whole: every part joins the set.
    for match in re.finditer(r'[^\W\d_]+(?:[-‐‑][^\W\d_]+)*|[.!?…]', text.replace('́', '')):
        token = match[0]
        if token in '.!?…':
            start = True; continue
        parts = [w.casefold().replace('ё', 'е') for w in re.split('[-‐‑]', token)]
        if token[0].isupper() and (not start or not all(_morph().word_is_known(w) for w in parts)):
            found.update(parts)
        # Invented lowercase words (игв, кроколиск): recognizers cannot spell them either
        # (iteration 3: игв heard as Игвы/Игва). Same guards as names.
        elif len(token) >= 3 and not token.isupper() and not any(_morph().word_is_known(w) for w in parts):
            found.update(parts); _INVENTED.update(parts)
        start = False
    return found


# A line made only of interjections: recognizers write a hummed угу as ммм and a
# laugh хо-хо as ха-ха. Within such a line a heard interjection of the same kind
# is the same small pronunciation difference; inside ordinary sentences nothing
# changes. Мда heard as да stays flagged (a sound is missing, not varied).
INTERJECTION_RULE = 'interjection-kind-v1'
_KINDS = {'hum': frozenset('угу ага м мм ммм хм хмм гм эм э'.split()), 'laugh': frozenset('ха хо хе хи'.split()),
          'sustained-u': frozenset('у оу о уу ооо'.split()),
          # Drum/beat chants (Пум-пум-пурум heard Пом-пом, iteration 6).
          'beat': frozenset('пум пом бум бом бам пам тум том там пурум пуром парам тарам'.split())}
# Hisses (ФС-С-С, Ш-ш-ш, Тс-с): recognizers write them in either alphabet (С С С, FSSS).
_INVENTED: set = set()  # lowercase invented words seen by proper_nouns(); they also need a similar spelling


def _similar(source: str, heard: str) -> bool:
    """Same first letter and at most 40% of letters different (игв/игвы yes, нахрена/зачем no)."""
    if not source or not heard or source[0] != heard[0]:return False
    previous = list(range(len(heard) + 1))
    for i, a in enumerate(source, 1):
        current = [i]
        for j, b in enumerate(heard, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] <= max(1, int(0.4 * max(len(source), len(heard))))


_HISS = re.compile(r'[фf]?[сsшц]+h?|[тt][сs]+|[пp][сs]+|sh+|ш+')


def _kind(word: str) -> str | None:
    if _HISS.fullmatch(word):return 'hiss'
    collapsed = re.sub(r'(.)\1+', r'\1\1', word)
    return next((k for k, words in _KINDS.items() if word in words or collapsed in words), None)


def interjection_tolerance(expected_words: list[str], heard_words: list[str]) -> list[dict] | None:
    """Changes when a whole interjection line was heard as interjections of the same kinds."""
    if not expected_words or not heard_words or any(_kind(w) is None for w in expected_words): return None
    kinds = {_kind(w) for w in expected_words}
    if len(kinds) != 1 or any(_kind(w) not in kinds for w in heard_words): return None
    if next(iter(kinds)) in ('laugh', 'beat') and abs(len(expected_words) - len(heard_words)) > 1: return None
    return [{'source_tokens': list(expected_words), 'heard_tokens': list(heard_words), 'rule': INTERJECTION_RULE,
             'kind': 'interjection_of_the_same_kind', 'user_instructions': [INSTRUCTIONS[0]]}]


def leading_interjection_tolerance(expected_words: list[str], heard_words: list[str]) -> tuple[list[str], list[dict]] | None:
    """A sentence opening with a hesitation or sustained sound (Эм-м, а нахрена?) heard as
    the same kind (м анахрина): the opening counts as that small pronunciation difference
    (iteration 2, 2026-10-16); every other word must still match, sound-alike rules included."""
    if len(expected_words) < 2 or len(heard_words) < 2: return None
    if expected_words[0] in ('угу', 'ага'): return None  # words of assent, not hesitation
    kind = _kind(expected_words[0])
    if kind is None or _kind(heard_words[0]) != kind or expected_words[0] == heard_words[0]: return None
    from .spoken_homophones import homophone_equivalence
    rest, sound_alike = homophone_equivalence(expected_words[1:], heard_words[1:])
    if rest != expected_words[1:]: return None
    return list(expected_words), [{'source_tokens': [expected_words[0]], 'heard_tokens': [heard_words[0]], 'rule': INTERJECTION_RULE,
                                   'kind': 'leading_interjection_of_the_same_kind', 'user_instructions': [INSTRUCTIONS[0]]},
                                  *[{**c, 'source_index': c['source_index'] + 1} for c in sound_alike]]


RESPELLING_RULE = 'listener-respelling-v1'
RESPELLING_INSTRUCTION = ('listener request 2026-10-21: the synthesis text respells the printed word (е as э or ё) '
                          'to fix its sound; the recognizer writes either spelling')
HARD_E_INSTRUCTION = ('user decision 2026-10-21 (automatic hard-е respelling, rule hard-e-respelling-v1): the synthesis text '
                      'respells the printed word (е as э) as a careful Russian narrator says borrowed and invented names; '
                      'the recognizer writes either spelling')


def respelling_tolerance(expected_words: list[str], heard_words: list[str], respellings) -> tuple[list[str], list[dict]]:
    """Heard words where a listener-respelled source word was written as the spoken (or printed) spelling.

    `respellings` are the unit's recorded {'printed','spoken'} pairs (rimsky.respelling). Only the
    exact tokens that differ between printed and spoken form are covered, and only a heard token
    that equals the printed or the spoken token (ё folded to е) counts. Anything else stays a mismatch.
    """
    from .respelling import validate_respelling, fold
    pairs = {}; origin = {}
    for item in respellings or ():
        printed, spoken = item['printed'], item['spoken']
        validate_respelling(printed, spoken)
        for a, b in zip(re.split('[-‐‑]', printed), re.split('[-‐‑]', spoken)):
            a, b = a.casefold().replace('ё', 'е').replace('́', ''), b.casefold().replace('́', '')
            if a != b.replace('ё', 'е'):
                pairs.setdefault(a, set()).update({a, b.replace('ё', 'е')})
                if item.get('source'):origin.setdefault(a, item['source'])
    if not pairs or expected_words == heard_words:return list(heard_words), []
    corrected = []; changes = []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=expected_words, b=heard_words, autojunk=False).get_opcodes():
        source, heard = expected_words[i:j], heard_words[k:l]
        if tag == 'replace' and len(source) == len(heard):
            for offset, (a, b) in enumerate(zip(source, heard)):
                if a in pairs and b in pairs[a]:
                    corrected.append(a)
                    changes.append({'source_tokens': [a], 'heard_tokens': [b], 'source_index': i + offset, 'rule': RESPELLING_RULE,
                                    'kind': 'listener_respelling_not_spelling_checked', 'user_instructions': [HARD_E_INSTRUCTION if origin.get(a) == 'model' else RESPELLING_INSTRUCTION],
                                    **({'respelling_source': origin[a]} if a in origin else {})})
                else:corrected.append(b)
        else:corrected.extend(heard)
    return (corrected if changes else list(heard_words)), changes


def tolerance(expected_words: list[str], heard_words: list[str], source_text: str, respellings=None) -> tuple[list[str], list[dict]]:
    """Accept any non-empty, similar-length heard slot where the source has only proper nouns.

    `respellings` (a unit's recorded listener respellings) first reconciles a respelled slot with its
    source spelling; the result is a disclosed 'listener-respelling-v1' deviation next to any others.
    """
    if respellings:
        heard_words, respelled = respelling_tolerance(expected_words, heard_words, respellings)
        if respelled:
            corrected, changes = tolerance(expected_words, heard_words, source_text)
            return corrected, [*respelled, *changes]
    if expected_words != heard_words:
        changes = interjection_tolerance(expected_words, heard_words)
        if changes: return list(expected_words), changes
        leading = leading_interjection_tolerance(expected_words, heard_words)
        if leading: return leading
    names = proper_nouns(source_text)
    corrected = []; changes = []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=expected_words, b=heard_words, autojunk=False).get_opcodes():
        source, heard = expected_words[i:j], heard_words[k:l]
        invented = [w for w in source if w in _INVENTED]
        if (tag == 'replace' and source and all(w in names for w in source) and heard
                and abs(_vowels(source) - _vowels(heard)) <= 1 and len(heard) <= len(source) + 1
                and (not invented or _similar(''.join(source), ''.join(heard)))):
            corrected.extend(source)
            changes.append({'source_tokens': source, 'heard_tokens': heard, 'source_index': i, 'rule': RULE,
                            'kind': 'proper_noun_not_spelling_checked', 'user_instructions': list(INSTRUCTIONS)})
        else:
            corrected.extend(heard)
    return corrected, changes
