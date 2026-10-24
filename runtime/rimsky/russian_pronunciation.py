"""Source-bound Russian phonetic spellings for synthesis input only.

This deliberately closed rule applies the user's two approved phonetic
spellings, never arbitrary -ого/-его endings or general vowel reduction. It
does not rewrite source text, choose stress, or establish what audio says.
"""
from __future__ import annotations

import unicodedata


RULE = 'russian-pronoun-phonetic-v1'
_ACUTE = '\u0301'
_HYPHENS = '-\u2010\u2011'
_PRONOUNS = frozenset(('кого', 'никого'))
_PARTICLES = frozenset(('то', 'либо', 'нибудь'))


def _word_character(char: str) -> bool:
    # Combining marks and joiners must not expose a matching substring inside
    # an otherwise unknown word. Only the known acute mark is ignored below.
    return (char.isalnum() or char == '_' or char in '\u200c\u200d'
            or unicodedata.category(char).startswith('M'))


def _tokens(text: str):
    position = 0
    while position < len(text):
        if not _word_character(text[position]):
            position += 1
            continue
        start = position
        position += 1
        while position < len(text):
            if _word_character(text[position]):
                position += 1
            elif (text[position] in _HYPHENS and position + 1 < len(text)
                  and _word_character(text[position + 1])):
                position += 1
            else:
                break
        yield start, position, text[start:position]


def _pronoun_component(token: str) -> tuple[int, int] | None:
    # Hyphenated forms are whole lexical tokens. Unknown compounds stay intact.
    normalized = token.lower().replace(_ACUTE, '')
    for hyphen in _HYPHENS[1:]:
        normalized = normalized.replace(hyphen, '-')
    if normalized in _PRONOUNS:
        return 0, len(token)
    if normalized.startswith('кого-') and normalized[5:] in _PARTICLES:
        end = next(i for i, char in enumerate(token) if char in _HYPHENS)
        return 0, end
    if normalized == 'кое-кого':
        start = next(i for i, char in enumerate(token) if char in _HYPHENS) + 1
        return start, len(token)
    return None


def apply(text: str) -> tuple[str, list[dict]]:
    """Return phonetic model input and exact input-relative substitution records.

    Every record binds a complete pronoun component to ``[start, end)`` Python
    character offsets. Length, case, acute stress marks and all surrounding
    punctuation remain unchanged. Keep the original input separately for
    display/alignment/audit and include this rule and records in synthesis keys.
    """
    if not isinstance(text, str):
        raise ValueError('Russian pronunciation input must be text')
    result = list(text)
    records = []
    for start, _end, token in _tokens(text):
        component = _pronoun_component(token)
        if component is None:
            continue
        local_start, local_end = component
        start, end = start + local_start, start + local_end
        original = text[start:end]
        # User-approved exact forms: кого -> каво and никого -> никаво.
        # The first о and the single г change; particles and stress stay put.
        chars = list(original)
        first_o = next(i for i, char in enumerate(chars) if char.lower() == 'о')
        chars[first_o] = 'А' if chars[first_o].isupper() else 'а'
        spoken = ''.join(chars).replace('г', 'в').replace('Г', 'В')
        result[start:end] = spoken
        records.append({'start': start, 'end': end, 'original': original,
                        'spoken': spoken, 'rule': RULE})
    return ''.join(result), records


def validate(source: str, spoken: str, records: list[dict] | None = None) -> None:
    """Reject anything other than the exact closed-rule transformation.

    Recompute from the original input, rather than trusting supplied spans or
    accepting a global letter equivalence. When records are supplied, all exact
    spans, spellings, ordering and rule identifiers must also match.
    """
    expected, expected_records = apply(source)
    if not isinstance(spoken, str) or spoken != expected:
        raise ValueError('Russian pronunciation changes unapproved text or omits an approved substitution')
    if records is not None:
        if (not isinstance(records, list)
                or any(not isinstance(record, dict)
                       or type(record.get('start')) is not int
                       or type(record.get('end')) is not int for record in records)
                or records != expected_records):
            raise ValueError('Russian pronunciation records do not match the source-bound substitutions')
