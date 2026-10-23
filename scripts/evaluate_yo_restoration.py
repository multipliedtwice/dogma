"""Hidden-ё regression for rimsky.yo_restoration on consistently yofied book text (CPU only; no provider).

Corpus: the Reality Benders EPUBs in content/ spell ё consistently (ещё 253 against еще 1, её 99 against ее 0 in
book 1; the Elirm IV fb2 does not: еще 215, ещё 0). Every ё is replaced by е, the layer's AUTOMATIC tiers run on
the stripped sentences, and the result is compared with the original spelling. Tier 1 (unique_yo, pymorphy3
dictionary only) runs on every sentence; tier 2 (ambiguous words accepted when silero and RUAccent agree and the
все grammar witness does not contradict) and the unknown-word rows run on a seeded sample, because silero and
RUAccent are slow. Nothing here is circular: the truth is the author's spelling, not a model output.
"""
import argparse, json, random, re, sys, zipfile, html, time
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky import yo_restoration as yo
from rimsky.util import write_json

BOOKS = ['reality-benders-1', 'reality-benders-2', 'reality-benders-3', 'reality-benders-7', 'reality-benders-11']


def sentences(path):
    z = zipfile.ZipFile(path); out = []
    for name in sorted(z.namelist()):
        if not name.endswith(('html', 'xhtml', 'htm')):continue
        for para in re.findall(r'<p[^>]*>(.*?)</p>', z.read(name).decode('utf8', 'ignore'), re.S):
            para = html.unescape(re.sub(r'<[^>]+>', '', para)).strip()
            out += [s for s in re.split(r'(?<=[.!?…])\s+', para) if 15 <= len(s) <= 400]
    return out


def tier1(corpus):
    """unique_yo precision/recall and where every genuine-ё word lands, over all sentences."""
    counts = Counter(); fp = Counter(); fn_classes = Counter(); fn = Counter()
    for orig in corpus:
        text = orig.replace('ё', 'е').replace('Ё', 'Е')
        yo_at = {m.start() for m in re.finditer('[ёЁ]', orig)}
        words = {m.start(): m for m in re.finditer(r'[А-Яа-яЁё]+', orig)}
        for e in yo.eligible(text, []):
            s0, s1 = e['start'], e['end']; truth = 'ё' in orig[s0:s1].lower(); counts[e['cls']] += 1
            if e['cls'] == 'unique_yo':
                counts['unique_yo_truth_yo' if truth else 'unique_yo_truth_e'] += 1
                if not truth:fp[e['word'].lower()] += 1
                elif orig[s0:s1] != e['yo']:counts['unique_yo_case_or_position_error'] += 1
            if truth:fn_classes[e['cls']] += 1
            if truth and e['cls'] != 'unique_yo':fn[e['word'].lower()] += 1
        counts['ё_words_total'] += sum(1 for m in words.values() if re.search('[ёЁ]', m[0]))
    return counts, fp, fn_classes, fn


def tier2(corpus, sample, seed):
    from rimsky.auto_pronunciation import yo_witnesses
    rng = random.Random(seed); pool = []
    for orig in corpus:
        text = orig.replace('ё', 'е').replace('Ё', 'Е')
        if any(e['cls'] in ('ambiguous', 'unknown') for e in yo.eligible(text, [])):pool.append(orig)
    rng.shuffle(pool); pool = pool[:sample]
    stats = Counter(); per_word = {}; wrong_auto = []; rows = []; unknown_rows = []; started = time.time()
    for n, orig in enumerate(pool):
        text = orig.replace('ё', 'е').replace('Ё', 'Е')
        spans, sil, dense = yo_witnesses(text, {}, (), True)
        out = yo.analyze(text, spans, sil, dense, yo.ruaccent_yo(text))
        key_of = {(u['key'], u['occ']): [orig[u['start']:u['end']]] for u in yo.units(orig)}
        for k, v in out['stats'].items():stats[k] += v
        for d in out['decisions']:
            real = 'yo' if 'ё' in key_of[(d['word'], d['index'])][0].lower() else 'e'
            ok = real == d['choice']; stats['auto_correct' if ok else 'auto_wrong'] += 1
            w = per_word.setdefault(d['word'], Counter()); w['correct' if ok else 'wrong'] += 1
            if not ok:wrong_auto.append({'sentence': text, 'word': d['surface'], 'chose': d['choice'], 'truth': real, 'witnesses': d['witnesses']})
        for r in out['rows']:
            real = 'yo' if 'ё' in key_of[(r['word'], r['index'])][0].lower() else 'e'
            row = {'kind': r['kind'], 'word': r['surface'], 'truth': real, 'witnesses': r['witnesses'], 'sentence': r['sentence'][:160]}
            (rows if r['kind'] == 'ambiguous' else unknown_rows).append(row)
            if real == 'yo':stats[r['kind'] + '_rows_truth_yo'] += 1
            else:stats[r['kind'] + '_rows_truth_e'] += 1
        if n % 100 == 0:print('tier2', n, len(pool), round(time.time() - started), 's', flush=True)
    return stats, per_word, wrong_auto, rows, unknown_rows, len(pool)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--books', nargs='+', default=BOOKS); p.add_argument('--sample', type=int, default=1500)
    p.add_argument('--seed', type=int, default=20261021)
    p.add_argument('--output', type=Path, default=ROOT/'training-work/yo-restoration-eval-v1')
    a = p.parse_args(); corpus = []
    for b in a.books:corpus += sentences(ROOT/'content'/f'{b}.epub')
    print('sentences', len(corpus), flush=True)
    c, fp, fn_classes, fn = tier1(corpus)
    tp = c['unique_yo_truth_yo']; fpn = c['unique_yo_truth_e']
    report = {'corpus': {'books': a.books, 'sentences': len(corpus), 'basis': 'author-spelled ё replaced by е; the author spelling is the truth'},
              'tier1_unique_yo': {'restored': c['unique_yo'], 'correct': tp, 'false_yo_on_genuine_e': fpn,
                                  'precision': tp / max(1, tp + fpn), 'case_or_position_errors': c['unique_yo_case_or_position_error'],
                                  'recall_over_all_yo_words': tp / max(1, c['ё_words_total']), 'yo_words_total': c['ё_words_total'],
                                  'where_genuine_yo_words_land': dict(fn_classes), 'top_false_yo': fp.most_common(25),
                                  'top_missed_yo_words': fn.most_common(40)},
              'class_counts_all_e_words': {k: c[k] for k in ('unique_yo', 'unique_e', 'ambiguous', 'multi_yo', 'unknown')}}
    stats, per_word, wrong, rows, unknown_rows, used = tier2(corpus, a.sample, a.seed)
    auto = stats['auto_correct'] + stats['auto_wrong']
    report['tier2_sample'] = {'sentences': used, 'seed': a.seed, 'ambiguous_words': stats['ambiguous'], 'auto_accepted': auto,
                              'auto_correct': stats['auto_correct'], 'auto_wrong': stats['auto_wrong'],
                              'auto_precision': stats['auto_correct'] / max(1, auto), 'sent_to_adjudication': stats['ambiguous_rows'],
                              'adjudication_truth_yo': stats['ambiguous_rows_truth_yo'], 'adjudication_truth_e': stats['ambiguous_rows_truth_e'],
                              'unknown_words': stats['unknown'], 'unknown_rows': stats['unknown_rows'],
                              'unknown_rows_truth_yo': stats['unknown_rows_truth_yo'], 'unknown_rows_truth_e': stats['unknown_rows_truth_e'],
                              'per_word_auto': {k: dict(v) for k, v in sorted(per_word.items(), key=lambda kv: -sum(kv[1].values()))[:40]},
                              'wrong_auto_examples': wrong[:40], 'adjudication_examples': rows[:40], 'unknown_row_examples': unknown_rows[:40]}
    a.output.mkdir(parents=True, exist_ok=True); write_json(a.output/'report.json', report)
    t1 = report['tier1_unique_yo']; t2 = report['tier2_sample']
    print(json.dumps({k: v for k, v in t1.items() if not k.startswith('top')}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in t2.items() if not k.endswith('examples') and k != 'per_word_auto'}, ensure_ascii=False))


if __name__ == '__main__':main()
