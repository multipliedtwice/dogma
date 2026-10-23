"""Adjudicate silero-vs-RUAccent homograph stress disagreements of a book (text only; no audio, no GPU).

For every unit of the chosen generations' assignments the exact text the worker hands to
auto_pronunciation is rebuilt (spoken_text, terminal punctuation, spoken-text plan, phrase
stress). Words silero marks as homographs where RUAccent 1.5.8.3 disagrees go to the text-only
provider (GPT-6 Luna low via Codex CLI, fallback Claude Haiku 5.5; user decision 2026-10-16) as
sentence + two candidates; it answers silero | ruaccent | neither and echoes the word (letters
can never change). Overrides bind to sha256(unit text) + word + occurrence. Requests, per-batch
attempts and answers are immutable; the actual provider/model is recorded per batch.
"""
import argparse, copy, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest
from rimsky import homograph_stress as hs

FOLDER = ROOT/'training-work/elirm-iv-higgs-mvp'
CLAUDE_ONLY = False
BATCH = 15  # 2026-10-17: 40-item batches failed the stress-echo check twice running (Luna dropped or swapped one mark)
read = lambda p: json.loads(Path(p).read_text('utf8'))


def native_texts(generations, chapters, with_specs=False, book_fixes=None):
    """{unit text -> chapter} of the exact auto_pronunciation inputs for the newest assignment of each chapter."""
    from render_higgs_book_trial import spoken_text
    from rimsky.spoken_pronunciation import phrase_stress
    texts = {}
    fixes = None
    if book_fixes:
        fixes = read(book_fixes)
        fixes = {'entries': fixes['entries'], 'sha256': file_digest(book_fixes)}
    for chapter in chapters:
        for generation in generations:
            files = []
            for chunk in sorted((FOLDER/generation/chapter).glob('chunk-*')):
                candidates = []
                for candidate in chunk.rglob('assignment.json'):
                    relative = candidate.relative_to(chunk)
                    if any(part.startswith(('take-', 'word-', 'recovery-')) for part in relative.parts[:-1]):continue
                    candidates.append(candidate)
                if candidates:
                    # A chunk keeps immutable checkpoints at each preparation
                    # wrapper (root -> presentation -> number groups).  The
                    # renderer consumes the deepest wrapper, not every ancestor.
                    # Equal-depth siblings are still ambiguous and must stop.
                    depth = max(len(p.relative_to(chunk).parts) for p in candidates)
                    final = [p for p in candidates if len(p.relative_to(chunk).parts) == depth]
                    if len(final) != 1:
                        raise ValueError(f'Multiple canonical assignments in {chunk}: {final}')
                    files.append(final[0])
            if files:break
        else:raise SystemExit('No assignment found for ' + chapter)
        seen = set()
        for path in files:
            assignment = read(path)
            for unit in assignment['units']:
                if unit['id'] in seen or unit.get('nonverbal_intent') or not unit.get('text'):continue
                seen.add(unit['id'])
                if fixes and not unit.get('spoken_text_plan'):
                    from render_higgs_palette_preview import carry_book_fix
                    unit = copy.deepcopy(unit)
                    carry_book_fix(unit, fixes)
                spoken = spoken_text(unit['text'], unit.get('spoken_number_policy', 'grouped-digits-v2'))
                if unit.get('finish_fragment') and not re.search(r'[.!?…,:;]$', spoken):spoken += ','
                native = spoken
                if unit.get('spoken_text_plan'):
                    from rimsky.spoken_edits import apply_spoken_edit
                    native, spoken = apply_spoken_edit(spoken, unit['spoken_text_plan'])
                # Keep preparation byte-for-byte aligned with render_higgs_book_trial:
                # reading recipes bind before the generation-level terminal-period policy.
                if assignment.get('terminal_punctuation') == 'period-if-missing-v1':
                    if not re.search(r'[.!?…,:;]$', native):native += '.'
                    if not re.search(r'[.!?…,:;]$', spoken):spoken += '.'
                native = phrase_stress(native)[0]
                spec = assignment.get('pronunciation')
                if spec:
                    from rimsky.auto_pronunciation import policy_options
                    from rimsky.respelling import respell_text
                    names = {cid: row['stressed'] for cid, row in spec['names'].items()}
                    names.update(spec.get('proper_nouns', {}))
                    options = policy_options(spec['policy'], spec)
                    native, _ = respell_text(native, names, spec.get('common', []), spec.get('terms'),
                                             options.get('implicit_single', False), spec.get('respelling_sources'))
                value = {'chapter': chapter, 'pronunciation': spec} if with_specs else chapter
                texts.setdefault(native, value)
        print(chapter, 'units', len(seen), 'from', Path(files[0]).parents[1].name, flush=True)
    return texts


def lexicon(book, answer):
    from rimsky.name_stress_analysis import common_words
    from rimsky.proper_noun_stress import proper_noun_lexicon
    names = {r['character_id']: r['stressed'] for r in answer['names']}
    texts = [b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    return {**names, **proper_noun_lexicon(texts, names, True)}, common_words(names, texts)


# Stress is linguistic judgement, decided once per book on a few dozen words: measured on 75 Elirm IV
# disagreements with hand labels (2026-10-17), wrong on 52 single-answer items: Opus 5.5 high 6 (half
# of them accepted variants), Luna low ~9 and unstable between runs, Haiku low ~8, Sonnet 5.5 medium 15.
# ~20 s per 25 items. Luna stays the per-sentence recovery model, where speed matters.
ADJUDICATOR = ('claude_cli', 'claude-opus-5-5', 'high')


def call(system, body, contract, folder, tag):
    """One batch through the pinned stress adjudicator; no silent fallback (the model actually used is recorded)."""
    provider, model, effort = ADJUDICATOR
    if CLAUDE_ONLY:from rimsky.word_recovery import FALLBACK_MODEL;provider, model, effort = FALLBACK_MODEL
    metadata = {}
    from rimsky.claude_analysis import structured_call as claude_call
    answer = claude_call(system, body, contract, effort=effort, model=model, metadata=metadata, timeout=900)
    metadata.update(provider=provider, model=model, effort=effort)
    return answer, metadata


def adjudicate(out, rows, req):
    items = req['items']; batches = []
    for start in range(0, len(items), BATCH):
        part = {'task': req['task'], 'items': items[start:start+BATCH]}
        ids = [i['id'] for i in part['items']]; contract = hs.schema(ids)
        bound = {'system': hs.SYSTEM, 'request': part, 'schema': contract}
        sha = file_digest_json(bound)
        done = out/'batches'/f'batch-{sha[:16]}.answer.json'
        if done.exists():
            saved = read(done); hs.validate(saved['answer'], part); batches.append(saved); continue
        body = part
        for attempt in range(3):
            path = out/'batches'/f'batch-{sha[:16]}.attempt-{attempt}.request.json'
            write_json(path, {**bound, 'request': body})
            print('HOMOGRAPH BATCH', sha[:8], len(ids), 'items attempt', attempt, flush=True)
            answer, metadata = call(hs.SYSTEM, body, contract, out, sha[:8])
            write_json(path.with_name(path.name.replace('.request', '.answer')), {'answer': answer, 'metadata': metadata, 'request_sha256': file_digest(path)})
            try:hs.validate(answer, part)
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:raise
                body = {**part, 'validation_errors': str(exc)[:1500]}; continue
            saved = {'request_sha256': sha, 'ids': ids, 'answer': answer, 'metadata': metadata, 'attempts': attempt + 1}
            write_json(done, saved); batches.append(saved); break
    return batches


def file_digest_json(value):
    from rimsky.util import digest
    return digest(value)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-full12', 'audio-palette-v3-descriptors'])
    p.add_argument('--output', type=Path, default=FOLDER/'homograph-stress-v2-opus')
    p.add_argument('--book-fixes', type=Path, help='Apply the same verified spoken-text fix list as the target render')
    p.add_argument('--dry-run', action='store_true', help='Count disagreements without calling the provider')
    p.add_argument('--claude-only', action='store_true', help='Comparison run with the fallback model; never the frozen generation answer')
    a = p.parse_args(); out = a.output; out.mkdir(parents=True, exist_ok=True)
    global CLAUDE_ONLY; CLAUDE_ONLY = a.claude_only
    book = read(FOLDER/'source/book.json'); names, common = lexicon(book, read(FOLDER/'name-stress-v1/characters.answer.json')['answer'])
    texts = native_texts(a.generations, a.chapters, with_specs=True, book_fixes=a.book_fixes)
    inputs = []
    for text, metadata in texts.items():
        spec = metadata.get('pronunciation') or {}
        local_names = names
        if spec:
            local_names = {cid: row['stressed'] for cid, row in spec['names'].items()}
            local_names.update(spec.get('proper_nouns', {}))
        inputs.append({'text': text, 'names': local_names, 'common': spec.get('common', common), 'implicit_single': True})
    rows = hs.disagreements(inputs)
    chapter_of = {hs.digest(t.encode('utf8')): metadata['chapter'] for t, metadata in texts.items()}
    for r in rows:r['chapter'] = chapter_of[r['sha256']]
    print('units', len(texts), 'disagreements', len(rows), flush=True)
    if a.dry_run:
        for r in rows:print(r['chapter'], r['silero'], r['ruaccent'], '|', r['sentence'][:90])
        return
    req = hs.request(rows)
    bound = {'rule': hs.RULE, 'system': hs.SYSTEM, 'request': req, 'bindings': [{k: r[k] for k in ('sha256', 'word', 'index', 'chapter', 'silero', 'ruaccent', 'silero_stress', 'ruaccent_stress')} for r in rows],
             'chapters': a.chapters, 'generations': a.generations,
             'book_fixes_sha256': file_digest(a.book_fixes) if a.book_fixes else None,
             'ruaccent': {'version': '1.5.8.3', 'omograph_model': hs.OMOGRAPH_MODEL, 'models_sha256sums': file_digest(hs.MODEL_DIR/'SHA256SUMS.txt')}}
    path = out/'homograph.request.json'
    if path.exists() and read(path) != bound:raise SystemExit('Frozen homograph request changed; use a new --output')
    if not path.exists():write_json(path, bound)
    batches = adjudicate(out, rows, req)
    merged = {'decisions': [d for b in batches for d in b['answer']['decisions']]}
    hs.validate(merged, req)
    saved = {'rule': hs.RULE, 'request_sha256': file_digest(path), 'answer': merged, 'overrides': hs.overrides(rows, merged),
             'metadata': {'batches': [{'request_sha256': b['request_sha256'], **b['metadata']} for b in batches]},
             'human_approval': False, 'scope': 'text-only stress proposals; audio gates and listening decide'}
    write_json(out/'homograph.answer.json', saved)
    import collections
    print('choices', dict(collections.Counter(d['choice'] for d in merged['decisions'])), 'overrides', len(saved['overrides']))


if __name__ == '__main__':main()
