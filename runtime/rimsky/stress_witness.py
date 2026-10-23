"""Acoustic word-stress witness as a retake trigger (never a gate or a hold).

Speech recognizers are stress-blind. For words carrying exactly one U+0301 in the
synthesis text, the espeak phones are CTC-aligned to the take (wav2vec2-xlsr
phoneme model) and a conditional-logit model calibrated on Vlad's narration scores
each vowel. A take is `suspect` when the intended vowel loses to another by more
than THETA. Prototype and calibration: scripts/measure_stress_witness.py.
Measured 2026-10-17 (5-fold by chapter): argmax 82.5%; theta=2 rejects 3.7% of
correct words and catches 69% of wrong, theta=3 1.4% / 52%; THETA sits between.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

RULE = 'stress-witness-trigger-v1'
THETA = 2.5
ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / 'training-work/models/wav2vec2-xlsr-phonemes'
WEIGHTS = ROOT / 'training-work/stress-witness-v1/human-calibration.json'
ACUTE = '́'
VOWEL_LETTERS = 'аеёиоуыэюя'
SR = 16000
HOP = 320  # model frame = 20 ms
FULL_GROUPS = {
    'а': 'a ɑ ä aː ɑː æ', 'я': 'a ɑ ä aː ɑː æ ja', 'о': 'o ɔ oː ɔː o̞', 'ё': 'o ɔ oː ɔː o̞',
    'е': 'e ɛ eː ɛː e̞', 'э': 'e ɛ eː ɛː e̞', 'и': 'i iː', 'у': 'u uː ju', 'ю': 'u uː ju', 'ы': 'ɨ y',
}
REDUCED = 'ə ɐ ʌ ɪ ᵻ ɜ ɚ ɜː'.split()


@lru_cache(maxsize=1)
def weights():
    raw = WEIGHTS.read_bytes()
    return json.loads(raw.decode('utf8'))['final_weights_list'], hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=1)
def _vocab():
    return json.loads((MODEL_DIR / 'vocab.json').read_text('utf8'))


@lru_cache(maxsize=1)
def _phonemizer():
    from espeak_phonemizer import Phonemizer
    return Phonemizer(default_voice='ru')


def _tokenize(piece):
    vocab = _vocab()
    piece = piece.replace('ˈ', '').replace('ˌ', '')
    ids, i = [], 0
    while i < len(piece):
        for j in range(len(piece), i, -1):
            if piece[i:j] in vocab:
                ids.append(piece[i:j]); i = j; break
        else:
            i += 1
    return ids


def sentence_words(text):
    return re.findall(r'[А-Яа-яЁё]+', text.replace(ACUTE, ''))


def phonemize_words(words):
    raw = _phonemizer().phonemize(' '.join(w.lower() for w in words), phoneme_separator='_', word_separator=' ')
    parts = re.sub(r'\([a-z\-]+\)', ' ', raw).split()
    if len(parts) != len(words):
        return None
    return [[t2 for t in part.split('_') for t2 in _tokenize(t)] for part in parts]


def is_vowel_token(tok):
    return tok[0] in 'aɑʌəoɐiɪeɛɨuʊyæɔɜᵻɚäø' or (tok[0] == 'j' and len(tok) > 1)


def marked_words(text):
    """[(plain word, intended vowel index, position among the sentence's words)] for words with exactly one U+0301."""
    res = []
    for pos, tok in enumerate(re.findall('[А-Яа-яЁё' + ACUTE + ']+', text)):
        if tok.count(ACUTE) != 1:
            continue
        idx = sum(c in VOWEL_LETTERS for c in tok[:tok.index(ACUTE)].lower()) - 1
        res.append((tok.replace(ACUTE, ''), idx, pos))
    return res


_M = None
LOADS = 0  # model loads in this process (cost accounting)


def model():
    global _M, LOADS
    if _M is None:
        LOADS += 1
        import torch
        from transformers import Wav2Vec2ForCTC
        torch.set_num_threads(4)
        m = Wav2Vec2ForCTC.from_pretrained(MODEL_DIR, local_files_only=True).eval()
        dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        _M = (m.to(dev), dev)
    return _M


def release():
    """Free the model (call before any native Higgs launch or other GPU recognizer)."""
    global _M
    if _M is None:
        return
    _M = None
    import gc
    gc.collect()
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _load_audio(path):
    import soundfile as sf
    from scipy.signal import resample_poly
    data, rate = sf.read(str(path), dtype='float32', always_2d=True)
    mono = data.mean(axis=1)
    if rate != SR:
        g = math.gcd(rate, SR)
        mono = resample_poly(mono, SR // g, rate // g).astype('float32')
    return mono


def _emissions(wav):
    import torch
    m, dev = model()
    x = torch.from_numpy(wav)
    x = (x - x.mean()) / (x.std() + 1e-7)
    with torch.inference_mode():
        logits = m(x[None].to(dev)).logits[0].float()
    return torch.log_softmax(logits, -1).cpu()


def _f0(wav, frames):
    import librosa
    import numpy as np
    f0, _, _ = librosa.pyin(wav, fmin=65, fmax=420, sr=SR, frame_length=1024, hop_length=HOP, fill_na=np.nan)
    f0 = f0[:frames]
    return f0 if len(f0) >= frames else np.concatenate([f0, np.full(frames - len(f0), np.nan)])


def features(wav_path, text, wanted):
    """{sentence word position: (word, z-scored vowel feature rows)} for the wanted positions with >= 2 vowels."""
    import numpy as np
    import torch
    import torchaudio.functional as AF
    words = sentence_words(text)
    ph = phonemize_words(words) if words else None
    if ph is None:
        return {}
    wav = _load_audio(wav_path)
    em = _emissions(wav)
    T = em.shape[0]
    vocab = _vocab()
    seq, owner = [], []
    for wi, toks in enumerate(ph):
        vi = 0
        for t in toks:
            if is_vowel_token(t):
                owner.append((wi, vi)); vi += 1
            else:
                owner.append((wi, -1))
            seq.append(t)
    if len(seq) + 2 > T:
        return {}
    try:
        align, _ = AF.forced_align(em[None], torch.tensor([[vocab[t] for t in seq]], dtype=torch.int32), blank=0)
    except Exception:  # noqa: BLE001
        return {}
    spans = AF.merge_tokens(align[0], torch.zeros(T), blank=0)
    if len(spans) != len(seq):
        return {}
    probs = em.exp().numpy()
    rms_db = 20 * np.log10(np.sqrt(np.convolve(wav ** 2, np.ones(HOP) / HOP, mode='same')) + 1e-5)
    f0 = _f0(wav, T)
    semis = 12 * np.log2(np.where(np.isfinite(f0), f0, np.nan) / 100.0)
    red_ids = [vocab[t] for t in REDUCED if t in vocab]
    out = {}
    for wi in sorted(wanted):
        if wi >= len(words):
            continue
        idx = [k for k, o in enumerate(owner) if o[0] == wi and o[1] >= 0]
        letters = [c for c in words[wi].lower() if c in VOWEL_LETTERS]
        if len(idx) < 2 or len(idx) != len(letters):
            continue
        rows = []
        for n, k in enumerate(idx):
            s = spans[k].start
            prev_end = spans[k - 1].end if k > 0 else max(s - 2, 0)
            nxt = spans[k + 1].start if k + 1 < len(spans) else min(spans[k].end + 2, T)
            lo = max(int(math.floor((prev_end + s) / 2)), 0)
            hi = max(min(int(math.ceil((spans[k].end + nxt) / 2)), T), lo + 1)
            r = rms_db[lo * HOP:hi * HOP + 1]
            seg = semis[lo:hi]
            seg = seg[np.isfinite(seg)]
            full = [vocab[t] for t in FULL_GROUPS[letters[n]].split() if t in vocab]
            pf = float(probs[lo:hi][:, full].max()) if full else 1e-6
            pr = float(probs[lo:hi][:, red_ids].max())
            red = math.log(pf + 1e-4) - math.log(pr + 1e-4)
            rows.append([math.log((hi - lo) * 0.02), float(r.max()) if len(r) else -100.0,
                         float(seg.mean()) if len(seg) else float('nan'), float(seg.max()) if len(seg) else float('nan'),
                         red, math.log(pf + 1e-4), math.log(pr + 1e-4), red])
        a = np.array(rows, dtype=float)
        for c in (2, 3):
            col = a[:, c]
            col[:] = 0.0 if np.isnan(col).all() else np.where(np.isnan(col), np.nanmean(col), col)
        z = a.copy()
        for c in range(7):
            sd = a[:, c].std()
            z[:, c] = (a[:, c] - a[:, c].mean()) / (sd if sd > 1e-6 else 1.0)
        out[wi] = (words[wi], z.tolist())
    return out


def scores(x, w):
    import numpy as np
    return np.array(x) @ np.array(w)


def margin(x, w, j):
    """score(j) - best other score: >= 0 means the intended vowel is the argmax."""
    s = scores(x, w)
    return float(s[j] - max(s[i] for i in range(len(s)) if i != j))


def judge(wav, synthesis_text):
    w, digest = weights()
    marked = marked_words(synthesis_text or '')
    rows = []
    if marked:
        feats = features(wav, synthesis_text, {p for _, _, p in marked})
        for word, idx, pos in marked:
            if pos in feats and 0 <= idx < len(feats[pos][1]):
                x = feats[pos][1]
                rows.append({'word': word, 'intended': idx, 'predicted': int(scores(x, w).argmax()), 'margin': margin(x, w, idx)})
    worst = min((r['margin'] for r in rows), default=None)
    return {'words': rows, 'suspect': worst is not None and worst < -THETA, 'worst_margin': worst,
            'rule': RULE, 'weights_sha256': digest, 'theta': THETA}


def judge_batch(items):
    """{key: judge result or None} for [(key, wav, synthesis text)] with one model load, released afterwards.
    A failure leaves that item unmeasured (None): the witness is a trigger, never a gate."""
    out = {}
    try:
        for key, wav, text in items:
            try:
                r = judge(wav, text)
            except Exception:  # noqa: BLE001
                r = None
            out[key] = r if r and r['words'] else None
    finally:
        release()
    return out
