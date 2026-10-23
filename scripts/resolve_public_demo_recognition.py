"""Recompute disclosed demo recognition resolutions without changing native inputs.

Default --dry-run writes private, content-addressed proposals only. --publish
explicitly publishes a verified prefix through the original renderer, replacing
its audit callback only in this process. No recognizer or synthesis is launched.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.speech_quality import transcript_check, word_tokens
from rimsky.util import file_digest
import render_public_audiobook_demo as renderer
import freeze_public_audiobook_demo as preparation

RULE = 'public-demo-source-bound-recognition-v1'
TITLE_RULE = 'source-recorded-english-title-orthography-v1'
NUMBER_RULE = 'source-recorded-english-number-orthography-v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(value):
    return renderer.sha(value)


def immutable(path, value):
    preparation.immutable(path, value)


def dependencies():
    return {str(path.relative_to(ROOT)).replace('\\', '/'): file_digest(path) for path in [
        Path(__file__), Path(preparation.__file__), ROOT / 'runtime/rimsky/word_verification.py',
        ROOT / 'scripts/witness_public_demo_turbo.py', ROOT / 'scripts/recover_public_audiobook_demo.py',
        ROOT / 'scripts/public_demo_english_agreement.py',
        ROOT / 'scripts/context_public_audiobook_recovery.py',
        ROOT / 'runtime/rimsky/name_tolerance.py', ROOT / 'runtime/rimsky/spoken_homophones.py',
        ROOT / 'runtime/rimsky/speech_quality.py']}


def validate_conversion(unit, language):
    conversion = unit['spoken_conversion']
    if conversion['source'] != unit['text']:
        raise ValueError('Spoken conversion no longer binds to source')
    if language == 'English':
        expanded, changes = preparation.english_numbers(unit['text'])
        if changes != conversion['changes']:
            raise ValueError('Recorded English conversion differs from deterministic source rule')
    else:
        from rimsky.spoken_numbers import expand, POLICY_V3
        expanded = expand(unit['text'], policy=POLICY_V3)
    if conversion['expanded'] != expanded or re.sub(r'\s+', ' ', expanded).strip() != unit['spoken']:
        raise ValueError('Spoken target differs from recorded source conversion')
    preparation.validate_pronunciation(unit['spoken'], unit.get('synthesis_text', unit['spoken']))


def conversion_slots(unit):
    """Exact positions of source-recorded expansions in the spoken word list."""
    conversion = unit['spoken_conversion']
    preparation.validate_conversion(unit['text'], conversion['expanded'], conversion['changes'])
    slots, prefix, cursor = [], '', 0
    for change in conversion['changes']:
        prefix += unit['text'][cursor:change['start']]
        start = len(word_tokens(prefix))
        spoken = word_tokens(change['spoken'])
        slots.append({'start': start, 'end': start + len(spoken), 'expected': spoken,
                      'heard': word_tokens(change['source']), 'source_surface': change['source'],
                      'source_start': change['start'], 'source_end': change['end'],
                      'rule': TITLE_RULE if change['kind'] == 'abbreviation' else NUMBER_RULE,
                      'conversion_rule': change['rule']})
        prefix += change['spoken']
        cursor = change['end']
    return slots


def english_equivalence(unit, heard, policy=None):
    """Only recorded source surfaces at their exact expanded word positions.

    A whole-sentence DP preserves all other words. It cannot rename characters,
    normalize arbitrary abbreviations, or repair insertions/omissions.
    """
    validate_conversion(unit, 'English')
    expected, actual = word_tokens(unit['spoken']), word_tokens(heard)
    slots = conversion_slots(unit)
    by_start = {slot['start']: slot for slot in slots}
    memo = {}

    def match(i, j):
        if (i, j) in memo:
            return memo[i, j]
        if i == len(expected) and j == len(actual):
            return []
        options = []
        if i < len(expected) and j < len(actual) and expected[i] == actual[j]:
            tail = match(i+1, j+1)
            if tail is not None:
                options.append(tail)
        slot = by_start.get(i)
        if slot and expected[i:slot['end']] == slot['expected'] and slot['heard']:
            end = j + len(slot['heard'])
            if actual[j:end] == slot['heard']:
                tail = match(slot['end'], end)
                if tail is not None:
                    options.append([{**slot, 'heard_start': j, 'heard_end': end}, *tail])
        from public_demo_english_agreement import options as english_options
        for source_end, heard_end, change in english_options(unit, expected, actual, i, j, policy):
            tail = match(source_end, heard_end)
            if tail is not None:
                options.append([change, *tail])
        memo[i, j] = min(options, key=len) if options else None
        return memo[i, j]

    changes = match(0, 0)
    return {'passed': changes is not None, 'canonical_words': expected if changes is not None else actual,
            'equivalences': [c for c in changes or [] if c['rule'] != 'proper-noun-slot-v1'],
            'tolerated_deviations': [c for c in changes or [] if c['rule'] == 'proper-noun-slot-v1']}


def russian_equivalence(unit, heard, source_paragraph, audio_sha256, verifier_sha256):
    from rimsky.word_verification import reconcile
    from rimsky import name_tolerance as tolerance
    from rimsky.spoken_homophones import homophone_equivalence, synthesis_stress, dependencies as homophone_dependencies
    raw = transcript_check(unit['spoken'], heard, audio_sha256, verifier_sha256)
    reconciled = reconcile(unit['spoken'], raw, 'Russian')
    equivalents = []
    canonical = heard
    if reconciled and reconciled != raw:
        canonical = reconciled['heard']
        equivalents = [*reconciled.get('phonological_equivalences', []),
                       *reconciled.get('proper_name_boundaries', [])]
    expected, actual = word_tokens(unit['spoken']), word_tokens(canonical)
    stress = synthesis_stress(unit.get('synthesis_text',unit['spoken']))
    actual, homophones = homophone_equivalence(expected,actual,stress=stress)
    if homophones:
        proof = {'module_sha256':file_digest(ROOT/'runtime/rimsky/spoken_homophones.py'),
                 'dependencies':homophone_dependencies(),'source_synthesis_stress':stress}
        equivalents.extend({**change,'phonological_provenance':proof} for change in homophones)
    # The shared helper keeps a process-global invented-word set. Isolate it to
    # this complete paragraph so unrelated units cannot enlarge a slot's scope.
    previous = set(tolerance._INVENTED)
    try:
        tolerance._INVENTED.clear()
        corrected, deviations = tolerance.tolerance(expected, actual, source_paragraph)
    finally:
        tolerance._INVENTED.clear()
        tolerance._INVENTED.update(previous)
    # Growls with no lexical vowel are not proper nouns. The shared explicit
    # same-kind interjection rule may resolve supported sounds, but an unknown
    # growl must not gain acceptance by being classified as an invented name.
    if any(d['rule'] == tolerance.RULE and any(not any(c in tolerance.VOWELS for c in w)
                                             for w in d['source_tokens']) for d in deviations):
        return {'passed': False, 'canonical_words': actual, 'equivalences': equivalents,
                'tolerated_deviations': [], 'hold_reason': 'nonlexical_sound_has_no_authorized_name_slot'}
    return {'passed': corrected == expected, 'canonical_words': corrected,
            'equivalences': equivalents, 'phonological_equivalences':equivalents,
            'tolerated_deviations': deviations}


def resolve_transcripts(unit, language, paragraph, evidence, english_policy=None):
    """Pure policy decision over already validated, independent raw evidence."""
    outcomes = []
    for item in evidence:
        raw = item['raw']
        if raw['status'] == 'asr_exact_match':
            result = dict(passed=True, canonical_words=raw['expected_words'], equivalences=[], tolerated_deviations=[])
        elif language == 'English':
            result = english_equivalence(unit, raw['heard'], english_policy)
        else:
            result = russian_equivalence(unit, raw['heard'], paragraph, raw['audio_sha256'], raw['verifier_sha256'])
        outcomes.append({**item, 'comparison': result})
    exact = next((e for e in outcomes if e['raw']['status'] == 'asr_exact_match'), None)
    if exact:
        return {'passed': True, 'status': 'independently_exact' if exact['role'] == 'witness' else 'primary_exact',
                'resolution': 'independent_witness_exact' if exact['role'] == 'witness' else 'primary_exact',
                'selected_evidence': exact['path'], 'evidence': outcomes,
                'tolerated_deviations': [], 'equivalences': []}
    primary = next((e for e in outcomes if e['role'] == 'primary' and e['family'] == 'qwen'), None)
    witness = next((e for e in outcomes if e['role'] == 'witness' and e['family'] == 'whisper' and e['comparison']['passed']), None)
    if not primary or not primary['comparison']['passed'] or not witness:
        return {'passed': False, 'status': 'unresolved', 'resolution': 'held', 'evidence': outcomes,
                'tolerated_deviations': [], 'equivalences': []}
    deviations = [dict(d, evidence=e['path']) for e in [primary, witness] for d in e['comparison']['tolerated_deviations']]
    equivalents = [dict(d, evidence=e['path']) for e in [primary, witness] for d in e['comparison']['equivalences']]
    return {'passed': True, 'status': 'permitted_tolerance' if deviations else 'source_bound_spoken_equivalence',
            'resolution': 'disclosed_permitted_tolerance' if deviations else 'independent_source_bound_equivalence',
            'selected_evidence': witness['path'], 'evidence': outcomes,
            'tolerated_deviations': deviations, 'equivalences': equivalents}


def checked_relative(folder, relative, checksum):
    path = (folder / relative).resolve()
    path.relative_to(folder)
    if not path.is_file() or file_digest(path) != checksum:
        raise ValueError('Missing or changed evidence: ' + str(path))
    return path


def verifier_family(config, frozen_implementation, role):
    model = config.get('model')
    if model == 'openai/whisper-large-v3-turbo':
        from witness_public_demo_turbo import validate_verifier_config
        validate_verifier_config(config)
        if role != 'witness':
            raise ValueError('Turbo is an independent witness, not the primary verifier')
        return 'whisper'
    if config.get('implementation_sha256') != frozen_implementation:
        raise ValueError('Recognizer implementation differs from frozen worker')
    family = 'qwen' if model == 'Qwen3-ASR-1.7B' else 'whisper' if model == 'openai/whisper-small' else None
    if family != ('qwen' if role == 'primary' else 'whisper'):
        raise ValueError('Evidence does not supply the required independent model family')
    return family


def validated_context(context, unit, receipt, receipt_sha256=None):
    """Strict adapter for the standalone full-context/crop proof namespace."""
    import context_public_audiobook_recovery as module
    spec = receipt['spec']
    marker = spec.get('context_recovery')
    if (not isinstance(marker, dict) or spec.get('recovery') or marker.get('policy') != module.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take') != marker['attempt']+5 or receipt.get('unit') != unit['id']
            or sha(spec) != receipt.get('spec_sha256') or spec.get('source') != unit
            or spec.get('generation') != context.generation):
        raise ValueError('Context take does not bind to frozen source, generation or bounded attempt')
    proof = module.validate_context_receipt(context.folder, unit, receipt)
    if (proof.get('policy') != module.POLICY or proof.get('crop_audio_sha256') != receipt['audio_sha256']
            or proof.get('crop') != marker.get('crop')
            or receipt_sha256 is not None and proof.get('receipt_sha256') != receipt_sha256):
        raise ValueError('Validated context crop/hash/receipt differs from canonical mirror')
    full, target = proof['full_context_decision'], proof['target_decision']
    if full.get('passed') is not True or target.get('passed') is not True:
        raise ValueError('Full context or target crop remains held')
    for decision, expected, audio_hash in [(full,marker['context']['spoken'],proof['native_audio_sha256']),
                                            (target,unit['spoken'],receipt['audio_sha256'])]:
        if (len(decision['evidence']) != 2 or
                {(e['role'],e['family']) for e in decision['evidence']} != {('primary','qwen'),('witness','whisper')}):
            raise ValueError('Context proof lacks independent source-blind primary/witness evidence')
        for evidence in decision['evidence']:
            path = checked_relative(context.folder,evidence['path'],evidence['sha256'])
            candidate = read(path)
            raw = transcript_check(expected,candidate['heard'],audio_hash,sha(evidence['verifier']))
            if (candidate.get('verifier') != evidence['verifier'] or evidence['raw'] != raw
                    or any(candidate.get(k) != value for k,value in raw.items())):
                raise ValueError('Context/crop raw transcript evidence or audio hash changed')
    spans = [{key:row[key] for key in ('id','source_id','start','end','text','spoken','synthesis_text') if key in row}
             for row in proof['source_spans']]
    # No original specs, palette references, protected training paths, or raw
    # model inputs enter this public-safe disclosure projection.
    sanitized = {key:proof[key] for key in ('policy','receipt_sha256','native_audio_sha256','crop_audio_sha256','crop')}
    sanitized.update(source_spans=spans, full_context_decision=full,
                     context_module_sha256=file_digest(Path(module.__file__)),
                     alignment_is_approval=False, human_approval=False)
    return target, sanitized


def register_context(context, receipt_path):
    """CPU-only append of a strictly verified crop; never rewrite any take."""
    import context_public_audiobook_recovery as module
    context.recheck()
    path = Path(receipt_path)
    path = path.resolve() if path.is_absolute() else (context.folder/path).resolve()
    path.relative_to(context.folder)
    original = path.read_bytes()
    receipt = read(path)
    unit = next((u for u in context.assignment['units'] if u['id'] == receipt.get('unit')),None)
    if unit is None or not re.fullmatch('[A-Za-z0-9_-]+',unit['id']):
        raise ValueError('Context receipt has no matching frozen source unit')
    marker = receipt.get('spec',{}).get('context_recovery',{})
    if type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3):
        raise ValueError('Context registration is bounded to attempts one through three')
    mirror = module.attempt_home(context.folder,unit,marker['attempt'])/'crop-receipt.json'
    if path != mirror.resolve():
        raise ValueError('Only the original explicit context crop receipt may be registered')
    _, provenance = validated_context(context,unit,receipt,file_digest(path))
    context.recheck()
    if path.read_bytes() != original:
        raise ValueError('Context receipt changed during strict validation')
    canonical = context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
    canonical.parent.mkdir(parents=True,exist_ok=True)
    if canonical.exists():
        if canonical.read_bytes() != original:
            raise ValueError('Existing canonical take differs; context registration cannot overwrite it')
    else:
        with canonical.open('xb') as handle:
            handle.write(original)
    registration = {'schema':1,'policy':module.POLICY,'unit':unit['id'],'take':receipt['take'],
                    'canonical':canonical.relative_to(context.folder).as_posix(),
                    'canonical_receipt_sha256':file_digest(canonical),'context_provenance':provenance,
                    'registration_module_sha256':file_digest(Path(__file__)), 'human_approval':False}
    immutable(mirror.parent/'canonical-registration.json',registration)
    print('REGISTERED CONTEXT CROP',unit['id'],receipt['take'],flush=True)
    return registration


class Context:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.assignment = read(self.folder / 'assignment.json')
        self.manifest = read(self.folder / 'manifest.json')
        self.frozen = read(self.folder / 'frozen.json')
        if self.frozen['assignment'] != self.assignment or self.frozen['manifest'] != self.manifest:
            raise ValueError('Native frozen input changed')
        if self.frozen['implementation_sha256'] != file_digest(Path(renderer.__file__)):
            raise ValueError('Original renderer implementation changed')
        self.generation = sha(self.frozen)
        book = preparation.verify_assignment_source(self.folder, self.assignment)
        self.blocks = {b['id']: b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')}
        self.english_policy = None
        if self.assignment['language'] == 'English':
            from public_demo_english_agreement import build_policy
            self.english_policy = build_policy(book, self.assignment, file_digest(self.folder/'prepared/book.json'))
        self.dependencies = dependencies()
        self.bindings = {name: file_digest(self.folder / name) for name in [
            'assignment.json', 'manifest.json', 'frozen.json', 'prepared/book.json',
            'pronunciation/result.json', 'pronunciation/binding.json']}
        if self.manifest['assignment_sha256'] != self.bindings['assignment.json']:
            raise ValueError('Manifest assignment pin differs')
        result = read(self.folder / 'pronunciation/result.json')
        if result['assignment'] != self.assignment:
            raise ValueError('Pronunciation checkpoint differs')
        for relative, checksum in result['evidence_sha256'].items():
            checked_relative(self.folder, relative, checksum)
        self.bindings.update(result['evidence_sha256'])
        for unit in self.assignment['units']:
            validate_conversion(unit, self.assignment['language'])

    def recheck(self):
        for relative, checksum in self.bindings.items():
            checked_relative(self.folder, relative, checksum)
        if dependencies() != self.dependencies:
            raise ValueError('Resolution implementation dependencies changed')
        if self.english_policy:
            from public_demo_english_agreement import engine_provenance
            if engine_provenance() != self.english_policy['phonemizer']:
                raise ValueError('English phonemizer package/data/DLL provenance changed')

    def evidence(self, unit, receipt):
        audio = receipt['audio_sha256']
        primary = self.folder / 'checks' / (audio + '.json')
        paths = ([primary] if primary.exists() else []) + sorted((self.folder / 'witnesses').glob(audio + '-*.json'))
        found = []
        for path in paths:
            candidate = read(path)
            config = candidate['verifier']
            signature = sha(config)
            raw = transcript_check(unit['spoken'], candidate['heard'], audio, signature)
            if any(candidate.get(k) != value for k, value in raw.items()):
                raise ValueError('Raw transcript receipt cannot be reproduced: ' + str(path))
            if config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True:
                raise ValueError('Recognizer was not source-blind and local')
            if not config.get('files') or any(not re.fullmatch('[a-f0-9]{64}', value) for value in config['files'].values()):
                raise ValueError('Recognizer model inventory missing')
            verifier_path = self.folder / 'verifiers' / (signature + '.json')
            if read(verifier_path) != config:
                raise ValueError('Verifier registry differs from raw evidence')
            role = 'primary' if path == primary else 'witness'
            family = verifier_family(config, self.frozen['implementation_sha256'], role)
            found.append({'path': path.relative_to(self.folder).as_posix(), 'sha256': file_digest(path),
                          'raw': raw, 'family': family, 'role': role, 'verifier': config})
        return found

    def assess(self, unit, receipt):
        self.recheck()
        if receipt.get('quality_passed') is not True:
            return None
        checked_relative(self.folder, receipt['audio'], receipt['audio_sha256'])
        receipt_path = self.folder / 'takes' / unit['id'] / ('take-%02d.json' % receipt['take'])
        if read(receipt_path) != receipt:
            raise ValueError('Take receipt differs from immutable take history')
        spec = receipt['spec']
        recovery = None
        context_provenance = None
        if sha(spec) != receipt['spec_sha256'] or spec['source'] != unit or spec['generation'] != self.generation:
            raise ValueError('Take is not bound to exact frozen source/synthesis request')
        if spec.get('context_recovery'):
            decision, context_provenance = validated_context(self,unit,receipt,file_digest(receipt_path))
        else:
            if spec['request']['text'] != unit.get('synthesis_text', unit['spoken']):
                raise ValueError('Take is not bound to exact frozen source/synthesis request')
            evidence = self.evidence(unit, receipt)
            if not evidence:
                return None
            decision = resolve_transcripts(unit,self.assignment['language'],self.blocks[unit['source_id']],evidence,
                                           self.english_policy)
        if spec.get('recovery'):
            from recover_public_audiobook_demo import validate_recovery_receipt
            recovery = validate_recovery_receipt(self.folder, unit, receipt)
        return {'schema': 1, 'rule': RULE, 'module_sha256': file_digest(Path(__file__)),
                'dependencies': self.dependencies, 'bindings': self.bindings,
                'unit': unit['id'], 'source_id': unit['source_id'], 'source_start': unit['start'], 'source_end': unit['end'],
                'source_text': unit['text'], 'expected_spoken': unit['spoken'],
                'source_paragraph_sha256': sha(self.blocks[unit['source_id']]),
                'audio_sha256': receipt['audio_sha256'], 'take_spec_sha256': receipt['spec_sha256'],
                'take_receipt_sha256': file_digest(receipt_path),
                'recovery_provenance': recovery,
                'context_provenance': context_provenance,
                'english_source_policy': self.english_policy,
                'decision': decision, 'human_approval': False,
                'scope': 'Recomputed source-bound machine evidence; raw mismatches remain recorded'}

    def proposal(self, unit, receipt):
        value = self.assess(unit, receipt)
        if value is None:
            return None
        path = self.folder / 'resolution-proposals' / (unit['id'] + '-' + receipt['audio_sha256'] + '-' + sha(value)[:20] + '.json')
        immutable(path, value)
        return path, value

    def audit(self, folder, unit, receipt):
        value = self.assess(unit, receipt)
        if value is None:
            return None
        path = self.folder / 'resolution-proposals' / (unit['id'] + '-' + receipt['audio_sha256'] + '-' + sha(value)[:20] + '.json')
        if not path.exists() or read(path) != value:
            return {'passed': False, 'resolution': 'unreviewed_evidence_requires_new_proposal'}
        decision = value['decision']
        return {**decision, 'resolution_sidecar_sha256': file_digest(path),
                'resolution_sidecar': path.relative_to(self.folder).as_posix()}


def report(context, only=()):
    rows, all_raw_flags, witness_raw_flags, context_raw_flags = [], 0, 0, 0
    for unit in context.assignment['units']:
        if only and unit['id'] not in only:
            continue
        decisions, chosen = [], None
        for receipt in renderer.takes(context.folder, unit):
            item = context.proposal(unit, receipt)
            if item is None:
                continue
            path, value = item
            decision = value['decision']
            primary = next((e for e in decision['evidence'] if e['role'] == 'primary'), None)
            flagged = bool(primary and primary['raw']['status'] != 'asr_exact_match')
            all_raw_flags += flagged
            witness_flags = sum(e['raw']['status'] != 'asr_exact_match' for e in decision['evidence'] if e['role'] == 'witness')
            witness_raw_flags += witness_flags
            context_provenance = value.get('context_provenance')
            full_flags = sum(e['raw']['status'] != 'asr_exact_match' for e in
                             (context_provenance or {}).get('full_context_decision',{}).get('evidence',[]))
            context_raw_flags += full_flags
            summary = {'take': receipt['take'], 'audio_sha256': receipt['audio_sha256'],
                       'raw_primary_flag': flagged, 'raw_witness_mismatch_count': witness_flags,
                       'passed': decision['passed'], 'status': decision['status'],
                       'resolution': decision['resolution'], 'sidecar_sha256': file_digest(path),
                       'context_provenance':context_provenance,'raw_full_context_mismatch_count':full_flags,
                       'source_text': unit['text'], 'expected_spoken': unit['spoken'],
                       'raw_transcripts': [{'role': e['role'], 'model': e['verifier']['model'],
                                            'heard': e['raw']['heard'], 'status': e['raw']['status'],
                                            'evidence_sha256': e['sha256']} for e in decision['evidence']],
                       'equivalences': decision['equivalences'], 'tolerated_deviations': decision['tolerated_deviations']}
            decisions.append(summary)
            if decision['passed']:
                chosen = summary
        rows.append({'id': unit['id'], 'selected': chosen, 'takes': decisions,
                     'status': 'verified' if chosen else 'held' if decisions else 'pending'})
    rules = {}
    for row in rows:
        for change in (row['selected'] or {}).get('equivalences', []) + (row['selected'] or {}).get('tolerated_deviations', []):
            rules[change['rule']] = rules.get(change['rule'], 0) + 1
    return {'schema': 1, 'rule': RULE, 'frozen_generation': context.generation,
            'resolution_module_sha256': file_digest(Path(__file__)),
            'english_verification_policy': context.english_policy,
            'language': context.assignment['language'], 'title': context.assignment['title'],
            'raw_primary_mismatch_take_count': all_raw_flags,
            'raw_witness_mismatch_check_count': witness_raw_flags,
            'raw_full_context_mismatch_check_count':context_raw_flags,
            'raw_mismatch_check_count': all_raw_flags + witness_raw_flags + context_raw_flags,
            'raw_primary_mismatch_unit_count': sum(any(t['raw_primary_flag'] for t in row['takes']) for row in rows),
            'resolved_with_disclosure_unit_count': sum(bool(row['selected'] and row['selected']['status'] in
                                                           ('permitted_tolerance', 'source_bound_spoken_equivalence')) for row in rows),
            'rules': rules, 'verified_units': sum(row['status'] == 'verified' for row in rows),
            'open_recognition_flags': sum(row['status'] == 'held' for row in rows),
            'pending_units': sum(row['status'] == 'pending' for row in rows), 'units': rows,
            'human_approval': False, 'disclosure': 'Raw recognizer mismatches are retained. Equivalence and permitted tolerance are disclosed separately from exact recognition; none certifies acoustic stress.'}


def stable_playback_config(markup, generation):
    """Keep the frozen book bookmark key across immutable mix/report versions."""
    pattern = re.compile(r'(<script\b[^>]*\bid="playback-config"[^>]*>)(.*?)(</script>)', re.S)
    def replace(match):
        config = json.loads(match[2])
        config['generation'] = generation
        return match[1] + json.dumps(config, ensure_ascii=False).replace('<', '\\u003c') + match[3]
    updated, count = pattern.subn(replace, markup)
    if count != 1:
        raise ValueError('Published player requires exactly one playback configuration')
    return updated


def publish(context, report_value, max_takes):
    context.recheck()
    report_hash = sha(report_value)
    publication = sha({'frozen_generation': context.generation, 'recognition_report_sha256': report_hash})
    original_audit, original_write = renderer.audit, renderer.write

    def write_with_disclosure(path, value, immutable=False):
        if isinstance(value, dict) and 'verified_units' in value and 'captions' in value:
            value = {**value, 'frozen_generation': context.generation,
                     'recognition_report': 'recognition-' + report_hash + '.json',
                     'recognition_report_sha256': report_hash,
                     'raw_primary_mismatch_take_count': report_value['raw_primary_mismatch_take_count'],
                     'raw_primary_mismatch_unit_count': report_value['raw_primary_mismatch_unit_count'],
                     'raw_witness_mismatch_check_count': report_value['raw_witness_mismatch_check_count'],
                     'raw_mismatch_check_count': report_value['raw_mismatch_check_count'],
                     'raw_full_context_mismatch_check_count':report_value['raw_full_context_mismatch_check_count'],
                     'resolved_with_disclosure_unit_count': report_value['resolved_with_disclosure_unit_count'],
                     'recognition_resolution_rules': report_value['rules'],
                     'open_recognition_flags': report_value['open_recognition_flags'],
                     'pending_recognition_units': report_value['pending_units']}
        original_write(path, value, immutable=immutable)

    renderer.audit, renderer.write = context.audit, write_with_disclosure
    try:
        complete = renderer.publish(context.folder, context.assignment, context.manifest, publication,
                                    SimpleNamespace(max_takes=max_takes))
    finally:
        renderer.audit, renderer.write = original_audit, original_write
    public = context.folder / 'public'
    if (public / 'latest.json').exists() and read(public / 'latest.json').get('generation') == publication:
        immutable(public / ('recognition-' + report_hash + '.json'), report_value)
        page = public / 'index.html'
        disclosure = ('<aside id="recognition-disclosure"><p>Raw recognition flags: '
                      + str(report_value['raw_mismatch_check_count']) + ' checks ('
                      + str(report_value['raw_primary_mismatch_take_count']) + ' primary and '
                      + str(report_value['raw_witness_mismatch_check_count']) + ' independent witness and '
                      + str(report_value['raw_full_context_mismatch_check_count'])+' full-context checks). Resolved with disclosed equivalence or tolerance: '
                      + str(report_value['resolved_with_disclosure_unit_count']) + ' units. Open: '
                      + str(report_value['open_recognition_flags']) + '.</p><p>'
                      + html.escape(report_value['disclosure']) + '</p><a href="recognition-' + report_hash
                      + '.json">Raw transcripts, exact rules, and resolution evidence</a></aside>')
        markup = page.read_text(encoding='utf-8')
        markup = stable_playback_config(markup, context.generation)
        markup = re.sub(r'<aside id="recognition-disclosure">.*?</aside>', '', markup, flags=re.S)
        page.write_text(markup + disclosure, encoding='utf-8')
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--publish', action='store_true')
    modes.add_argument('--register-context',type=Path,help='Strictly validate and append an explicit crop receipt; path relative to --folder or absolute')
    parser.add_argument('--unit', action='append', default=[])
    parser.add_argument('--max-takes', type=int, default=3)
    args = parser.parse_args()
    if (args.publish or args.register_context) and args.unit:
        parser.error('--unit is limited to private dry-run proposals')
    if not 1 <= args.max_takes <= 3:
        parser.error('--max-takes must be 1..3')
    context = Context(args.folder)
    if args.register_context:
        register_context(context,args.register_context)
        return
    value = report(context, args.unit)
    destination = context.folder / 'resolution-proposals' / ('report-' + sha(value) + '.json')
    immutable(destination, value)
    print(json.dumps({key: value[key] for key in ['raw_primary_mismatch_take_count', 'verified_units',
                                                'open_recognition_flags', 'pending_units', 'rules']}, ensure_ascii=True))
    print('PRIVATE REPORT', destination, flush=True)
    if args.publish:
        publish(context, value, args.max_takes)


if __name__ == '__main__':
    main()
