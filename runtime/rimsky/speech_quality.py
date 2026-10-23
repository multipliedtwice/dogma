"""Independent spoken-word check; recognizer never receives the expected text."""
import re
from .util import digest


def word_tokens(text):
    # Hyphens and spaces both separate components. Punctuation/case and Russian
    # e/yo spelling are not speech mismatches; joined words remain different.
    return re.findall(r'[^\W_]+', text.casefold().replace('ё', 'е').replace('\u0301',''), flags=re.UNICODE)


def spoken_word_tokens(text):
    """Spelled sustained sounds are one utterance, not extra lexical words.

    Only trailing single-letter repetitions of the word's final letter are
    collapsed. Compounds and repeated whole words keep their ordinary tokens.
    Source text and the actual synthesis input are never changed here.
    """
    def sustained(match):
        word, tail = match.groups()
        letters = re.findall(r'[А-Яа-яЁё]', tail)
        return word if all(letter.casefold() == word[-1].casefold() for letter in letters) else match[0]
    canonical = re.sub(r'(?<!\w)([А-Яа-яЁё]+)((?:[-‐‑–][А-Яа-яЁё])+)(?!\w)', sustained, text)
    return word_tokens(canonical)


def transcript_check(expected, heard, audio_sha256, verifier_sha256, normalize_elongations=False):
    tokenize = spoken_word_tokens if normalize_elongations else word_tokens
    wanted, actual = tokenize(expected), tokenize(heard)
    exact = bool(wanted) and wanted == actual
    return {'schema': 1, 'status': 'asr_exact_match' if exact else 'spoken_text_mismatch',
            'audio_sha256': audio_sha256, 'verifier_sha256': verifier_sha256,
            'expected_text_sha256': digest(expected.encode('utf-8')), 'heard': heard,
            'expected_words': wanted, 'heard_words': actual}
