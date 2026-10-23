"""Milestone 1 of the automatic-assignment plan: offline preparation only.

Builds, in one checksum-pinned folder:
  evidence-manifest.json    hashes of every input, including referenced audio
  assignment-evidence.jsonl explicit human choices with text context (Books 1-2)
  synthesis-evidence.jsonl  the subset that passes the existing audio/text gates
  split.json                sealed chapter-separated development/test split
  character-policies.json   per-character delivery policies from DEVELOPMENT chapters only
  coverage.json             evidence per character, voice and mood, with split gaps
  scorecard.json            fresh offline baselines on the sealed test chapters
  codex-run-plan.json       proposed online payloads and their volume (nothing is sent)

Speaker labels in the corpus are model proposals, so no speaker score here is
ground truth; the historical frozen speaker evaluation is quoted separately.
Book 3 is never used for policies or training; it appears only in the run plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]
from build_mood_rulebook import ENTRIES, display_names  # noqa: E402

BOOKS = ('reality-benders-1', 'reality-benders-2-full')
BASELINES = ('calm', 'neutral')
SOURCES = {'reality-benders-1': 'book1-source/speech-inputs.jsonl',
           'reality-benders-2-full': ROOT / 'training-work/character-rb2/full/source/speech-inputs.jsonl'}
BOOK3 = ROOT / 'training-work/character-rb3/complete/source'
PINNED = ['manual-labeling/reality-benders-1/review.json', 'manual-labeling/reality-benders-1/latest.json',
          'manual-labeling/reality-benders-1/archive-manifest.json', 'manual-labeling/reality-benders-1/metadata/candidates.jsonl',
          'manual-labeling/reality-benders-2-full/review.json', 'manual-labeling/reality-benders-2-full/latest.json',
          'manual-labeling/reality-benders-2-full/archive-manifest.json', 'manual-labeling/reality-benders-2-full/metadata/split-plan.json',
          'manual-labeling/reality-benders-2-full/metadata/speaker-overrides.json',
          'training-work/character-rb2-full/fine-tune-all/candidates.jsonl', 'training-work/character-rb2/full/context.json',
          'training-work/character-rb2/full/clip-asr-audit.jsonl', 'training-work/character-rb1/fine-tune-all/precision-v2/status.json',
          'training/voice_families.json', 'training-work/character-rb1/character-reference-coverage.json',
          'content/reality-benders-1.epub', 'content/reality-benders-2.epub', 'content/reality-benders-3.epub',
          'training-work/character-rb3/complete/source/speech-inputs.jsonl',
          'training-work/speaker-resolution-eval-qwen35-v1/report.json']
TEST_SHARE = .2
SEARCH = 4000
SEED = 20261012


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, value) -> str:
    # Exact bytes: text mode on Windows would turn LF into CRLF and break the pinned hash.
    data = (json.dumps(value, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_jsonl(path: Path, rows) -> str:
    data = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8')
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- evidence

def performer_names() -> dict:
    path = ROOT / 'training-work/character-rb1/character-reference-coverage.json'
    found = {}

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('id'), str) and isinstance(value.get('name'), str):
                found[value['id']] = value['name']
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(json.loads(path.read_text(encoding='utf-8')))
    return found


def performer(unit: dict, names: dict) -> str:
    """Series-level performer name: the narrator, a named character, or unknown."""
    if (unit.get('voice') or '').startswith('narrator') or unit.get('role_proposed') == 'narration' and not unit.get('speaker'):
        return 'narrator'
    speaker = unit.get('speaker')
    if not speaker or speaker == 'unknown':
        return 'unknown'
    return names.get(speaker, speaker)


# Canonical identities. Each merge records the evidence that the names denote one character.
IDENTITIES = {
    'Комар': {'members': ['Комар', 'Кирилл Комаров'],
              'basis': 'Book 2 cast lists alias «Кирилл» for Комар, the first-person narrator; Book 1 cast names him '
                       'Кирилл Комаров; human voice labels agree (masculine_4: 205/233 and 69/69). His narration stays '
                       'the separate narrator identity.'},
    'Фокси': {'members': ['Фокси', 'Морф (Фокси)'],
              'basis': 'Both Book 2 cast entries describe the morph companion of Комар («морф», «лисичка»); human voice '
                       'labels agree (feminine_3: 34/41 and 40/41). The «Айни» alias on the Фокси entry is real: in '
                       'later chapters the morph poses as the translator «Айни».'},
}
# Names that denote different characters in different chapters stay separate and are never merged.
AMBIGUOUS = {
    'Айни': 'Means the Myelon translator Айни Ури-Миайуу on the station (c0024), but the morph Фокси posing as the '
            'translator in c0030 («не унимался морф» … «Айни поняла»). Kept as its own identity; never merged.',
}
IDENTITIES |= {
    'Тамара': {'members': ['Тамара', 'герд Тамара'],
               'basis': 'Book 2 cast lists «герд Тамара» as an alias of Тамара, leader of the Second Legion; Book 1 cast '
                        'names her «герд Тамара»; human voice labels agree (feminine_2: 32/39 and 29/29).'},
}
CANONICAL = {member: name for name, item in IDENTITIES.items() for member in item['members']}


def route(unit: dict) -> str:
    """Presentation route: how the text is delivered, judged from the human voice label when present."""
    voice = (unit.get('voice') or '') if unit.get('voice_basis') == 'human' else ''
    if voice == 'narrator_opening':
        return 'opening'
    if voice == 'narrator_historical_prose':
        return 'historical_prose'
    if voice.startswith('narrator'):
        return 'narration'
    if 'mechanical' in voice:
        return 'mechanical'
    if voice and voice != 'voice_pending':
        return 'dialogue'
    return {'narration': 'narration', 'game_ui': 'mechanical'}.get(unit.get('role_proposed'), 'dialogue')


def unresolved_part(unit: dict, who: str) -> dict:
    return {'id': unit['id'], 'book': unit['book'], 'chapter_id': unit['chapter_id'], 'source_id': unit['source_id'],
            'unit': unit['unit'], 'parent_id': unit.get('parent_id'), 'text': unit['text'], 'paragraph': unit.get('paragraph'),
            'before': unit.get('before'), 'after': unit.get('after'), 'role_proposed': unit.get('role_proposed'),
            'performer': who, 'identity': 'unresolved_part', 'route': 'unresolved', 'speaker': unit.get('speaker'),
            'speaker_basis': unit.get('speaker_basis'), 'voice': unit.get('voice'), 'voice_basis': unit.get('voice_basis'),
            'mood': unit['mood'], 'mood_basis': 'human', 'origin': unit.get('origin'),
            'boundary_held': bool(unit.get('boundary_held')), 'audio_sha256': unit.get('audio_sha256')}


def neighbour_text(path: Path) -> dict:
    """Previous and next paragraph text per source paragraph, in reading order."""
    paragraphs, order = {}, []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            item = json.loads(line)
            if item['source_id'] not in paragraphs:
                order.append(item['source_id'])
                paragraphs[item['source_id']] = ''
            paragraphs[item['source_id']] += item['text']
    return {sid: (paragraphs[order[n - 1]] if n else None, paragraphs[order[n + 1]] if n + 1 < len(order) else None)
            for n, sid in enumerate(order)}


def evidence_rows(explicit: dict, names: dict, book1_context: dict | None = None) -> list[dict]:
    rows = []
    for unit in explicit['units']:
        if unit['book'] not in BOOKS:
            continue  # the original Book 2 pilot overlaps the full edition
        who = performer(unit, names)
        if unit['unit'] == 'split_part' and unit.get('voice_basis') != 'human':
            # An untouched prefilled part voice says nothing about who performs the part.
            rows.append(unresolved_part(unit, who))
            continue
        presented = route(unit)
        identity = 'narrator' if presented in ('narration', 'opening', 'historical_prose') else CANONICAL.get(who, who)
        if identity == 'narrator' and who not in ('narrator', 'unknown'):
            identity = CANONICAL.get(who, who)  # a character's line the listener voiced as narration
        before, after = unit.get('before'), unit.get('after')
        if unit['book'] == 'reality-benders-1' and book1_context and before is None and after is None:
            # The Book 1 export kept no neighbours; take them from the verified Book 1 source import.
            before, after = book1_context.get(unit['source_id'], (None, None))
        rows.append({'id': unit['id'], 'book': unit['book'], 'chapter_id': unit['chapter_id'], 'source_id': unit['source_id'],
                     'unit': unit['unit'], 'parent_id': unit.get('parent_id'), 'text': unit['text'],
                     'paragraph': unit.get('paragraph'), 'before': before, 'after': after,
                     'role_proposed': unit.get('role_proposed'), 'performer': who,
                     'identity': identity, 'route': presented,
                     'speaker': unit.get('speaker'), 'speaker_basis': unit.get('speaker_basis'),
                     'voice': unit.get('voice'), 'voice_basis': unit.get('voice_basis'),
                     'mood': unit['mood'], 'mood_basis': 'human', 'origin': unit.get('origin'),
                     'boundary_held': bool(unit.get('boundary_held')), 'audio_sha256': unit.get('audio_sha256')})
    return rows


def synthesis_gate(rows: list[dict]) -> tuple[list[dict], Counter]:
    """Clips that already pass the existing exact-text and audio gates. Split parts
    still need independent edge checks, so none qualify yet."""
    status = json.loads((ROOT / 'training-work/character-rb1/fine-tune-all/precision-v2/status.json').read_text(encoding='utf-8'))['clips']
    audit = {}
    for line in (ROOT / 'training-work/character-rb2/full/clip-asr-audit.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            item = json.loads(line)
            audit[item['id']] = item
    kept, reasons = [], Counter()
    for row in rows:
        why = None
        if row['unit'] != 'clip':
            why = 'split part awaiting independent edge check'
        elif row['boundary_held']:
            why = 'boundary hold'
        elif row['voice_basis'] != 'human':
            why = 'voice not human-confirmed'
        elif row['book'] == 'reality-benders-1' and (status.get(row['id']) or {}).get('status') != 'precision_pass':
            why = 'Book 1 precision gate not passed'
        elif row['book'] == 'reality-benders-2-full' and not (
                (audit.get(row['id']) or {}).get('status') == 'exact_asr_match'
                and audit[row['id']].get('audio_sha256') == row['audio_sha256']):
            why = 'Book 2 exact ASR gate not passed for this audio'
        reasons[why or 'passes'] += 1
        if why is None:
            kept.append(row)
    return kept, reasons


def manifest(folder: Path, rows: list[dict]) -> dict:
    files = {path: sha(ROOT / path) for path in PINNED if (ROOT / path).is_file()}
    missing_inputs = [path for path in PINNED if not (ROOT / path).is_file()]
    audio, missing_audio = {}, []
    for row in rows:
        if row['book'] == 'reality-benders-1':
            path = ROOT / 'manual-labeling/reality-benders-1/audio' / f"{row['audio_sha256']}.wav"
        elif row['unit'] == 'clip':
            path = ROOT / 'training-work/character-rb2/full/clips' / f"{row['id']}.wav"
        else:
            continue
        if not path.is_file():
            missing_audio.append(row['id'])
            continue
        actual = sha(path)
        audio[row['id']] = actual
        if actual != row['audio_sha256']:
            missing_audio.append(row['id'] + ' (hash differs)')
    combined = hashlib.sha256(json.dumps(audio, sort_keys=True).encode()).hexdigest()
    return {'inputs': files, 'missing_inputs': missing_inputs, 'audio_files_verified': len(audio),
            'audio_problems': missing_audio, 'audio_combined_sha256': combined,
            'book1_source': {path.name: sha(path) for path in (folder / 'book1-source').glob('*.json*')}}


# ------------------------------------------------------------------- split

def split(rows: list[dict]) -> dict:
    """Pick ~20% of each book's chapters as test, maximising classes seen on both sides."""
    rng = random.Random(SEED)
    sealed = {}
    for book in BOOKS:
        units = [row for row in rows if row['book'] == book]
        by_chapter = defaultdict(list)
        for row in units:
            by_chapter[row['chapter_id']].append(row)
        chapters = sorted(by_chapter)
        keys = lambda row: [('mood', row['mood']), ('voice', row['voice']), ('identity', row['identity']), ('route', row['route'])]
        totals = Counter(key for row in units for key in keys(row))
        classes = [key for key, n in totals.items() if n >= 4]
        mood_share = Counter(row['mood'] for row in units)
        best = None
        for _ in range(SEARCH):
            size = max(1, round(len(chapters) * TEST_SHARE)) + rng.choice((-1, 0, 0, 1))
            test = set(rng.sample(chapters, size))
            in_test = Counter(key for ch in test for row in by_chapter[ch] for key in keys(row))
            both = sum(1 for key in classes if 0 < in_test[key] < totals[key])
            count = sum(len(by_chapter[ch]) for ch in test)
            share = count / len(units)
            test_moods = Counter(row['mood'] for ch in test for row in by_chapter[ch])
            drift = sum(abs(test_moods[m] / max(count, 1) - mood_share[m] / len(units)) for m in mood_share) / 2
            score = both - 40 * abs(share - TEST_SHARE) - 20 * drift
            if best is None or score > best[0]:
                best = (score, sorted(test), both, share, drift)
        _, test, both, share, drift = best
        gaps = sorted(f'{kind}:{name}' for kind, name in classes
                      if not 0 < sum(1 for ch in test for row in by_chapter[ch] if (kind, name) in keys(row)) < totals[(kind, name)])
        sealed[book] = {'test_chapters': test, 'development_chapters': sorted(set(chapters) - set(test)),
                        'test_units': sum(len(by_chapter[ch]) for ch in test), 'units': len(units),
                        'test_share': round(share, 3), 'mood_distribution_drift': round(drift, 3),
                        'classes_on_both_sides': both, 'classes_considered': len(classes), 'gaps': gaps}
    return {'schema': 1, 'seed': SEED, 'search': SEARCH, 'target_test_share': TEST_SHARE,
            'rule': 'Chapters, not clips, are separated. Policies and few-shot examples may use development chapters only.',
            'books': sealed}


def side(row: dict, sealed: dict) -> str:
    """Test, development, or held (e.g. calibration chapters a later split reserves from both)."""
    book = sealed['books'][row['book']]
    if row['chapter_id'] in book['test_chapters']:
        return 'test'
    return 'development' if row['chapter_id'] in book['development_chapters'] else 'held'


# ---------------------------------------------------------------- policies

def policies(rows: list[dict]) -> dict:
    """Machine-readable per-performer delivery policies from development rows only."""
    names = display_names()
    by_performer = defaultdict(list)
    for row in rows:
        by_performer[(row['identity'], row['route'])].append(row)
    out = {}
    for (who, presented), items in sorted(by_performer.items(), key=lambda item: -len(item[1])):
        if who == 'unresolved_part':
            continue
        moods = Counter(row['mood'] for row in items)
        voiced = Counter(row['voice'] for row in items if row['voice_basis'] == 'human' and row['voice'])
        top_voice, top_n = voiced.most_common(1)[0] if voiced else (None, 0)
        purity = top_n / sum(voiced.values()) if voiced else 0
        base = max(BASELINES, key=lambda mood: (moods[mood], mood == 'calm'))
        departures = []
        for mood, n in moods.most_common():
            if mood in BASELINES or n < 3:
                continue
            examples = [row for row in items if row['mood'] == mood]
            text = [row['text'].rstrip() for row in examples]
            departures.append({'mood': mood, 'name': names.get(mood, mood), 'examples': n,
                               'share': round(n / len(items), 3), 'meaning': ENTRIES[mood][0],
                               'boundary': {'versus': ENTRIES[mood][1], 'rule': ENTRIES[mood][2]},
                               'text_signals': {'ends_question': round(sum(t.endswith('?') for t in text) / n, 2),
                                                'ends_exclamation': round(sum(t.endswith('!') for t in text) / n, 2),
                                                'ends_ellipsis': round(sum(t.endswith(('…', '...')) for t in text) / n, 2)},
                               'example_ids': [row['id'] for row in examples[:5]]})
        notes = []
        if len(items) < 10:
            notes.append('sparse: fewer than 10 development choices')
        if voiced and purity < .8:
            notes.append(f'voice split across {dict(voiced.most_common(3))}')
        rare = sorted(mood for mood, n in moods.items() if mood not in BASELINES and n < 3)
        if rare:
            notes.append('moods seen fewer than 3 times: ' + ', '.join(rare))
        out.setdefault(who, {'routes': {}})['routes'][presented] = {'development_choices': len(items),
                    'voice': {'frozen': top_voice if top_n >= 3 and purity >= .8 else None, 'purity': round(purity, 3),
                              'human_voice_choices': sum(voiced.values()), 'distribution': dict(voiced.most_common(4))},
                    'baseline_mood': base, 'baseline_share': round((moods['calm'] + moods['neutral']) / len(items), 3),
                    'mood_distribution': dict(moods.most_common()), 'departures': departures,
                    'evidence_limits': notes}
    return {'schema': 2, 'built_from': 'development chapters only', 'identities': IDENTITIES, 'ambiguous_names': AMBIGUOUS,
            'route_rule': 'Route comes from the human voice label (narration, opening, historical_prose, mechanical, '
                          'dialogue), else the proposed role. Moods are judged within a route.',
            'performers': out,
            'delivery_definitions': {mood: {'name': names.get(mood, mood), 'meaning': meaning, 'versus': rival, 'boundary': rule}
                                     for mood, (meaning, rival, rule) in ENTRIES.items()}}


# ---------------------------------------------------------- coverage, score

def coverage(rows: list[dict], sealed: dict) -> dict:
    table = {}
    for kind in ('identity', 'route', 'voice', 'mood'):
        counts = defaultdict(Counter)
        for row in rows:
            counts[row[kind] or 'unresolved'][side(row, sealed)] += 1
        table[kind] = {name: {'development': c['development'], 'test': c['test']}
                       for name, c in sorted(counts.items(), key=lambda item: -sum(item[1].values()))}
    test_only = {kind: sorted(name for name, c in table[kind].items() if c['test'] and not c['development']) for kind in table}
    dev_only = {kind: sorted(name for name, c in table[kind].items() if c['development'] and not c['test']) for kind in table}
    return {'by': table, 'only_in_test': test_only, 'only_in_development': dev_only}


def wilson(hits: int, total: int) -> list[float] | None:
    if not total:
        return None
    z, p = 1.96, hits / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0., centre - half), 3), round(min(1., centre + half), 3)]


def scorecard(test: list[dict], policy: dict) -> dict:
    performers = policy['performers']
    lookup = lambda row: ((performers.get(row['identity']) or {}).get('routes') or {}).get(row['route']) or {}
    voiced = [row for row in test if row['voice_basis'] == 'human' and row['voice']]
    predicted = []
    for row in voiced:
        frozen = lookup(row).get('voice', {}).get('frozen')
        if frozen:
            predicted.append((row, frozen))
    voice_hits = sum(row['voice'] == guess for row, guess in predicted)
    voice = {'method': 'frozen development voice per identity and route; abstain otherwise. The route is taken from the '
                       'human test label here, so this measures voice routing given a correct route, not route detection.',
             'scored_against': 'human voice choices in test chapters', 'test_rows': len(voiced),
             'accepted': len(predicted), 'coverage': round(len(predicted) / len(voiced), 3),
             'precision': round(voice_hits / len(predicted), 3), 'precision_95ci': wilson(voice_hits, len(predicted)),
             'errors': dict(Counter(f"{row['identity']}/{row['route']}: {guess} -> {row['voice']}" for row, guess in predicted
                                    if row['voice'] != guess).most_common(8))}
    expressive = [row for row in test if row['mood'] not in BASELINES]
    methods = {'always_calm': lambda row: 'calm', 'always_normal': lambda row: 'neutral',
               'performer_baseline': lambda row: lookup(row).get('baseline_mood', 'calm'),
               'performer_majority': lambda row: next(iter(lookup(row).get('mood_distribution') or {'calm': 1}))}
    mood = {}
    for name, guess in methods.items():
        hits = sum(guess(row) == row['mood'] for row in test)
        expressive_hits = sum(guess(row) == row['mood'] for row in expressive)
        per = {}
        for who in ('narrator', *[p for p, _ in Counter(row['identity'] for row in test).most_common(6) if p != 'narrator']):
            rows = [row for row in test if row['identity'] == who]
            if rows:
                per[who] = {'rows': len(rows), 'exact': round(sum(guess(row) == row['mood'] for row in rows) / len(rows), 3)}
        mood[name] = {'exact_agreement': round(hits / len(test), 3), 'exact_95ci': wilson(hits, len(test)),
                      'expressive_rows': len(expressive), 'expressive_recall': round(expressive_hits / len(expressive), 3),
                      'missed_expression': round(1 - expressive_hits / len(expressive), 3), 'per_performer': per}
    return {'kind': 'fresh offline baselines on sealed test chapters; floors the automatic assigner must beat',
            'voice': voice, 'mood': mood,
            'speaker': {'kind': 'historical, not fresh', 'note': 'Corpus speakers are model proposals, not ground truth. Quoted from the frozen speaker-resolution evaluation.',
                        'report': 'training-work/speaker-resolution-eval-qwen35-v1/report.json',
                        'codex_untouched_test': '896/912 spans', 'claude_untouched_test': '892/912 spans'}}


# ---------------------------------------------------------------- run plan

def run_plan(folder: Path, sealed: dict, policy: dict) -> dict:
    from rimsky.analysis import SYSTEM_PROMPT
    from rimsky.speech_structure import STRUCTURE_SYSTEM, needs_structure_analysis, speech_structure_request
    target, context = 2600, 2 * 2 * 400
    definitions = len(json.dumps(policy['delivery_definitions'], ensure_ascii=False))
    profile = sorted(len(json.dumps(item, ensure_ascii=False)) for identity in policy['performers'].values() for item in identity['routes'].values())
    per_profile = profile[len(profile) // 2] if profile else 0
    sources = {'reality-benders-1': folder / SOURCES['reality-benders-1'],
               'reality-benders-2-full': SOURCES['reality-benders-2-full'],
               'reality-benders-3': BOOK3 / 'speech-inputs.jsonl'}
    plan, sample = {}, None
    for book, path in sources.items():
        rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
        keep = (set(sealed['books'][book]['test_chapters']) if book in sealed['books'] else {row['chapter_id'] for row in rows})
        rows = [row for row in rows if row['chapter_id'] in keep]
        text = sum(len(row['text']) for row in rows)
        dashes = [row for row in rows if needs_structure_analysis(row['text'])]
        batches = max(1, math.ceil(text / target))
        passes = {
            'structure': {'requests': max(1, math.ceil(sum(len(r['text']) for r in dashes) / target)),
                          'input_characters': sum(len(r['text']) for r in dashes) + math.ceil(sum(len(r['text']) for r in dashes) / target) * (len(STRUCTURE_SYSTEM) + context)},
            'identity': {'requests': batches, 'input_characters': text + batches * (len(SYSTEM_PROMPT) + context + 40 * 150)},
            'critic': {'requests': batches, 'input_characters': text + batches * (len(SYSTEM_PROMPT) + context + 40 * 150)},
            'delivery': {'requests': batches, 'input_characters': text + batches * (definitions + 3 * per_profile + context)},
        }
        total = sum(item['input_characters'] for item in passes.values())
        plan[book] = {'chapters': len(keep), 'passages': len(rows), 'book_text_characters': text, 'passes': passes,
                      'input_characters': total, 'approx_input_tokens': round(total / 3.2),
                      'note': 'test chapters only' if book in sealed['books'] else 'all narration chapters'}
        if sample is None and dashes:
            request, schema = speech_structure_request({'segments': [{'id': dashes[0]['id'], 'text': dashes[0]['text']}]})
            sample = {'system': STRUCTURE_SYSTEM, 'request': request, 'schema': schema}
    write(folder / 'sample-structure-request.json', sample)
    total = sum(item['input_characters'] for item in plan.values())
    return {'schema': 1, 'provider': 'codex (production policy order: Codex, then Claude Code, then local)',
            'sends': 'Book text of the listed chapters with neighbouring context, the analysis instructions, delivery '
                     'definitions and development-only character policies. No audio and no human labels of test chapters.',
            'assumptions': {'characters_per_request': target, 'context_characters_per_request': context,
                            'registry_characters_per_request': 40 * 150, 'delivery_definitions_characters': definitions,
                            'median_profile_characters': per_profile, 'profiles_per_delivery_request': 3,
                            'characters_per_token': 3.2, 'adjudication': 'flagged cases only; not estimated'},
            'books': plan, 'input_characters': total, 'approx_input_tokens': round(total / 3.2),
            'sample_request': 'sample-structure-request.json'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    explicit = json.loads((folder / 'explicit-choices.json').read_text(encoding='utf-8'))
    rows = evidence_rows(explicit, performer_names(), neighbour_text(folder / SOURCES['reality-benders-1']))
    hashes = {'assignment-evidence.jsonl': write_jsonl(folder / 'assignment-evidence.jsonl', rows)}
    trained, reasons = synthesis_gate(rows)
    hashes['synthesis-evidence.jsonl'] = write_jsonl(folder / 'synthesis-evidence.jsonl', trained)
    # The split is sealed once: later identity or policy changes must not move test chapters.
    if (folder / 'split.json').is_file():
        sealed = json.loads((folder / 'split.json').read_text(encoding='utf-8'))
        hashes['split.json'] = sha(folder / 'split.json')
    else:
        sealed = split(rows)
        hashes['split.json'] = write(folder / 'split.json', sealed)
    development = [row for row in rows if side(row, sealed) == 'development']
    test = [row for row in rows if side(row, sealed) == 'test']
    policy = policies(development)
    hashes['character-policies.json'] = write(folder / 'character-policies.json', policy)
    hashes['coverage.json'] = write(folder / 'coverage.json', coverage(rows, sealed))
    hashes['scorecard.json'] = write(folder / 'scorecard.json', scorecard(test, policy))
    hashes['codex-run-plan.json'] = write(folder / 'codex-run-plan.json', run_plan(folder, sealed, policy))
    pinned = manifest(folder, rows)
    write(folder / 'evidence-manifest.json', {'schema': 1, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                              'script_sha256': sha(Path(__file__)), 'explicit_choices_sha256': sha(folder / 'explicit-choices.json'),
                                              **pinned, 'outputs': hashes, 'assignment_rows': len(rows),
                                              'synthesis_rows': len(trained), 'synthesis_gate': dict(reasons)})
    print(json.dumps({'assignment_rows': len(rows), 'synthesis_rows': len(trained), 'synthesis_gate': dict(reasons),
                      'audio_problems': len(pinned['audio_problems']), 'missing_inputs': pinned['missing_inputs'],
                      'split': {book: {k: v for k, v in item.items() if k in ('test_chapters', 'test_share', 'classes_on_both_sides', 'classes_considered')}
                                for book, item in sealed['books'].items()}}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
