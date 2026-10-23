"""Build a development-only, character/voice-specific performance rulebook.

Read-only on Claude's benchmark and human labels. Outputs an independent,
versioned JSON package and a readable HTML document, not another labeling UI.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import unicodedata
import copy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from production_rulebook_rules import (MOOD_RULES, DIRECTOR_PROTOCOL,
    CHARACTER_DIRECTIVES, GENERIC_CHARACTER_DIRECTIVES)
from rimsky.delivery_rulebook import delivery_context, validate_rulebook, example_citation_key
from merge_production_instructions import extend_aliases, merge_instructions


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def normalize(name):
    return ' '.join(unicodedata.normalize('NFKC', str(name)).casefold().replace('ё', 'е').split())


def cast_index(cast):
    candidates = defaultdict(set)
    records = {}
    for item in cast['characters']:
        records[item['id']] = item
        for name in (item['id'], item['name'], *item.get('aliases', [])):
            candidates[normalize(name)].add(item['id'])
    # One documented orthographic source variant, not a general digit rewrite.
    if 'rb1_minno' in records:
        candidates[normalize('Минн-0 Ла-Фин')].add('rb1_minno')
    return candidates, records


def identity(row, aliases, cast):
    if row.get('identity') == 'unresolved_part':
        return None
    voice = row.get('voice') or ''
    if voice.startswith('narrator_'):
        return 'narrator', 'Рассказчик', 'reviewed_voice_route_requires_structure_confirmation'
    if voice == 'mechanical_stats':
        return 'game_system', 'Игровая система', 'reviewed_voice_route_requires_system_channel_confirmation'
    name = row.get('identity') or row.get('speaker') or row.get('performer')
    if not name or normalize(name) in ('unknown', 'narrator', 'narration'):
        return None
    found = aliases.get(normalize(name), set())
    if len(found) > 1:
        return None
    if len(found) == 1:
        ident = next(iter(found))
        return ident, cast[ident]['name'], 'reviewed_cast_alias; occurrence_identity_may_be_proposed'
    # Book 2 names stay distinct unless the cast actually establishes an alias.
    return 'book2:' + name, name, row.get('speaker_basis') or 'proposal'


def presentation(voice):
    kinds = {'narrator_baseline': 'first_person_storytelling',
             'narrator_opening': 'opening_section',
             'narrator_historical_prose': 'historical_prose',
             'mechanical_stats': 'game_system_readout',
             'mechanical_envoy': 'recorded_envoy_broadcast'}
    return kinds.get(voice, 'observed_mechanical_variant' if 'mechanical' in voice else 'direct_character_voice')


def route_instructions(voice):
    rules = {
        'narrator_baseline': ['Keep the first-person storytelling route distinct from the protagonist\'s direct speech; preserve narrator emotion only where his present stance supports it.'],
        'narrator_opening': ['Opening is a structural full-section register, including dialogue, under the project voice policy. Keep speaker/intent analysis inside the section even when its presentation voice stays Opening.',
            'Do not impose the main first-person narrator\'s baseline on an enemy-viewpoint opening. Preserve reviewed per-part exceptions and do not assume that all future opening moods are Normal.'],
        'narrator_historical_prose': ['Use this separately evidenced historical/expository register only where section and pack routing establish it. Do not infer it from past tense or soft mood.'],
        'mechanical_stats': ['Confirm explicit game/interface readout structure. A skill increase ending in ! may remain Normal; narration about the readout stays outside this route.'],
        'mechanical_envoy': ['Keep the recorded envoy/broadcast presentation distinct from the narrator and ordinary direct dialogue; orders and formal information still need their own intent analysis.'],
    }
    if voice in rules:
        return rules[voice]
    if 'mechanical' in voice:
        return ['This is an observed mechanical presentation variant, not proof of a game-system speaker. Establish written, telepathic, broadcast or other channel context before routing it.',
                'Keep the originating character\'s intention. A mechanical presentation does not imply emotionless Normal delivery.']
    return ['Use the fixed character voice in this route. Delivery changes do not select a new identity or voice family.']


def choose_examples(rows, character_id, route_id, limit=3):
    chosen, sources, chapters = [], set(), set()
    ordered = sorted(rows, key=lambda r: digest((r['book'] + ':' + r['id']).encode()))
    for diverse in (True, False):
        for row in ordered:
            key = row['book'], row['id']
            chapter = row['book'], row['chapter_id']
            if key in sources or len(chosen) >= limit or (diverse and chapter in chapters):
                continue
            chosen.append({k: row.get(k) for k in ('id', 'book', 'chapter_id', 'source_id', 'parent_id',
                'unit', 'text', 'paragraph', 'before', 'after', 'mood', 'speaker', 'speaker_basis', 'voice', 'voice_basis',
                'boundary_held', 'audio_sha256', 'origin')} | {'policy_character_id': character_id, 'policy_route_id': route_id})
            sources.add(key)
            chapters.add(chapter)
    return chosen


def build(rows, sealed, cast, names, input_hashes, policies=None):
    development, excluded = [], Counter()
    calibration_build = any(item.get('calibration_chapters') for item in sealed['books'].values())
    for item in sealed['books'].values():
        training, test, calibration = (set(item.get(key, [])) for key in
                                      ('development_chapters', 'test_chapters', 'calibration_chapters'))
        if training & test or training & calibration or test & calibration:
            raise ValueError('Development, calibration and test chapters must be disjoint')
    for row in rows:
        book = row.get('book')
        if book not in sealed['books']:
            excluded['outside_frozen_books'] += 1
            continue
        chapter = row['chapter_id']
        if chapter in sealed['books'][book]['test_chapters']:
            excluded['sealed_test'] += 1
            continue
        if chapter in sealed['books'][book].get('calibration_chapters', []):
            excluded['calibration'] += 1
            continue
        if chapter not in sealed['books'][book]['development_chapters']:
            raise ValueError('Chapter is in neither side of the sealed split')
        if row.get('mood_basis') != 'human' or row.get('mood') not in MOOD_RULES:
            excluded['not_explicit_mood_evidence'] += 1
            continue
        development.append(row)
    # Neighbour strings carry no source/chapter IDs. Keep only neighbours
    # independently present in this chapter's development evidence, so a
    # chapter-boundary neighbour cannot expose sealed text in a prompt.
    paragraphs = {(r['book'], r['chapter_id'], (r.get('paragraph') or '').strip()) for r in development}
    development = [dict(r, **{key: r.get(key) if
        (r['book'], r['chapter_id'], (r.get(key) or '').strip()) in paragraphs else None
        for key in ('before', 'after')}) for r in development]
    aliases, cast_records = cast_index(copy.deepcopy(cast))
    resolved_aliases = extend_aliases(aliases, cast_records, policies) if policies else []
    characters = {}
    grouped = defaultdict(list)
    unresolved = []
    for row in development:
        ident = identity(row, aliases, cast_records)
        voice = row.get('voice')
        if row.get('voice_basis') != 'human' or not voice or voice == 'voice_pending' or ident is None:
            unresolved.append({'book': row['book'], 'id': row['id'], 'chapter_id': row['chapter_id'],
                               'reason': 'reviewed voice route or occurrence identity unresolved'})
            continue
        cid, name, basis = ident
        if cid not in characters:
            record = cast_records.get(cid, {})
            characters[cid] = {'id': cid, 'name': name, 'aliases': set(record.get('aliases', [])),
                'identity_basis': basis, 'routes': [],
                'director_instructions': (GENERIC_CHARACTER_DIRECTIVES if calibration_build else
                                          CHARACTER_DIRECTIVES.get(cid, GENERIC_CHARACTER_DIRECTIVES))}
        observed = row.get('speaker') or row.get('performer')
        if observed and cid not in ('narrator', 'game_system'):
            characters[cid]['aliases'].add(observed)
        grouped[cid, voice].append(row)
    for (cid, voice), evidence in sorted(grouped.items()):
        counts = Counter(r['mood'] for r in evidence)
        baseline_count = counts['calm'] + counts['neutral']
        baseline = max(('calm', 'neutral'), key=lambda m: (counts[m], m == 'calm')) if baseline_count else None
        chapters = sorted({(r['book'], r['chapter_id']) for r in evidence})
        route_id = cid + '/' + voice
        rules = []
        for mood, count in counts.most_common():
            choose, avoid, rival = MOOD_RULES[mood]
            positives = [r for r in evidence if r['mood'] == mood]
            negatives = [r for r in evidence if r['mood'] == rival]
            if not negatives:
                negatives = [r for r in evidence if r['mood'] in ('calm', 'neutral') and r['mood'] != mood]
            rules.append({'id': route_id + '/mood/' + mood, 'mood': mood,
                'choose_when': choose, 'do_not_choose_when': avoid, 'nearest_competing_mood': rival,
                'character_application': characters[cid]['director_instructions'],
                'positive_examples': choose_examples(positives, cid, route_id),
                'contrast_examples': choose_examples(negatives, cid, route_id, 2),
                'contrast_note': 'Contrasts carry their own actual labels; they do not prove an acoustically separable boundary.',
                'development_support': count,
                'support_chapters': len({(r['book'], r['chapter_id']) for r in positives}),
                'evidence_status': 'development_supported_hypothesis' if count >= 3 else 'sparse_hypothesis',
                'acceptance_status': 'not_calibrated'})
        characters[cid]['routes'].append({'id': route_id, 'voice_family': voice,
            'presentation': presentation(voice),
            'route_director_instructions': route_instructions(voice),
            'selection_instruction': 'Use only after fixed structure and cast/channel evidence establish this route. Mood changes do not select a different voice.',
            'development_choices': len(evidence), 'support_chapters': chapters,
            'voice_status': 'candidate_requires_route_validation',
            'baseline': {'mood': baseline, 'basis': 'development Calm/Normal preference; not a separately labelled register',
                         'support': baseline_count, 'scene_instruction': 'Retain an established coherent register. Use this preference only for unmarked delivery; escalate unresolved strong expressive cues.',
                         'alternatives': {m: counts[m] for m in ('calm', 'neutral')}},
            'mood_counts': dict(counts), 'mood_rules': rules,
            'unobserved_moods': sorted(set(MOOD_RULES) - set(counts)),
            'evidence_limits': ['Human moods and voice families do not independently verify the proposed speaker identity.',
                'Same-character/same-voice comparisons only; rare and unseen moods require context and critic.',
                'Examples preserve their audio and boundary holds and are not training approvals.']})
    profiles = []
    for cid, profile in sorted(characters.items()):
        profile['aliases'] = sorted(profile['aliases'] - {profile['name']})
        if len(profile['routes']) > 1:
            profile['routing_warning'] = 'Multiple observed voice routes. Establish structural/channel distinctions or investigate attribution; do not choose the majority blindly.'
        profiles.append(profile)
    document = {'schema': 1, 'status': 'ready_for_assignment_benchmark; not_release_validated',
        'source': 'development chapters only; explicit mood and voice evidence',
        'input_sha256': input_hashes, 'director_protocol': DIRECTOR_PROTOCOL,
        'policy_kind': 'authored scene/intent hypotheses supported by development examples; frequencies are not assignment triggers',
        **({'calibration_policy_scope': {
            'character_directives': 'New reduced-development instruction packets only; legacy character directives are not reused.',
            'global_definitions': 'Frozen generic mood vocabulary and structure/scene protocol.',
            'excluded_chapters': {b: s.get('calibration_chapters', []) for b, s in sealed['books'].items()}}}
           if calibration_build else {}),
        'vocabulary': {m: {'name': names.get(m, m), 'choose_when': choose,
                         'do_not_choose_when': avoid, 'nearest_competing_mood': rival,
                         'kind': 'general authored definition; not a per-character measurement'}
                       for m, (choose, avoid, rival) in MOOD_RULES.items()},
        'characters': profiles,
        'resolved_aliases': resolved_aliases,
        'alias_candidates_not_merged': [
            *([] if policies else [{'names': ['Фокси', 'Морф (Фокси)'], 'action': 'Require cast/scene corroboration before identity merge; retain distinct evidence until then.'}]),
            {'names': ['Айни', 'Айни Ури-Миайуу'], 'action': 'Require unambiguous cast alias; voice-family agreement is insufficient.'},
            {'names': ['Тини', 'Тини Уи-Комар'], 'action': 'Require cast/scene corroboration; do not merge from voice similarity.'}],
        'decision_contract': {'required': ['source_id', 'start', 'end', 'speaker_id', 'route_id', 'mood',
                                          'rule_ids', 'source_evidence', 'nearest_alternative', 'reason', 'status'],
            'statuses': ['proposed', 'needs_adjudication'],
            'constraints': ['Retain fixed structural parts and source offsets.',
                'Cite source evidence separately from development examples.',
                'For an unseen mood cite the general mood rule and explain the strong source cue.',
                'No self-issued auto-acceptance or corpus/training approval.']},
        'background_repair': {'review': ['missed expression', 'unsupported expression', 'speaker/route disagreement',
                'emotion leaking into author clauses', 'unmotivated register changes', 'ASR/render failures'],
            'repair_record': ['before', 'after', 'source_evidence', 'rule_ids', 'reason', 'affected_units', 'policy_version'],
            'application': 'Validated patches affect only relevant plans and upcoming audio; currently playing units finish before replacement. Preserve all prior evidence.'},
        'coverage': {'development_rows': len(development), 'routed_rows': sum(len(x) for x in grouped.values()),
            'characters': len(profiles), 'routes': len(grouped), 'mood_rules': sum(len(r['mood_rules']) for p in profiles for r in p['routes']),
            'excluded': dict(excluded), 'unresolved_development': unresolved}}
    document['version'] = digest(json_bytes(document))[:16]
    if policies:
        merge_instructions(document, development, policies, lambda row: identity(row, aliases, cast_records))
    return document, development


def readable(book):
    escape = html.escape
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Production delivery rulebook</title><style>body{font:16px/1.6 system-ui;max-width:1000px;margin:auto;padding:24px;color:#21382c;overflow-wrap:anywhere}details{margin:16px 0;padding:12px;border:1px solid #cbd8cc;border-radius:8px}blockquote{border-left:3px solid #759881;padding-left:14px}small{color:#58695f}h3{margin-bottom:4px}li{margin:5px 0}a{color:#236147}</style>',
        '<h1>Production delivery rulebook</h1>',
        '<p>Instructions for the LLM performance director. Development evidence only. Ready for benchmarking; automatic assignment precision and Higgs controls are not yet validated.</p>',
        '<p>Version: '+escape(book['version'])+'. '+str(book['coverage']['characters'])+' identities, '+str(book['coverage']['routes'])+' distinct voice routes. No labeling or approval task is required here.</p>',
        '<h2>Director protocol</h2><ol>']
    parts.extend('<li>'+escape(x)+'</li>' for x in book['director_protocol'])
    parts.append('</ol><h2>Characters and voice routes</h2>')
    for profile in book['characters']:
        parts.append('<details><summary><strong>'+escape(profile['name'])+'</strong> · '+str(len(profile['routes']))+' voice route(s)</summary>')
        parts.append('<p>Aliases: '+escape(', '.join(profile['aliases']) or 'none')+'</p><ul>')
        parts.extend('<li>'+escape(x)+'</li>' for x in profile['director_instructions'])
        parts.append('</ul>')
        if profile.get('routing_warning'):
            parts.append('<p>'+escape(profile['routing_warning'])+'</p>')
        for route in profile['routes']:
            parts.append('<details><summary>'+escape(route['voice_family'])+' · '+escape(route['presentation'])+'</summary>')
            parts.append('<p>Baseline preference: '+escape(route['baseline']['mood'] or 'not established')+'. '+escape(route['baseline']['scene_instruction'])+'</p>')
            parts.append('<ul>' + ''.join('<li>'+escape(x)+'</li>' for x in route['route_director_instructions']) + '</ul>')
            if route.get('character_director_instructions'):
                parts.append('<h3>Character delivery guidance</h3><ul>' + ''.join(
                    '<li>'+escape(x)+'</li>' for x in route['character_director_instructions']) + '</ul>')
            for rule in route['mood_rules']:
                parts.append('<details><summary>'+escape(book['vocabulary'][rule['mood']]['name'])+' · '+str(rule['development_support'])+' development choices</summary>')
                parts.append('<p><strong>Choose:</strong> '+escape(rule['choose_when'])+'</p><p><strong>Avoid:</strong> '+escape(rule['do_not_choose_when'])+'</p>')
                for specific in rule.get('character_specific_instructions', []):
                    for rival in specific['versus']:
                        parts.append('<p><strong>Versus '+escape(rival['mood'])+':</strong> '+escape(rival['how_to_tell'])+'</p>')
                    if specific.get('continuity'):
                        parts.append('<p><strong>Continuity:</strong> '+escape(specific['continuity'])+'</p>')
                    if specific.get('limits'):
                        parts.append('<p><strong>Limits:</strong> '+escape(specific['limits'])+'</p>')
                parts.append('<p>Nearest alternative: '+escape(rule['nearest_competing_mood'])+'. '+escape(rule['evidence_status'])+'.</p>')
                for kind in ('positive_examples', 'contrast_examples'):
                    parts.append('<h3>'+('Examples' if kind == 'positive_examples' else 'Same-character/voice contrasts')+'</h3>')
                    for example in rule[kind]:
                        parts.append('<blockquote>'+escape(example['text'])+'<br><small>'+escape(example['mood']+' · '+example['book']+' / '+example['chapter_id']+' · '+example['id'])+'</small></blockquote>')
                        if example.get('paragraph') and example['paragraph'].strip() != example['text'].strip():
                            parts.append('<details><summary>Paragraph context</summary><p>'+escape(example['paragraph'])+'</p></details>')
                    if not rule[kind]:
                        parts.append('<p>No development example in this same route.</p>')
                parts.append('</details>')
            parts.append('</details>')
        parts.append('</details>')
    parts.append('<h2>Background repair</h2><p>'+escape(book['background_repair']['application'])+'</p></html>')
    return '\n'.join(parts).encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, default=ROOT/'training-work/assignment-benchmark/m1-20261012')
    parser.add_argument('--output', type=Path, default=ROOT/'training-work/production-rulebook')
    args = parser.parse_args()
    paths = {'assignment': args.benchmark/'assignment-evidence.jsonl', 'split': args.benchmark/'split.json',
        'delivery_policies': args.benchmark/'delivery-policies.json',
        'character_policies': args.benchmark/'character-policies.json',
        'cast': ROOT/'training-work/character-rb1/benchmark/reviewed_cast_v3.json',
        'vocabulary': ROOT/'scripts/book1_review_shared.js', 'authoring': Path(__file__).with_name('production_rulebook_rules.py'),
        'context_compiler': ROOT/'runtime/rimsky/delivery_rulebook.py',
        'builder': Path(__file__)}
    paths['merger'] = Path(__file__).with_name('merge_production_instructions.py')
    for key, name in [('mood_groups', 'mood-groups.json'), ('writer_brief', 'WRITER_BRIEF.md')]:
        if (args.benchmark/name).is_file():
            paths[key] = args.benchmark/name
    # Capture exact inputs once. Concurrent assignment work can continue freely.
    raw = {key: path.read_bytes() for key, path in paths.items()}
    hashes = {str(paths[key].resolve().relative_to(ROOT).as_posix()): digest(value) for key, value in raw.items()}
    rows = [json.loads(line) for line in raw['assignment'].decode('utf-8').splitlines() if line.strip()]
    sealed, cast = json.loads(raw['split']), json.loads(raw['cast'])
    policies = json.loads(raw['delivery_policies'])
    if policies['inputs']['character-policies.json'] != digest(raw['character_policies']):
        raise ValueError('Delivery policies are stale against the current character policies')
    for who in policies['performers'].values():
        for item in who.values():
            relative = item['instructions_file']
            path = (args.benchmark / relative).resolve()
            if not path.is_relative_to(args.benchmark.resolve()):
                raise ValueError('Instruction path escapes benchmark package')
            content = path.read_bytes()
            if digest(content) != item['instructions_sha256'] or json.loads(content) != item['instructions']:
                raise ValueError('Instruction file changed since delivery policies were assembled')
            key = 'instruction:' + relative
            paths[key], raw[key] = path, content
    hashes = {str(paths[key].resolve().relative_to(ROOT).as_posix()): digest(value) for key, value in raw.items()}
    block = raw['vocabulary'].decode('utf-8').split('const MOOD_LABELS = {')[1].split('};')[0]
    names = dict(re.findall(r"(\w+):'([^']+)'", block))
    if set(names) != set(MOOD_RULES):
        raise ValueError('Authored rules must cover exactly the supported board vocabulary')
    book, development = build(rows, sealed, cast, names, hashes, policies)
    validate_rulebook(book)
    for key in raw:
        if paths[key].read_bytes() != raw[key]:
            raise ValueError('Benchmark changed during capture; rerun to capture a consistent revision')
    folder = args.output / book['version']
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {'rulebook.json': json_bytes(book), 'index.html': readable(book),
        'citation-index.json': json_bytes({example_citation_key(r): {key: r.get(key) for key in
            ('book', 'chapter_id', 'source_id', 'id', 'audio_sha256')} for r in development}),
        'development-evidence.jsonl': ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in development).encode('utf-8'),
        'split.json': raw['split']}
    if 'mood_groups' in raw:
        outputs['mood-groups.json'] = raw['mood_groups']
    sample_profile = next((p for p in book['characters'] if p['id'] == 'rb1_ivan'), book['characters'][0])
    sample_route = max(sample_profile['routes'], key=lambda r: r['development_choices'])
    sample_moods = [m for m in ('calm', 'neutral', 'confident', 'explaining', 'solemn', 'energetic') if m in book['vocabulary']]
    outputs['sample-context.json'] = json_bytes(delivery_context(book, [sample_route['id']], sample_moods, max_characters=50000))
    outputs['integration.json'] = json_bytes({'version': book['version'], 'rulebook': 'rulebook.json',
        'reader': 'rimsky.delivery_rulebook.load_rulebook(path)',
        'context_compiler': 'rimsky.delivery_rulebook.delivery_context(book, route_ids, candidate_moods)',
        'sample': 'sample-context.json', 'runtime_changes_required': 'Caller selects resolved character/voice routes; include policy_version and rule_ids in proposals and repair records.',
        'example_provenance': 'Model payloads use opaque citation_key plus text only. Resolve those keys offline with citation-index.json; never send that index to the provider.',
        **({'calibration': {
            'training_scope': 'Reduced development only; calibration and sealed test chapters are excluded before all learned counts, baselines and examples.',
            'legacy_character_directives_reused': False,
            'fresh_baseline_guidance': 'character_routes[].character_director_instructions in character-specific context.',
            'secondary_score_groups': 'mood-groups.json' if 'mood_groups' in raw else None,
            'secondary_score_groups_sha256': digest(raw['mood_groups']) if 'mood_groups' in raw else None}}
           if book.get('calibration_policy_scope') else {}),
        'acceptance': 'Candidate instructions only. Sealed benchmark calibration and Higgs control validation remain separate gates.',
        'character_aliases': 'Reviewed-cast aliases include Radugin/lang Radugin. Documented M1 cast links merge Kirill/Komar, Tamara and Foxi/Morph; short Ayni remains ambiguous. Unknown is not a system-wide alias.',
        'benchmark_ablation': 'Freeze one merged version. Compare general-definitions and character-specific context on the same fixed structure/identities/routes. Do not tune on the trial test labels; reserve other chapters for final scoring.'})
    for name, content in outputs.items():
        (folder/name).write_bytes(content)
    manifest = {'schema': 1, 'version': book['version'], 'created_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': hashes, 'output_sha256': {name: digest((folder/name).read_bytes()) for name in outputs},
        'protected_labels_written': False, 'benchmark_written': False, 'online_provider_calls': 0}
    (folder/'manifest.json').write_bytes(json_bytes(manifest))
    (args.output/'latest.json').write_bytes(json_bytes({'version': book['version'], 'directory': book['version'],
        'rulebook_sha256': digest(outputs['rulebook.json']), 'manifest_sha256': digest((folder/'manifest.json').read_bytes())}))
    print(json.dumps({'directory': str(folder), 'version': book['version'], 'coverage': {k:v for k,v in book['coverage'].items() if k != 'unresolved_development'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
