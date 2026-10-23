from __future__ import annotations

from collections import Counter, defaultdict
from functools import cache
from pathlib import Path
import posixpath
import re
import unicodedata

from .util import AppError, check_stop, inside

CASTING_SCHEMA = 7
GENDERS = ('female', 'male', 'unknown')
AGE_BANDS = ('child', 'teen', 'adult', 'elderly', 'unknown')
TRAITS = {'gender': GENDERS, 'age_band': AGE_BANDS}
ROLES = ('narrator', 'narrator_variant', 'mechanical', 'character')
KINDS = ('person', 'mechanical', 'collective')
TRAIT_EVIDENCE_LIMIT = 100
ALIAS_COMPARISON_LIMIT = 256
NOTICE_NAMES = 20
DIALOGUE_WINDOW = 6
NEGATIONS = {'not', 'no', 'never', 'neither', 'nor', 'without', 'non', 'не', 'нет', 'без', 'ни'}
METADATA_LABELS = {'gender': 'gender', 'age_bands': 'age bands'}
NOTICES = {
    'shared_performance_contexts': '{count} local dialogue context(s) still share a character performance. Voice reuse elsewhere in the book is allowed.',
    'no_compatible_profile': '{count} character(s) have no compatible voice profile; another profile is used.',
    'displaced_for_collision': '{count} character(s) use another profile: a compatible profile was not used to avoid a local performance collision.',
    'no_collision_gain': '{count} character(s) use an incompatible profile although a compatible profile would add no local performance collision.',
    'disputed_traits': '{count} character(s) have conflicting gender or age evidence and are cast as unknown.',
    'pack_missing_metadata': 'No character voice profile declares {fields}; these character traits cannot guide casting.',
    'alias_unmerged_capacity': '{count} possible alias pair(s) exceeded the model context or output limit and remain separate characters.',
    'alias_skipped_limit': '{count} possible alias pair(s) were not compared because the {limit}-comparison limit was reached; they remain separate characters.',
    'unknown_gender_speakers': '{count} speaking character(s) have unknown gender and are cast by the gender balance of the book: {names}.',
    'unknown_gender_unbalanced': '{count} speaking character(s) have unknown gender; no character with a known gender speaks, so their voices follow no gender balance: {names}.',
    'unresolved_speech': '{count} speech part(s) have no resolved speaker and are held for review.',
    'degraded_passages': '{count} passage(s) could not be analysed for speakers and are held for review.',
    'no_mechanical_voice': '{count} mechanical character(s) use character voices because the pack has no mechanical voice.',
    'narrator_performance_shared': '{count} character(s) share the narrator performance in chapters where they speak.',
    'genderless_profile_for_gendered': '{count} character(s) with a known gender use a voice profile without a declared gender.',
    'series_voice_binding_applied': 'A reviewed series voice binding was applied to {count} character(s): {names}.',
    'series_voice_binding_ambiguous': '{count} character(s) match more than one reviewed series voice binding and use normal casting: {names}.',
}
LEGACY_NOTICES = ('shared_performance_contexts', 'no_compatible_profile', 'displaced_for_collision', 'disputed_traits',
                  'pack_missing_metadata', 'alias_unmerged_capacity', 'alias_skipped_limit')
TARGET_ID = re.compile(r'(.+)-([0-9]+)-([0-9]+)')


def game_ui_voice(voice):
    if 'game_ui' in voice.get('tags', []):
        return True
    labels = (voice.get('id', ''), voice.get('family', ''), voice.get('group', ''))
    words = set(re.findall(r'[a-z0-9]+', ' '.join(label for label in labels if isinstance(label, str)).casefold()))
    return 'mechanical' in words and bool(words & {'game', 'stats', 'stat', 'system', 'ui'})


def legacy_mechanical(voice):
    return voice.get('role', 'character') == 'character' and game_ui_voice(voice)


def role_review(voice):
    labels = (voice['id'], voice.get('family', ''), voice.get('group', ''), *voice.get('tags', []))
    return (voice.get('role', 'character') == 'character' and not legacy_mechanical(voice)
            and any(isinstance(label, str) and 'mechanical' in label.casefold() for label in labels))


def voice_role(voice):
    return 'mechanical' if legacy_mechanical(voice) else voice.get('role', 'character')


def ordinary_voice(voice):
    return voice_role(voice) == 'character' and not role_review(voice)


def voice_family(voice):
    return voice.get('family', voice['id'])


def voice_fields(voice):
    return {'voice_id': voice['id'], 'voice_name': voice.get('name', ''), 'role': voice_role(voice),
            'family': voice_family(voice), 'group': voice.get('group', '')}


def voice_details(voice):
    return {'id': voice['id'], 'name': voice.get('name', ''), 'role': voice_role(voice), 'family': voice_family(voice),
            'group': voice.get('group', ''), 'gender': voice.get('gender', ''), 'age_bands': voice.get('age_bands', []),
            'castable': not role_review(voice)}


def performance_key(voice, variant, files=None, root=None):
    if variant == 'clone':
        audio = voice.get('references', {}).get('neutral', {}).get('audio')
        if isinstance(audio, str):
            relative = inside(Path(root), audio).relative_to(Path(root).resolve()).as_posix() if root else posixpath.normpath(audio.replace('\\', '/'))
            return ('reference', (files or {}).get(relative, relative))
    else:
        speaker = voice.get('speaker')
        if isinstance(speaker, str) and speaker.strip():
            return ('speaker', speaker.strip().casefold())
    return ('profile', voice['id'])


def voice_identities(voices, variant, files=None, root=None):
    parent = {}
    def find(node):
        while node in parent:
            node = parent[node]
        return node
    for voice in voices:
        for label in (('family', voice_family(voice)), performance_key(voice, variant, files, root)):
            first, second = find(('voice', voice['id'])), find(label)
            if first != second:
                parent[max(first, second)] = min(first, second)
    return {voice['id']: find(('voice', voice['id'])) for voice in voices}


def resolve_kind(votes):
    votes = {kind: count for kind, count in votes.items() if kind in KINDS and type(count) is int and count > 0} if isinstance(votes, dict) else {}
    top = max(votes.values(), default=0)
    leaders = [kind for kind, count in votes.items() if count == top]
    return leaders[0] if len(leaders) == 1 else 'person'


def kind_votes(character):
    votes = character.get('kind_votes')
    if isinstance(votes, dict):
        return Counter({kind: count for kind, count in votes.items() if kind in KINDS and type(count) is int and count > 0})
    return Counter({character['kind']: 1} if character.get('kind') in KINDS else {})


def kind_support(character):
    support = character.get('kind_support')
    return {kind: ids for kind, ids in support.items() if kind in KINDS and isinstance(ids, list) and ids} if isinstance(support, dict) else {}


def kind_totals(character):
    return kind_votes(character) + Counter({kind: len(ids) for kind, ids in kind_support(character).items()})


def character_kind(character):
    kind = character.get('kind')
    return kind if kind in KINDS else resolve_kind(kind_totals(character))


def casting_pool(character, voices):
    mechanical = [voice for voice in voices if voice_role(voice) == 'mechanical']
    if character_kind(character) == 'mechanical' and mechanical:
        return mechanical
    return [voice for voice in voices if ordinary_voice(voice)]


def trait_support(trait):
    if not isinstance(trait, dict):
        return {}
    if 'support' in trait:
        return {value: ids for value, ids in trait['support'].items() if ids}
    if trait.get('conflict') or trait.get('value', 'unknown') == 'unknown' or not trait.get('evidence'):
        return {}
    return {trait['value']: trait['evidence']}


def resolve_trait(support):
    support = {value: ids for value, ids in support.items() if ids}
    top = max(map(len, support.values()), default=0)
    leaders = [value for value, ids in support.items() if len(ids) == top]
    return {'value': leaders[0] if len(leaders) == 1 else 'unknown', 'support': support} | ({'conflict': True} if len(leaders) > 1 else {})


def merge_trait(old, new):
    first, second = trait_support(old), trait_support(new)
    return resolve_trait({value: list(dict.fromkeys(first.get(value, []) + second.get(value, [])))[:TRAIT_EVIDENCE_LIMIT]
                          for value in dict.fromkeys([*first, *second])})


def trait_value(character, name):
    return (character.get(name) or {}).get('value', 'unknown')


def trait_fit(value, declared):
    return 0 if value == 'unknown' or value in declared else 2 if declared else 1


def gender_fit(character, voice, target=None):
    value = trait_value(character, 'gender')
    if value == 'unknown':
        return int(bool(target) and voice.get('gender', target) != target)
    return trait_fit(value, [voice['gender']] if voice.get('gender') else [])


def age_fit(character, voice):
    return trait_fit(trait_value(character, 'age_band'), voice.get('age_bands', []))


def compatible(character, voice):
    return gender_fit(character, voice) < 2


def normal(text):
    text = ''.join(char for char in unicodedata.normalize('NFKC', text).casefold() if unicodedata.category(char) != 'Mn').replace('ё', 'е')
    return re.sub(r"n['’]t\b", ' not', text)


def words(text):
    return tuple(re.findall(r'\w+', normal(text)))


def clauses(text):
    return [words(clause) for clause in re.split(r'[.,;:!?()\[\]\n—–]', normal(text))]


def positions(found, phrase):
    return (index for index in range(len(found) - len(phrase) + 1) if found[index:index + len(phrase)] == phrase)


def contains(found, phrase):
    return any(True for _ in positions(found, phrase))


def mentions(found, phrase):
    return any(not NEGATIONS & set(clause[max(0, index - 2):index]) for clause in found for index in positions(clause, phrase))


def casting_inputs(parts):
    return [
        {key: part[key] for key in ('chapter_id', 'source_id', 'ordinal', 'start', 'speaker')}
        | {'kind': part.get('kind', 'paragraph'), 'characters': len(part['text']),
           'scene_id': part.get('scene_id', '')}
        for part in parts
    ]


def speech_volume(segments):
    counts = Counter()
    for segment in segments:
        counts[segment['speaker']] += segment.get('characters', len(segment.get('text', '')))
    return counts


def target_ranges(targets):
    ranges = defaultdict(list)
    for match in filter(None, (TARGET_ID.fullmatch(target) for target in targets if isinstance(target, str))):
        ranges[match[1]].append((int(match[2]), int(match[3])))
    return ranges


def dialogue_contexts(segments):
    paragraphs = {}
    for index, segment in enumerate(segments):
        source = segment.get('source_id')
        if not isinstance(source, str):
            raise AppError('Conversation-aware casting requires ordered source passages, not chapter totals.')
        key = (segment['chapter_id'], source)
        paragraph = paragraphs.setdefault(key, {
            'chapter_id': key[0], 'source_id': source, 'kind': segment.get('kind', 'paragraph'),
            'scene_id': segment.get('scene_id', ''),
            'order': (segment.get('ordinal', index), index), 'speakers': set(),
        })
        speaker = segment['speaker']
        if speaker != 'narrator':
            paragraph['speakers'].add(speaker)
    contexts = []
    current = None
    quiet = 0
    for paragraph in sorted(paragraphs.values(), key=lambda item: item['order']):
        if not paragraph['speakers']:
            quiet += 1
            if paragraph['kind'] != 'paragraph' or quiet > 1:
                current = None
            continue
        skipped, quiet = quiet, 0
        if (paragraph['kind'] != 'paragraph' or current is None or current['chapter_id'] != paragraph['chapter_id']
                or current['scene_id'] != paragraph['scene_id']):
            current = {'chapter_id': paragraph['chapter_id'], 'scene_id': paragraph['scene_id'], 'source_ids': [], 'speakers': set(),
                       'turns': [], 'position': 0}
            contexts.append(current)
        else:
            current['position'] += 1 + skipped
        current['source_ids'].append(paragraph['source_id'])
        current['speakers'].update(paragraph['speakers'] - {'unknown'})
        current['turns'].append((current['position'], paragraph['speakers'] - {'unknown'}))
        if paragraph['kind'] != 'paragraph':
            current = None
    return [{key: value for key, value in item.items() if key not in ('scene_id', 'turns', 'position')}
            | {'speakers': sorted(item['speakers']), 'pairs': window_pairs(item['turns'])} for item in contexts]


def window_pairs(turns):
    pairs = set()
    for index, (position, speakers) in enumerate(turns):
        for later, others in turns[index:]:
            if later - position >= DIALOGUE_WINDOW:
                break
            pairs.update(tuple(sorted((one, two))) for one in speakers for two in others if one != two)
    return sorted(pairs)


def conflict_graph(contexts, characters):
    known = {character['id'] for character in characters}
    neighbours = {cid: Counter() for cid in known}
    for context in contexts:
        for a, b in context['pairs']:
            if a in known and b in known:
                neighbours[a][b] += 1
                neighbours[b][a] += 1
    return neighbours


def colliding_pairs(assigned, keys, neighbours, characters):
    return {tuple(sorted((cid, other))) for cid in characters if cid in assigned for other in neighbours[cid]
            if other in assigned and keys[assigned[cid]] == keys[assigned[other]]}


def collision_weight(pairs, neighbours):
    return sum(neighbours[a][b] for a, b in pairs)


def gender_targets(characters, pools, counts):
    known = Counter()
    for character in characters:
        if (gender := trait_value(character, 'gender')) != 'unknown':
            known[gender] += counts[character['id']]
    total, balance, targets = sum(known.values()), Counter(), {}
    for character in sorted(characters, key=lambda item: (-counts[item['id']], item['id'])):
        groups = sorted({voice['gender'] for voice in pools[character['id']] if voice.get('gender')})
        if total and groups and trait_value(character, 'gender') == 'unknown':
            spent = sum(balance.values()) + counts[character['id']]
            target = targets[character['id']] = max(groups, key=lambda group: (known[group] * spent - balance[group] * total, known[group]))
            balance[target] += counts[character['id']]
    return targets


def hint_partners(hints, neighbours):
    partners = defaultdict(set)
    for first, second in hints:
        if first != second and first in neighbours and second in neighbours and not neighbours[first][second]:
            partners[first].add(second)
            partners[second].add(first)
    return partners


def optimize_casting(assigned, speaking, characters, voices, keys, neighbours, matches, stop=None, fit=None, hints=None, pools=None):
    assigned = dict(assigned)
    fit = fit or (lambda cid, vid: (not compatible(characters[cid], voices[vid]), gender_fit(characters[cid], voices[vid]),
                                     age_fit(characters[cid], voices[vid]), -matches(cid, vid)))
    hints = hints or {}
    allowed = {cid: set(pools[cid]) if pools else set(voices) for cid in assigned}
    def cost(change):
        pairs = colliding_pairs(assigned, keys, neighbours, change)
        missed = {tuple(sorted((cid, other))) for cid in change for other in hints.get(cid, ())
                  if other in assigned and keys[assigned[cid]] != keys[assigned[other]]}
        incompatible, *rest = map(sum, zip(*(fit(cid, assigned[cid]) for cid in change)))
        return (incompatible, collision_weight(pairs, neighbours), len(missed), *rest)
    def better(change):
        old = cost(change)
        previous = {cid: assigned[cid] for cid in change}
        assigned.update(change)
        new = cost(change)
        assigned.update(previous)
        return new < old
    order = sorted(set(speaking) & set(assigned))
    while True:
        changed = False
        for cid in order:
            check_stop(stop)
            for vid in sorted(allowed[cid]):
                if vid != assigned[cid] and better({cid: vid}):
                    assigned[cid] = vid
                    changed = True
        for index, cid in enumerate(order):
            check_stop(stop)
            for other in order[index + 1:]:
                swap = {cid: assigned[other], other: assigned[cid]}
                if (assigned[cid] != assigned[other] and swap[cid] in allowed[cid] and swap[other] in allowed[other]
                        and better(swap)):
                    assigned.update(swap)
                    changed = True
        if not changed:
            return assigned


def cast_voices(characters, voices, segments, files=None, root=None, variant='custom', stop=None, same_hints=(), family_guidance=None):
    family_guidance = family_guidance if isinstance(family_guidance, dict) else {}
    gender_map={'masculine':'male','feminine':'female','neutral':''}
    # Registry labels describe the performed voice, not the character's exact
    # biography.  "Young" in the review UI means a youthful/young-adult voice;
    # children are explicit so an adult protagonist is not penalised for the
    # listener's saved ``young`` descriptor.
    age_map={'child':['child'],'young':['teen','adult'],'adult':['adult'],
             'mature':['adult'],'elderly':['elderly']}
    def guided(voice):
        descriptors=family_guidance.get(voice_family(voice),{}).get('descriptors',{})
        gender=gender_map.get(descriptors.get('performed_gender'))
        ages=age_map.get(descriptors.get('perceived_age'))
        return {**voice,**({'gender':gender} if gender is not None else {}),**({'age_bands':ages} if ages else {})}
    voices=[guided(voice) for voice in voices]
    narrator = next((voice for voice in voices if voice.get('role') == 'narrator'), None)
    if narrator is None:
        raise AppError('A voice pack needs a narrator profile.')
    if not any(ordinary_voice(voice) for voice in voices):
        held = [voice['id'] for voice in voices if role_review(voice)]
        raise AppError('A voice pack needs at least one character performance.'
                       + (f' Review the declared role of: {", ".join(held)}.' if held else ''))
    counts = speech_volume(segments)
    neighbours = conflict_graph(dialogue_contexts(segments), characters)
    keys = voice_identities(voices, variant, files, root)
    pools = {character['id']: casting_pool(character, voices) for character in characters}
    # A narrator-specific pack may bind a recurring series character to its
    # reviewed performance by name. Bindings are data, never family IDs. An
    # exact single binding is stronger evidence than inferred traits.
    bindings = {}
    for character in characters:
        labels = {words(value) for value in [character.get('name', ''), *character.get('aliases', [])] if words(value)}
        matches = {voice['id']: sorted(' '.join(label) for label in
                   labels & {words(value) for value in voice.get('character_names', []) if words(value)})
                   for voice in pools[character['id']]}
        bound = [voice for voice in pools[character['id']] if matches[voice['id']]]
        if len(bound) == 1:
            pools[character['id']] = bound
            bindings[character['id']] = {'status': 'applied', 'voice_ids': [bound[0]['id']],
                                         'matched_names': matches[bound[0]['id']]}
        elif len(bound) > 1:
            bindings[character['id']] = {'status': 'ambiguous', 'voice_ids': [voice['id'] for voice in bound],
                                         'matched_names': sorted(set().union(*(matches[voice['id']] for voice in bound)))}
    targets = gender_targets(characters, pools, counts)
    partners = hint_partners(same_hints, neighbours)
    by_id = {character['id']: character for character in characters}
    voice_by_id = {voice['id']: voice for voice in voices}
    tags = {voice['id']: {words(tag) for tag in voice.get('tags', [])} - {()} for voice in voices}
    descriptions = {cid: clauses(character.get('description', '')) for cid, character in by_id.items()}
    description_words={cid:set(words(character.get('description',''))) for cid,character in by_id.items()}
    stop_words={'voice','character','sound','with','this','that','from','для','этот','эта','голос','персонаж'}
    def guidance_words(vid,field):
        text=family_guidance.get(voice_family(voice_by_id[vid]),{}).get(field,'')
        return {word for word in words(text) if len(word)>2 and word not in stop_words}

    def exclusive_guidance(vid):
        """Treat a plain ``… only`` note as a reservation, not an exclusion.

        The original UI accepted free text under "Avoid / distinguish".  A
        listener consequently saved ``main protagonist only`` to reserve a
        family for that role.  Explicit negative language remains an avoid rule.
        """
        text=family_guidance.get(voice_family(voice_by_id[vid]),{}).get('avoid_for','')
        tokens=guidance_words(vid,'avoid_for')
        negative={'avoid','except','never','different','exclude','excluding','not'}
        return (tokens-{'only'}) if 'only' in words(text) and not (tokens&negative) else set()

    @cache
    def matches(cid, vid):
        return sum(mentions(descriptions[cid], tag) for tag in tags[vid])

    @cache
    def fit(cid, vid):
        character, voice = by_id[cid], voice_by_id[vid]
        gender = gender_fit(character, voice, targets.get(cid))
        exclusive=exclusive_guidance(vid)
        positive=len(description_words[cid]&(guidance_words(vid,'casting_description')|
                                              guidance_words(vid,'character_fit')|exclusive))
        avoid=0 if exclusive else len(description_words[cid]&guidance_words(vid,'avoid_for'))
        exclusive_miss=bool(exclusive and not description_words[cid]&exclusive)
        return tuple(max(1, counts[cid]) * value for value in (
            gender == 2, keys[vid] == keys[narrator['id']], gender, age_fit(character, voice),
            exclusive_miss, avoid, -positive, -matches(cid, vid)))

    remaining = dict(by_id)
    assigned, placed, volume = {}, {}, Counter()
    while remaining:
        check_stop(stop)
        cid = min(remaining, key=lambda item: (
            -len({placed[other] for other in neighbours[item] if other in placed}),
            -sum(neighbours[item].values()), trait_value(remaining[item], 'gender') == 'unknown', -counts[item], item,
        ))
        del remaining[cid]
        def rank(voice):
            key = keys[voice['id']]
            collisions = sum(weight for other, weight in neighbours[cid].items() if placed.get(other) == key)
            missed = sum(placed[other] != key for other in partners[cid] if other in placed)
            incompatible, *rest = fit(cid, voice['id'])
            return (incompatible, collisions, missed, *rest, volume[key], voice['id'])
        vid = min(pools[cid], key=rank)['id']
        assigned[cid], placed[cid] = vid, keys[vid]
        volume[keys[vid]] += counts[cid]
    assigned = optimize_casting(assigned, set(counts), by_id, voice_by_id, keys, neighbours, matches, stop, fit, partners,
                                {cid: [voice['id'] for voice in pool] for cid, pool in pools.items()})
    usage = Counter(keys[vid] for cid, vid in assigned.items() if cid in counts)
    result = [{'id': 'narrator', 'name': 'Narrator', 'aliases': [], 'description': 'Narration', 'evidence': [], **voice_fields(narrator)}]
    result.extend({**character, 'kind': character_kind(character), **voice_fields(voice_by_id[assigned[character['id']]]),
                   'gender_target': targets.get(character['id'], ''),
                   'voice_reused': usage[keys[assigned[character['id']]]] - (character['id'] in counts) > 0,
                   'voice_compatible': compatible(character, voice_by_id[assigned[character['id']]]),
                   **({'series_voice_binding': bindings[character['id']]} if character['id'] in bindings else {})}
                  for character in characters)
    return result


def shared_contexts(cast, identities, segments):
    owners = {character['id']: identities[character['voice_id']] for character in cast
              if character['id'] != 'narrator' and character.get('voice_id') in identities}
    conflicts = []
    for context in dialogue_contexts(segments):
        shared = defaultdict(set)
        for a, b in context['pairs']:
            if a in owners and b in owners and owners[a] == owners[b]:
                shared[owners[a]].update((a, b))
        groups = [sorted(group) for group in shared.values()]
        if groups:
            conflicts.append({key: value for key, value in context.items() if key != 'pairs'} | {'shared_speakers': sorted(groups)})
    return conflicts


def casting_conflicts(cast, voices, segments, files=None, root=None, variant='custom'):
    return shared_contexts(cast, voice_identities(voices, variant, files, root), segments)


def casting_notices(cast, voices, segments, conflicts, identities=None, unmerged=0, skipped=0, degraded=0, degraded_sources=(), narrators=None):
    counts = speech_volume(segments)
    excluded = target_ranges(degraded_sources)
    unresolved = [segment for segment in segments if segment['speaker'] == 'unknown'
                  and not any(start <= segment.get('start', -1) < end for start, end in excluded.get(segment.get('source_id'), ()))]
    voice_by_id = {voice['id']: voice for voice in voices}
    characters = [character for character in cast if character['id'] != 'narrator']
    speakers = sorted((character for character in characters if character['id'] in counts), key=lambda item: (-counts[item['id']], item['id']))
    narrator = (next((character.get('voice_id') for character in cast if character['id'] == 'narrator'), None)
                or next((voice['id'] for voice in voices if voice.get('role') == 'narrator'), None))
    narrators, spoken, narrated = narrators or {}, defaultdict(set), set()
    for segment in segments:
        spoken[segment['chapter_id']].add(segment['speaker'])
    narration = {chapter: identities.get(narrators.get(chapter) or narrators.get('') or narrator) for chapter in spoken} if identities else {}
    placed = {character['id']: identities[character['voice_id']] for character in characters
              if identities and character.get('voice_id') in identities}
    neighbours = conflict_graph(dialogue_contexts(segments), characters) if identities else {}
    mechanical = any(voice_role(voice) == 'mechanical' for voice in voices)
    def collisions(cid, vid):
        return sum(weight for other, weight in neighbours[cid].items() if placed.get(other) == identities[vid])
    groups = defaultdict(list)
    for character in characters:
        binding = character.get('series_voice_binding')
        if not isinstance(binding, dict):
            continue
        status = binding.get('status')
        if status == 'applied' and character.get('voice_id') in binding.get('voice_ids', []):
            groups['series_voice_binding_applied'].append(character)
        elif status == 'ambiguous':
            groups['series_voice_binding_ambiguous'].append(character)
    for character in speakers:
        cid, pool, voice = character['id'], casting_pool(character, voices), voice_by_id.get(character.get('voice_id'))
        gender, gendered = trait_value(character, 'gender'), any(option.get('gender') for option in pool)
        if not (compatible(character, voice) if voice else character.get('voice_compatible') is not False):
            options = [option['id'] for option in pool if compatible(character, option)]
            groups['no_compatible_profile' if not options else 'no_collision_gain'
                   if identities and voice and min(collisions(cid, vid) for vid in options) <= collisions(cid, voice['id'])
                   else 'displaced_for_collision'].append(character)
        if any((character.get(name) or {}).get('conflict') for name in TRAITS):
            groups['disputed_traits'].append(character)
        if gendered and gender == 'unknown':
            groups['unknown_gender_unbalanced' if character.get('gender_target', True) == '' else 'unknown_gender_speakers'].append(character)
        if character_kind(character) == 'mechanical' and not mechanical:
            groups['no_mechanical_voice'].append(character)
        if voice and identities and (shared := {chapter for chapter, present in spoken.items()
                                                if cid in present and narration[chapter] == identities[voice['id']]}):
            groups['narrator_performance_shared'].append(character)
            narrated |= shared
        if voice and gendered and gender != 'unknown' and not voice.get('gender'):
            groups['genderless_profile_for_gendered'].append(character)
    castable = [voice for voice in voices if ordinary_voice(voice) or voice_role(voice) == 'mechanical']
    missing = [field for field in METADATA_LABELS if not any(voice.get(field) for voice in castable)]
    extra = {'narrator_performance_shared': {'chapters': [chapter for chapter in spoken if chapter in narrated]}}
    found = {code: (len(group), {'names': [character.get('name') or character['id'] for character in group[:NOTICE_NAMES]],
                                 'ids': [character['id'] for character in group[:NOTICE_NAMES]], **extra.get(code, {})}) for code, group in groups.items()}
    found |= {'shared_performance_contexts': (len(conflicts), {'chapters': list(dict.fromkeys(item['chapter_id'] for item in conflicts))}),
              'pack_missing_metadata': (len(missing), {'fields': missing}), 'alias_unmerged_capacity': (unmerged, {}),
              'alias_skipped_limit': (skipped, {'limit': ALIAS_COMPARISON_LIMIT}),
              'unresolved_speech': (len(unresolved), {'characters': speech_volume(unresolved)['unknown']}),
              'degraded_passages': (degraded, {})}
    return [{'code': code, 'count': found[code][0], 'params': found[code][1]} for code in NOTICES if found.get(code, (0,))[0]]


def notice_text(notice):
    params, count = notice['params'], notice['count']
    names = params.get('names', [])
    return NOTICES[notice['code']].format(count=count, limit=params.get('limit', ALIAS_COMPARISON_LIMIT),
                                          fields=' or '.join(METADATA_LABELS[field] for field in params.get('fields', [])),
                                          names=', '.join(names) + (f' and {count - len(names)} more' if names and count > len(names) else ''))


def cast_summary(cast, voices, segments, files=None, root=None, variant='custom', rendered_chapters=None, unmerged=0, skipped=0, degraded=0,
                 degraded_sources=(), narrators=None):
    if rendered_chapters is not None:
        rendered = set(rendered_chapters)
        segments = [segment for segment in segments if segment['chapter_id'] in rendered]
    identities = voice_identities(voices, variant, files, root)
    conflicts = shared_contexts(cast, identities, segments)
    notices = casting_notices(cast, voices, segments, conflicts, identities, unmerged, skipped, degraded, degraded_sources, narrators)
    return {'conflicts': conflicts, 'warnings': [notice_text(notice) for notice in notices], 'notices': notices}


def casting_warnings(cast, conflicts, segments, voices, unmerged=0, skipped=0):
    return [notice_text(notice) for notice in casting_notices(cast, voices, segments, conflicts, None, unmerged, skipped)
            if notice['code'] in LEGACY_NOTICES]
