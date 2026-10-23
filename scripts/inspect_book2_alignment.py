"""Summarize word-match coverage before choosing review clip boundaries."""
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from prepare_book_label_pilot import ROOT
import sys
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]
from rimsky.books import sentence_spans
from rimsky.preparation import narratable
from rimsky_training.pairing import tokens


def main():
    out = ROOT / 'training-work/character-rb2/full'
    book = json.loads((out / 'source/book.json').read_text(encoding='utf-8'))
    for track in ('00', '01', '02'):
        chapter = next(c for c in book['chapters'] if c['id'] == f'c{int(track)+3:04d}')
        words = [w for win in json.loads((out/'recordings'/track/'align.json').read_text(encoding='utf-8'))['windows'] for w in win['words']]
        audio = [t for w in words for t, _, _ in tokens(w['text'])]
        source, spans = [], []
        for block in chapter['blocks']:
            if not narratable(block):
                continue
            entries = tokens(block['text'])
            base = len(source)
            source.extend(t for t, _, _ in entries)
            for start, end in sentence_spans(block['text']):
                idx = [base+i for i, (_, a, b) in enumerate(entries) if a >= start and b <= end]
                if len(idx) >= 3:
                    spans.append((block['text'][start:end], idx))
        mapping = {}
        for match in SequenceMatcher(None, source, audio, autojunk=False).get_matching_blocks():
            if match.size >= 2:
                mapping.update({match.a+i: match.b+i for i in range(match.size)})
        counts = Counter()
        examples = []
        for text, idx in spans:
            found = [mapping.get(i) for i in idx]
            fraction = sum(v is not None for v in found)/len(found)
            edges = found[0] is not None and found[-1] is not None
            monotonic = all(a < b for a, b in zip((v for v in found if v is not None), (v for v in found if v is not None)))
            counts['total'] += 1
            for threshold in (.5, .7, .8, .9, 1.0):
                if fraction >= threshold:
                    counts[f'coverage_{threshold}'] += 1
                    if edges:
                        counts[f'edges_{threshold}'] += 1
            if .8 <= fraction < 1 and edges and len(examples) < 5:
                examples.append({'text': text[:180], 'matched': round(fraction, 2)})
        print(track, len(source), len(audio), dict(counts), examples)


if __name__ == '__main__':
    main()
