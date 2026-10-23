"""Prepare a sealed custom Higgs pack, pending actual listening approvals.

This creates a new staging pack; it does not select it in the app or invent
human approvals. Large immutable model/runtime files use hard links; reviewed
references are independent copies. The pack stays rejected until its listening
report and corresponding controls contain real approvals.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from rimsky.cli_process import run_cli
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.util import file_digest, write_json
from export_higgs_training import read


def linked_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def apply_qualification(voices,qualification,model_sha256,reference_hashes):
    """Attach a bounded approved palette; never turn pending rows into votes."""
    if qualification.get('model_sha256')!=model_sha256:raise ValueError('Qualification uses another checkpoint')
    approved=[r for r in qualification.get('reviews',[]) if r.get('verdict')=='approved'
              and r.get('control_type')=='combination']
    for voice in voices:
        rows=[r for r in approved if r.get('voice_family')==voice['family'] and
              r.get('reference_sha256')==reference_hashes[voice['id']]]
        if len(rows)>12:raise ValueError('Too many qualification combinations for one voice')
        voice['higgs_controls']['combinations']=[{'id':r['control_id'],'reference':'neutral',
            **{k:r[k] for k in ('register','technique','pace','tags')},'serves':r.get('serves',[]),
            **({'warmth':r['warmth']} if 'warmth' in r else {}),
            'permanent_tags':r.get('permanent_tags',[])} for r in rows]
        voice['signatures']=[{'id':r['control_id'],'reference':'neutral','permanent_tags':r.get('permanent_tags',[]),
            'vocal_traits':r.get('vocal_traits',{}),'gain':r.get('gain',1.),'level_policy':r.get('level_policy',{})}
            for r in rows if r.get('register')=='ordinary' and r.get('pace')=='normal']
        # Keep only qualified legacy prefixes when a baseline has actually
        # been auditioned. Until then the voice remains pending and unloadable.
        if not any(r['tags']==[] and r.get('register')=='ordinary' and r.get('technique') in ('ordinary','hoarse') and
                   r.get('pace')=='normal' for r in rows):continue
        for table in ('moods','paces'):
            voice['higgs_controls'][table]={key:{**value,'reviewed':True} for key,value in voice['higgs_controls'][table].items()
                if any(r['tags']==value['tags'] for r in rows)}
    return voices

def prepare(folder, listening,qualification_path=None):
    if folder.exists():
        raise ValueError('Use a fresh staging pack directory.')
    plan = read(listening / 'plan.json')
    base = ROOT / 'training-work/higgs-production'
    pinned = resolve_production_higgs_model(ROOT)
    model, proof = pinned.path, pinned.provenance
    folder.mkdir(parents=True)
    linked_copy(model, folder / 'speech/model.gguf')
    write_json(folder / 'speech/training-provenance.json', proof)
    runner = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda'
    for source in [runner / 'audiocpp_cli.exe', *runner.glob('*.dll')]:
        linked_copy(source, folder / 'runner' / source.name)
    version = run_cli([str(runner / 'audiocpp_cli.exe'), '--version'], cwd=runner, timeout=30).stdout.strip()
    text = ROOT / 'training-work/book1-17b-demo-pack/models/text/model.gguf'
    linked_copy(text, folder / 'text/model.gguf')
    asr = ROOT / 'training-work/models/Qwen3-ASR-0.6B'
    for source in asr.rglob('*'):
        if source.is_file() and '.cache' not in source.parts:
            linked_copy(source, folder / 'verifier' / source.relative_to(asr))
    families = {r['id']: r for r in read(ROOT / 'training/voice_families.json')['families']}
    evidence = read(ROOT / 'training-work/assignment-benchmark/m1-20261012/evidence-manifest.json')
    reviews, voices = [], []
    for case in plan['cases']:
        if case['kind'] != 'control_comparison':
            continue
        family = case['voice']
        reference = case['reference']
        source = base / 'datasets/reviewed-20261012-v1' / reference['audio']
        if file_digest(source) != reference['audio_sha256']:
            raise ValueError('Reviewed reference changed.')
        audio = f'references/{family}.wav'
        (folder / 'references').mkdir(exist_ok=True)
        shutil.copyfile(source, folder / audio)
        book = reference['evidence_id'].split(':', 1)[0]
        reference_review = evidence['inputs'][f'manual-labeling/{book}/review.json']
        control = lambda tags: {'tags': tags, 'reviewed': False, 'listening_sha256': '0' * 64}
        moods = {'neutral': control([])}
        if family == 'narrator_baseline':
            moods['calm'] = control([])  # user's Calm reference is the default register
        elif case['reviewed_mood'] != 'neutral':
            moods[case['reviewed_mood']] = control(case['candidate_tags'])
        voice = {'id': family, 'family': family, 'name': families[family]['label'],
                 'role': 'narrator' if family == 'narrator_baseline' else 'character',
                 'group': families[family]['group'], 'gender': 'female' if family.startswith('feminine') else 'male',
                 'references': {'neutral': {'audio': audio, 'text': reference['text'], 'reviewed': True,
                                             'voice_family': family, 'review_sha256': reference_review,
                                             'evidence_id': reference['evidence_id'], 'mood': reference['mood']}},
                 'higgs_controls': {'moods': moods, 'paces': {'normal': control([])}}}
        for kind, table in (('mood', moods), ('pace', voice['higgs_controls']['paces'])):
            for cid, mapping in table.items():
                reviews.append({'voice_family': family, 'control_type': kind, 'control_id': cid,
                                'tags': mapping['tags'], 'reference_sha256': reference['audio_sha256'], 'verdict': 'pending'})
        voices.append(voice)
    if qualification_path:
        qualification=read(qualification_path)
        reference_hashes={v['id']:file_digest(folder/v['references']['neutral']['audio']) for v in voices}
        apply_qualification(voices,qualification,proof['model_sha256'],reference_hashes)
        reviews.extend(qualification['reviews'])
    write_json(folder / 'speech/listening-review.json', {'schema': 1, 'model_sha256': proof['model_sha256'], 'reviews': reviews,
                                                       'note': 'Draft. Pending means no human approval has been recorded.'})
    listening_hash = file_digest(folder / 'speech/listening-review.json')
    for voice in voices:
        for table in voice['higgs_controls'].values():
            if isinstance(table,list):continue
            for mapping in table.values():
                mapping['listening_sha256'] = listening_hash
    pack = {'schema': 1, 'name': 'Vlad Rimsky Higgs voice LoRA v1 — pending listening',
            'text_model': {'path': 'text/model.gguf', 'context': 8192, 'analysis_mode': 'auto', 'dialogue_style': 'russian_dash'},
            'speech': {'backend': 'higgs_audio_cpp', 'path': 'speech', 'variant': 'clone', 'model': 'speech/model.gguf',
                       'precision': 'q8_0', 'runner': 'runner/audiocpp_cli.exe', 'runner_version': version, 'threads': 4,
                       'max_new_tokens': 2048, 'effects_policy':'disabled', 'training_provenance': 'speech/training-provenance.json',
                       'listening_review': 'speech/listening-review.json', 'verifier': {'backend': 'qwen3_asr', 'path': 'verifier'},
                       'sampling': {'seed': 20261012, 'temperature': .8, 'top_k': 50, 'top_p': .95, 'repetition_penalty': 1.1, 'do_sample': True}},
            'voices': voices, 'release_status': 'pending_listening'}
    pack['files'] = {p.relative_to(folder).as_posix(): file_digest(p) for p in folder.rglob('*') if p.is_file()}
    write_json(folder / 'pack.json', pack)
    return {'pack': str(folder), 'voices': len(voices), 'release_status': pack['release_status']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--listening', type=Path, default=ROOT / 'training-work/higgs-production/listening/voice-lora-v1')
    parser.add_argument('--qualification',type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output.resolve(), args.listening.resolve(),args.qualification), indent=2))
