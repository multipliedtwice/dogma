"""Render an evaluation-only base/custom and mood-control listening set locally.

Runs the same Higgs backend as the app. Candidate tags are explicitly unapproved
and never become clip labels or installed production control mappings.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from rimsky.books import sentence_spans
from rimsky.higgs_backend import HiggsBackend, gguf_precision
from rimsky.util import AppError, digest, file_digest, write_json
from export_higgs_training import read, rows, sha
from higgs_file_checks import cached_sha
from train_higgs_local import verify_dataset

FAMILIES = ('narrator_baseline', 'masculine_2', 'masculine_4', 'feminine_3')
CANDIDATES = {'calm': ['contentment', 'speed_slow'], 'excited': ['enthusiasm'],
              'energetic': ['expressive_high'], 'confident': ['determination'], 'curious': ['contemplation'],
              'surprised': ['surprise'], 'soft': ['expressive_low'], 'irritated': ['anger']}


def build_plan(dataset, book3_source):
    dataset_hash = verify_dataset(dataset)
    references = read(dataset / 'references.json')
    # The user's chosen narrator production register is Calm. A global count
    # (dominated by Book 1 Normal choices) must not silently choose its reference.
    calm = [r for r in rows(dataset / 'train.jsonl') if r['metadata']['voice_family'] == 'narrator_baseline'
            and r['metadata']['mood'] == 'calm' and 3 <= r['metadata']['seconds'] <= 7
            and len(sentence_spans(r['text'])) == 1]
    if calm:
        selected = sorted(calm, key=lambda r: (abs(r['metadata']['seconds'] - 5), r['id']))[0]
        references['narrator_baseline'] = [{'audio': selected['audio'], 'text': selected['text'], 'mood': 'calm',
                                          'reviewed': True, 'evidence_id': selected['id'],
                                          'audio_sha256': selected['metadata']['audio_sha256'],
                                          'basis': 'human-reviewed development Calm recording; narrator default chosen by the user'}]
    validation = rows(dataset / 'validation.jsonl')
    cases = []
    for voice in FAMILIES:
        eligible = [r for r in validation if r['metadata']['voice_family'] == voice
                    and r['metadata']['mood'] in CANDIDATES and len(sentence_spans(r['text'])) == 1
                    and 4 <= r['metadata']['seconds'] <= 10 and not re.search(r'\d|[\[\]<>]', r['text'])]
        if not eligible:
            continue
        row = sorted(eligible, key=lambda r: (r['metadata']['mood'] != ('calm' if voice == 'narrator_baseline' else 'excited'), r['id']))[0]
        cases.append({'id': f'control-{voice}', 'kind': 'control_comparison', 'voice': voice,
                      'text': row['text'], 'reviewed_mood': row['metadata']['mood'],
                      'reference': references[voice][0], 'source_audio': row['audio'],
                      'source_audio_sha256': row['metadata']['audio_sha256'],
                      'candidate_tags': CANDIDATES[row['metadata']['mood']], 'evidence_id': row['id']})
    # A continuous narrative opening from chapter 1, with direct speech held
    # outside this audio-only rehearsal. This is not a text assignment benchmark.
    source = rows(book3_source)
    start = next(i for i, r in enumerate(source) if re.match(r'^Глава\s+(?:первая|1)(?:[.\s]|$)', r['text'].strip(), re.I))
    collected, context = [], []
    for row in source[start + 1:]:
        if row['chapter_id'] != source[start]['chapter_id'] or '—' in row['text'] or re.search(r'\d|[\[\]<>()]', row['text']):
            break
        context.append(row)
        for a, b in sentence_spans(row['text']):
            if row['text'][a:b].strip():
                collected.append({'id': f'book3-{len(collected):02d}', 'kind': 'book3_narration_excerpt',
                                  'voice': 'narrator_baseline', 'text': row['text'][a:b],
                                  'reference': references['narrator_baseline'][0], 'candidate_tags': [],
                                  'source_id': row['source_id'], 'start': row['start'] + a, 'end': row['start'] + b,
                                  'assignment_basis': 'offline narrative-only excerpt; no automatic speaker/mood accuracy claim'})
        if len(collected) >= 8:
            break
    cases.extend(collected[:8])
    return {'schema': 1, 'purpose': 'evaluation_only', 'production_approved': False,
            'dataset_sha256': dataset_hash, 'book3_source_sha256': sha(book3_source), 'cases': cases,
            'book3_context': context, 'controls': 'Candidate mappings only. Compare by ear per voice; no clip labels change.'}


def candidate_pack(dataset, model, case, runner, version, tagged):
    reference = case['reference']
    path = dataset / reference['audio']
    if cached_sha(path) != reference['audio_sha256']:
        raise ValueError('Reviewed reference audio changed.')
    relative = lambda p: p.resolve().relative_to(ROOT).as_posix()
    files = {relative(p): cached_sha(p) for p in (model, runner, path)}
    voice = {'id': case['voice'], 'family': case['voice'],
             'references': {'neutral': {'audio': relative(path), 'text': reference['text']}},
             'higgs_controls': {'moods': {'neutral': {'tags': case['candidate_tags'] if tagged else [], 'reviewed': False}},
                               'paces': {'normal': {'tags': [], 'reviewed': False}}}}
    pack = {'speech': {'backend': 'higgs_audio_cpp', 'purpose': 'evaluation_only', 'model': relative(model),
                        'effects_policy':'qualification',
                        'runner': relative(runner), 'runner_version': version, 'precision': gguf_precision(model), 'threads': 4,
                        'max_new_tokens': 2048, 'sampling': {'seed': 20261012, 'temperature': .8, 'top_k': 50,
                                                           'top_p': .95, 'repetition_penalty': 1.1, 'do_sample': True}},
            'voices': [voice], 'files': files}
    pack['_fingerprint'], pack['_root'] = digest(pack), str(ROOT)
    return pack


def listening_page(output, plan, results):
    parts = ['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>Higgs voice and delivery comparison</title><style>body{font:18px system-ui;max-width:900px;margin:30px auto;padding:16px}article{border:1px solid #bbb;border-radius:12px;padding:18px;margin:20px 0}audio{display:block;width:100%;margin:10px 0}small{color:#555}</style>',
             '<h1>Higgs voice and delivery comparison</h1><p>Candidate fine-tune, pending listening approval. Judge each voice separately: words, identity, naturalness and delivery. Tags are experimental; saved labels are unchanged.</p>']
    for case in plan['cases']:
        parts.append('<article><h2>' + html.escape(case['voice']) + '</h2><p>' + html.escape(case['text']) + '</p>')
        for result in results:
            if result['case_id'] != case['id']:
                continue
            parts.append('<p>' + html.escape(result['variant']) + '</p>')
            if result.get('audio'):
                parts.append('<audio controls preload="none" src="' + html.escape(result['audio']) + '"></audio>')
            if result.get('error'):
                parts.append('<p>' + html.escape(result['error']) + '</p>')
        if case.get('source_audio'):
            parts.append('<p>Vlad’s reviewed recording</p><audio controls preload="none" src="source-' + case['id'] + '.wav"></audio>')
        parts.append('</article>')
    parts.append('<small>Read-only listening artifact. Generated audio is not release-approved.</small>')
    (output / 'index.html').write_text('\n'.join(parts), encoding='utf-8')


def main():
    import shutil
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / 'training-work/higgs-production'
    pilot = ROOT / 'training-work/higgs-tts-3-pilot'
    parser.add_argument('--dataset', type=Path, default=base / 'datasets/reviewed-20261012-v1')
    parser.add_argument('--custom-model', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plan = build_plan(args.dataset, ROOT / 'training-work/character-rb3/complete/source/speech-inputs.jsonl')
    plan_path = args.output / 'plan.json'
    if plan_path.exists() and read(plan_path) != plan:
        raise ValueError('Listening plan changed. Use a new output folder.')
    write_json(plan_path, plan)
    if not args.run:
        print(json.dumps({'cases': len(plan['cases']), 'control_pairs': sum(c['kind'] == 'control_comparison' for c in plan['cases']),
                          'book3_sentences': sum(c['kind'] == 'book3_narration_excerpt' for c in plan['cases'])}))
        return
    if args.custom_model is None:
        parser.error('--run requires the converted custom model.')
    proof = read(args.custom_model.with_suffix('.provenance.json'))
    if proof['model_sha256'] != sha(args.custom_model) or proof['kind'] != 'lora' or proof['optimizer_steps'] < 1:
        raise ValueError('Custom model provenance mismatch.')
    runner = pilot / 'audio-cpp-cuda/audiocpp_cli.exe'
    from rimsky.cli_process import run_cli
    version = run_cli([str(runner), '--version'], cwd=runner.parent, timeout=30).stdout.strip()
    stock = pilot / 'models/Higgs-Audio-v3-TTS-4B-GGUF/higgs-audio-v3-tts-4b-q8_0.gguf'
    results = []
    backend = HiggsBackend()
    for case in plan['cases']:
        variants = [('Custom baseline', args.custom_model, False)]
        if case['kind'] == 'control_comparison':
            variants = [('Stock baseline', stock, False), *variants, ('Custom with candidate mood controls', args.custom_model, True)]
            source = args.dataset / case['source_audio']
            if sha(source) != case['source_audio_sha256']:
                raise ValueError('Reviewed source WAV changed.')
            shutil.copyfile(source, args.output / ('source-' + case['id'] + '.wav'))
        for index, (variant, model, tagged) in enumerate(variants):
            filename = case['id'] + f'-{index}.wav'
            pack = candidate_pack(args.dataset, model, case, runner, version, tagged)
            request = {'pack': pack, 'device': args.device, 'voice_id': case['voice'], 'text': case['text'], 'delivery': 'neutral',
                       'output': str(args.output / filename)}
            request['resolved_speech'] = backend.probe(request)
            result = {'case_id': case['id'], 'variant': variant, 'model_sha256': sha(model), 'production_approved': False}
            try:
                rendered = backend.render(request)
                result.update(audio=filename, audio_sha256=sha(args.output / filename), **rendered)
            except AppError as error:
                result['error'] = str(error)
            results.append(result)
            write_json(args.output / 'results.json', {'plan_sha256': sha(plan_path), 'results': results})
            listening_page(args.output, plan, results)
            print(case['id'], variant, result.get('error', 'rendered'), flush=True)
    print(str(args.output / 'index.html'))


if __name__ == '__main__':
    main()
