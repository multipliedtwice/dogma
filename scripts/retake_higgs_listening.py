"""Retake only word-flagged Book 3 sentences; preserve the frozen first takes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
import worker
from prepare_higgs_listening import candidate_pack, listening_page
from rimsky.higgs_backend import HiggsBackend
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.cli_process import run_cli
from rimsky.jobs import concatenate
from rimsky.util import AppError, file_digest, write_json


def retake(folder, attempts=2):
    read = lambda path: json.loads(path.read_text('utf-8'))
    plan, rendered, audited = (read(folder / name) for name in ('plan.json', 'results.json', 'word-checks.json'))
    if (rendered['plan_sha256'] != file_digest(folder / 'plan.json')
            or audited['results_sha256'] != file_digest(folder / 'results.json')
            or audited['plan_sha256'] != rendered['plan_sha256']
            or len(audited['checks']) != len(rendered['results'])):
        raise ValueError('First-take word audit is incomplete or stale.')
    cases = {c['id']: c for c in plan['cases']}
    base = ROOT / 'training-work/higgs-production'
    model = resolve_production_higgs_model(ROOT).path
    runner = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
    version = run_cli([str(runner), '--version'], cwd=runner.parent, timeout=30).stdout.strip()
    dataset = base / 'datasets/reviewed-20261012-v1'
    prefix = 'training-work/models/Qwen3-ASR-0.6B'
    verifier = {'_root': str(ROOT), 'speech': {'verifier': {'backend': 'qwen3_asr', 'path': prefix}},
                'files': audited['verifier_files']}
    for name, expected in verifier['files'].items():
        if file_digest(ROOT / name) != expected:
            raise ValueError('Word-verifier files changed.')
    backend, retakes, selected = HiggsBackend(), [], {}
    for audit in audited['checks']:
        case = cases[audit['case_id']]
        if case['kind'] != 'book3_narration_excerpt':
            continue
        original = next(r for r in rendered['results'] if r['case_id'] == case['id'])
        if file_digest(folder / original['audio']) != original['audio_sha256']:
            raise ValueError('First-take audio changed.')
        if audit['status'] == 'asr_exact_match':
            selected[case['id']] = {**original, 'word_check': audit}
            continue
        for take in range(1, attempts + 1):
            filename = case['id'] + f'-retake-{take}.wav'
            pack = candidate_pack(dataset, model, case, runner, version, False)
            request = {'pack': pack, 'device': 'cuda', 'voice_id': case['voice'], 'text': case['text'],
                       'delivery': 'neutral', 'take': take, 'output': str(folder / filename)}
            request['resolved_speech'] = backend.probe(request)
            result = {'case_id': case['id'], 'variant': f'Custom baseline retake {take}', 'take': take,
                      'first_take_sha256': original['audio_sha256'], 'production_approved': False}
            try:
                result.update(**backend.render(request), audio=filename, audio_sha256=file_digest(folder / filename))
                check = worker.verify_audio({'pack': verifier, 'audio': str(folder / filename), 'text': case['text'], 'language': 'Russian'})
                result['word_check'] = check
                if check['status'] == 'asr_exact_match':
                    selected[case['id']] = result
            except AppError as error:
                result['error'] = str(error)
            retakes.append(result)
            write_json(folder / 'retakes.json', {'first_results_sha256': file_digest(folder / 'results.json'),
                                               'first_word_checks_sha256': file_digest(folder / 'word-checks.json'), 'retakes': retakes})
            print(case['id'], take, result.get('word_check', {}).get('status', result.get('error')), flush=True)
            if case['id'] in selected:
                break
    narrative = [c for c in plan['cases'] if c['kind'] == 'book3_narration_excerpt']
    unresolved = [c['id'] for c in narrative if c['id'] not in selected]
    playlist = {'schema': 1, 'production_approved': False, 'word_verified_sentences': len(selected),
                'sentences': len(narrative), 'unresolved': unresolved, 'parts': [selected[c['id']] for c in narrative if c['id'] in selected]}
    if not unresolved:
        paths = [folder / selected[c['id']]['audio'] for c in narrative]
        gaps = [0, *[.2 if narrative[i]['source_id'] == narrative[i + 1]['source_id'] else .5 for i in range(len(narrative) - 1)], 0]
        joined = concatenate(paths, folder / 'book3-excerpt.wav', gaps)
        playlist['audio'] = 'book3-excerpt.wav'
        playlist['audio_sha256'] = joined['sha256']
    write_json(folder / 'book3-playlist.json', playlist)
    # Keep every original comparison and its original word flags visible.
    all_results = []
    by_audio = {a['audio']: a for a in audited['checks']}
    for result in rendered['results']:
        check = by_audio.get(result.get('audio'), {})
        variant = result['variant'] + (' — word-check flag' if check.get('status') == 'spoken_text_mismatch' else ' — words checked')
        all_results.append({**result, 'variant': variant})
    all_results.extend({**r, 'variant': r['variant'] + (' — words checked' if r.get('word_check', {}).get('status') == 'asr_exact_match' else ' — word-check flag')} for r in retakes)
    listening_page(folder, plan, all_results)
    page = folder / 'index.html'
    content = page.read_text('utf-8')
    if not unresolved:
        content = content.replace('<h1>', '<p>Continuous Book 3 excerpt: words passed independent checks; artistic quality still needs listening.</p><audio controls preload="none" src="book3-excerpt.wav"></audio><h1>', 1)
    else:
        content = content.replace('<h1>', f'<p>{len(unresolved)} Book 3 sentences still have transcript flags after controlled retakes. No complete excerpt is claimed.</p><h1>', 1)
    page.write_bytes(content.encode('utf-8'))
    return {k: playlist[k] for k in ('word_verified_sentences', 'sentences', 'unresolved')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--attempts', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    print(json.dumps(retake(args.folder.resolve(), args.attempts)))
