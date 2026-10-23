"""Decide hard-е (е -> э) respellings for the names and coinages of a book (text only; no audio, no GPU).

Rule hard-e-respelling-v1 (rimsky.hard_e; user decision 2026-10-21, plug and play: no review gate). Candidates are every
character-name word of the name-stress answer and every proper-noun / invented-word base form of
rimsky.proper_noun_stress that has an е directly after a consonant letter. Words a listener already respelled are
excluded. The pinned adjudicator (Claude Opus 5.5 high, as for stress and ё) answers each word respelled (candidate
е -> э only) or unchanged. Requests, per-batch attempts (validation errors fed back, up to 3) and answers are
immutable. Output: hard-e.request.json, batches/, hard-e.answer.json and respellings.json (character entries in the
apply_listener_names schema plus a 'proper_nouns' section; pass it as --hard-e; listener respellings override it).
--dry-run lists the candidates and counts without calling a provider.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, digest
from rimsky import hard_e as he
import build_homograph_stress as base
from build_homograph_stress import FOLDER, read, call

BATCH = 30


def gather(folder, answer_path, listener_path):
    from rimsky.proper_noun_stress import proper_noun_lexicon
    book = read(folder/'source/book.json'); answer = read(answer_path)['answer']
    names = {r['character_id']: {'name': r['name'], 'stressed': r['stressed']} for r in answer['names']}
    texts = [b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    lexicon = proper_noun_lexicon(texts, {k: v['stressed'] for k, v in names.items()}, True)
    listener = read(listener_path) if listener_path and Path(listener_path).exists() else {}
    skip_c = {e['character_id'] for e in listener.get('entries', [])}; skip_n = {e['key'] for e in listener.get('proper_nouns', [])}
    rows = he.candidates(names, lexicon, skip_c, skip_n); he.evidence(rows, texts)
    return rows, {k: v for k, v in names.items() if k not in skip_c}, lexicon, listener


def adjudicate(out, req):
    items = req['items']; batches = []
    for start in range(0, len(items), BATCH):
        part = {'task': req['task'], 'items': items[start:start+BATCH]}
        ids = [i['id'] for i in part['items']]; contract = he.schema(ids)
        bound = {'system': he.SYSTEM, 'request': part, 'schema': contract}
        sha = digest(bound); done = out/'batches'/f'batch-{sha[:16]}.answer.json'
        if done.exists():
            saved = read(done); he.validate(saved['answer'], part); batches.append(saved); continue
        body = part
        for attempt in range(3):
            path = out/'batches'/f'batch-{sha[:16]}.attempt-{attempt}.request.json'
            write_json(path, {**bound, 'request': body})
            print('HARD-E BATCH', sha[:8], len(ids), 'items attempt', attempt, flush=True)
            answer, metadata = call(he.SYSTEM, body, contract, out, sha[:8])
            write_json(path.with_name(path.name.replace('.request', '.answer')), {'answer': answer, 'metadata': metadata, 'request_sha256': file_digest(path)})
            try:he.validate(answer, part)
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:raise
                body = {**part, 'validation_errors': str(exc)[:1500]}; continue
            saved = {'request_sha256': sha, 'ids': ids, 'answer': answer, 'metadata': metadata, 'attempts': attempt + 1}
            write_json(done, saved); batches.append(saved); break
    return batches


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder', type=Path, default=FOLDER)
    p.add_argument('--name-stress', type=Path, help='default: <folder>/name-stress-v1/characters.answer.json')
    p.add_argument('--listener-respellings', type=Path, help='default: <folder>/listener-respellings/respellings.json when it exists')
    p.add_argument('--output', type=Path, help='default: <folder>/hard-e-v1-opus')
    p.add_argument('--dry-run', action='store_true', help='List the candidates and counts without calling the provider')
    p.add_argument('--claude-only', action='store_true', help='Comparison run with the fallback model; never the frozen generation answer')
    a = p.parse_args(); folder = a.folder
    answer_path = a.name_stress or folder/'name-stress-v1/characters.answer.json'
    listener_path = a.listener_respellings or folder/'listener-respellings/respellings.json'
    out = a.output or folder/'hard-e-v1-opus'; base.CLAUDE_ONLY = a.claude_only
    rows, names, lexicon, listener = gather(folder, answer_path, listener_path)
    kinds = {}
    for r in rows:kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    print('candidates', len(rows), json.dumps(kinds, ensure_ascii=False), 'listener-excluded characters', len(listener.get('entries', [])), flush=True)
    if a.dry_run:
        for r in rows:print(r['id'], r['kind'], r['word'], r['candidate_positions'], json.dumps(r['forms'], ensure_ascii=False))
        return
    out.mkdir(parents=True, exist_ok=True)
    req = he.request(rows)
    bound = {'rule': he.RULE, 'system': he.SYSTEM, 'request': req, 'name_stress_answer_sha256': file_digest(answer_path),
             'listener_respellings_sha256': file_digest(listener_path) if Path(listener_path).exists() else None,
             'source_sha256': file_digest(folder/'source/book.json')}
    path = out/'hard-e.request.json'
    if path.exists() and read(path) != bound:raise SystemExit('Frozen hard-e request changed; use a new --output')
    if not path.exists():write_json(path, bound)
    batches = adjudicate(out, req) if rows else []
    merged = {'decisions': [d for b in batches for d in b['answer']['decisions']]}
    he.validate(merged, req)
    decisions = {d['id']: d['respelled'] for d in merged['decisions']}
    models = sorted({b['metadata'].get('model', '') for b in batches})
    cost = round(sum((b['metadata'].get('cost_usd') or 0) for b in batches), 4)
    saved = {'rule': he.RULE, 'request_sha256': file_digest(path), 'answer': merged,
             'metadata': {'batches': [{'request_sha256': b['request_sha256'], **b['metadata']} for b in batches], 'models': models, 'cost_usd': cost},
             'human_approval': False, 'scope': 'text-only automatic hard-е proposals; listener respellings override; audio gates and listening decide'}
    write_json(out/'hard-e.answer.json', saved)
    provenance = {'source': 'model', 'rule': he.RULE, 'model': models[0] if len(models) == 1 else models, 'effort': base.ADJUDICATOR[2],
                  'answer_sha256': file_digest(out/'hard-e.answer.json')}
    doc = he.document(rows, decisions, names, lexicon, provenance)
    doc.update(book=str(folder.name), base_name_stress_answer=str(answer_path), base_name_stress_answer_sha256=file_digest(answer_path))
    write_json(out/'respellings.json', doc)
    hard = [(r['kind'], r['word'], decisions[r['id']]) for r in rows if decisions[r['id']] != r['word']]
    print('HARD', len(hard), 'SOFT', len(rows) - len(hard), 'cost_usd', cost, 'models', models)
    for kind, word, new in hard:print('  ', kind, word, '->', new)


if __name__ == '__main__':main()
