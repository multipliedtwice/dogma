"""Acoustic Russian word-stress witness: prototype and calibration (measure only).

Question asked of a take: for a word with a known intended stress, is the
intended vowel the prominent one in the audio?  Speech recognizers are
stress-blind, so this is a separate witness.  Nothing here is wired into the
pipeline.

Method
1. The espeak phone sequence of the (unmarked) sentence is force-aligned (CTC
   Viterbi, torchaudio) to the audio using the multilingual phoneme model
   training-work/models/wav2vec2-xlsr-phonemes.  Phone strings come from
   espeak without stress marks because espeak mishandles U+0301.
2. Every vowel of a multi-vowel target word gets a region (midpoints to the
   neighbouring phone spikes) and prosodic/spectral features: log duration,
   peak RMS, mean/max F0, and a reduction cue (log posterior of the letter's
   full vowel minus the reduced vowels [ə ɐ ɪ ʌ ...]).  Features are z-scored
   within the word.
3. A small conditional-logit model (softmax over the vowels of a word) turns the
   features into p(stress = vowel i).  The witness accepts the intended vowel
   when its score is within `theta` of the best vowel.

Modes
  human   calibrate on Vlad's reviewed narration (manual-labeling, read only)
  higgs   apply the human-trained model to marked words of a Higgs generation
Results go to training-work/stress-witness-v1/.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

os.environ.setdefault('NUMBA_NUM_THREADS', '4')
os.environ.setdefault('OMP_NUM_THREADS', '4')
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime')]
OUT = ROOT / 'training-work/stress-witness-v1'
MODEL_DIR = ROOT / 'training-work/models/wav2vec2-xlsr-phonemes'
ACUTE = '́'
VOWEL_LETTERS = 'аеёиоуыэюя'
SR = 16000
HOP = 320  # model frame = 20 ms

FULL_GROUPS = {  # letter -> full-vowel phones the model may emit
    'а': 'a ɑ ä aː ɑː æ', 'я': 'a ɑ ä aː ɑː æ ja', 'о': 'o ɔ oː ɔː o̞', 'ё': 'o ɔ oː ɔː o̞',
    'е': 'e ɛ eː ɛː e̞', 'э': 'e ɛ eː ɛː e̞', 'и': 'i iː', 'у': 'u uː ju', 'ю': 'u uː ju', 'ы': 'ɨ y',
}
REDUCED = 'ə ɐ ʌ ɪ ᵻ ɜ ɚ ɜː'.split()
FEATURES = ['dur', 'rms', 'f0mean', 'f0max', 'red', 'logpf', 'logpr', 'red_raw']


# ---------------------------------------------------------------- phones
@lru_cache(maxsize=1)
def _vocab():
    return json.loads((MODEL_DIR / 'vocab.json').read_text('utf8'))


@lru_cache(maxsize=1)
def _phonemizer():
    from espeak_phonemizer import Phonemizer
    return Phonemizer(default_voice='ru')


def _tokenize(piece: str):
    vocab = _vocab()
    piece = piece.replace('ˈ', '').replace('ˌ', '')
    ids = []
    i = 0
    while i < len(piece):
        for j in range(len(piece), i, -1):
            if piece[i:j] in vocab:
                ids.append(piece[i:j]); i = j; break
        else:
            i += 1  # unknown symbol (length mark etc.)
    return ids


def sentence_words(text: str):
    """Lower-case letter words; hyphenated words are split like espeak does."""
    clean = text.replace(ACUTE, '')
    return [w for w in re.findall(r'[А-Яа-яЁё]+', clean)]


def phonemize_words(words):
    raw = _phonemizer().phonemize(' '.join(w.lower() for w in words), phoneme_separator='_', word_separator=' ')
    raw = re.sub(r'\([a-z\-]+\)', ' ', raw)
    parts = raw.split()
    if len(parts) != len(words):
        return None
    out = []
    for part in parts:
        toks = []
        for t in part.split('_'):
            toks.extend(_tokenize(t))
        out.append(toks)
    return out


def is_vowel_token(tok: str) -> bool:
    return tok[0] in 'aɑʌəoɐiɪeɛɨuʊyæɔɜᵻɚäø' or (tok[0] == 'j' and len(tok) > 1)


# ---------------------------------------------------------------- model
_M = None


def model():
    global _M
    if _M is None:
        import torch
        from transformers import Wav2Vec2ForCTC
        torch.set_num_threads(4)
        m = Wav2Vec2ForCTC.from_pretrained(MODEL_DIR, local_files_only=True).eval()
        dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        _M = (m.to(dev), dev)
    return _M


def release():
    global _M
    _M = None
    import gc
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_audio(path):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    data, rate = sf.read(str(path), dtype='float32', always_2d=True)
    mono = data.mean(axis=1)
    if rate != SR:
        g = math.gcd(rate, SR)
        mono = resample_poly(mono, SR // g, rate // g).astype('float32')
    return mono


def emissions(wav):
    import torch
    m, dev = model()
    x = torch.from_numpy(wav)
    x = (x - x.mean()) / (x.std() + 1e-7)
    with torch.inference_mode():
        logits = m(x[None].to(dev)).logits[0].float()
    return torch.log_softmax(logits, -1).cpu()


def f0_track(wav, frames):
    import librosa
    import numpy as np
    f0, _, _ = librosa.pyin(wav, fmin=65, fmax=420, sr=SR, frame_length=1024, hop_length=HOP, fill_na=np.nan)
    f0 = f0[:frames]
    if len(f0) < frames:
        f0 = np.concatenate([f0, np.full(frames - len(f0), np.nan)])
    return f0


def analyze(wav_path, text, min_vowels=2):
    """Per multi-vowel word: feature matrix (n_vowels x len(FEATURES)). Returns (words, info)."""
    import numpy as np
    import torch
    import torchaudio.functional as AF
    words = sentence_words(text)
    if not words:
        return [], 'no-words'
    ph = phonemize_words(words)
    if ph is None:
        return [], 'phonemizer-word-count-mismatch'
    wav = load_audio(wav_path)
    em = emissions(wav)
    T = em.shape[0]
    vocab = _vocab()
    seq, owner = [], []  # token list, (word index, vowel index or -1)
    for wi, toks in enumerate(ph):
        vi = 0
        for t in toks:
            if is_vowel_token(t):
                owner.append((wi, vi)); vi += 1
            else:
                owner.append((wi, -1))
            seq.append(t)
    if len(seq) + 2 > T:
        return [], 'too-short'
    targets = torch.tensor([[vocab[t] for t in seq]], dtype=torch.int32)
    try:
        align, _ = AF.forced_align(em[None], targets, blank=0)
    except Exception as e:  # noqa: BLE001
        return [], f'align-failed:{e}'
    spans = AF.merge_tokens(align[0], torch.zeros(T), blank=0)
    if len(spans) != len(seq):
        return [], 'span-count-mismatch'
    probs = em.exp().numpy()
    rms = np.sqrt(np.convolve(wav ** 2, np.ones(HOP) / HOP, mode='same'))
    rms_db = 20 * np.log10(rms + 1e-5)
    f0 = f0_track(wav, T)
    semis = 12 * np.log2(np.where(np.isfinite(f0), f0, np.nan) / 100.0)
    ids = {t: vocab[t] for t in vocab}
    red_ids = [ids[t] for t in REDUCED if t in ids]
    out = []
    for wi, word in enumerate(words):
        idx = [k for k, o in enumerate(owner) if o[0] == wi and o[1] >= 0]
        letters = [c for c in word.lower() if c in VOWEL_LETTERS]
        if len(idx) < min_vowels or len(idx) != len(letters):
            continue
        rows = []
        for n, k in enumerate(idx):
            s = spans[k].start
            prev_end = spans[k - 1].end if k > 0 else max(s - 2, 0)
            nxt = spans[k + 1].start if k + 1 < len(spans) else min(spans[k].end + 2, T)
            lo = int(math.floor((prev_end + s) / 2)); hi = int(math.ceil((spans[k].end + nxt) / 2))
            lo = max(lo, 0); hi = max(min(hi, T), lo + 1)
            dur = (hi - lo) * 0.02
            r = rms_db[lo * HOP:hi * HOP + 1]
            rpeak = float(r.max()) if len(r) else -100.0
            seg = semis[lo:hi]
            seg = seg[np.isfinite(seg)]
            f0m = float(seg.mean()) if len(seg) else float('nan')
            f0x = float(seg.max()) if len(seg) else float('nan')
            full = [ids[t] for t in FULL_GROUPS[letters[n]].split() if t in ids]
            pf = float(probs[lo:hi][:, full].max()) if full else 1e-6
            pr = float(probs[lo:hi][:, red_ids].max())
            red = math.log(pf + 1e-4) - math.log(pr + 1e-4)
            rows.append([math.log(dur), rpeak, f0m, f0x, red, math.log(pf + 1e-4), math.log(pr + 1e-4), red])
        a = np.array(rows, dtype=float)
        # unvoiced regions: fill with the word mean (neutral after z-scoring)
        for c in (2, 3):
            col = a[:, c]
            if np.isnan(col).all():
                col[:] = 0.0
            else:
                col[np.isnan(col)] = np.nanmean(col)
        z = a.copy()
        for c in range(7):  # red_raw (last column) stays unnormalised
            sd = a[:, c].std()
            z[:, c] = (a[:, c] - a[:, c].mean()) / (sd if sd > 1e-6 else 1.0)
        out.append({'word': word, 'index': wi, 'vowels': len(idx), 'features': z.tolist()})
    return out, 'ok'


# ---------------------------------------------------------------- stress dictionary
@lru_cache(maxsize=1)
def _accentor():
    from silero_stress import load_accentor
    return load_accentor()


def dictionary_stress(word):
    """Vowel index of an unambiguous dictionary stress, or None (homograph/unknown)."""
    w = word.lower()
    if 'ё' in w:
        return None
    acc = _accentor()
    homo = acc(w, put_stress=False, put_stress_homo=True, put_yo=False, put_yo_homo=False, stress_single_vowel=False)
    if '+' in homo:
        return None
    marked = acc(w, put_stress=True, put_stress_homo=True, put_yo=False, put_yo_homo=False, stress_single_vowel=False)
    if marked.count('+') != 1:
        return None
    pos = marked.index('+')
    return sum(c in VOWEL_LETTERS for c in marked[:pos])


# ---------------------------------------------------------------- scoring model
def fit(items, l2=0.05, steps=400):
    """Conditional logit: items = list of (X[n,F], true index). Returns weight vector."""
    import torch
    F = len(items[0][0][0])
    w = torch.zeros(F, requires_grad=True)
    opt = torch.optim.LBFGS([w], lr=0.5, max_iter=steps)
    data = [(torch.tensor(x, dtype=torch.float32), t) for x, t in items]

    def closure():
        opt.zero_grad()
        loss = sum(-torch.log_softmax(x @ w, 0)[t] for x, t in data) / len(data) + l2 * (w ** 2).sum()
        loss.backward()
        return loss
    opt.step(closure)
    return w.detach().numpy().tolist()


def scores(x, w):
    import numpy as np
    return np.array(x) @ np.array(w)


def margin(x, w, j):
    """score(j) - best other score: >= 0 means the intended vowel is the argmax."""
    s = scores(x, w)
    others = [s[i] for i in range(len(s)) if i != j]
    return float(s[j] - max(others))


def evaluate(items, w, thetas):
    """Positive: hypothesis = dictionary stress.  Negative: every other vowel as the hypothesis."""
    n_ok = 0
    pos, neg = [], []
    for x, t in items:
        s = scores(x, w)
        n_ok += int(s.argmax() == t)
        pos.append((len(s), margin(x, w, t)))
        for j in range(len(s)):
            if j != t:
                neg.append((len(s), margin(x, w, j)))
    res = {'words': len(items), 'argmax_accuracy': n_ok / len(items), 'by_threshold': {}}
    for th in thetas:
        fr = sum(m < -th for _, m in pos) / len(pos)
        fa = sum(m >= -th for _, m in neg) / len(neg)
        res['by_threshold'][str(th)] = {'false_reject': fr, 'false_accept': fa}
    return res


def cv_weights(items, groups, l2):
    """Leave-group-out weights per item (so each word is scored by a model that never saw its group)."""
    gs = sorted(set(groups))
    folds = [gs[i::5] for i in range(5)]
    per_item = [None] * len(items)
    for fold in folds:
        tr = [it for it, g in zip(items, groups) if g not in fold]
        w = fit(tr, l2=l2)
        for i, g in enumerate(groups):
            if g in fold:
                per_item[i] = w
    return per_item


# ---------------------------------------------------------------- modes
def run_human(args):
    rows = [json.loads(l) for l in (ROOT / 'manual-labeling/reality-benders-1/labels.jsonl').open(encoding='utf8')]
    rows = [r for r in rows if r.get('precision_status') == 'precision_pass' and not re.search(r'\d', r['text'])]
    random.Random(7).shuffle(rows)
    rows = rows[:args.clips]
    OUT.mkdir(parents=True, exist_ok=True)
    items, meta, skipped = [], [], {}
    t0 = time.time(); nwords = 0
    for n, r in enumerate(rows):
        wav = ROOT / 'manual-labeling/reality-benders-1' / r['audio']['archive']
        try:
            words, info = analyze(wav, r['text'])
        except Exception as e:  # noqa: BLE001
            words, info = [], f'error:{type(e).__name__}:{e}'
        if info != 'ok':
            skipped[info] = skipped.get(info, 0) + 1
        text_words = sentence_words(r['text'])
        for wd in words:
            w0 = wd['word']
            # proper-noun / capitalised mid-sentence words are excluded
            if w0[:1].isupper() and wd['index'] > 0:
                continue
            if len(w0) < 4:
                continue
            t = dictionary_stress(w0)
            if t is None or t >= wd['vowels']:
                continue
            items.append((wd['features'], t))
            meta.append({'word': w0.lower(), 'vowels': wd['vowels'], 'true': t, 'chapter': r['chapter_id'], 'clip': r['id']})
            nwords += 1
        if (n + 1) % 25 == 0:
            print(f'{n + 1}/{len(rows)} clips, {nwords} words, {time.time() - t0:.0f}s', flush=True)
    elapsed = time.time() - t0
    release()
    groups = [m['chapter'] for m in meta]
    thetas = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0]
    out = {'clips': len(rows), 'skipped': skipped, 'words': len(items), 'seconds': elapsed,
           'seconds_per_word_including_clip_analysis': elapsed / max(len(items), 1),
           'seconds_per_clip': elapsed / len(rows), 'features': FEATURES}
    # single-feature baselines (hand-set sign, no training): argmax of that feature
    base = {}
    for ci, name in enumerate(FEATURES):
        for sign in (1, -1):
            acc = sum(int((sign * __import__('numpy').array(x)[:, ci]).argmax() == t) for x, t in items) / len(items)
            base[f'{name}{"+" if sign > 0 else "-"}'] = acc
    out['single_feature_argmax_accuracy'] = base
    chance = sum(1 / len(x) for x, _ in items) / len(items)
    out['chance_accuracy'] = chance
    best = None
    for l2 in (0.01, 0.05, 0.2):
        ws = cv_weights(items, groups, l2)
        # evaluate with per-item out-of-fold weights
        n_ok = sum(int(scores(x, w).argmax() == t) for (x, t), w in zip(items, ws))
        out.setdefault('cv_by_l2', {})[str(l2)] = n_ok / len(items)
        if best is None or n_ok > best[0]:
            best = (n_ok, l2, ws)
    _, l2, ws = best
    cvres = {'words': len(items), 'l2': l2, 'by_threshold': {}}
    cvres['argmax_accuracy'] = best[0] / len(items)
    pos = [(len(x), margin(x, w, t)) for (x, t), w in zip(items, ws)]
    neg = [(len(x), margin(x, w, j)) for (x, t), w in zip(items, ws) for j in range(len(x)) if j != t]
    for th in thetas:
        cvres['by_threshold'][str(th)] = {
            'false_reject': sum(m < -th for _, m in pos) / len(pos),
            'false_accept': sum(m >= -th for _, m in neg) / len(neg)}
    out['cross_validated'] = cvres
    byv = {}
    for k in sorted({m['vowels'] for m in meta}):
        sel = [i for i, m in enumerate(meta) if m['vowels'] == k]
        okk = sum(int(scores(items[i][0], ws[i]).argmax() == items[i][1]) for i in sel)
        posk = [pos[i][1] for i in sel]
        negk = [margin(items[i][0], ws[i], j) for i in sel for j in range(k) if j != items[i][1]]
        byv[str(k if k < 5 else '5+')] = byv.get(str(k if k < 5 else '5+'), {'words': 0, 'ok': 0, 'pos': [], 'neg': []})
        d = byv[str(k if k < 5 else '5+')]
        d['words'] += len(sel); d['ok'] += okk; d['pos'] += posk; d['neg'] += negk
    out['by_vowel_count'] = {k: {'words': d['words'], 'argmax_accuracy': d['ok'] / d['words'], 'chance': None,
                                 'at_theta_0.5': {'false_reject': sum(m < -0.5 for m in d['pos']) / len(d['pos']),
                                                  'false_accept': sum(m >= -0.5 for m in d['neg']) / len(d['neg'])}}
                             for k, d in byv.items()}
    final_w = fit(items, l2=l2)
    out['final_weights'] = dict(zip(FEATURES, final_w))
    out['final_weights_list'] = final_w
    (OUT / 'human-calibration.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), 'utf8')
    (OUT / 'human-items.json').write_text(json.dumps({'meta': meta, 'items': items}, ensure_ascii=False), 'utf8')
    print(json.dumps({k: v for k, v in out.items() if k != 'final_weights_list'}, ensure_ascii=False, indent=1))


def marked_words(text):
    """[(plain word, intended vowel index)] for words carrying exactly one U+0301."""
    res = []
    for tok in re.findall(r'[А-Яа-яЁё́]+', text):
        if tok.count(ACUTE) != 1:
            continue
        pos = tok.index(ACUTE)
        idx = sum(c in VOWEL_LETTERS for c in tok[:pos].lower()) - 1
        res.append((tok.replace(ACUTE, ''), idx))
    return res


def run_higgs(args):
    cal = json.loads((OUT / 'human-calibration.json').read_text('utf8'))
    w = cal['final_weights_list']
    base = ROOT / 'training-work/elirm-iv-higgs-mvp/audio-palette-v3-full12'
    named = {'c0003-u0102', 'c0003-u0001', 'c0003-u0066'}
    cand = []
    for f in sorted(base.glob('c0*/chunk-*/take-*/render-plan.json')):
        for u in json.loads(f.read_text('utf8'))['units']:
            wavp = f.parent / (u['id'] + '.wav')
            if not wavp.exists():
                continue
            mw = marked_words(u['synthesis'].get('text') or '')
            if mw:
                cand.append((u['id'], str(wavp.relative_to(ROOT)), u['synthesis']['text'], mw, f.parent.name))
    # one take per unit id (latest take dir), plus the named units
    seen = {}
    for c in cand:
        seen[c[0]] = c
    cand = list(seen.values())
    rnd = random.Random(11)
    rnd.shuffle(cand)
    pick = [c for c in cand if c[0] in named] + [c for c in cand if c[0] not in named][:args.clips]
    results = []; t0 = time.time(); nwords = 0
    for n, (uid, rel, text, mw, take) in enumerate(pick):
        try:
            words, info = analyze(ROOT / rel, text)
        except Exception as e:  # noqa: BLE001
            words, info = [], f'error:{type(e).__name__}:{e}'
        byw = {}
        for wd in words:
            byw.setdefault(wd['word'].lower(), []).append(wd)
        for word, idx in mw:
            for wd in byw.get(word.lower(), []):
                if idx >= wd['vowels']:
                    continue
                s = scores(wd['features'], w)
                results.append({'unit': uid, 'take': take, 'word': word, 'intended': idx, 'predicted': int(s.argmax()),
                                'margin': margin(wd['features'], w, idx), 'scores': [round(float(v), 3) for v in s]})
                nwords += 1
                break
        if (n + 1) % 25 == 0:
            print(f'{n + 1}/{len(pick)} units, {nwords} words, {time.time() - t0:.0f}s', flush=True)
    release()
    elapsed = time.time() - t0
    summary = {'units': len(pick), 'words': len(results), 'seconds': elapsed, 'results': results}
    ms = [r['margin'] for r in results]
    summary['follow_rate_by_theta'] = {str(th): sum(m >= -th for m in ms) / max(len(ms), 1)
                                       for th in (0.0, 0.25, 0.5, 1.0, 1.5, 2.0)}
    (OUT / 'higgs-marked-words.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), 'utf8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, indent=1))
    for r in results:
        if r['unit'] in named:
            print(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['human', 'higgs'])
    ap.add_argument('--clips', type=int, default=1300)
    args = ap.parse_args()
    {'human': run_human, 'higgs': run_higgs}[args.mode](args)


if __name__ == '__main__':
    main()
