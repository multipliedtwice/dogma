"""Read-only, scoped LLM context for the production delivery rulebook.

The caller still owns structure, provider isolation, calibration and acceptance.
This module never writes labels, renders audio or calls an online provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def validate_rulebook(book):
    if book.get('schema') != 1 or not isinstance(book.get('characters'), list):
        raise ValueError('Unsupported delivery rulebook')
    unsigned = {key: value for key, value in book.items() if key != 'version'}
    if hashlib.sha256(encoded(unsigned)).hexdigest()[:16] != book.get('version'):
        raise ValueError('Delivery rulebook version does not match its content')
    identities, routes = set(), set()
    for character in book['characters']:
        if character['id'] in identities:
            raise ValueError('Duplicate character identity')
        identities.add(character['id'])
        for route in character['routes']:
            if route['id'] in routes or not route['id'].startswith(character['id'] + '/'):
                raise ValueError('Invalid or duplicate character route')
            routes.add(route['id'])
            for rule in route['mood_rules']:
                if rule['id'] != route['id'] + '/mood/' + rule['mood'] or rule['mood'] not in book['vocabulary']:
                    raise ValueError('Invalid scoped mood rule')
                for example in rule['positive_examples'] + rule['contrast_examples']:
                    if example['voice'] != route['voice_family'] or example['voice_basis'] != 'human':
                        raise ValueError('Example belongs to another or unreviewed voice')
                    if example.get('policy_character_id') != character['id'] or example.get('policy_route_id') != route['id']:
                        raise ValueError('Example belongs to another character or route')
                if any(example['mood'] != rule['mood'] for example in rule['positive_examples']):
                    raise ValueError('Positive example has another mood')
    return book


def load_rulebook(path):
    return validate_rulebook(json.loads(Path(path).read_bytes()))


def example_citation_key(example):
    """Opaque stable reference; provenance stays in the offline rulebook."""
    identity = json.dumps([example['book'], example['id']], ensure_ascii=False, separators=(',', ':'))
    return 'e_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]


def runtime_instruction(value):
    """Archive references in authored caveats are offline provenance too."""
    if isinstance(value, str):
        return re.sub(r'\b(?:rb[12]_|clip_|expanded_|sentence_)[a-f0-9]{10,}(?:~\d+-\d+)?\b',
                      '[offline example reference]', value)
    if isinstance(value, list):
        return [runtime_instruction(item) for item in value]
    if isinstance(value, dict):
        return {key: runtime_instruction(item) for key, item in value.items()}
    return value


def delivery_context(book, route_ids, candidate_moods, *, examples_per_mood=1,
                     paragraph_characters=600, max_characters=30000,
                     include_character_instructions=True,
                     exclude_example_chapters=()):
    """Compile only requested character/voice routes and relevant mood rules.

    Candidate moods guide retrieval, not allowed-output labels. Unseen moods
    remain available with strong evidence and further adjudication.

    exclude_example_chapters contains (book_id, chapter_id) pairs. Filtering
    precedes the per-mood limit and applies to examples, contrasts and their
    paragraph context. Provenance is read offline and never sent to the model.
    This filters retrieval only: written rules, support counts, route choices
    and baselines remain those of the supplied policy. Genuine held-out
    calibration requires a policy built without the held-out chapter's labels.
    """
    if not route_ids or not candidate_moods or examples_per_mood < 0 or paragraph_characters < 0:
        raise ValueError('Specify routes, candidate moods and valid context limits')
    excluded = set()
    if isinstance(exclude_example_chapters, (str, bytes)) or exclude_example_chapters is None:
        raise ValueError('Example chapter exclusions must be (book_id, chapter_id) pairs')
    for pair in exclude_example_chapters:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2 or
                any(not isinstance(value, str) or not value.strip() for value in pair)):
            raise ValueError('Example chapter exclusions must be (book_id, chapter_id) pairs')
        excluded.add(tuple(pair))
    missing = set(candidate_moods) - set(book['vocabulary'])
    if missing:
        raise ValueError('Unknown mood: ' + ', '.join(sorted(missing)))
    catalog = {route['id']: (character, route) for character in book['characters'] for route in character['routes']}
    if set(route_ids) - set(catalog):
        raise ValueError('Unresolved character/voice route; do not substitute another character')
    relevant = set(candidate_moods)
    relevant.update(book['vocabulary'][m]['nearest_competing_mood'] for m in candidate_moods)
    for ident in route_ids:
        _, route = catalog[ident]
        for rule in route['mood_rules']:
            if rule['mood'] in candidate_moods:
                relevant.add(rule['nearest_competing_mood'])
                for specific in rule.get('character_specific_instructions', []):
                    relevant.update(v['mood'] for v in specific['versus'])
    selected = []
    for ident in dict.fromkeys(route_ids):
        character, route = catalog[ident]
        observed = {rule['mood']: rule for rule in route['mood_rules']}
        rules = []
        for mood in sorted(relevant):
            rule = observed.get(mood)
            payload = {'id': ident + '/mood/' + mood, 'mood': mood,
                'development_support': rule['development_support'] if rule else 0,
                'status': rule['evidence_status'] if rule else 'unobserved_in_development',
                'examples': [], 'contrasts': [], 'contrast_moods': {}, 'example_context': {}}
            # Rival definitions and examples clarify the boundary without
            # recursively copying every rival's full character policy.
            if rule and include_character_instructions and mood in candidate_moods:
                payload.update({key: runtime_instruction(rule[key]) for key in ('choose_when', 'do_not_choose_when',
                    'nearest_competing_mood') if key in rule})
                if rule.get('character_specific_instructions'):
                    payload['character_specific_instructions'] = [
                        {key: runtime_instruction(value) for key, value in instruction.items() if key in ('versus', 'continuity', 'limits')}
                        for instruction in rule['character_specific_instructions']]
            if rule:
                for source, target in (('positive_examples', 'examples'), ('contrast_examples', 'contrasts')):
                    eligible = []
                    for example in rule[source]:
                        if excluded:
                            if not example.get('book') or not example.get('chapter_id'):
                                raise ValueError('Cannot exclude example chapters without offline provenance')
                            if (example['book'], example['chapter_id']) in excluded:
                                continue
                        eligible.append(example)
                    for example in eligible[:examples_per_mood]:
                        if example['voice'] != route['voice_family'] or example.get('policy_character_id') != character['id'] or example.get('policy_route_id') != route['id']:
                            raise ValueError('Cross-character/voice example in context')
                        paragraph = example.get('paragraph') or ''
                        citation = example_citation_key(example)
                        payload[target].append({'citation_key': citation, 'text': example['text']})
                        if target == 'contrasts':
                            payload['contrast_moods'][citation] = example['mood']
                        # Context is text only, keyed by the same opaque citation.
                        # No archive IDs, book names or chapter/source metadata.
                        context = []
                        before = (example.get('before') or '')[-paragraph_characters:] if paragraph_characters else ''
                        after = (example.get('after') or '')[:paragraph_characters]
                        if before:
                            context.append('Previous paragraph (tail): ' + before)
                        if paragraph_characters and paragraph:
                            context.append('Containing paragraph' + (' (partial)' if len(paragraph) > paragraph_characters else '') + ': ' + paragraph[:paragraph_characters])
                        if after:
                            context.append('Next paragraph (head): ' + after)
                        if context:
                            payload['example_context'][citation] = '\n'.join(context)
            rules.append(payload)
        selected.append({'character_id': character['id'], 'name': character['name'],
            'route_id': ident, 'voice_family': route['voice_family'], 'presentation': route['presentation'],
            'route_instruction': route['selection_instruction'], 'baseline': route['baseline'],
            'route_director_instructions': route.get('route_director_instructions', []),
            'director_instructions': character['director_instructions'] if include_character_instructions else [],
            'rules': rules})
        if route.get('character_director_instructions'):
            selected[-1]['character_director_instructions'] = (
                runtime_instruction(route['character_director_instructions']) if include_character_instructions else [])
    result = {'policy_version': book['version'], 'policy_status': book['status'],
        'director_protocol': book['director_protocol'],
        'context_mode': 'character_specific' if include_character_instructions else 'general_definitions',
        'all_supported_mood_ids': sorted(book['vocabulary']),
        'retrieval_note': 'Listed candidates narrow example retrieval, not permitted moods. A strong unlisted interpretation must request its rule context and critic; no silent baseline fallback.',
        'definitions': {m: book['vocabulary'][m] for m in sorted(relevant)},
        'character_routes': selected, 'decision_contract': book['decision_contract'],
        'background_repair': book['background_repair']}
    if len(json.dumps(result, ensure_ascii=False)) > max_characters:
        raise ValueError('Rulebook context exceeds budget; narrow relevant routes/moods explicitly')
    return result
