"""Merge development-authored delivery instructions into exact voice routes.

Never copy another character's examples or turn a presentation-route majority
into permission to reuse its instructions across all observed voices.
"""
from __future__ import annotations

from collections import defaultdict
import re

WORKFLOW = re.compile(r'\bbook\s*[123]\b|\bchapter\b|\bc\d{4}\b|\bкниг[аиуе]\s*\d|\bглав[аеуы]\s*\d', re.I)


def safe_text(value, excluded, location):
    if not value:
        return value
    if WORKFLOW.search(value):
        excluded.append({'where': location, 'text': value})
        return None
    return value


def extend_aliases(aliases, records, policies):
    """Use documented identity links; ambiguous names never enter the index."""
    resolved = []
    ambiguous = {name.casefold() for name in policies.get('ambiguous_names', {})}
    from build_production_rulebook import normalize
    for name, link in policies.get('identities', {}).items():
        members = link['members']
        if not link.get('basis') or any(m.casefold() in ambiguous for m in members):
            raise ValueError('Alias link lacks evidence or includes an ambiguous name')
        found = set().union(*(aliases.get(normalize(m), set()) for m in members))
        if len(found) > 1:
            raise ValueError('Alias link contradicts reviewed cast identities')
        cid = next(iter(found)) if found else 'book2:' + name
        records.setdefault(cid, {'id': cid, 'name': name, 'aliases': []})
        for member in members:
            aliases[normalize(member)].add(cid)
        records[cid]['aliases'] = sorted(set(records[cid].get('aliases', [])) | set(members))
        resolved.append({'character_id': cid, **link})
    return resolved


def merge_instructions(book, development, policies, resolve):
    by_id = defaultdict(list)
    for row in development:
        by_id[row['id']].append(row)
    routes = {r['id']: (p, r) for p in book['characters'] for r in p['routes']}
    report = {'source_packets': 0, 'attached_packets': 0, 'specific_mood_rules': 0,
              'excluded_workflow_lines': [], 'unrouted_citations': [], 'unattached_packets': [],
              'baseline_resolutions': []}
    for who, presented in policies['performers'].items():
        for kind, item in presented.items():
            report['source_packets'] += 1
            written = item['instructions']
            if written['identity'] != who or written['route'] != kind:
                raise ValueError('Instruction identity/route mismatch')
            digest = item['instructions_sha256']
            # Baseline explanations carry important negative examples that are
            # not necessarily repeated under each individual mood entry.
            for citation in written.get('baseline', {}).get('evidence_ids', []):
                matches = by_id.get(citation, [])
                if (len(matches) != 1 or matches[0].get('identity') != who or
                        matches[0].get('route') != kind):
                    raise ValueError('Baseline citation is not this route\'s development evidence: ' + citation)
            scoped = defaultdict(lambda: defaultdict(list))
            for entry in written.get('moods', []):
                mood = entry['mood']
                if mood not in book['vocabulary']:
                    raise ValueError('Unknown instruction mood')
                for category, field in [('positive', 'examples'), ('contrast', 'counterexamples')]:
                    for citation in entry.get(field, []):
                        matches = by_id.get(citation['id'], [])
                        if len(matches) != 1:
                            raise ValueError('Instruction citation is not unique development evidence: ' + citation['id'])
                        row = matches[0]
                        if row.get('identity') == 'unresolved_part':
                            report['unrouted_citations'].append({'id': row['id'], 'packet': written['packet'],
                                                               'reason': 'current split identity unresolved'})
                            continue
                        if row.get('identity') != who or row.get('route') != kind:
                            raise ValueError(f'Instruction citation crosses character or presentation route: {citation["id"]} ({who}/{kind} vs {row.get("identity")}/{row.get("route")})')
                        if (category == 'positive') != (row['mood'] == mood):
                            raise ValueError('Instruction citation has the wrong mood')
                        if category == 'contrast' and citation.get('labelled_mood', row['mood']) != row['mood']:
                            raise ValueError('Counterexample label differs from current evidence')
                        ident = resolve(row)
                        route_id = ident[0] + '/' + str(row.get('voice')) if ident else None
                        if row.get('voice_basis') != 'human' or route_id not in routes:
                            report['unrouted_citations'].append({'id': row['id'], 'packet': written['packet']})
                            continue
                        scoped[route_id][mood].append((category, row, citation))
            if not scoped:
                report['unattached_packets'].append(written['packet'])
            for route_id, entries in scoped.items():
                profile, route = routes[route_id]
                report['attached_packets'] += 1
                packets = route.setdefault('instruction_sources', [])
                packets.append({'file': item['instructions_file'], 'sha256': digest,
                                'identity': who, 'presentation_route': kind})
                guidance = route.setdefault('character_director_instructions', [])
                for field, text in [('summary', written.get('summary')),
                                    ('baseline.description', written.get('baseline', {}).get('description')),
                                    ('route_notes', written.get('route_notes'))]:
                    safe = safe_text(text, report['excluded_workflow_lines'], written['packet'] + '/' + field)
                    if safe and safe not in guidance:
                        guidance.append(safe)
                for mood, citations in entries.items():
                    originals = [m for m in written['moods'] if m['mood'] == mood]
                    if len(originals) != 1:
                        raise ValueError('Duplicate written mood rule')
                    original = originals[0]
                    location = written['packet'] + '/' + mood
                    excluded = report['excluded_workflow_lines']
                    specific = {'choose_when': [], 'avoid_when': [], 'versus': [],
                                'source_file': item['instructions_file'], 'source_sha256': digest}
                    for key in ('choose_when', 'avoid_when'):
                        specific[key] = [v for n, text in enumerate(original.get(key, []))
                                         if (v := safe_text(text, excluded, f'{location}/{key}/{n}'))]
                    for n, rival in enumerate(original.get('versus', [])):
                        if rival['mood'] not in book['vocabulary']:
                            raise ValueError('Unknown contrasting mood')
                        text = safe_text(rival['how_to_tell'], excluded, f'{location}/versus/{n}')
                        if text:
                            specific['versus'].append({'mood': rival['mood'], 'how_to_tell': text})
                    for key in ('continuity', 'limits'):
                        specific[key] = safe_text(original.get(key), excluded, location + '/' + key)
                    # Prose without a positive example on this exact voice route
                    # stays a general hypothesis rather than a character rule.
                    if not any(category == 'positive' for category, _, _ in citations):
                        continue
                    rule = next(r for r in route['mood_rules'] if r['mood'] == mood)
                    rule.setdefault('character_specific_instructions', []).append(specific)
                    rule['instruction_basis'] = 'example_derived_character_and_exact_voice; not_calibrated'
                    from build_production_rulebook import choose_examples
                    for category, field in [('positive', 'positive_examples'), ('contrast', 'contrast_examples')]:
                        rows = [row for cat, row, _ in citations if cat == category]
                        if rows:
                            rule[field] = choose_examples(rows, profile['id'], route_id, 3 if category == 'positive' else 2)
                    all_specific = rule['character_specific_instructions']
                    choose = [text for value in all_specific for text in value['choose_when']]
                    avoid = [text for value in all_specific for text in value['avoid_when']]
                    if choose:
                        rule['choose_when'] = ' '.join(dict.fromkeys(choose))
                    if avoid:
                        rule['do_not_choose_when'] = ' '.join(dict.fromkeys(avoid))
                    if specific['versus']:
                        rule['nearest_competing_mood'] = specific['versus'][0]['mood']
                    report['specific_mood_rules'] += 1
                # Respect a policy baseline only on its explicitly selected
                # voice. An unresolved voice never freezes by mood majority.
                if item.get('frozen_voice') == route['voice_family']:
                    baseline = item['production_baseline']
                    if book.get('calibration_policy_scope'):
                        written_baseline = written.get('baseline', {}).get('mood')
                        if written_baseline not in ('calm', 'neutral'):
                            raise ValueError('Reduced-development instructions require a Calm/Normal baseline')
                        if not route['mood_counts'].get(written_baseline):
                            raise ValueError('Written baseline has no reviewed evidence on this exact voice route')
                        if written_baseline != baseline:
                            report['baseline_resolutions'].append({'route_id': route_id,
                                'statistics_preference': baseline, 'written_baseline': written_baseline,
                                'selected': written_baseline, 'source_file': item['instructions_file']})
                        baseline = written_baseline
                    route['baseline']['mood'] = baseline
                    route['baseline']['basis'] = ('explicit reduced-development writer baseline; raw counts retained'
                        if book.get('calibration_policy_scope') else 'merged development policy; unmarked delivery only')
    book['merge_report'] = report
    book['policy_kind'] = 'merged example-derived main-cast instructions and authored sparse-character hypotheses; not calibrated'
    # The merged file remains immutable and uses the same runtime contract.
    from build_production_rulebook import digest, json_bytes
    book['version'] = digest(json_bytes({k: v for k, v in book.items() if k != 'version'}))[:16]
    return book
