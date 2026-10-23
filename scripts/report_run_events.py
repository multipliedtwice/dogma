"""Per-iteration report from a generation's events.jsonl: where every minute went and why each retake happened.

Reads only the timestamped events written by rimsky.run_events (worker,
native renders, recognizer audits, stage marks, take results, publications).
Prints and writes run-report.json beside the event log.
"""
import argparse, collections, json
from pathlib import Path


def load(path):
    return [json.loads(line) for line in Path(path).read_text('utf-8').splitlines() if line.strip()]


def report(events):
    events.sort(key=lambda e: e['t'])
    start = next((e['t'] for e in events if e['kind'] == 'run.start'), events[0]['t']); end = events[-1]['t']
    out = {'wall_minutes': round((end - start)/60, 1)}
    native = [e for e in events if e['kind'] == 'native']
    out['higgs'] = {'launches': len(native), 'generation_minutes': round(sum(e['generation_seconds'] for e in native)/60, 1),
                    'load_overhead_minutes': round(sum(e['overhead_seconds'] for e in native)/60, 1),
                    'by_stage_minutes': {k: round(v/60, 1) for k, v in collections.Counter(
                        {s: sum(e['seconds'] for e in native if e.get('stage') == s) for s in {e.get('stage') for e in native}}).most_common()}}
    asr = collections.defaultdict(float)
    for e in events:
        if e['kind'] in ('asr.hash_model', 'asr.load', 'asr.transcribe'):asr[e['kind'].split('.')[1]] += e['seconds']
    out['primary_recognizer_minutes'] = {k: round(v/60, 1) for k, v in asr.items()}
    proc = collections.defaultdict(lambda: [0, 0.0])
    for e in events:
        if e['kind'] == 'process.end':proc[e['label']][0] += 1; proc[e['label']][1] += e['seconds']
    out['child_processes'] = {k: {'runs': n, 'minutes': round(s/60, 1)} for k, (n, s) in sorted(proc.items(), key=lambda kv: -kv[1][1])}
    # Stage durations: consecutive marks within each section.
    marks = [e for e in events if e['kind'] == 'mark']; stage_time = collections.defaultdict(float)
    for a, b in zip(marks, marks[1:] + [{'t': end}]):
        stage_time[(a.get('section'), a.get('stage'))] += b['t'] - a['t']
    by_stage = collections.defaultdict(float)
    for (sec, st), s in stage_time.items():by_stage[st] += s
    out['stage_minutes'] = {k: round(v/60, 1) for k, v in sorted(by_stage.items(), key=lambda kv: -kv[1])}
    # Retake causes: every non-passing take result and the attempt that finally passed.
    results = [e for e in events if e['kind'] == 'take.result']
    causes = collections.Counter(); per_unit = collections.defaultdict(list)
    for e in results:
        reason = ([r for r, flag in (('clipped start', e['unsafe_start']), ('clipped end', e['unsafe_end'])) if flag]
                  + ([] if e['word_status'] in ('asr_exact_match', 'nonverbal_not_spoken_text') else ['word flag']))
        per_unit[e['unit']].append({'attempt': e['attempt'], 'stage': e.get('stage'), 'reasons': reason, 'heard': e['heard'][:120]})
        if e['attempt'] == 0:
            for r in reason or ['passed']:causes[r] += 1
    final = {e['unit']: e for e in events if e['kind'] == 'unit.final'}
    out['first_takes'] = {'units': sum(1 for e in results if e['attempt'] == 0), 'causes': dict(causes)}
    out['retaken_units'] = {u: {'takes': rows, 'final_take': final.get(u, {}).get('take'), 'verified_via': final.get(u, {}).get('via'),
                                'rules': final.get(u, {}).get('rules')} for u, rows in per_unit.items() if len(rows) > 1 or rows[0]['reasons']}
    pubs = [e for e in events if e['kind'] == 'publish']
    out['publish_timeline'] = [{'after_minutes': round((e['t'] - start)/60, 1), 'chapter': e['chapter'], 'playable_minutes': e['minutes']} for e in pubs]
    if pubs:out['first_publish_after_minutes'] = round((pubs[0]['t'] - start)/60, 1)
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('generation', type=Path)
    p.add_argument('--last-run', action='store_true', help='only events since the latest run.start')
    a = p.parse_args(); events = load(a.generation/'events.jsonl')
    if a.last_run:
        last = max(e['t'] for e in events if e['kind'] == 'run.start'); events = [e for e in events if e['t'] >= last]
    result = report(events)
    (a.generation/'run-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    brief = {k: v for k, v in result.items() if k != 'retaken_units'}
    print(json.dumps(brief, ensure_ascii=False, indent=1)); print('retaken units:', len(result['retaken_units']))
