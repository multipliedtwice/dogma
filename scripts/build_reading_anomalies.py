"""Find typesetting slips that make the narrator misread a book (text only; no audio, no GPU).

One request per chapter goes to two independent text-only providers (GPT-6 Luna low via Codex CLI
and Claude Haiku 5.5; user decision 2026-10-16); only repairs both propose identically are applied. Requests and answers are cached by the exact
request sha256 and never overwritten. Proposals are validated by runtime/rimsky/reading_anomalies.py
(unique snippet, letters and digits unchanged, closed kinds); rejected items are kept with reasons.
The source book and protected labels are never touched.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest
from rimsky import reading_anomalies as ra

FOLDER = ROOT/'training-work/elirm-iv-higgs-mvp'
read = lambda p: json.loads(Path(p).read_text('utf8'))


def call(system, body, contract, which):
    """One named provider (no silent fallback): consensus needs both answers to be what they claim."""
    from rimsky.word_recovery import PRIMARY_MODEL, FALLBACK_MODEL
    provider, model, effort = PRIMARY_MODEL if which == 'primary' else FALLBACK_MODEL
    metadata = {}
    if provider == 'codex_cli':
        from rimsky.codex_analysis import structured_call
        answer = structured_call(system, body, contract, effort=effort, model=model, timeout=900)
    else:
        from rimsky.claude_analysis import structured_call
        answer = structured_call(system, body, contract, effort=effort, model=model, metadata=metadata, timeout=900)
    metadata.update(provider=provider, model=model, effort=effort)
    return answer, metadata


def units_of(chapter, generations):
    for generation in generations:
        files = sorted((FOLDER/generation/chapter).glob('chunk-*/assignment.json'))
        if files:
            seen, units = set(), []
            for path in files:
                for u in read(path)['units']:
                    if u['id'] not in seen and u.get('text'):seen.add(u['id']); units.append(u)
            return generation, units
    return None, []


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003', 'c0004', 'c0005'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-descriptors', 'audio-palette-v3-full12'])
    p.add_argument('--output', type=Path, default=FOLDER/'reading-anomalies-v1')
    p.add_argument('--policy', default='grouped-digits-v3')
    a = p.parse_args(); out = a.output; out.mkdir(parents=True, exist_ok=True)
    from render_higgs_book_trial import spoken_text
    spoken_of = lambda t: spoken_text(t, a.policy)
    source = FOLDER/'source/book.json'; book = read(source); source_sha = file_digest(source)
    summary = {'rule': ra.RULE, 'source_sha256': source_sha, 'chapters': {}, 'human_approval': False,
               'scope': ra.SCOPE}
    all_records, all_corrections, all_unmapped = [], {}, []
    for chapter in a.chapters:
        blocks = next(c for c in book['chapters'] if c['id'] == chapter)['blocks']
        req = ra.request(blocks, spoken_of); contract = ra.schema([b['id'] for b in req['blocks']])
        bound = ra.bound(req); sha = ra.request_sha256(req)
        request_path, answer_path = out/f'{chapter}.request.json', out/f'{chapter}.answer.json'
        if request_path.exists() and read(request_path).get('request_sha256') != sha:
            raise SystemExit(f'{chapter}: frozen request changed (book, prompt or number policy); use a new --output')
        if not request_path.exists():write_json(request_path, {**bound, 'request_sha256': sha, 'source_sha256': source_sha})
        answers = {}
        for which, path in (('primary', answer_path), ('secondary', out/f'{chapter}.answer-secondary.json')):
            if path.exists():
                saved = read(path)
                if saved['request_sha256'] != sha:raise SystemExit(f'{chapter}: cached {which} answer is for another request')
                print(chapter, which, 'cached answer', sha[:8], flush=True)
            else:
                print(chapter, 'asking', which, 'provider', len(req['blocks']), 'blocks', sha[:8], flush=True)
                answer, metadata = call(ra.SYSTEM, req, contract, which)
                saved = {'request_sha256': sha, 'answer': answer, 'metadata': metadata}
                write_json(path, saved)
            answers[which] = (path, saved)
        (answer_path, saved), (second_path, second) = answers['primary'], answers['secondary']
        valid, rejected = ra.validate(saved['answer'], req)
        second_valid, second_rejected = ra.validate(second['answer'], req)
        accepted, unconfirmed = ra.consensus(valid, second_valid, req)
        meta = saved['metadata']
        evidence = {'path': str(answer_path), 'sha256': file_digest(answer_path), 'request_sha256': sha,
                    'answer_sha256': file_digest(answer_path), 'provider': meta['provider'], 'model': meta['model'],
                    'effort': meta['effort'], 'rule': ra.RULE,
                    'confirmed_by': {'path': str(second_path), 'sha256': file_digest(second_path),
                                     'provider': second['metadata']['provider'], 'model': second['metadata']['model']}}
        records = ra.to_records(accepted, chapter, evidence)
        generation, units = units_of(chapter, a.generations)
        corrections, unmapped = ra.to_corrections(records, units, spoken_of) if units else ({}, [])
        all_records += records; all_corrections.update(corrections); all_unmapped += unmapped
        summary['chapters'][chapter] = {
            'request_sha256': sha, 'provider': meta['provider'], 'model': meta['model'], 'effort': meta['effort'],
            'blocks': len(req['blocks']), 'proposed': len(saved['answer']['repairs']),
            'accepted': [{k: r[k] for k in ('block_id', 'original', 'replacement', 'kind', 'rationale')} for r in accepted],
            'rejected': rejected, 'unconfirmed': [{k: r[k] for k in ('block_id', 'original', 'replacement', 'kind')} for r in unconfirmed],
            'secondary_rejected': second_rejected, 'mapped_units': sorted(corrections), 'mapping_generation': generation,
            'unmapped': [{k: r.get(k) for k in ('source_id', 'original', 'reason')} for r in unmapped]}
        print(chapter, 'accepted', len(accepted), 'rejected', len(rejected), 'mapped', len(corrections), flush=True)
    write_json(out/'summary.json', summary)
    write_json(out/'repairs.json', {'rule': ra.RULE, 'source_sha256': source_sha, 'records': all_records})
    write_json(out/'spoken-corrections.mapped.json', {
        'schema': 1, 'source_sha256': source_sha, 'units': all_corrections,
        'note': 'Mapped onto the units of the listed generations only; remap with reading_anomalies.to_corrections against the new assignment.'})


if __name__ == '__main__':main()
