"""Confirm which formatted stand-alone lines are scene transitions (rimsky.section_markers).

Per chapter: candidates from the book's markup, classified by two independent text models
(GPT-6 Luna low via Codex CLI and Claude Haiku 5.5 low); only lines both call a transition are
confirmed. Requests and answers are cached by exact request sha256. Output: confirmed.json, copied
into a generation as section-markers.json by seed_iteration_generation.py.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest
from rimsky import section_markers as sm

FOLDER = ROOT/'training-work/elirm-iv-higgs-mvp'
read = lambda p: json.loads(Path(p).read_text('utf8'))


def call(req, which):
    from rimsky.word_recovery import PRIMARY_MODEL, FALLBACK_MODEL
    provider, model, effort = PRIMARY_MODEL if which == 'primary' else FALLBACK_MODEL
    contract = sm.schema([r['id'] for r in req['candidates']]); metadata = {}
    if provider == 'codex_cli':
        from rimsky.codex_analysis import structured_call
        answer = structured_call(sm.SYSTEM, req, contract, effort=effort, model=model, timeout=600)
    else:
        from rimsky.claude_analysis import structured_call
        answer = structured_call(sm.SYSTEM, req, contract, effort=effort, model=model, metadata=metadata, timeout=600)
    return answer, {**metadata, 'provider': provider, 'model': model, 'effort': effort}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003', 'c0004', 'c0005'])
    p.add_argument('--output', type=Path, default=FOLDER/'section-markers-v1')
    a = p.parse_args(); out = a.output; out.mkdir(parents=True, exist_ok=True)
    source = FOLDER/'source/book.json'; book = read(source)
    confirmed, report = [], {}
    for chapter in a.chapters:
        rows = sm.candidates(next(c for c in book['chapters'] if c['id'] == chapter)['blocks'])
        if not rows:report[chapter] = {'candidates': 0}; continue
        req = sm.request(rows); sha = sm.request_sha256(req)
        request_path = out/f'{chapter}.request.json'
        if request_path.exists() and read(request_path)['request_sha256'] != sha:raise SystemExit(f'{chapter}: frozen request changed')
        if not request_path.exists():write_json(request_path, {**sm.bound(req), 'request_sha256': sha})
        roles = {}
        for which in ('primary', 'secondary'):
            path = out/f'{chapter}.answer-{which}.json'
            if not path.exists():
                answer, meta = call(req, which)
                write_json(path, {'request_sha256': sha, 'answer': answer, 'metadata': meta})
            saved = read(path)
            if saved['request_sha256'] != sha:raise SystemExit(f'{chapter}: cached {which} answer is for another request')
            roles[which] = sm.validate(saved['answer'], req)
        chosen = sm.consensus(roles['primary'], roles['secondary']); confirmed += chosen
        texts = {r['id']: r['text'] for r in rows}
        report[chapter] = {'candidates': len(rows), 'confirmed': [(k, texts[k]) for k in chosen],
                           'disagreements': [(k, texts[k], roles['primary'][k], roles['secondary'][k]) for k in texts
                                             if roles['primary'][k] != roles['secondary'][k]]}
        print(chapter, 'candidates', len(rows), 'confirmed', chosen, flush=True)
    write_json(out/'confirmed.json', {'rule': sm.RULE, 'source_sha256': file_digest(source), 'confirmed': sorted(confirmed),
                                      'report': report, 'human_approval': False})


if __name__ == '__main__':main()
