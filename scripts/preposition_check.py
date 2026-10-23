"""Phone-level presence check for one-letter prepositions (с, в, к).

Word recognizers fill a swallowed с/в/к back in from context (С потолка was
written although only a 20 ms hiss, 33 dB below the vowels, remained). The
phonetic model writes sounds only. Its phones are aligned to the source phones;
a preposition whose consonant aligns to nothing of its family is reported
missing. Calibration on 421 verified Chapter 1-3 sentences (2026-10-16): 55
reports, 15 of them before a word starting with the same sound (merged, not
missing; `merged` lets callers skip those). Not yet a gate: measured only.
"""
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
import audit_higgs_phone_contrast as pc
from rimsky.spoken_homophones import phrase

FAMILY = {'с': {'s', 'z', 'ʃ', 'ʒ', 'sʲ', 'zʲ', 'ɕ'}, 'в': {'f', 'v', 'fʲ', 'vʲ'}, 'к': {'k', 'g', 'x', 'kʲ', 'gʲ', 'ɣ'}}
MERGE = {'с': 'сзшжщч', 'в': 'вф', 'к': 'кгх'}


def ref_phones(words):
    """(phone, word index) for each phone of each word, through the same espeak path."""
    out = []
    for i, w in enumerate(words):
        for kind, phone, _ in phrase(w):
            out.append((pc.CLASS.get(phone, phone) if kind == 'V' else phone, i))
    return out


def align(ref, hyp):
    """Levenshtein alignment: for each reference phone, the aligned heard phone or None."""
    n, m = len(ref), len(hyp); d = [[0]*(m+1) for _ in range(n+1)]
    for i in range(n+1):d[i][0] = i
    for j in range(m+1):d[0][j] = j
    for i in range(1, n+1):
        for j in range(1, m+1):
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+(ref[i-1][0] != hyp[j-1]))
    i, j, got = n, m, [None]*n
    while i > 0 and j > 0:
        if d[i][j] == d[i-1][j-1]+(ref[i-1][0] != hyp[j-1]):got[i-1] = hyp[j-1]; i -= 1; j -= 1
        elif d[i][j] == d[i-1][j]+1:i -= 1
        else:j -= 1
    return got


def merged(word, following):
    """True when the next word starts with the same sound, so one longer consonant is expected."""
    return bool(following) and following[:1] in MERGE[word]


def check(spoken, wav):
    """[(word index, preposition, 'prep next')] for с/в/к whose consonant was not heard; None if none occur."""
    words = re.findall(r'[а-яё]+', spoken.casefold().replace('́', ''))
    targets = [i for i, w in enumerate(words) if w in FAMILY]
    if not targets:return None
    ref = ref_phones(words); got = align(ref, pc.decode(Path(wav)))
    missing = []
    for t in targets:
        idx = [k for k, (_, wi) in enumerate(ref) if wi == t]
        if not idx or all(got[k] is None or got[k] not in FAMILY[words[t]] for k in idx):
            missing.append((t, words[t], ' '.join(words[t:t+2])))
    return missing
