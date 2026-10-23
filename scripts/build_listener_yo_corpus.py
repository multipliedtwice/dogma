"""Build the durable corpus of listener е/ё corrections (read-only on the listener database and generation folders).

Why: every listener correction about е against ё is evidence for the automatic ё layer
(runtime/rimsky/yo_restoration.py), not only a one-sentence audio retake. This script reads the immutable
`stress_reports` table of the listener library plus each generation's retake queue and writes:

* cases.jsonl     labelled cases, deterministic order (created, id):
                  listener_explicit              a stress report whose intended form differs from the printed word by
                                                 е->ё, or whose heard word had ё and the intended form has none;
                  listener_free_text_interpreted a free-text note with an agent-derived record (listener-stress-queue/derived);
                                                 the target form is an agent interpretation, the listener comment is verbatim;
* pending.jsonl   free-text notes that mention ё/е but have no derived record yet (never counted as labelled);
* conflicts.jsonl words on which notes disagree (excluded from cases);
* manifest.json   schema, build time, input and builder hashes, counts.

The database is the source of truth: the output folder is rewritten on every run.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sqlite3, sys, time, unicodedata
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from process_listener_stress_queue import _selection_matches
from rimsky.respelling import validate_respelled_stress
from rimsky.spoken_pronunciation import ACUTE, yo_fold
from rimsky.util import write_json

SCHEMA = 1
DEFAULT_DATABASE = ROOT/'training-work/elirm-iv-higgs-mvp/production-listener-library/library.sqlite3'
DEFAULT_OUTPUT = ROOT/'manual-labeling/listener-yo-corrections'
FREE_TEXT = ('other', 'pronunciation')
# A note is about е/ё when it names ё, a lone е, or the все/всё pair.
MENTION = re.compile(r'ё|(?<![\w])[еe](?![\w])|(?<![\w])вс[её](?![\w])', re.I)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):h.update(chunk)
    return h.hexdigest()


def read_json(path):
    try:return json.loads(Path(path).read_text('utf8'))
    except (OSError, ValueError):return None


def unmarked(text):
    return unicodedata.normalize('NFC', text).replace(ACUTE, '')


def open_readonly(path):
    db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def folders(db, workspace):
    """{generation_id: folder name or None} from analysis.external_preview.generation."""
    out = {}
    for row in db.execute('SELECT id, analysis FROM generations'):
        try:name = (json.loads(row['analysis'] or '{}').get('external_preview') or {}).get('generation')
        except ValueError:name = None
        out[row['id']] = name if isinstance(name, str) and name and (workspace/name).is_dir() else None
    return out


class Generation:
    """Lazy view of one external generation folder: policy, queue items, instructions, derived records."""
    def __init__(self, folder):
        self.folder = folder; self.queue = folder/'listener-stress-queue' if folder else None
        manifest = (read_json(folder/'manifest.json') if folder else None) or {}
        self.policy = (manifest.get('pronunciation') or {}).get('policy')
        self.items = {}
        if self.queue and self.queue.is_dir():
            for path in sorted(self.queue.glob('*.json')):
                item = read_json(path)
                if isinstance(item, dict):self.items[path.stem] = item

    def derived(self, report_id):
        data = read_json(self.queue/'derived'/f'{report_id}.json') if self.queue else None
        return data if isinstance(data, dict) and data.get('report_id') == report_id else None

    def instruction(self, report_id):
        data = read_json(self.queue/'instructions'/f'{report_id}.json') if self.queue else None
        return data if isinstance(data, dict) else None

    def instructions(self):
        if not hasattr(self, '_instructions'):
            folder = self.queue/'instructions' if self.queue else None
            self._instructions = [j for j in (read_json(p) for p in sorted(folder.glob('*.json'))) if isinstance(j, dict)] if folder and folder.is_dir() else []
        return self._instructions

    def selected(self, unit_id):
        """Units of the generation's published takes (c*/chunk-*/**/selected.json) with this id."""
        if not hasattr(self, '_selected'):
            self._selected = {}
            for path in sorted(self.folder.glob('c*/chunk-*/**/selected.json')) if self.folder else ():
                rows = read_json(path)
                for key, row in (rows.items() if isinstance(rows, dict) else ()):
                    if isinstance(row, dict) and isinstance(row.get('unit'), dict):self._selected.setdefault(key, []).append(row['unit'])
        return self._selected.get(unit_id, [])

    def applied(self, segment_id):
        return any(i.get('status') == 'applied' and (i.get('report') or {}).get('segment_id') == segment_id for i in self.items.values())

    def retake(self, report_id):
        """Queue item of the report itself (applied wins, otherwise the newest) as {status, replacement_audio_sha256}."""
        rows = [i for i in self.items.values() if (i.get('report') or {}).get('id') == report_id]
        if not rows:return None
        rows.sort(key=lambda i: (i.get('status') == 'applied', i.get('updated') or '', i.get('id') or ''))
        best = rows[-1]
        return {'status': best.get('status'), 'replacement_audio_sha256': best.get('replacement_audio_sha256')}


def heard_unit(row, generation, instruction):
    """(exact text Higgs heard, its source unit text, offset of that unit in the report's coordinates, evidence)."""
    def from_unit(unit):
        text = (unit.get('synthesis') or {}).get('text')
        return (text, unit['text'], unit.get('start', 0)) if unit.get('text') and isinstance(text, str) else None
    found = from_unit(((instruction or {}).get('before') or {}).get('unit') or {})
    if found:return (*found, 'instruction')
    for other in generation.instructions():
        unit = (other.get('before') or {}).get('unit') or {}
        if unit.get('id') == row['segment_id'] and (other.get('listener_report') or {}).get('audio_hash') == row['audio_hash'] and from_unit(unit):
            return (*from_unit(unit), 'instruction_same_audio')
    # The published take is the heard one unless a retake of this unit was already applied.
    if not generation.applied(row['segment_id']):
        takes = generation.selected(row['segment_id'])
        if len({t['synthesis']['text'] for t in takes if from_unit(t)}) == 1 and from_unit(takes[0]):
            return (*from_unit(takes[0]), 'selected_take')
    try:prompt = json.loads(row['synthesis_snapshot'] or 'null')['prompt']
    except (ValueError, TypeError, KeyError):prompt = None
    spoken = row['spoken_text'] or ''
    start = (row['passage_text'] or '').find(spoken) if spoken else -1
    if isinstance(prompt, str) and start >= 0:return prompt, spoken, start, 'snapshot'
    return None, None, None, None


def heard_word(row, heard, unit_text, unit_start):
    if heard is None:return None
    local = row['text_offset'] - unit_start
    index = next((n for n, (a, _) in enumerate(_selection_matches(unit_text, row['selected_text'])) if a == local), None)
    found = _selection_matches(heard, row['selected_text'])
    return None if index is None or index >= len(found) else heard[found[index][0]:found[index][1]]


def direction_of(target, selected, heard):
    target_yo = 'ё' in target.lower(); base = selected if heard is None else heard
    if target_yo and 'ё' not in base.lower():return 'missed_yo'
    if not target_yo and heard is not None and 'ё' in heard.lower():return 'false_yo'
    return None


def intended_of(row, generation):
    """(label_source, intended form, derived record) of a report that names a target form for its word, else None."""
    if row['kind'] == 'stress' and row['intended_stress']:
        try:substitutions = validate_respelled_stress(row['selected_text'], row['intended_stress'])
        except ValueError:return None
        if any(s['spoken'] not in 'ёЁ' for s in substitutions):return None
        return 'listener_explicit', row['intended_stress'], None
    if row['kind'] in FREE_TEXT:
        derived = generation.derived(row['id'])
        if derived is None or not derived.get('intended_stress'):return None
        intended = derived['intended_stress']
        if len(unmarked(intended)) != len(row['selected_text']) or yo_fold(unmarked(intended)).casefold() != yo_fold(row['selected_text']).casefold():return None
        return 'listener_free_text_interpreted', intended, derived
    return None


def candidate(row, generation, name):
    """The case of one report, or None when it carries no е/ё aspect."""
    found = intended_of(row, generation)
    if found is None:return None
    label, intended, derived = found
    target = unmarked(intended)
    instruction = generation.instruction(row['id'])
    heard, unit_text, unit_start, evidence = heard_unit(row, generation, instruction)
    word = heard_word(row, heard, unit_text, unit_start) if heard is not None else None
    way = direction_of(target, row['selected_text'], word)
    if way is None or (word is not None and unmarked(word) == target):return None
    instructed = evidence in ('instruction', 'instruction_same_audio', 'selected_take')
    case = {'id': row['id'], 'label_source': label, 'book_id': row['book_id'], 'generation_id': row['generation_id'],
            'generation': name, 'pronunciation_policy': generation.policy, 'chapter_id': row['chapter_id'], 'segment_id': row['segment_id'],
            'sentence': unit_text if instructed else row['passage_text'],
            'sentence_offset': row['text_offset'] - (unit_start if instructed else 0),
            'passage_text': row['passage_text'], 'selected_text': row['selected_text'], 'text_offset': row['text_offset'],
            'target_form': target, 'target_stressed': intended, 'direction': way,
            'heard_native_text': heard, 'heard_source': evidence, 'heard_word': word, 'audio_hash': row['audio_hash'], 'created': row['created'],
            'listener_comment': row['comment'] or '', 'retake': generation.retake(row['id'])}
    if derived is not None:
        case['listener_comment'] = derived.get('listener_comment', row['comment'] or '')
        case['interpretation'] = derived.get('interpretation', '')
        case['interpretation_basis'] = 'agent interpretation of free text; the target form is not a listener-typed form'
    return case


def build(database, output, workspace=None):
    database = Path(database); output = Path(output); workspace = Path(workspace) if workspace else database.parent.parent
    db = open_readonly(database)
    try:
        rows = db.execute('SELECT * FROM stress_reports ORDER BY created, id').fetchall()
        names = folders(db, workspace)
    finally:db.close()
    gens = {}
    for gid, name in names.items():gens[gid] = Generation(workspace/name if name else None)
    labelled = {}; targets = {}; pending = []; skipped = 0
    for row in rows:
        gen = gens.get(row['generation_id']) or Generation(None); name = names.get(row['generation_id'])
        named = intended_of(row, gen)
        if named:targets.setdefault((row['generation_id'], row['segment_id'], row['text_offset']), []).append((row, unmarked(named[1])))
        case = candidate(row, gen, name)
        if case:labelled.setdefault((row['generation_id'], row['segment_id'], row['text_offset']), []).append(case)
        elif row['kind'] in FREE_TEXT and gen.derived(row['id']):skipped += 1
    cases = []; conflicts = []
    for key, group in labelled.items():
        group.sort(key=lambda c: (c['created'], c['id']))
        # notes that name different forms for one word contradict each other, even when only one of them is an е/ё case
        if len({form for _, form in targets[key]}) > 1:
            conflicts.append({'generation_id': key[0], 'segment_id': key[1], 'text_offset': key[2], 'selected_text': group[-1]['selected_text'],
                              'reports': [{'id': r['id'], 'kind': r['kind'], 'created': r['created'], 'target_form': form, 'listener_comment': r['comment'] or ''}
                                          for r, form in sorted(targets[key], key=lambda t: (t[0]['created'], t[0]['id']))]})
            continue
        newest = dict(group[-1]); newest['duplicate_report_ids'] = [c['id'] for c in group[:-1]]
        cases.append(newest)
    by_key = {(c['generation_id'], c['segment_id'], c['text_offset']): c for c in cases}
    conflicted = {(c['generation_id'], c['segment_id'], c['text_offset']) for c in conflicts}
    for row in rows:
        if row['kind'] not in FREE_TEXT or not MENTION.search(row['comment'] or ''):continue
        key = (row['generation_id'], row['segment_id'], row['text_offset'])
        gen = gens.get(row['generation_id']) or Generation(None)
        if gen.derived(row['id']) or key in conflicted:continue
        if key in by_key:
            # the same word is already labelled by another note: keep this note as a duplicate, not as pending
            if row['id'] not in by_key[key]['duplicate_report_ids']:by_key[key]['duplicate_report_ids'].append(row['id'])
            continue
        pending.append({'id': row['id'], 'book_id': row['book_id'], 'generation_id': row['generation_id'], 'generation': names.get(row['generation_id']),
                        'kind': row['kind'], 'chapter_id': row['chapter_id'], 'segment_id': row['segment_id'], 'selected_text': row['selected_text'],
                        'text_offset': row['text_offset'], 'passage_text': row['passage_text'], 'audio_hash': row['audio_hash'],
                        'created': row['created'], 'listener_comment': row['comment'], 'reason': 'mentions е/ё; no derived interpretation record'})
    cases.sort(key=lambda c: (c['created'], c['id'])); pending.sort(key=lambda c: (c['created'], c['id']))
    conflicts.sort(key=lambda c: (c['generation_id'], c['segment_id'], c['text_offset']))
    for case in cases:case['duplicate_report_ids'].sort()
    output.mkdir(parents=True, exist_ok=True)
    for name, items in (('cases', cases), ('pending', pending), ('conflicts', conflicts)):
        (output/f'{name}.jsonl').write_text(''.join(json.dumps(i, ensure_ascii=False, sort_keys=True) + '\n' for i in items), 'utf8', newline='\n')
    counts = {'reports': len(rows), 'cases': len(cases), 'pending': len(pending), 'conflicts': len(conflicts),
              'skipped_interpreted_without_yo_aspect': skipped}
    for field in ('label_source', 'direction'):
        counts['by_' + field] = {k: sum(1 for c in cases if c[field] == k) for k in sorted({c[field] for c in cases})}
    seen = {}
    for case in cases:seen.setdefault(case['generation_id'], {'generation': case['generation'], 'pronunciation_policy': case['pronunciation_policy'], 'cases': 0})['cases'] += 1
    manifest = {'schema': SCHEMA, 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'database': str(database),
                'database_sha256': sha256(database), 'builder_sha256': sha256(__file__), 'counts': counts, 'generations': seen}
    write_json(output/'manifest.json', manifest)
    (output/'README.md').write_text(README, 'utf8', newline='\n')
    return manifest


README = """# Listener е/ё corrections

Derived from the immutable `stress_reports` of the production listener library. It is evidence for refining the
automatic ё layer (`runtime/rimsky/yo_restoration.py`), not a training set and not a human labelling archive.

Rebuild (read-only on the database and generation folders; this folder is rewritten):

    training-work/higgs-production/.venv/Scripts/python.exe -X utf8 scripts/build_listener_yo_corpus.py

Files: `cases.jsonl` (labelled), `pending.jsonl` (notes mentioning е/ё without a derived record, never labelled),
`conflicts.jsonl` (notes that disagree on one word, excluded), `manifest.json` (hashes and counts).

* `listener_explicit`: the listener marked the word (intended form differs by е->ё, or the heard word had ё and the
  intended form does not). `direction` is `missed_yo` (ё wanted, е heard) or `false_yo` (е wanted, ё heard).
* `listener_free_text_interpreted`: the listener wrote a free-text note; `listener_comment` is verbatim, but the
  target form comes from an agent's interpretation (`interpretation`). Treat it as less certain than explicit cases.
* Repeated notes on one word are merged (`duplicate_report_ids`).

Check against the layer with `scripts/evaluate_yo_restoration.py --listener-corpus cases.jsonl --listener-only`.
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--database', type=Path, default=DEFAULT_DATABASE); p.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    p.add_argument('--workspace', type=Path, help='folder holding the generation folders (default: the database data dir parent)')
    a = p.parse_args(); manifest = build(a.database, a.output, a.workspace)
    print(json.dumps(manifest['counts'], ensure_ascii=False, indent=2))


if __name__ == '__main__':main()
