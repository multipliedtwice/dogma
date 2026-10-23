"""Freeze source-bound demo pronunciation and production inputs; never synthesize.

Run --stage pronunciation in the ASR Python environment (CPU stress models).
All book text, CLI requests, answers and immutable checkpoints stay in --folder.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.util import file_digest

NUMBER_POLICY = 'english-source-numbers-v1'
ABBREVIATION_POLICY = 'english-title-abbreviations-v1'
ABBREVIATIONS = {'Mr.': 'Mister', 'Mrs.': 'Missus', 'Mme.': 'Madame'}
RUNNER = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
SAMPLING = dict(temperature=.8, top_k=50, top_p=.95, repetition_penalty=1.1,
                do_sample=True, max_tokens=512, text_chunk_mode='tag_aware', text_chunk_size=65536)
NUMBER = re.compile(r'(?<![\w$])(?:Mrs\.|Mr\.|Mme\.|\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?|\d+(?:st|nd|rd|th)|\d+(?:\.\d+)?)(?![\w\d])')
# Currency as an attributive measure uses singular dollar: a ten dollar bill.
# Closed ordinary noun vocabulary avoids guessing grammar from a model.
MODIFIER = re.compile(r'^\s+(?:(?:gold|silver)\s+)?(?:piece|coin|bill|note|flat|apartment|room|house|gift|present|watch|meal|ticket|fee|budget|limit)\b', re.I)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def immutable(path, value):
    path = Path(path)
    if path.exists():
        if read(path) != value:
            raise ValueError('Immutable record differs: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def english_numbers(text):
    """Expand numeric surfaces/titles; every untouched source character survives."""
    from num2words import num2words
    changes, pieces, cursor = [], [], 0
    for match in NUMBER.finditer(text):
        surface = match[0]
        if surface in ABBREVIATIONS:
            spoken = ABBREVIATIONS[surface]
        elif surface.startswith('$'):
            amount = surface[1:].replace(',', '')
            dollars, _, cents = amount.partition('.')
            dollars, cents = int(dollars), int(cents or '0')
            modifier = not cents and bool(MODIFIER.match(text[match.end():]))
            spoken = num2words(dollars, lang='en') + (' dollar' if dollars == 1 or modifier else ' dollars')
            if cents:
                spoken += ' and ' + num2words(cents, lang='en') + (' cent' if cents == 1 else ' cents')
        elif re.search(r'(st|nd|rd|th)$', surface):
            spoken = num2words(int(surface[:-2]), lang='en', to='ordinal')
        else:
            spoken = num2words(surface if '.' in surface else int(surface), lang='en')
        pieces.extend([text[cursor:match.start()], spoken])
        abbreviation = surface in ABBREVIATIONS
        changes.append(dict(start=match.start(), end=match.end(), source=surface, spoken=spoken,
                            rule=ABBREVIATION_POLICY if abbreviation else NUMBER_POLICY,
                            kind='abbreviation' if abbreviation else 'currency' if surface.startswith('$') else 'number'))
        cursor = match.end()
    pieces.append(text[cursor:])
    spoken = ''.join(pieces)
    validate_conversion(text, spoken, changes)
    if re.search(r'\d|\$', spoken):
        raise ValueError('Unsupported numeric surface remains; requires a source-bound rule')
    return spoken, changes


def validate_conversion(source, spoken, changes):
    cursor, pieces = 0, []
    for change in changes:
        start, end = change['start'], change['end']
        if not cursor <= start < end <= len(source) or source[start:end] != change['source']:
            raise ValueError('Conversion is not bound to the exact source span')
        pieces.extend([source[cursor:start], change['spoken']])
        cursor = end
    pieces.append(source[cursor:])
    if ''.join(pieces) != spoken:
        raise ValueError('Number conversion changed words outside its recorded source spans')


def pronunciation_letters(text):
    return text.replace('\u0301', '').replace('ё', 'е').replace('Ё', 'Е')


def validate_pronunciation(spoken, synthesis):
    if pronunciation_letters(spoken) != pronunciation_letters(synthesis):
        raise ValueError('Pronunciation changed source letters, tokens or punctuation')
    if re.search(r'\[(?:emotion|prosody|pitch|speed|vocal)', synthesis, re.I):
        raise ValueError('Disabled Higgs effect tag')


def verify_assignment_source(folder, assigned):
    from rimsky.speech_quality import word_tokens
    book_path = folder / 'prepared/book.json'
    if file_digest(book_path) != assigned['source_sha256']:
        raise ValueError('Assigned analysis no longer binds to prepared source')
    book = read(book_path)
    if book['source_hash'] != assigned['source_epub_sha256']:
        raise ValueError('Source EPUB pin changed')
    blocks = {b['id']: b for c in book['chapters'] for b in c['blocks'] if b.get('text', '').strip()}
    seen, spans = set(), {key: [] for key in blocks}
    for unit in assigned['units']:
        if unit['id'] in seen:
            raise ValueError('Duplicate unit')
        seen.add(unit['id'])
        block = blocks[unit['source_id']]
        start, end = unit['start'], unit['end']
        if not 0 <= start < end <= len(block['text']) or block['text'][start:end] != unit['text']:
            raise ValueError('Unit differs from exact source span: ' + unit['id'])
        spans[unit['source_id']].append((start, end, unit['text']))
    for key, block in blocks.items():
        rows = sorted(spans[key])
        if any(a[1] > b[0] for a, b in zip(rows, rows[1:])):
            raise ValueError('Overlapping source units: ' + key)
        if word_tokens(' '.join(t for _, _, t in rows)) != word_tokens(block['text']):
            raise ValueError('Source words omitted or duplicated: ' + key)
    return book


def attach_structure(folder, units, book):
    request_path = folder / 'analysis/structure.request.json'
    answer_path = folder / 'analysis/structure.answer.json'
    saved = read(answer_path)
    request_hash, answer_hash = file_digest(request_path), file_digest(answer_path)
    if saved['request_sha256'] != request_hash:
        raise ValueError('Structure answer no longer binds to the original request')
    source = {b['id']: b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text', '').strip()}
    structure = saved['answer']
    for key, text in source.items():
        if ''.join(p['text'] for p in structure[key]) != text:
            raise ValueError('Structure changed source paragraph: ' + key)
    for unit in units:
        offset, applicable = 0, []
        for index, part in enumerate(structure[unit['source_id']]):
            end = offset + len(part['text'])
            left, right = max(offset, unit['start']), min(end, unit['end'])
            if right > left:
                applicable.append({'text': source[unit['source_id']][left:right],
                                   'start': left, 'end': right, 'mode': part['mode'],
                                   'source_part_index': index, 'source_part_text': part['text'],
                                   'source_part_start': offset, 'source_part_end': end,
                                   'structure_request_sha256': request_hash,
                                   'structure_answer_sha256': answer_hash})
            offset = end
        if ''.join(p['text'] for p in applicable) != unit['text']:
            raise ValueError('Internal structure omitted unit text: ' + unit['id'])
        unit['analysis_parts'] = applicable
        unit['performance'] = {'analysis_parts': copy.deepcopy(applicable)}
    return {'request_sha256': request_hash, 'answer_sha256': answer_hash,
            'request': 'analysis/structure.request.json', 'answer': structure}


def name_candidates(texts, cast):
    """Collect full-paragraph proper-noun slots, then shared base-family grouping."""
    from rimsky import homograph_stress as hs
    from rimsky import proper_noun_stress as pn
    candidates = set(pn._candidates(texts, initial=True))
    for text in texts:
        for match in re.finditer(r'[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)*', text):
            if hs.is_proper(text, match.start(), match[0]):
                candidates.add(match[0])
    for row in cast.values():
        candidates.update(re.findall(r'[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)*', row['name']))
    # Lowercase non-dictionary coinages use the same base-family protections,
    # with an Opus judgement rather than an extra hidden Silero base guess.
    normalized = set(pn._coinages(texts))
    source_bases = []
    for word in sorted(candidates, key=lambda w: (len(w), w)):
        if pn._base_of(word, source_bases) is None:
            source_bases.append(word)
    for word in source_bases:
        # Dictionary proper names/places seen in an oblique case get their
        # nominative base before one Opus decision is applied to all forms.
        parses = pn._morph().parse(word)
        proper = next((p for p in parses if 'sing' in p.tag and
                       set(str(p.tag).replace(',', ' ').split()) & {'Name', 'Surn', 'Patr', 'Geox'} and
                       (pn._base_of(word, [p.normal_form.capitalize()]) is not None or
                        p.normal_form.capitalize() in candidates)), None)
        # Do not turn colloquial spelling into a dictionary name, or interpret
        # a surname ending -ов as the plural of an unrelated shorter name.
        normalized.add(proper.normal_form.capitalize() if proper else word)
    normalized = {w for w in normalized if not (w[:1].isupper() and w.casefold() in normalized)}
    bases = []
    for word in sorted(normalized, key=lambda w: (len(w), w)):
        if pn._base_of(word, bases) is None:
            bases.append(word)
    return {('cw:' if word.islower() else 'pn:') + f'{i:04d}': word for i, word in enumerate(bases)}


def source_inflections(texts, names):
    """Carry the decided base vowel into exact surname forms missing ENDINGS.

    In particular, Russian surname instrumental -ым is adjective-like. The
    shared noun-ending table intentionally stays unchanged; this book-local
    lexicon retains the printed form and adds only the base's stressed vowel
    ordinal. A final base vowel may change with its case ending.
    """
    from rimsky import proper_noun_stress as pn
    from rimsky.name_stress import build, VOWELS, ACUTE
    forms = build(names, implicit_single=True)
    bases = {}
    for cid, marked in names.items():
        for word in re.findall(r'[А-Яа-яЁё́]+', marked):
            plain = word.replace(ACUTE, '')
            positions = [i for i, ch in enumerate(plain) if ch in VOWELS]
            index = word.index(ACUTE)-1 if ACUTE in word else next((i for i,ch in enumerate(plain) if ch in 'ёЁ'), None)
            if index is None and len(positions) == 1:
                index = positions[0]
            if index is not None:
                bases[plain.casefold()] = (cid, plain, index)
    extra, evidence = {}, []
    for text in texts:
        for match in re.finditer(r'[А-ЯЁ][а-яё]+', text):
            word = match[0]
            if word.casefold().replace('ё', 'е') in forms:
                continue
            for parsed in pn._morph().parse(word):
                tags = set(str(parsed.tag).replace(',', ' ').split())
                base = bases.get(parsed.normal_form.casefold())
                if base is None or 'sing' not in parsed.tag or not tags & {'Name','Surn','Patr','Geox'}:
                    continue
                cid, plain, index = base
                base_vowels = [i for i, ch in enumerate(plain) if ch in VOWELS]
                form_vowels = [i for i, ch in enumerate(word) if ch in VOWELS]
                ordinal = base_vowels.index(index)
                target = form_vowels[ordinal] if ordinal < len(form_vowels) else None
                # Morphology already bound this exact form to the decided
                # base. Require its stem before the stressed vowel unchanged;
                # the final inflection vowel itself can be different.
                if (target is None or word[:target].casefold() != plain[:index].casefold()
                        or word[target].casefold() != plain[index].casefold() and index != len(plain)-1):
                    raise ValueError('Inflection loses the base stressed vowel: ' + word)
                marked = word if len(form_vowels) < 2 or word[target] in 'ёЁ' else word[:target+1] + ACUTE + word[target+1:]
                key = 'pn-form:' + word
                if key not in extra:
                    extra[key] = marked
                    evidence.append({'source_form': word, 'base_id': cid, 'base': plain,
                                     'base_stressed_vowel_index': index, 'stressed_vowel_index': target,
                                     'stressed_vowel_ordinal': ordinal, 'synthesis_form': marked,
                                     'rule': 'source-form-inherits-base-stress-v2'})
                break
    return extra, evidence


def russian_pronunciation(folder, assigned, units, book):
    from rimsky import name_stress_analysis as ns
    from rimsky.proper_noun_stress import RULE_V2
    from rimsky.auto_pronunciation import auto_pronunciation
    from rimsky import homograph_stress as hs
    from prepare_public_audiobook_demo import call
    import build_homograph_stress as builder
    out = folder / 'pronunciation'
    texts = [b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    characters = name_candidates(texts, assigned['cast'])
    request = ns.request(characters, texts)
    validate_names = ns.validator(characters)

    def validate(answer):
        validate_names(answer)
        if len(answer['names']) != len(characters) or len({r['character_id'] for r in answer['names']}) != len(characters):
            raise ValueError('Each proper-noun base must appear once')
        for row in answer['names']:
            if row['stressed'].replace('\u0301', '') != row['name']:
                raise ValueError('Name stress may not respell a source name')

    names_answer = call(out, 'name-stress', ns.SYSTEM, request, ns.SCHEMA, validate,
                        provider='claude_cli', model='claude-opus-5-5')
    names = ns.lexicon(names_answer)
    # The shared proper-noun candidate/family policy collected every base before
    # this single Opus request; no later per-form stress guess replaces it.
    extra, inflections = source_inflections(texts, names)
    names.update(extra)
    common = ns.common_words(names, texts)
    immutable(out / 'name-lexicon.json', {'names': names, 'common': common, 'proper_noun_rule': RULE_V2,
                                       'inflected_forms': inflections,
                                       'provider': 'claude_cli', 'model': 'claude-opus-5-5', 'effort': 'high'})
    inputs = [dict(text=u['spoken'], names=names, common=common, implicit_single=True) for u in units]
    rows_path = out / 'homograph-rows.json'
    if rows_path.exists():
        rows = read(rows_path)
    else:
        rows = hs.disagreements(inputs)
        immutable(rows_path, rows)
    request = hs.request(rows)
    binding = dict(rule=hs.RULE, request=request, inputs=inputs,
                   provider='claude_cli', model='claude-opus-5-5', effort='high',
                   ruaccent_model=hs.OMOGRAPH_MODEL,
                   ruaccent_models_sha256sums=file_digest(hs.MODEL_DIR / 'SHA256SUMS.txt'))
    immutable(out / 'homograph.request.json', binding)
    builder.CLAUDE_ONLY = False
    batches = builder.adjudicate(out, rows, request)
    if any(b['metadata'].get(k) != value for b in batches for k, value in
           [('provider', 'claude_cli'), ('model', 'claude-opus-5-5'), ('effort', 'high')]):
        raise ValueError('Homograph adjudicator differs from pinned Opus high')
    answer = {'decisions': [d for batch in batches for d in batch['answer']['decisions']]}
    hs.validate(answer, request)
    answer_path = out / 'homograph.answer.json'
    immutable(answer_path, dict(rule=hs.RULE, request_sha256=file_digest(out / 'homograph.request.json'),
                               answer=answer, overrides=hs.overrides(rows, answer),
                               metadata={'batches': [b['metadata'] for b in batches]}, human_approval=False))
    block = hs.block(answer_path)
    for unit in units:
        synthesis, record = auto_pronunciation(unit['spoken'], names, common=common,
                                              rare_words=True, implicit_single=True, homograph=block)
        validate_pronunciation(unit['spoken'], synthesis)
        if (record or {}).get('homograph_overrides', {}).get('unbound'):
            raise ValueError('Unbound homograph override: ' + unit['id'])
        unit['synthesis_text'] = synthesis
        unit['pronunciation'] = record
    return {'name_lexicon_sha256': file_digest(out / 'name-lexicon.json'),
            'homograph_answer_sha256': file_digest(answer_path), 'proper_noun_rule': RULE_V2,
            'policy': 'stress-rare-v3', 'scope': 'Text preparation only; audio verification remains required'}


def pronunciation(folder):
    assigned_path = folder / 'analysis/assigned.json'
    if not assigned_path.exists():
        raise FileNotFoundError('PENDING analysis/assigned.json: ' + str(folder))
    assigned = read(assigned_path)
    book = verify_assignment_source(folder, assigned)
    out = folder / 'pronunciation'
    binding = {'schema': 1, 'assigned_sha256': file_digest(assigned_path),
               'book_sha256': file_digest(folder / 'prepared/book.json'),
               'implementation_sha256': file_digest(Path(__file__))}
    immutable(out / 'binding.json', binding)
    result_path = out / 'result.json'
    if result_path.exists():
        result = read(result_path)
        for relative, checksum in result['evidence_sha256'].items():
            if file_digest(folder / relative) != checksum:
                raise ValueError('Pronunciation evidence changed: ' + relative)
        immutable(folder / 'assignment.json', result['assignment'])
        return result['assignment']
    units = copy.deepcopy(assigned['units'])
    structure = attach_structure(folder, units, book)
    for unit in units:
        if assigned['language'] == 'English':
            spoken, conversion = english_numbers(unit['text'])
            policy = NUMBER_POLICY + '+' + ABBREVIATION_POLICY
        else:
            from rimsky.spoken_numbers import expand, POLICY_V3
            spoken = expand(unit['text'], policy=POLICY_V3)
            # A whole-unit record retains the exact before/after of the shared
            # Russian expansion; punctuation cleanup only collapses whitespace.
            conversion = [] if spoken == unit['text'] else [dict(source=unit['text'], spoken=spoken, rule=POLICY_V3)]
            policy = POLICY_V3
        unit['spoken'] = re.sub(r'\s+', ' ', spoken).strip()
        unit['spoken_conversion'] = {'policy': policy, 'source': unit['text'], 'expanded': spoken,
                                     'changes': conversion, 'whitespace': 'collapse-and-strip'}
    details = russian_pronunciation(folder, assigned, units, book) if assigned['language'] == 'Russian' else {'policy': NUMBER_POLICY}
    assignment = {**assigned, 'units': units, 'pronunciation': details,
                  'assigned_sha256': binding['assigned_sha256'], 'effects_policy': 'disabled',
                  'analysis': {'speech_structure': structure}}
    evidence = {p.relative_to(folder).as_posix(): file_digest(p) for p in sorted(out.rglob('*.json'))
                if p != result_path}
    for name in ('structure.request.json', 'structure.answer.json', 'assigned.json'):
        evidence['analysis/' + name] = file_digest(folder / 'analysis' / name)
    immutable(result_path, {'schema': 1, 'assignment': assignment, 'evidence_sha256': evidence})
    immutable(folder / 'assignment.json', assignment)
    return assignment


def freeze(folder, music_path=None):
    from rimsky.production_higgs_model import resolve_production_higgs_model
    from rimsky.opening import load_opening_pack, MANIFEST_SHA256
    from rimsky.higgs_reference import check_reference
    assignment = read(folder / 'assignment.json')
    result = read(folder / 'pronunciation/result.json')
    if result['assignment'] != assignment:
        raise ValueError('Assignment differs from completed pronunciation checkpoint')
    binding = read(folder / 'pronunciation/binding.json')
    # Completed preparation is immutable evidence from the recorded version.
    # A later freeze may consume it without rerunning pronunciation; a request
    # to rerun pronunciation still checks the current implementation binding.
    if not re.fullmatch('[a-f0-9]{64}', binding['implementation_sha256']):
        raise ValueError('Missing pronunciation implementation provenance')
    for relative, checksum in result['evidence_sha256'].items():
        if file_digest(folder / relative) != checksum:
            raise ValueError('Pronunciation evidence changed: ' + relative)
    assigned = read(folder / 'analysis/assigned.json')
    verify_assignment_source(folder, assignment)
    if assignment['assigned_sha256'] != file_digest(folder / 'analysis/assigned.json'):
        raise ValueError('Pronunciation no longer binds to analysis')
    for original, unit in zip(assigned['units'], assignment['units'], strict=True):
        if original['id'] != unit['id'] or original['text'] != unit['text']:
            raise ValueError('Pronunciation reordered or changed source units')
        validate_pronunciation(unit['spoken'], unit.get('synthesis_text', unit['spoken']))
        ref = unit['palette_reference']
        if ref.get('reviewed') is not True or ref.get('voice_family') != unit['voice'] or ref.get('mood') != unit['mood']:
            raise ValueError('Missing matching reviewed voice/mood reference')
        check_reference(Path(ref['path']), ref['audio_sha256'])
    music_path = Path(music_path) if music_path else folder / 'music/selected.json'
    if not music_path.is_file():
        raise FileNotFoundError('PENDING verified music selection; no manifest frozen: ' + str(music_path))
    music = read(music_path)
    if not music.get('bed') or not music.get('ending'):
        raise ValueError('Verified music selection requires both bed and ending')
    from render_public_audiobook_demo import music_asset
    music_asset(music['bed'], 'bed')
    music_asset(music['ending'], 'ending')
    pin = resolve_production_higgs_model(ROOT)
    brand, brand_folder = load_opening_pack(ROOT / 'resources')
    key = {'Russian': 'ru', 'English': 'en'}[assignment['language']]
    opening = brand['languages'][key]['variants']['music']
    manifest = {'schema': 1, 'purpose': 'Complete public-domain audiobook demonstration',
                'assignment_sha256': file_digest(folder / 'assignment.json'),
                'pronunciation_sha256': file_digest(folder / 'pronunciation/result.json'),
                'pronunciation_implementation_sha256': binding['implementation_sha256'],
                'freeze_implementation_sha256': file_digest(Path(__file__)),
                'effects_policy': 'disabled', 'seed': 202610230,
                'speech_model': {'checkpoint': pin.checkpoint, 'path': str(pin.path), 'sha256': pin.sha256,
                                 'manifest_sha256': file_digest(pin.manifest_path),
                                 'provenance_sha256': file_digest(pin.provenance_path), 'provenance': pin.provenance},
                'runner': {'path': str(RUNNER), 'sha256': file_digest(RUNNER),
                           'dlls': {p.name: file_digest(p) for p in sorted(RUNNER.parent.glob('*.dll'))},
                           'backend': 'cuda', 'device': 0, 'threads': 4},
                'sampling': SAMPLING,
                'music': music, 'music_selection_sha256': file_digest(music_path),
                'asr_model_path': str(ROOT / 'training-work/models/Qwen3-ASR-1.7B'),
                'ffmpeg': 'C:/ffmpeg/bin/ffmpeg.exe',
                'opening': {'sha256': opening['audio_hash'], 'language': assignment['language'],
                            'manifest_sha256': MANIFEST_SHA256, 'variant': 'music',
                            'path': str(brand_folder / opening['file']), 'music_policy': 'always_musical_branding'}}
    immutable(folder / 'manifest.json', manifest)
    print('FROZEN', folder.name, len(assignment['units']), 'units', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--stage', choices=['pronunciation', 'freeze', 'all'], default='all')
    parser.add_argument('--music-path', type=Path, help='Verified music selection (default: --folder/music/selected.json)')
    args = parser.parse_args()
    folder = args.folder.resolve()
    try:
        if args.stage in ('pronunciation', 'all'):
            pronunciation(folder)
        if args.stage in ('freeze', 'all'):
            freeze(folder, args.music_path.resolve() if args.music_path else None)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
