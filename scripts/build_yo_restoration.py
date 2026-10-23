"""Decide ё for the ambiguous and unknown words of a book (text only; no audio, no GPU) for policy stress-rare-v4.

For every unit of the chosen generations' assignments the exact text the worker hands to auto_pronunciation is
rebuilt (reusing scripts/build_homograph_stress.py). rimsky.yo_restoration classifies each е word against the
pymorphy3 dictionary: unique ё words are restored at render time without a decision; ambiguous words (все/всё,
осел/осёл) are accepted automatically only when silero and RUAccent agree and, for все, the first finite verb of
the clause does not contradict; every other ambiguous word, and every unknown word that silero stresses on е or
RUAccent writes with ё, goes to the pinned adjudicator (Claude Opus 5.5 high, as for stress) as sentence + the e and
ё candidates. It answers e | yo and echoes the word; letters can never change. Decisions bind to
sha256(unit text) + word + occurrence. Requests, per-batch attempts and answers are immutable.
--dry-run prints the class counts and the rows without calling a provider.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, digest
from rimsky import yo_restoration as yo
import build_homograph_stress as base
from build_homograph_stress import FOLDER, read, native_texts, lexicon, call

BATCH = 25


def analyse_texts(texts, names, common, ruaccent=yo.ruaccent_yo):
    """(decisions, rows, stats) over {unit text -> chapter}; stats summed per class and per chapter."""
    from rimsky.auto_pronunciation import yo_witnesses
    decisions = []; rows = []; stats = {}
    for text, metadata in texts.items():
        chapter = metadata['chapter'] if isinstance(metadata, dict) else metadata
        spec = metadata.get('pronunciation') if isinstance(metadata, dict) else None
        local_names, local_common = names, common
        if spec:
            local_names = {cid: row['stressed'] for cid, row in spec['names'].items()}
            local_names.update(spec.get('proper_nouns', {}))
            local_common = spec.get('common', [])
        text, marked_e = yo.normalize_marked_e(text)
        spans, sil, dense = yo_witnesses(text, local_names, local_common, True)
        part = yo.analyze(text, spans, sil, dense, ruaccent(text))
        part['stats']['marked_e_normalized'] = len(marked_e['edits'])
        for r in part['rows']:r['chapter'] = chapter
        for d in part['decisions']:d['chapter'] = chapter
        decisions += part['decisions']; rows += part['rows']
        for k, v in part['stats'].items():stats[k] = stats.get(k, 0) + v
    return decisions, rows, stats


def adjudicate(out, req):
    items = req['items']; batches = []
    for start in range(0, len(items), BATCH):
        part = {'task': req['task'], 'items': items[start:start+BATCH]}
        ids = [i['id'] for i in part['items']]; contract = yo.schema(ids)
        bound = {'system': yo.SYSTEM, 'request': part, 'schema': contract}
        sha = digest(bound); done = out/'batches'/f'batch-{sha[:16]}.answer.json'
        if done.exists():
            saved = read(done); yo.validate(saved['answer'], part); batches.append(saved); continue
        body = part
        for attempt in range(3):
            path = out/'batches'/f'batch-{sha[:16]}.attempt-{attempt}.request.json'
            write_json(path, {**bound, 'request': body})
            print('YO BATCH', sha[:8], len(ids), 'items attempt', attempt, flush=True)
            answer, metadata = call(yo.SYSTEM, body, contract, out, sha[:8])
            write_json(path.with_name(path.name.replace('.request', '.answer')), {'answer': answer, 'metadata': metadata, 'request_sha256': file_digest(path)})
            try:yo.validate(answer, part)
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:raise
                body = {**part, 'validation_errors': str(exc)[:1500]}; continue
            saved = {'request_sha256': sha, 'ids': ids, 'answer': answer, 'metadata': metadata, 'attempts': attempt + 1}
            write_json(done, saved); batches.append(saved); break
    return batches


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-full12', 'audio-palette-v3-descriptors'])
    p.add_argument('--output', type=Path, default=FOLDER/'yo-restoration-v1-opus')
    p.add_argument('--book-fixes', type=Path, help='Apply the same verified spoken-text fix list as the target render')
    p.add_argument('--dry-run', action='store_true', help='Count classes and print the adjudication rows without calling the provider')
    p.add_argument('--claude-only', action='store_true', help='Comparison run with the fallback model; never the frozen generation answer')
    a = p.parse_args(); out = a.output
    base.CLAUDE_ONLY = a.claude_only
    book = read(FOLDER/'source/book.json'); names, common = lexicon(book, read(FOLDER/'name-stress-v1/characters.answer.json')['answer'])
    texts = native_texts(a.generations, a.chapters, with_specs=True, book_fixes=a.book_fixes)
    decisions, rows, stats = analyse_texts(texts, names, common)
    print('units', len(texts), 'stats', json.dumps(stats), 'auto decisions', len(decisions), 'adjudication rows', len(rows), flush=True)
    if a.dry_run:
        for r in rows:print(r['chapter'], r['kind'], r['surface'], '->', r['yo'], json.dumps(r['witnesses'], ensure_ascii=False), '|', r['sentence'][:90])
        return
    out.mkdir(parents=True, exist_ok=True)
    req = yo.request(rows)
    bound = {'rule': yo.RULE, 'system': yo.SYSTEM, 'request': req, 'bindings': [{k: r[k] for k in ('sha256', 'word', 'index', 'surface', 'yo', 'kind', 'chapter', 'witnesses')} for r in rows],
             'auto_decisions_sha256': digest(decisions), 'chapters': a.chapters, 'generations': a.generations,
             'book_fixes_sha256': file_digest(a.book_fixes) if a.book_fixes else None,
             'versions': yo._versions()}
    path = out/'yo.request.json'
    if path.exists() and read(path) != bound:raise SystemExit('Frozen yo request changed; use a new --output')
    if not path.exists():write_json(path, bound)
    batches = adjudicate(out, req) if rows else []
    merged = {'decisions': [d for b in batches for d in b['answer']['decisions']]}
    yo.validate(merged, req)
    saved = {'rule': yo.RULE, 'request_sha256': file_digest(path), 'answer': merged, 'stats': stats,
             'decisions': decisions + yo.decisions_from(rows, merged),
             'metadata': {'batches': [{'request_sha256': b['request_sha256'], **b['metadata']} for b in batches]},
             'human_approval': False, 'scope': 'text-only ё proposals; audio gates and listening decide'}
    write_json(out/'yo.answer.json', saved)
    import collections
    print('choices', dict(collections.Counter(d['choice'] for d in merged['decisions'])), 'decisions', len(saved['decisions']))


if __name__ == '__main__':main()
