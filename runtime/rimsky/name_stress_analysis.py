"""Educated guess of each character's name stress by the pinned analysis provider.

Nobody sets name stress by hand. The provider sees each display name (with
aliases), every inflected form found in the book with counts and examples, and
a dictionary-model hint, and returns one stressed spelling per name. Answers
are validated mechanically (same letters, one stress per multi-vowel word);
rimsky.name_stress then applies that single decision to every case form.
"""
from __future__ import annotations
import re
from collections import Counter

SYSTEM = (
    'You are an expert Russian audiobook narrator preparing the pronunciation of character names '
    'in a Russian novel. For every name, decide where a careful native narrator would put the stress '
    'so that every case form is read consistently. Use the inflected forms and sentences as evidence '
    'of how the name declines, the apparent origin of the name (Russian, Latin, German, Arabic, '
    'English, invented fantasy words) and how comparable names are normally pronounced in Russian. '
    'The dictionary model hint is a weak guess for invented names and may be wrong. Never change '
    'letters, never add or remove ё, keep spaces, hyphens and parentheses exactly. Put U+0301 '
    'COMBINING ACUTE ACCENT immediately after the stressed vowel of every word that has two or more '
    'vowels; single-vowel words stay unmarked. Return high confidence only for real names with an '
    'established Russian pronunciation.'
)
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['names'], 'properties': {
    'names': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['character_id', 'name', 'stressed', 'confidence', 'basis'], 'properties': {
            'character_id': {'type': 'string'}, 'name': {'type': 'string'}, 'stressed': {'type': 'string'},
            'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
            'basis': {'type': 'string', 'maxLength': 300}}}}}}
_WORD = re.compile(r'[А-ЯЁ][а-яё]+')


def evidence(characters: dict[str, str], chapters_text: list[str], examples: int = 2) -> list[dict]:
    """Inflected forms (stem + up to 3 letters) of every name word, with counts and sentences."""
    sentences = [s for text in chapters_text for s in re.split(r'(?<=[.!?…])\s+', text)]
    rows = []
    for cid, name in characters.items():
        words = {w for w in _WORD.findall(name)}
        forms, samples = Counter(), []
        for sentence in sentences:
            hit = False
            for token in _WORD.findall(sentence):
                for w in words:
                    stem = w[:-1] if w[-1] in 'аяоейь' and len(w) > 3 else w
                    if token.startswith(stem) and len(token) - len(stem) <= 3:
                        forms[token] += 1; hit = True
            if hit and len(samples) < examples:
                samples.append(sentence.strip()[:240])
        rows.append({'character_id': cid, 'name': name, 'forms': dict(forms.most_common(12)), 'examples': samples})
    return rows


def model_hint(name: str) -> str:
    try:
        from silero_stress import load_accentor
    except ImportError:
        return ''
    marked = load_accentor()(name, put_yo=False, put_yo_homo=False, stress_single_vowel=False)
    return re.sub(r'\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])', lambda m: m[1] + '́', marked)


def request(characters: dict[str, str], chapters_text: list[str]) -> dict:
    rows = evidence(characters, chapters_text)
    for row in rows:
        row['dictionary_model_hint'] = model_hint(row['name'])
    return {'task': 'character_name_stress', 'characters': rows}


def validator(characters: dict[str, str]):
    from .auto_pronunciation import validate_name_stress

    def validate(answer: dict) -> None:
        seen = {}
        for row in answer['names']:
            if row['character_id'] not in characters:
                raise ValueError(f"Unknown character {row['character_id']}")
            if row['name'] != characters[row['character_id']]:
                raise ValueError(f"Name changed for {row['character_id']}")
            validate_name_stress(row['name'], row['stressed'])
            seen[row['character_id']] = row
        missing = sorted(set(characters) - set(seen))
        if missing:
            raise ValueError('Missing names: ' + ', '.join(missing))
    return validate


def lexicon(answer: dict) -> dict[str, str]:
    """Character id -> the provider's stressed name (aliases included)."""
    return {row['character_id']: row['stressed'] for row in answer['names']}


def common_words(name_stress: dict[str, str], book_texts: list[str], ratio: float = 0.25) -> list[str]:
    """Name words that the book also uses as ordinary words (Старый, Гром).

    A capitalised one of these at a sentence start may be ordinary prose, so it
    is left to normal reading there; mid-sentence it is always the character.
    Вел (24 capitalised, 1 lowercase in Book 2) stays a name everywhere.
    """
    text = ' '.join(book_texts)
    words = {w.replace('́', '') for value in name_stress.values() for w in re.findall(r'[А-Яа-яЁё́]+', value)}
    result = []
    for word in sorted(words):
        body = re.escape(word[1:].casefold()).replace('е', '[её]').replace('ё', '[её]')
        lower = len(re.findall(r'(?<![\w-])' + re.escape(word[0].lower()) + body + r'(?![\w-])', text))
        upper = len(re.findall(r'(?<![\w-])' + re.escape(word[0].upper()) + body + r'(?![\w-])', text))
        if lower and lower >= ratio * max(upper, 1):
            result.append(word)
    return result
