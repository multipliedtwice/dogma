"""v3 against v4 on the native unit texts of a book (CPU only, no provider): the regression evidence for stress-rare-v4.

v3 uses the frozen homograph answer; v4 adds an in-memory yo block of the builder's automatic decisions with every
adjudication row left UNBOUND. Reports words where v3 had ё and v4 does not (split: unbound row or anything else),
words where v4 adds ё, and the acute marks removed by drop_held_marks and close_mark_guard.
"""
import argparse, json, re, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, digest
from rimsky import yo_restoration as yo
from rimsky.auto_pronunciation import auto_pronunciation, policy_options
from rimsky.homograph_stress import block as homograph_block
from build_homograph_stress import FOLDER, read, native_texts, lexicon
from build_yo_restoration import analyse_texts

W = re.compile(r'[А-Яа-яЁё́]+'); A = '́'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-full12', 'audio-palette-v3-descriptors'])
    p.add_argument('--homograph', type=Path, default=FOLDER/'homograph-stress-v2-opus/homograph.answer.json')
    p.add_argument('--output', type=Path, default=ROOT/'training-work/yo-restoration-eval-v1/v3-v4-diff.json')
    a = p.parse_args()
    book = read(FOLDER/'source/book.json'); names, common = lexicon(book, read(FOLDER/'name-stress-v1/characters.answer.json')['answer'])
    texts = native_texts(a.generations, a.chapters)
    decisions, rows, stats = analyse_texts(texts, names, common)
    unbound_rows = {(r['sha256'], r['word'], r['index']) for r in rows}
    hs = homograph_block(a.homograph)
    v3o = policy_options('stress-rare-v3', {'homograph_stress': hs})
    v4o = policy_options('stress-rare-v4', {'homograph_stress': hs, 'yo_restoration': {'rule': yo.RULE, 'answer_sha256': 'dry-run-unbound', 'decisions': decisions}})
    lost_row = []; lost_other = []; added = []; drop_e = []; drop_guard = []; mark_diff = Counter(); ok = Counter()
    for text, chapter in texts.items():
        o3, _ = auto_pronunciation(text, names, common, **v3o); o4, p4 = auto_pronunciation(text, names, common, **v4o)
        w3, w4, w0 = W.findall(o3), W.findall(o4), list(W.finditer(text))
        if not (len(w3) == len(w4) == len(w0)):raise SystemExit('word count mismatch in ' + text[:60])
        sha = digest(text.encode('utf8'))
        unit_at = {pt: (u['key'], u['occ']) for u in yo.units(text) for pt in u['parts']}
        for m, x, y in zip(w0, w3, w4):
            k = unit_at.get(m.start()); ctx = text[max(0, m.start() - 40):m.end() + 30]
            if 'ё' in x.lower() and 'ё' not in y.lower():
                row = {'chapter': chapter, 'v3': x, 'v4': y, 'context': ctx}
                (lost_row if k and (sha, *k) in unbound_rows else lost_other).append(row)
            elif 'ё' in y.lower() and 'ё' not in x.lower():added.append({'chapter': chapter, 'source': m[0], 'v3': x, 'v4': y})
            if x != y:mark_diff['words_differ'] += 1
            ok['words'] += 1
        drop_e += [{'chapter': chapter, **d} for d in p4['yo_restoration']['dropped_e_marks']]
        drop_guard += [{'chapter': chapter, **d} for d in p4['close_mark_guard']['dropped']]
    freq = Counter((x['source'].lower(), x['v4']) for x in added)
    report = {'units': len(texts), 'words': ok['words'], 'words_differing_v3_v4': mark_diff['words_differ'], 'builder_stats': stats,
              'unbound_adjudication_rows': len(rows),
              'v3_yo_lost_in_v4': {'because_unbound_adjudication_row': len(lost_row), 'anything_else': len(lost_other),
                                   'rows': lost_row, 'other': lost_other},
              'v4_adds_yo': {'count': len(added), 'distinct': len(freq), 'listing': [{'source': s, 'v4': v, 'n': n} for (s, v), n in freq.most_common(150)]},
              'dropped_by_drop_held_marks': {'count': len(drop_e), 'examples': drop_e[:20]},
              'dropped_by_close_mark_guard': {'count': len(drop_guard), 'examples': drop_guard[:20]}}
    a.output.parent.mkdir(parents=True, exist_ok=True); write_json(a.output, report)
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if not isinstance(vv, list)}) for k, v in report.items()}, ensure_ascii=False))


if __name__ == '__main__':main()
