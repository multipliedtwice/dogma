"""Measure possible whole-sentence audio pairing from the existing timed ASR."""
from __future__ import annotations

import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]
from rimsky.books import sentence_spans  # noqa: E402
from rimsky.preparation import narratable  # noqa: E402
from rimsky_training.pairing import tokens  # noqa: E402


def main() -> None:
    root = ROOT / 'training-work/corpus-rb1-current'
    corpus = json.loads((root / 'corpus.json').read_text(encoding='utf-8'))
    book = json.loads((root / corpus['books'][0]['manifest']).read_text(encoding='utf-8'))
    chapters = {chapter['id']: chapter for chapter in book['chapters']}
    totals = Counter()
    chapters_report = []
    for recording in corpus['recordings']:
        number = int(recording['id'].rsplit('-', 1)[-1])
        chapter_id = f'c{number+4:04d}'
        chapter = chapters[chapter_id]
        transcript = json.loads((ROOT / 'training-work/full-book-aligned' / recording['id'] /
                                 'transcript.json').read_text(encoding='utf-8'))
        if transcript['recording_sha256'] != recording['sha256']:
            raise ValueError(f'Changed recording: {recording["id"]}')
        words = transcript['words']
        source_tokens = []
        spans = []
        for block in chapter['blocks']:
            if not narratable(block) or 'knigolub.net' in block['text'].casefold():
                continue
            for start, end in sentence_spans(block['text']):
                text = block['text'][start:end]
                lexical = tokens(text)
                if not lexical:
                    continue
                first = len(source_tokens)
                source_tokens.extend(token[0] for token in lexical)
                spans.append((block['id'], start, end, first, len(lexical)))
        asr_tokens = [tokens(word['text'])[0][0] for word in words]
        mapping = {}
        for match in SequenceMatcher(None, source_tokens, asr_tokens, autojunk=False).get_matching_blocks():
            if match.size >= 2:
                mapping.update((match.a + offset, match.b + offset) for offset in range(match.size))
        count, minutes = 0, 0.0
        for _, _, _, first, length in spans:
            if length < 3:
                continue
            aligned = [mapping.get(first + offset) for offset in range(length)]
            matched = [index for index in aligned if index is not None]
            if (len(matched) / length < .7 or aligned[0] is None or aligned[-1] is None
                    or aligned[-1] - aligned[0] > length + 4):
                continue
            start, end = words[aligned[0]]['start'], words[aligned[-1]]['end']
            if 1 <= end - start <= 30:
                count += 1
                minutes += (end - start) / 60
        totals['sentences'] += len(spans)
        totals['candidate_sentences'] += count
        totals['candidate_minutes'] += minutes
        chapters_report.append({'recording_id': recording['id'], 'chapter_id': chapter_id,
                                'sentences': len(spans), 'candidate_sentences': count,
                                'candidate_minutes': round(minutes, 1)})
    report = {'source_hours': round(sum(r['audio']['duration'] for r in corpus['recordings'])/3600, 2),
              'sentence_count': totals['sentences'], 'possible_candidate_sentences': totals['candidate_sentences'],
              'possible_candidate_hours': round(totals['candidate_minutes']/60, 2),
              'criteria': 'First/last words exactly anchored; >=70% token match; <=4 ASR insertions; 1-30 s.',
              'chapters': chapters_report}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
