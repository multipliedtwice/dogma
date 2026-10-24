"""Seed a fresh Elirm IV preview generation for a timed iteration (no audio, no analysis calls).

The new generation differs from the v3 source only in what is requested here:
pronunciation policy (with name and proper-noun stress built exactly as the
worker builds them), optional reference onset padding, and its listening page.
It copies the frozen cast/manifest, presentation routes, primary-ASR policy,
delegated revisions, the requested chapter's direction caches and the verified
scene music (hard links). Launch the supervisor with the same flags afterwards;
a mismatch makes the worker refuse and write manifest.rejected.json.
"""
import argparse, json, os, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest
from rimsky.name_stress_analysis import common_words
from rimsky.production_higgs_model import resolve_production_higgs_model

FOLDER = ROOT/'training-work/elirm-iv-higgs-mvp'
SRC = FOLDER/'audio-palette-v3-descriptors'
ANSWER = FOLDER/'name-stress-v1/characters.answer.json'
DEFAULT_PRONUNCIATION_POLICY = 'stress-rare-v4'
read = lambda p: json.loads(Path(p).read_text('utf8'))


def production_engine_binding(root=ROOT):
    """The exact engine record expected by render_higgs_palette_preview."""
    pin = resolve_production_higgs_model(root)
    return {'engine': 'higgs', 'checkpoint': pin.checkpoint,
            'model': pin.path.name, 'model_sha256': pin.sha256}


def book_fixes_binding(path, book_path):
    """Validate and freeze a carried-forward spoken-text fix list."""
    fixes = read(path)
    if fixes.get('schema') != 1 or fixes.get('book_source_sha256') != file_digest(book_path):
        raise ValueError('Book fix list belongs to another source or schema')
    return {'path': str(Path(path).resolve()), 'sha256': file_digest(path)}


def pronunciation_preflight(generations, chapters, pronunciation, book_fixes=None):
    """Validate every requested unit before a generation is eligible to start.

    Progressive rendering materializes its audio chunk by chunk, but frozen
    linguistic decisions cover the whole requested prefix.  Checking them here
    prevents a later chapter/chunk from discovering preparation drift after the
    GPU has already started.
    """
    from build_homograph_stress import native_texts
    from rimsky.auto_pronunciation import auto_pronunciation, policy_options
    from rimsky.respelling import respell_text
    texts = native_texts(generations, chapters, with_specs=False, book_fixes=book_fixes)
    names = {cid: row['stressed'] for cid, row in pronunciation['names'].items()}
    names.update(pronunciation.get('proper_nouns', {}))
    options = policy_options(pronunciation['policy'], pronunciation)
    failures = []
    for text in texts:
        prepared, _ = respell_text(
            text, names, pronunciation.get('common', []), pronunciation.get('terms'),
            options.get('implicit_single', False), pronunciation.get('respelling_sources'))
        _, automatic = auto_pronunciation(prepared, names, pronunciation.get('common', []), **options)
        homograph = (automatic or {}).get('homograph_overrides') or {}
        yo = (automatic or {}).get('yo_restoration') or {}
        if homograph.get('unbound') or yo.get('unbound'):
            failures.append({'text': text, 'homograph': homograph.get('unbound', []),
                             'yo': yo.get('unbound', [])})
    if failures:
        sample = json.dumps(failures[:3], ensure_ascii=False)
        raise ValueError(f'Whole-generation pronunciation preflight failed '
                         f'({len(failures)}/{len(texts)} units): {sample}')
    return {'schema': 1, 'status': 'passed', 'units': len(texts),
            'chapters': list(chapters), 'policy': pronunciation['policy'],
            'homograph_answer_sha256': (pronunciation.get('homograph_stress') or {}).get('answer_sha256'),
            'yo_answer_sha256': (pronunciation.get('yo_restoration') or {}).get('answer_sha256')}


def frozen_pronunciation_generations(answer_path):
    """Assignment generations used to build an immutable linguistic answer."""
    request_path = Path(answer_path).with_name('homograph.request.json')
    if not request_path.exists():
        raise ValueError('Homograph answer has no sibling immutable request')
    generations = read(request_path).get('generations')
    if not isinstance(generations, list) or not generations or not all(isinstance(x, str) and x for x in generations):
        raise ValueError('Homograph request has no valid source generations')
    return generations


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--name', required=True, help='generation folder name, e.g. audio-palette-v3-iter2')
    p.add_argument('--chapter', nargs='+', default=['c0003']); p.add_argument('--page', required=True)
    p.add_argument('--pronunciation-policy', default=DEFAULT_PRONUNCIATION_POLICY, help='New development generations default to v4; frozen generations are never upgraded in place'); p.add_argument('--homograph-stress', type=Path, help='homograph.answer.json for stress-rare-v3/v4'); p.add_argument('--yo-restoration', type=Path, help='yo.answer.json for stress-rare-v4'); p.add_argument('--reference-onset-padding', action='store_true')
    p.add_argument('--number-policy', choices=['grouped-digits-v3']); p.add_argument('--terminal-punctuation', action='store_true'); p.add_argument('--stress-witness', action='store_true', help='retake word-exact takes whose acoustic stress contradicts a mark')
    p.add_argument('--one-shot', action='store_true', help='Freeze a first-take-only render policy; no synthesis retakes or recovery renders')
    p.add_argument('--section-markers', type=Path, help='confirmed.json from build_section_markers.py (structural pauses)')
    p.add_argument('--spoken-corrections', type=Path, help='spoken-corrections file the run will pass (its hash is frozen in the direction contract)')
    p.add_argument('--book-fixes', type=Path, help='verified carried-forward spoken-text plans; pass the same file to the supervisor')
    p.add_argument('--listener-respellings', type=Path, help='listener-respellings/respellings.json (rimsky.respelling.apply_listener_names)')
    p.add_argument('--hard-e', type=Path, help='hard-e-v1-opus/respellings.json (scripts/build_hard_e.py); listener respellings override it')
    a = p.parse_args(); dst = FOLDER/a.name
    if (dst/'manifest.json').exists():raise SystemExit('Generation already exists: ' + str(dst))
    if a.pronunciation_policy == 'stress-rare-v4' and (not a.homograph_stress or not a.yo_restoration):
        p.error('New v4 preparation requires --homograph-stress and --yo-restoration bound answers')
    dst.mkdir()
    book = read(FOLDER/'source/book.json'); answer = read(ANSWER)['answer']
    names = {r['character_id']: {'name': r['name'], 'stressed': r['stressed'], 'confidence': r['confidence'],
                                 'basis': r['basis'], 'source': 'model'} for r in answer['names']}
    model_doc = read(a.hard_e) if a.hard_e else None; sources = None
    if model_doc:
        from rimsky.respelling import apply_name_respellings
        names, sources = apply_name_respellings(names, model_doc, read(a.listener_respellings) if a.listener_respellings else None)
    elif a.listener_respellings:
        # Listener е->э/ё name respellings (Мозес -> Мо́зэс) override the model answer before every lexicon is built.
        from rimsky.respelling import apply_listener_names
        names = apply_listener_names(names, read(a.listener_respellings))
    texts = [b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    pronunciation = {'policy': a.pronunciation_policy, 'names': names,
                     'common': common_words({k: v['stressed'] for k, v in names.items()}, texts),
                     'name_stress_answer_sha256': file_digest(ANSWER),
                     **({'listener_respellings_sha256': file_digest(a.listener_respellings)} if a.listener_respellings else {}),
                     **({'hard_e_sha256': file_digest(a.hard_e)} if a.hard_e else {})}
    if a.pronunciation_policy == 'stress-rare-v2':
        from rimsky.proper_noun_stress import proper_noun_lexicon, RULE
        pronunciation.update(proper_nouns=proper_noun_lexicon(texts, {k: v['stressed'] for k, v in names.items()}), proper_noun_rule=RULE)
    if a.pronunciation_policy == 'stress-rare-v3':
        if not a.homograph_stress:raise SystemExit('stress-rare-v3 needs --homograph-stress')
        from rimsky.homograph_stress import v3_block
        pronunciation.update(v3_block(texts, {k: v['stressed'] for k, v in names.items()}, a.homograph_stress))
    if a.pronunciation_policy == 'stress-rare-v4':
        if not a.homograph_stress or not a.yo_restoration:raise SystemExit('stress-rare-v4 needs --homograph-stress and --yo-restoration')
        from rimsky.yo_restoration import v4_block
        pronunciation.update(v4_block(texts, {k: v['stressed'] for k, v in names.items()}, a.homograph_stress, a.yo_restoration))
    if model_doc:
        from rimsky.respelling import apply_noun_respellings
        if pronunciation.get('proper_nouns'):
            pronunciation['proper_nouns'], sources = apply_noun_respellings(pronunciation['proper_nouns'], model_doc, read(a.listener_respellings) if a.listener_respellings else None, sources)
        pronunciation['respelling_sources'] = sources
    manifest = {**read(SRC/'manifest.json'), 'pronunciation': pronunciation,
                'tts_engine': production_engine_binding(ROOT)}
    if a.number_policy:manifest['spoken_number_policy'] = a.number_policy
    if a.spoken_corrections:
        if 'spoken_edits_sha256' not in manifest.get('direction_contract', {}):raise SystemExit('Source manifest has no spoken-corrections contract')
        manifest['direction_contract'] = {**manifest['direction_contract'], 'spoken_edits_sha256': file_digest(a.spoken_corrections)}
    if a.book_fixes:
        manifest['book_fixes'] = book_fixes_binding(a.book_fixes, FOLDER/'source/book.json')
    if a.terminal_punctuation:manifest['terminal_punctuation'] = 'period-if-missing-v1'
    if a.stress_witness:
        from rimsky.stress_witness import RULE
        manifest['stress_witness'] = RULE
    if a.one_shot:manifest['render_policy'] = 'one-shot-v1'
    if a.reference_onset_padding:
        from rimsky.reference_onset import POLICY
        manifest['reference_onset'] = POLICY
    preflight = pronunciation_preflight(
        frozen_pronunciation_generations(a.homograph_stress), a.chapter, pronunciation, a.book_fixes
    ) if a.pronunciation_policy == 'stress-rare-v4' else None
    write_json(dst/'manifest.json', manifest)
    if preflight:write_json(dst/'pronunciation-preflight.json', preflight)
    routes = read(SRC/'presentation-routes-v1/complete.json')
    routes.update(generation=dst.name, generation_manifest_sha256=file_digest(dst/'manifest.json'))
    write_json(dst/'presentation-routes-v1/complete.json', routes)
    for name in ('frozen-gains.json', 'primary-asr-policy.json', 'delegated-revisions.json', 'scene-music.request.json', 'scene-music.queue-receipt.json'):
        if (SRC/name).exists():shutil.copy2(SRC/name, dst/name)
    copied = 0
    for direction in (d for chapter in a.chapter for d in (SRC/chapter).glob('chunk-*/**/direction')):
        if any(x.startswith(('word-', 'take-')) for x in direction.relative_to(SRC).parts):continue
        shutil.copytree(direction, dst/direction.relative_to(SRC), dirs_exist_ok=True); copied += 1
    for path in (SRC/'scene-music-v2').rglob('*'):
        target = dst/'scene-music-v2'/path.relative_to(SRC/'scene-music-v2')
        if path.is_dir():target.mkdir(parents=True, exist_ok=True); continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:os.link(path, target)
            except OSError:shutil.copy2(path, target)
    if a.section_markers:
        markers = read(a.section_markers)
        if markers['source_sha256'] != file_digest(FOLDER/'source/book.json'):raise SystemExit('Section markers belong to another source')
        write_json(dst/'section-markers.json', {**markers, 'copied_from': str(a.section_markers), 'copied_sha256': file_digest(a.section_markers)})
    (ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp'/a.page).mkdir(parents=True, exist_ok=True)
    print('seeded', dst, '| direction caches', copied, '| policy', a.pronunciation_policy, '| onset padding', a.reference_onset_padding)


if __name__ == '__main__':main()
