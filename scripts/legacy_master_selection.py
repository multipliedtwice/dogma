"""Reference baseline: the legacy master analysis path on the frozen selection cases.

The legacy code runs only from an explicit, verified checkout of master
de954783 in its own Python process; nothing of this candidate's runtime is imported
there. Inference receives only selection-case text, its context passages and the
registry the run builds itself (never gold speakers, cast, speech chunks, labels or
test cases). `run` calls the legacy CLI analysis; `replay` revalidates saved raw
answers through the legacy validator without any model call and checks that the
outer units match the saved output. Scoring keeps the legacy validator's internal
`analysis_parts`, so analysis is compared with analysis; the outer-unit projection
is reported separately and is not model structure or audio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))

import evaluate_speakers as harness
from rimsky.analysis import DEGRADED_HOLD

LEGACY_REVISION = 'de954783f48d786779071f1e6c7ab15467fb6e64'
DIFFERENCES = [
    'Each selection case is analysed alone with its own gold before/after passages, in gold order, as in the candidate case mode; a master generation analyses whole chapters.',
    'Only the case passage is a target; master generations also analyse the context passages as targets.',
    'A batch still invalid after master\'s one repair is recorded as degraded and the run continues; a master generation would fail.',
    'Master CLI effort is fixed at low in master source (codex model_reasoning_effort="low", claude --effort low).',
    'Master has no critic or agreement holds; unresolved (unknown) and degraded speech is the only held speech.',
]

LEGACY = r'''
import json, sys
legacy = sys.argv[1]
sys.path.insert(0, legacy + '/runtime')
import worker
from rimsky.analysis import analysis_target, batches, validate_analysis
from rimsky.jobs import reconcile_cast
from rimsky.preparation import speech_sources
from rimsky.util import AnalysisCapacityError, AppError

job = json.load(sys.stdin)

def case_sources(case):
    blocks = [*case['before'], case, *case['after']]
    book = {'chapters': [{'id': case['chapter_id'], 'blocks': [{'id': block['source_id'], 'kind': 'paragraph', 'text': block['text']} for block in blocks]}]}
    inputs = list(speech_sources(book, [case['chapter_id']]))
    return inputs, [source for source in inputs if source['source_id'] == case['source_id']]

def context(inputs, batch):
    lo, hi = inputs.index(batch[0]), inputs.index(batch[-1]) + 1
    before = [analysis_target(source) for source in inputs[max(0, lo - 2):lo] if source.get('scene_id') == batch[0].get('scene_id')]
    after = [analysis_target(source) for source in inputs[hi:hi + 2] if source.get('scene_id') == batch[-1].get('scene_id')]
    return before, after

def unit(item):
    return {'start': item['start'], 'end': item['end'], 'speaker': item['speaker'], 'warning': item['warning'], 'evidence': item.get('evidence', []),
            'analysis_parts': [{'start': part['start'], 'end': part['end'], 'speaker': part['speaker'], 'evidence': part.get('evidence', [])}
                               for part in (item.get('performance') or {}).get('analysis_parts') or [item]]}

def unwrap(result):
    if isinstance(result, dict) and 'analysis' in result and set(result) <= {'analysis', 'speech_structure', 'speaker_resolution'}:
        return result['analysis'], result.get('speech_structure', {})
    return result, {}

cases = {case['id']: case for case in job['cases']}
cast, output, audit, calls = [], {cid: {'parts': [], 'degraded': []} for cid in cases}, [], 0
if job['mode'] == 'run':
    for case in job['cases']:
        inputs, targets = case_sources(case)
        output[case['id']]['targets'] = [source['id'] for source in targets]
        pending = [list(batch) for batch in batches(targets)][::-1]
        while pending:
            batch = pending.pop()
            before, after = context(inputs, batch)
            error, rejected, attempts, parts = '', None, [], None
            for attempt in range(2):
                try:
                    calls += 1
                    raw = worker.analyze({'analysis_mode': job['provider'], 'segments': batch, 'cast': cast, 'before': before, 'after': after,
                                          'retry_error': error, 'retry_response': rejected})
                except AnalysisCapacityError as exc:
                    attempts.append({'error': str(exc), 'category': 'analysis_capacity'})
                    if len(batch) > 1:
                        middle = len(batch) // 2
                        pending.extend([batch[middle:], batch[:middle]])
                    break
                except AppError as exc:
                    error, rejected = str(exc), None
                    attempts.append({'error': error, 'category': type(exc).__name__})
                    continue
                proposal, structure = unwrap(raw)
                try:
                    parts, updated = validate_analysis(proposal, batch, cast, [item['id'] for item in before + after], before + after)
                except AppError as exc:
                    error, rejected = str(exc), proposal if isinstance(proposal, dict) else None
                    attempts.append({'answer': proposal, 'speech_structure': structure, 'error': error, 'category': 'validation'})
                    continue
                attempts.append({'answer': proposal, 'speech_structure': structure})
                break
            audit.append({'case': case['id'], 'targets': [source['id'] for source in batch], 'attempts': attempts})
            if parts is None:
                if attempts and attempts[-1].get('category') == 'analysis_capacity' and len(batch) > 1:
                    continue
                output[case['id']]['degraded'].extend({'source': source['id'], 'reason': error or attempts[-1].get('error', '')} for source in batch)
                continue
            cast = updated
            output[case['id']]['parts'].extend(unit(item) for item in parts)
        print(f'{case["id"]}: {len(output[case["id"]]["parts"])} units, {len(output[case["id"]]["degraded"])} degraded', file=sys.stderr, flush=True)
    reconcile = None
    if len(cast) > 1:
        texts = {source['id']: source['text'] for case in job['cases'] for source in case_sources(case)[0]}
        evidence = {pid: texts[pid] for character in cast for pid in character.get('evidence', []) if pid in texts}
        try:
            calls += 1
            cast, merges = reconcile_cast(worker.reconcile({'analysis_mode': job['provider'], 'cast': cast, 'evidence': evidence}), cast, [], evidence)
        except AppError as exc:
            reconcile = {'error': str(exc)}
        else:
            reconcile = {'merges': merges}
    parity = None
else:
    saved = job['saved']
    for cid, entry in saved['cases'].items():
        output[cid] = {'targets': entry['targets'], 'parts': [], 'degraded': entry['degraded']}
    for entry in saved['audit']:
        final = entry['attempts'][-1]
        if 'answer' not in final or 'error' in final:
            continue
        inputs, _ = case_sources(cases[entry['case']])
        sources = {source['id']: source for source in inputs}
        batch = [sources[tid] for tid in entry['targets']]
        before, after = context(inputs, batch)
        parts, cast = validate_analysis(final['answer'], batch, cast, [item['id'] for item in before + after], before + after)
        output[entry['case']]['parts'].extend(unit(item) for item in parts)
    reconcile, audit = saved.get('reconcile'), saved['audit']
    cast = saved['cast']
    merges = (reconcile or {}).get('merges', {})
    mismatched = [cid for cid, entry in output.items()
                  if [{'start': part['start'], 'end': part['end'], 'speaker': merges.get(part['speaker'], part['speaker']), 'warning': part['warning']}
                      for part in entry['parts']] != [{key: part[key] for key in ('start', 'end', 'speaker', 'warning')} for part in saved['cases'][cid]['parts']]]
    parity = {'cases': len(output), 'outer_mismatches': mismatched}
merges = (reconcile or {}).get('merges', {})
for entry in output.values():
    for part in entry['parts']:
        part['speaker'] = merges.get(part['speaker'], part['speaker'])
        for inner in part['analysis_parts']:
            inner['speaker'] = merges.get(inner['speaker'], inner['speaker'])
json.dump({'cast': cast, 'reconcile': reconcile, 'cases': output, 'audit': audit, 'model_calls': calls, 'outer_parity': parity}, sys.stdout, ensure_ascii=False)
'''


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


def status(path, *paths, untracked='all'):
    """Porcelain status rows with both status columns intact."""
    output = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', f'--untracked-files={untracked}', '--', *paths],
                            capture_output=True, text=True, check=True).stdout
    return [line for line in output.split('\n') if line]


def legacy_checkout(path):
    """The verified legacy checkout state; refuses another revision or tracked modifications."""
    path = Path(path).resolve()
    revision = git(path, 'rev-parse', 'HEAD')
    if revision != LEGACY_REVISION:
        raise SystemExit(f'--legacy must be a checkout of {LEGACY_REVISION}, not {revision}.')
    modified = status(path, untracked='no')
    if modified:
        raise SystemExit('--legacy has tracked modifications:\n' + '\n'.join(modified))
    untracked = status(path)
    return {'path': str(path), 'revision': revision, 'tracked_clean': True, 'untracked': untracked}


def candidate_source(root=ROOT):
    """The index tree this process imports from; refuses unstaged or untracked runtime/scorer source."""
    drift = [line for line in status(root, 'runtime', 'scripts') if line[1:2] != ' ']
    if drift:
        raise SystemExit('Candidate runtime/scripts differ from the index; stage or discard them so the report names the executed source:\n' + '\n'.join(drift))
    return {'index_tree': git(root, 'write-tree'), 'runtime_and_scripts_match_index': True, 'head': git(root, 'rev-parse', 'HEAD')}


def selection(legacy):
    cases, gold_cast, protocol = harness.load_gold(harness.GOLD, 'selection')
    legacy_gold = Path(legacy['path']) / 'manual-labeling' / 'speaker-resolution-gold-v1' / 'gold.json'
    if hashlib.sha256(legacy_gold.read_bytes()).hexdigest() != hashlib.sha256((harness.GOLD / 'gold.json').read_bytes()).hexdigest():
        raise SystemExit('The legacy checkout carries a different gold.json.')
    stripped = [{'id': case['id'], 'chapter_id': case['chapter_id'], 'source_id': case['source_id'], 'text': case['text'],
                 'before': [{'source_id': item['source_id'], 'text': item['text']} for item in case['before']],
                 'after': [{'source_id': item['source_id'], 'text': item['text']} for item in case['after']]} for case in cases]
    return cases, gold_cast, protocol, stripped


def legacy_process(legacy, job):
    result = subprocess.run([sys.executable, '-c', LEGACY, legacy['path']], input=json.dumps(job, ensure_ascii=False), capture_output=True, text=True,
                            cwd=legacy['path'])
    if result.returncode:
        raise SystemExit('Legacy process failed:\n' + result.stderr[-4000:])
    return json.loads(result.stdout)


def score(cases, gold_cast, predicted, projection):
    state = {'cast': predicted['cast'], 'records': [], 'degraded': [], 'targets': {}, 'parts': {}}
    for case in cases:
        entry = predicted['cases'][case['id']]
        state['targets'][case['id']] = entry['targets']
        state['degraded'].extend({**item, 'case': case['id']} for item in entry['degraded'])
        degraded = [{'start': int(item['source'].rsplit('-', 2)[1]), 'end': int(item['source'].rsplit('-', 2)[2]), 'speaker': 'unknown',
                     'warning': DEGRADED_HOLD, 'held': True} for item in entry['degraded']]
        parts = [{**inner, 'warning': unit['warning']} for unit in entry['parts'] for inner in unit['analysis_parts']] if projection == 'analysis' \
            else [{key: unit[key] for key in ('start', 'end', 'speaker', 'warning')} for unit in entry['parts']]
        state['parts'][case['id']] = [{**part, 'held': part['speaker'] == 'unknown'} for part in parts] + degraded
    report = harness.score(cases, state, gold_cast)
    return {key: value for key, value in report.items() if key not in ('proposals', 'records')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('mode', choices=('run', 'replay'))
    parser.add_argument('--legacy', type=Path, required=True, help=f'Checkout of master {LEGACY_REVISION[:12]} without tracked modifications.')
    parser.add_argument('--provider', choices=('codex_cli', 'claude_cli'), help='run: the legacy CLI provider.')
    parser.add_argument('--raw', type=Path, help='replay: a saved legacy prediction file (with its raw answers in `audit`).')
    parser.add_argument('--out', type=Path, required=True, help='New report file.')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('--out must name a new file.')
    legacy = legacy_checkout(args.legacy)
    candidate = candidate_source()
    cases, gold_cast, protocol, stripped = selection(legacy)
    if args.mode == 'run':
        if not args.provider:
            parser.error('run needs --provider.')
        predicted = legacy_process(legacy, {'mode': 'run', 'provider': args.provider, 'cases': stripped})
        provider, source = args.provider, {'model_calls': predicted['model_calls']}
    else:
        if not args.raw:
            parser.error('replay needs --raw.')
        raw = args.raw.read_bytes()
        saved = json.loads(raw)
        if saved.get('gold_content_sha256') != protocol['gold_content_sha256'] or saved.get('master_commit') != LEGACY_REVISION:
            parser.error('--raw was produced for another gold set or revision.')
        predicted = legacy_process(legacy, {'mode': 'replay', 'cases': stripped, 'saved': saved})
        if predicted['outer_parity']['outer_mismatches']:
            raise SystemExit(f'Replay differs from the saved outer output: {predicted["outer_parity"]["outer_mismatches"]}')
        provider, source = saved['provider'], {'raw_report': str(args.raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'model_calls': 0,
                                               'outer_replay': predicted['outer_parity']}
    report = {'baseline': f'legacy master selection {args.mode}', 'provider': provider, 'gold_content_sha256': protocol['gold_content_sha256'],
              'legacy': legacy, 'differences': DIFFERENCES, 'reconcile': predicted['reconcile'],
              'provenance': {**source, 'gold_to_inference': False, 'scorer': 'scripts/evaluate_speakers.py score', 'candidate': candidate},
              'analysis': score(cases, gold_cast, predicted, 'analysis'),
              'outer_units_projection': {key: value for key, value in score(cases, gold_cast, predicted, 'outer').items() if key in ('acceptance', 'boundaries', 'speakers')},
              'predictions': predicted}
    with args.out.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=1))
    analysis, outer = report['analysis'], report['outer_units_projection']
    print(f'{provider} {args.mode}: analysis accepted {analysis["acceptance"]["accepted"]}, wrongly accepted {analysis["acceptance"]["accepted_wrong"]}, '
          f'useful coverage {analysis["acceptance"]["useful_coverage"]}, boundaries {analysis["boundaries"]["matched"]}/{analysis["boundaries"]["predicted"]}/'
          f'{analysis["boundaries"]["gold"]}; outer-unit boundaries {outer["boundaries"]["matched"]}/{outer["boundaries"]["predicted"]}; '
          f'degraded {analysis["degraded"]["targets"]}; model calls {source["model_calls"]}.')


if __name__ == '__main__':
    main()
