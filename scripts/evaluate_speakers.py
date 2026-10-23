from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.analysis import DEGRADED_HOLD, DEGRADED_WARNING, analyse_sources, common_words
from rimsky.casting import speech_volume
from rimsky.jobs import Engine, Jobs, canonical_name, name_tokens
from rimsky.packs import load_pack, pack_unchanged
from rimsky.preparation import analysis_chapters, speech_sources
from rimsky.store import speaker_review_holds
from rimsky.util import AppError, write_json

GOLD = ROOT / 'manual-labeling' / 'speaker-resolution-gold-v1'
SPLITS = ('selection', 'test')
PROVIDERS = ('local', 'codex_cli', 'claude_cli')
TEST_NOTE = 'Test cases are for final acceptance only. Do not use them to tune prompts, rules, thresholds or the model choice.'
LIMITS = ('Not validated with a real model: this harness was developed and unit-tested with fake engines only.',
          'Each case is analysed alone with its own gold before/after context, in gold order. The app analyses whole chapters in batches, '
          'so registry growth and batch composition differ.',
          'Predicted characters without a name or alias match are mapped to gold characters by majority overlap with the scored spans, '
          'which favours the model.',
          'Boundaries are compared after moving each one forward to the next letter or digit, so whitespace and punctuation placement do not count. '
          'Delivery, traits, voices and audio are not scored.')


def load_gold(folder, split='selection', final_acceptance=False):
    folder = Path(folder)
    if split not in SPLITS:
        raise AppError('Choose the selection or test split.')
    if split == 'test' and not final_acceptance:
        raise AppError(f'The test split needs explicit final acceptance. {TEST_NOTE}')
    protocol = json.loads((folder / 'evaluation-protocol.json').read_text('utf-8'))
    raw = (folder / 'gold.json').read_bytes()
    manifest = folder / 'manifest.json'
    if manifest.is_file() and json.loads(manifest.read_text('utf-8')).get('sha256') != hashlib.sha256(raw).hexdigest():
        raise AppError('gold.json does not match the checksum in manifest.json.')
    gold = json.loads(raw)
    if not gold.get('content_sha256') or protocol.get('gold_content_sha256') != gold['content_sha256']:
        raise AppError('The evaluation protocol was frozen for another gold version.')
    selection, test = set(protocol['selection_case_ids']), set(protocol['test_case_ids'])
    if selection & test:
        raise AppError('A case is listed in both the selection and the test split.')
    chosen = selection if split == 'selection' else test
    cases = [case for case in gold['cases'] if case['id'] in chosen]
    if len(cases) != len(chosen):
        raise AppError(f'gold.json lacks {split} cases listed in the evaluation protocol.')
    return cases, gold['cast'], protocol


def case_sources(case):
    blocks = [*case.get('before', []), case, *case.get('after', [])]
    book = {'chapters': [{'id': case['chapter_id'], 'blocks': [{'id': block['source_id'], 'kind': 'paragraph', 'text': block['text']} for block in blocks]}]}
    inputs = list(speech_sources(book, [case['chapter_id']]))
    return inputs, [source for source in inputs if source['source_id'] == case['source_id']]


def load_book(path, folder, cases):
    """The exact Book 1 import the gold was frozen from; refuses any other file or changed case text."""
    raw = Path(path).read_bytes()
    expected = json.loads((Path(folder) / 'gold.json').read_text('utf-8'))['sources']['book']
    if hashlib.sha256(raw).hexdigest() != expected['sha256']:
        raise AppError(f'--book must be the training-book.json the gold was frozen from (sha256 {expected["sha256"]}, source {expected["source_hash"]}).')
    book = json.loads(raw)
    blocks = {(chapter['id'], block['id']): block for chapter in book['chapters'] for block in chapter['blocks']}
    for case in cases:
        block = blocks.get((case['chapter_id'], case['source_id']))
        if block is None or hashlib.sha256(block['text'].encode('utf-8')).hexdigest() != case['source_sha256']:
            raise AppError(f'Book text differs from gold case {case["id"]}.')
    return book


def work_plan(cases, book=None):
    """Isolated cases with their own context, or the production chapter plan (default narration plus the cases' chapters, in book
    order, unscored earlier and bridging chapters included) scoring only the given cases."""
    if book is None:
        return [{'cases': [case['id']], 'owners': {case['source_id']: case['id']}, 'inputs': inputs, 'targets': targets}
                for case in cases for inputs, targets in [case_sources(case)]]
    bases = list(speech_sources(book, analysis_chapters(book, list(dict.fromkeys(case['chapter_id'] for case in cases)))))
    plan = []
    for chapter in book['chapters']:
        inputs = [source for source in bases if source['chapter_id'] == chapter['id']]
        if inputs:
            owned = [case for case in cases if case['chapter_id'] == chapter['id']]
            plan.append({'chapter': chapter['id'], 'cases': [case['id'] for case in owned], 'owners': {case['source_id']: case['id'] for case in owned},
                         'inputs': inputs, 'targets': inputs})
    return plan


def predict(cases, jobs, arguments, reconcile=True, secondary=None, secondary_repair='none', book=None):
    names = lru_cache(maxsize=16384)(lambda text: canonical_name(name_tokens(text)))
    common = (common_words(source['text'] for work in work_plan(cases, book) for source in work['inputs']) if book is not None else
              common_words(block['text'] for case in cases for block in [*case.get('before', []), case, *case.get('after', [])]))
    state = {'cast': [], 'accepted': {}, 'volume': Counter(), 'inputs': [], 'texts': {}, 'parts': {}, 'targets': {}, 'degraded': [],
             'records': [], 'sources': {}, 'invoked': {}, 'case_of': {}, 'done': [], 'stopped': None, 'reconcile': None}

    def invoked(provider):
        def call(payload):
            state['invoked'].update((item['id'], None) for item in payload['segments'])
            return provider(payload)
        return call

    def analyse(work):
        inputs, targets, owners = work['inputs'], work['targets'], work['owners']
        state['texts'].update((source['id'], source['text']) for source in inputs)
        state['sources'].update((source['id'], source) for source in inputs)
        for source_id, cid in owners.items():
            state['parts'][cid], state['targets'][cid] = [], [source['id'] for source in targets if source['source_id'] == source_id]
            state['case_of'].update((tid, cid) for tid in state['targets'][cid])
        owner = lambda source_id: owners.get(source_id)

        def commit(done, units, failed, key, records=(), structure=None, reviews=(), audit=()):
            held = speaker_review_holds(units, list(records))[1]
            others = [record for record in records if record.get('tier') != 'agreement']
            single = speaker_review_holds(units, others)[1] if secondary else held
            first = speaker_review_holds(units, others + [record for entry in audit for record in entry.get('first_records', [])])[1] if secondary else held
            aligned = speaker_review_holds(units, [record for record in records if not (record.get('alignment') or {}).get('aligned')])[1] if secondary else held
            for unit in units:
                if owner(unit['source_id']) is None:
                    continue
                for part in (unit.get('performance') or {}).get('analysis_parts') or [unit]:
                    state['parts'][owner(unit['source_id'])].append({'start': part['start'], 'end': part['end'], 'speaker': part['speaker'],
                                                       'warning': unit['warning'], 'held': unit['id'] in held, 'held_single': unit['id'] in single,
                                                       'held_first': unit['id'] in first, 'held_aligned': unit['id'] in aligned})
            state['degraded'].extend({**item, 'case': owner(state['sources'][item['source']]['source_id'])} for item in failed
                                     if owner(state['sources'][item['source']]['source_id']))
            state['records'].extend({**record, 'case': owner(record['source_id'])} for record in records if owner(record.get('source_id')))

        analyse_sources(targets, inputs, state, invoked(lambda payload: jobs._call('analyze', {**arguments, **payload})), commit, names=names, common=common,
                        critique=lambda request, schema: jobs._call('critique', {**arguments, 'request': request, 'schema': schema}),
                        second=invoked(lambda payload: jobs._call('analyze', {**arguments, 'analysis_mode': secondary, **payload})) if secondary else None,
                        second_repair=secondary_repair == 'own-v1')

    for work in work_plan(cases, book):
        try:
            analyse(work)
        except AppError as error:
            state['stopped'] = {'case': work['cases'][0] if work['cases'] else '', **({'chapter': work['chapter']} if 'chapter' in work else {}), 'error': str(error)}
            break
        state['done'].extend(work['cases'])
    if reconcile and state['stopped'] is None and len(state['cast']) > 1:
        try:
            cast, merges, unmerged, skipped, _ = jobs._reconcile(arguments, state['cast'], state['texts'], speech_volume(state['inputs']))
        except AppError as error:
            state['reconcile'] = {'error': str(error)}
        else:
            state['cast'] = cast
            if secondary:
                state['records'].extend({'case': cid, 'start': part['start'], 'end': part['end'], 'speaker': merges[part['speaker']], 'primary_speaker': part['speaker'],
                                         'tier': 'agreement', 'triggers': ['alias_merge_after_agreement'], 'status': 'review'}
                                        for cid, parts in state['parts'].items() for part in parts if part['speaker'] in merges and not part['held'])
            state['parts'] = {cid: [{**part, 'speaker': merges.get(part['speaker'], part['speaker']),
                                     **({'held': True, 'held_first': True, 'held_aligned': True} if secondary and part['speaker'] in merges else {})} for part in parts]
                              for cid, parts in state['parts'].items()}
            state['reconcile'] = {'merges': len(merges), 'unmerged': unmerged, 'skipped': skipped}
    return state


def advance(text, position):
    while position < len(text) and not text[position].isalnum():
        position += 1
    return position


def cuts(text, spans):
    return {advance(text, end) for (end, first), (_, second) in zip(spans, spans[1:])
            if None not in (first, second) and first != second} - {len(text)}


def letters(text, start, end):
    return sum(character.isalnum() for character in text[start:end])


def ratio(part, whole):
    return round(part / whole, 4) if whole else None


def name_keys(character):
    return {key for key in (canonical_name(name_tokens(label)) for label in [character.get('name', ''), *character.get('aliases', [])]) if key}


def mapping(cast, gold_cast, votes):
    labels = {item['id']: name_keys(item) for item in gold_cast}
    result = {}
    for character in cast:
        if character['id'] == 'narrator':
            continue
        own, keys = canonical_name(name_tokens(character.get('name', ''))), name_keys(character)
        scores = {gid: (own in found, len(keys & found)) for gid, found in labels.items() if keys & found}
        best = max(scores.values(), default=None)
        named = [gid for gid, value in scores.items() if value == best]
        if len(named) == 1:
            result[character['id']] = {'gold': named[0], 'by': 'name'}
            continue
        tally = {gid: count for gid, count in votes.get(character['id'], Counter()).items() if count and (not named or gid in named)}
        result[character['id']] = ({'gold': min(tally, key=lambda gid: (-tally[gid], gid)), 'by': 'overlap'} if tally
                                   else {'gold': None, 'by': None})
    return result


def score(cases, state, gold_cast, flag='held'):
    gold_ids = {item['id'] for item in gold_cast}
    predictions = {case['id']: sorted(state['parts'][case['id']], key=lambda part: part['start']) for case in cases}
    votes = {}
    for case in cases:
        for gold in case['parts']:
            if gold['mode'] != 'speech' or gold['speaker'] not in gold_ids:
                continue
            for part in predictions[case['id']]:
                low, high = max(gold['start'], part['start']), min(gold['end'], part['end'])
                if low < high and part['speaker'] not in ('narrator', 'unknown') and not part[flag]:
                    votes.setdefault(part['speaker'], Counter())[gold['speaker']] += letters(case['text'], low, high)
    mapped = mapping(state['cast'], gold_cast, votes)

    def degraded_part(part):
        return DEGRADED_WARNING in part['warning'] or DEGRADED_HOLD in part['warning']

    def label(part):
        if degraded_part(part):
            return 'degraded'
        return part['speaker'] if part['speaker'] in ('narrator', 'unknown') else mapped.get(part['speaker'], {}).get('gold') or part['speaker']

    degraded = {}
    for item in state['degraded']:
        degraded.setdefault(item['case'], []).append({'source': item['source'], 'reason': item['reason']})
    totals, errors, held_spans = Counter(), [], []
    for case in cases:
        text, parts = case['text'], predictions[case['id']]
        expected = cuts(text, [(gold['end'], gold['mode'] == 'speech') for gold in case['parts']])
        found = cuts(text, [(part['end'], None if degraded_part(part) else part['speaker'] != 'narrator') for part in parts])
        totals.update(matched=len(expected & found), predicted=len(found), gold=len(expected))
        wrong = []
        for gold in case['parts']:
            if gold['mode'] != 'speech':
                continue
            weights, accepted = Counter(), Counter()
            for part in parts:
                low, high = max(gold['start'], part['start']), min(gold['end'], part['end'])
                if low < high:
                    weights[label(part)] += letters(text, low, high)
                    if not part[flag]:
                        accepted[label(part)] += letters(text, low, high)
            weights, accepted = +weights, +accepted
            got = min(weights, key=lambda item: (-weights[item], item)) if weights else 'narrator'
            held = not accepted
            if gold['speaker'] not in gold_ids:
                totals['gold_unresolved'] += 1
                totals['gold_unresolved_agreed'] += got in ('unknown', 'degraded')
                continue
            outcome = 'correct' if got == gold['speaker'] else got if got in ('narrator', 'unknown', 'degraded') else 'character'
            totals['spans'] += 1
            totals[outcome] += 1
            if held:
                totals['held'] += 1
                totals['held_' + ('correct' if outcome == 'correct' else 'wrong')] += 1
                reasons = [record for record in state['records'] if record.get('case') == case['id'] and record.get('status') == 'review'
                           and (flag != 'held_single' or record.get('tier') != 'agreement')
                           and record.get('start', 0) < gold['end'] and gold['start'] < record.get('end', 0)]
                held_spans.append({'case': case['id'], 'start': gold['start'], 'end': gold['end'], 'gold': gold['speaker'], 'proposal': got,
                                   'proposal_correct': outcome == 'correct', 'triggers': sorted({trigger for record in reasons for trigger in record.get('triggers', [])}),
                                   'critic': [{'speaker': record.get('critic_speaker'), 'reason': record.get('critic_reason')} for record in reasons if record.get('critic_reason')],
                                   'text': text[gold['start']:gold['end']][:120]})
            else:
                totals['accepted'] += 1
                wrongly = [item for item in accepted if item != gold['speaker']]
                totals['accepted_correct'] += not wrongly
                totals['accepted_partly_held'] += sum(weights.values()) > sum(accepted.values())
                if wrongly:
                    totals['accepted_wrong_letters'] += sum(accepted[item] for item in wrongly)
            if outcome != 'correct':
                wrong.append({'start': gold['start'], 'end': gold['end'], 'gold': gold['speaker'], 'predicted': got, 'text': text[gold['start']:gold['end']][:120]})
        if expected != found or wrong or case['id'] in degraded:
            errors.append({'id': case['id'], 'chapter_id': case['chapter_id'], 'missed_boundaries': sorted(expected - found),
                           'extra_boundaries': sorted(found - expected), 'speakers': wrong, 'degraded': degraded.get(case['id'], [])})
    matched, predicted, expected = totals['matched'], totals['predicted'], totals['gold']
    targets = sum(len(state['targets'][case['id']]) for case in cases)
    passages = [entry for case in cases for entry in degraded.get(case['id'], [])]
    names = {character['id']: character.get('name', '') for character in state['cast']}
    return {'cases': len(cases), 'targets': targets,
            'boundaries': {'gold': expected, 'predicted': predicted, 'matched': matched, 'precision': ratio(matched, predicted),
                           'recall': ratio(matched, expected), 'f1': ratio(2 * matched, predicted + expected)},
            'speakers': {'spans': totals['spans'], 'correct': totals['correct'], 'accuracy': ratio(totals['correct'], totals['spans']),
                         **{key: totals[key] for key in ('character', 'narrator', 'unknown', 'degraded')}},
            'acceptance': {'accepted': totals['accepted'], 'accepted_correct': totals['accepted_correct'],
                           'accepted_wrong': totals['accepted'] - totals['accepted_correct'],
                           'accepted_wrong_letters': totals['accepted_wrong_letters'], 'accepted_partly_held': totals['accepted_partly_held'],
                           'accepted_precision': ratio(totals['accepted_correct'], totals['accepted']),
                           'useful_coverage': ratio(totals['accepted_correct'], totals['spans']),
                           'held': totals['held'], 'held_proposal_correct': totals['held_correct'], 'held_proposal_wrong': totals['held_wrong']},
            'holds': Counter(trigger for record in state['records'] if record.get('status') == 'review' and (flag != 'held_single' or record.get('tier') != 'agreement')
                             for trigger in record.get('triggers', [])),
            'held_spans': held_spans,
            'proposals': {case['id']: predictions[case['id']] for case in cases},
            'records': [record for record in state['records'] if record.get('case') in {case['id'] for case in cases}],
            'unresolved_rate': ratio(totals['unknown'], totals['spans']),
            'degraded': {'targets': len(passages), 'rate': ratio(len(passages), targets), 'passages': passages},
            'gold_unresolved': {'spans': totals['gold_unresolved'], 'predicted_unresolved': totals['gold_unresolved_agreed']},
            'mapping': {cid: {'name': names.get(cid, ''), **entry} for cid, entry in mapped.items()},
            'errors': errors}


def cli_version(provider):
    import shutil, subprocess
    name = {'codex_cli': 'codex', 'claude_cli': 'claude'}.get(provider)
    executable = name and shutil.which(name)
    if not executable:
        return ''
    try:
        return subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def runtime_binaries():
    resource = os.environ.get('RIMSKY_RESOURCE_DIR')
    return Path(resource) / 'runtime' if resource else ROOT / 'resources' / 'runtime'


def evaluate(cases, gold_cast, pack, engine_factory=Engine, device='auto', reconcile=True, binaries=None, provider='local', tie_breaker='none', effort='low', secondary=None,
             secondary_repair='none', book=None):
    binaries = binaries or runtime_binaries()
    jobs = Jobs(None, binaries, engine_factory)
    arguments = {'pack': pack, 'device': device, 'analysis_mode': provider, 'tie_breaker': tie_breaker, 'analysis_effort': effort}
    if pack is not None:
        pack_unchanged(pack)
    jobs.engine = engine_factory(binaries)
    try:
        state = predict(cases, jobs, arguments, reconcile, secondary, secondary_repair, book)
    finally:
        if jobs.engine:
            jobs.engine.close()
    done = [case for case in cases if case['id'] in set(state['done'])]
    located = lambda tid: {'chapter_id': state['sources'][tid].get('chapter_id', ''), **({'case': state['case_of'][tid]} if tid in state['case_of'] else {})}
    audit = [{**entry, **located(tid)} for tid, entry in state.get('agreement_trail', {}).items()]
    attempted = {state['case_of'][tid] for tid in state['invoked'] if tid in state['case_of']}
    control = {}
    if secondary:
        single = score(done, state, gold_cast, 'held_single')
        control = {'single_provider_control': {key: single[key] for key in ('acceptance', 'holds', 'held_spans', 'mapping')}}
        if secondary_repair != 'none':
            unrepaired = score(done, state, gold_cast, 'held_first')
            control['first_secondary_outcome'] = {'acceptance': unrepaired['acceptance']}
        aligned = score(done, state, gold_cast, 'held_aligned')
        control['new_identity_alignment_counterfactual'] = {
            'acceptance': aligned['acceptance'],
            'alignments': [{key: record.get(key) for key in ('case', 'target_id', 'start', 'end', 'speaker', 'secondary_speaker', 'alignment')}
                           for record in state['records'] if 'alignment' in record]}
    return {**score(done, state, gold_cast), **control, 'planned_cases': len(cases), 'attempted_cases': len(attempted),
            'attempted_targets': [{'target_id': tid, **located(tid)} for tid in state['invoked']], 'agreement_audit': audit,
            'reconcile': state['reconcile'], 'stopped': state['stopped']}


def summary(report):
    value = lambda number: 'n/a' if number is None else f'{number:.3f}'
    bounds, speakers, degraded = report['boundaries'], report['speakers'], report['degraded']
    lines = [f'Speaker evaluation: {report["split"]} split, {report["cases"]} cases, {report["targets"]} target passages.',
             (f'Pack {report["pack"]["name"]} (revision {report["pack"]["revision"]}), ' if report.get('pack') else '') + f'provider {report.get("provider", "local")}, device {report["device"]}.',
             f'Speech/narration boundaries: precision {value(bounds["precision"])}, recall {value(bounds["recall"])}, F1 {value(bounds["f1"])} '
             f'({bounds["matched"]} matched, {bounds["predicted"]} predicted, {bounds["gold"]} in gold).',
             f'Speaker attribution: {value(speakers["accuracy"])} ({speakers["correct"]} of {speakers["spans"]} gold speech spans; '
             f'other character {speakers["character"]}, narrator {speakers["narrator"]}, unresolved {speakers["unknown"]}, degraded {speakers["degraded"]}).',
             f'Unresolved rate {value(report["unresolved_rate"])}; degraded target passages {degraded["targets"]} (rate {value(degraded["rate"])}).',
             f'Accepted speaker spans {report["acceptance"]["accepted"]}: precision {value(report["acceptance"]["accepted_precision"])}, '
             f'wrongly accepted {report["acceptance"]["accepted_wrong"]}, useful coverage {value(report["acceptance"]["useful_coverage"])}; '
             f'held {report["acceptance"]["held"]} (proposal correct {report["acceptance"]["held_proposal_correct"]}).',
             f'Cases with errors: {len(report["errors"])}.']
    if report.get('single_provider_control'):
        control = report['single_provider_control']['acceptance']
        lines.insert(-1, f'Same run without the agreement gate: accepted {control["accepted"]}, wrongly accepted {control["accepted_wrong"]}, '
                         f'useful coverage {value(control["useful_coverage"])}; held {control["held"]} (proposal correct {control["held_proposal_correct"]}).')
    if report.get('new_identity_alignment_counterfactual'):
        aligned = report['new_identity_alignment_counterfactual']
        lines.insert(-1, f'Counterfactual source-anchored new-identity alignment (not applied): accepted {aligned["acceptance"]["accepted"]}, '
                         f'wrongly accepted {aligned["acceptance"]["accepted_wrong"]}, useful coverage {value(aligned["acceptance"]["useful_coverage"])}; '
                         f'{sum(item["alignment"]["aligned"] for item in aligned["alignments"])} of {len(aligned["alignments"])} new-identity holds aligned.')
    if report.get('first_secondary_outcome'):
        first = report['first_secondary_outcome']['acceptance']
        lines.insert(-1, f'Gate with the unrepaired second answers: accepted {first["accepted"]}, wrongly accepted {first["accepted_wrong"]}, '
                         f'useful coverage {value(first["useful_coverage"])}; held {first["held"]}.')
    reconcile = report['reconcile']
    if reconcile:
        lines.append(f'Alias reconciliation failed: {reconcile["error"]}' if 'error' in reconcile else
                     f'Alias reconciliation: {reconcile["merges"]} merges, {reconcile["unmerged"]} unmerged, {reconcile["skipped"]} skipped.')
    if report['stopped']:
        lines.append(f'Stopped at case {report["stopped"]["case"]}: {report["stopped"]["error"]} Later cases were not scored.')
    return '\n'.join([*lines, 'Limits:', *(f'- {item}' for item in report['limits'])])


def main(argv=None, engine_factory=Engine):
    parser = argparse.ArgumentParser(description='Score the app speaker analysis against the frozen speaker-resolution gold set. Offline; needs a real local model pack.')
    parser.add_argument('--pack', type=Path, help='Sealed local model pack whose text model is evaluated.')
    parser.add_argument('--gold', type=Path, default=GOLD, help='Folder with gold.json, evaluation-protocol.json and manifest.json.')
    parser.add_argument('--split', choices=SPLITS, default='selection')
    parser.add_argument('--final-acceptance', action='store_true', help='Required for the test split.')
    parser.add_argument('--limit', type=int, help='Score only the first N cases of the split.')
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda', 'mps'), default='auto')
    parser.add_argument('--no-reconcile', action='store_true', help='Skip alias reconciliation after analysis.')
    parser.add_argument('--out', type=Path, help='Write the full JSON report to this file.')
    parser.add_argument('--provider', choices=PROVIDERS, default='local', help='Analysis provider; the CLI providers need a signed-in CLI and no local pack.')
    parser.add_argument('--tie-breaker', choices=('none', 'codex_cli', 'claude_cli'), default='none', help='Hard-case speaker check for the local staged path.')
    parser.add_argument('--effort', choices=('low', 'medium', 'high'), default='low', help='CLI reasoning effort; low is the pinned production setting.')
    parser.add_argument('--secondary', choices=PROVIDERS, help='Development gate: a second blind provider answers the same frozen request; only agreed identities are accepted.')
    parser.add_argument('--book', type=Path, help='Chapter-ordered mode: the exact training-book.json the gold was frozen from; whole chapters are analysed in '
                        'book order with one growing registry, and only the split cases are scored.')
    parser.add_argument('--secondary-repair', choices=('none', 'own-v1'), default='none',
                        help='own-v1: one repair of an invalid second answer from its own rejected answer and error only.')
    args = parser.parse_args(argv)
    if args.secondary == 'local' and args.secondary_repair != 'none':
        parser.error('--secondary-repair own-v1 needs a CLI second provider: the local analysis paths rerun their structure pass instead of reusing the fixed structure.')
    if args.split == 'test':
        print(TEST_NOTE, file=sys.stderr)
        if not args.final_acceptance:
            parser.error('the test split needs --final-acceptance')
    if args.pack is None and args.provider == 'local':
        parser.error('--pack is required for the local provider; point it at a real sealed model pack')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be a positive number')
    try:
        cases, gold_cast, protocol = load_gold(args.gold, args.split, args.final_acceptance)
        pack = load_pack(str(args.pack), verify=True) if args.pack else None
        book = load_book(args.book, args.gold, cases) if args.book else None
        result = evaluate(cases[:args.limit], gold_cast, pack, engine_factory, args.device, not args.no_reconcile, provider=args.provider, tie_breaker=args.tie_breaker,
                          effort=args.effort, secondary=args.secondary, secondary_repair=args.secondary_repair, book=book)
    except (AppError, OSError, ValueError, KeyError) as error:
        print(f'Evaluation failed: {error}', file=sys.stderr)
        return 1
    note = protocol.get('selection_note', '') if args.split == 'selection' else TEST_NOTE
    report = {'schema': 1, 'split': args.split, 'final_acceptance': args.final_acceptance,
              'gold': {'folder': str(args.gold), 'content_sha256': protocol['gold_content_sha256'], 'split_cases': len(cases)},
              'provider': args.provider, 'tie_breaker': args.tie_breaker,
              'configuration': {'provider': args.provider, 'effort': args.effort, 'tie_breaker': args.tie_breaker, 'reconcile': not args.no_reconcile,
                                'mode': 'chapters' if args.book else 'cases',
                                'cli_version': cli_version(args.provider), 'passes': ['structure', 'primary', 'critic'],
                                **({'secondary': args.secondary, 'secondary_cli_version': cli_version(args.secondary), 'gate': 'blind_agreement',
                                    'secondary_repair': args.secondary_repair} if args.secondary else {})},
              'pack': {'name': pack.get('name', ''), 'revision': pack['_fingerprint'][:12], 'context': pack['text_model'].get('context', 8192)} if pack else None,
              'device': args.device, **result, 'limits': [*LIMITS, *([note] if note else [])]}
    if args.out:
        write_json(args.out, report)
    print(summary(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
