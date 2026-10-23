"""Selected Mist pairing: new Russian wording with the reviewed Calm narrator.

Only the Russian speech is synthesized. Both versions reuse the chosen verified
Mist music; the English ident is copied unchanged from the first audition.
"""
from __future__ import annotations

import argparse
import copy
import html
from pathlib import Path
import shutil
import zipfile

import build_dogma_intros as pipeline

ROOT = pipeline.ROOT
SOURCE = ROOT / 'training-work/dogma-intros/20261022-v1'
OUTPUT = ROOT / 'training-work/dogma-intros/20261022-mist-v2'
SOFT_OUTPUT = ROOT / 'training-work/dogma-intros/20261022-mist-soft-v3'
DISPLAY = '\u041e\u0437\u0432\u0443\u0447\u0435\u043d\u043e \u0432 Dogma. \u041f\u0440\u0438\u044f\u0442\u043d\u043e\u0433\u043e \u043f\u0440\u043e\u0441\u043b\u0443\u0448\u0438\u0432\u0430\u043d\u0438\u044f.'
SPOKEN = DISPLAY.replace('Dogma', '\u0414\u043e\u0301\u0433\u043c\u0430')


def copied(source, destination, expected=None):
    digest = pipeline.file_digest(source)
    if expected and digest != expected:
        raise ValueError('Imported asset checksum mismatch: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if pipeline.file_digest(destination) != digest:
            raise ValueError('Immutable imported asset changed: ' + str(destination))
    else:
        shutil.copyfile(source, destination)
    return digest


def soft_reference(reference_json=None):
    from rimsky.higgs_reference import check_reference
    bank_path = ROOT / 'training-work/higgs-production/reference-bank/development-20261014-v2.json'
    bank = pipeline.read(bank_path)
    requested = pipeline.read(reference_json) if reference_json else {}
    evidence_id = requested.get('evidence_id', requested.get('id', 'reality-benders-1:clip_ab4cd29f8446a81a82e8'))
    entry, candidate = next((entry, c) for entry in bank['entries'] for c in entry.get('candidates', [])
                            if c['id'] == evidence_id and entry.get('mood') == 'soft'
                            and entry.get('voice_family') == 'narrator_baseline'
                            and entry.get('route') == 'narration')
    if candidate.get('voice_basis') != 'human' or candidate.get('mood_basis') != 'human':
        raise ValueError('Soft reference does not have human voice and mood labels')
    dataset = ROOT / 'training-work/higgs-production/datasets/reviewed-20261012-v1'
    train_path = dataset / 'train.jsonl'
    rows = [(line, pipeline.json.loads(line)) for line in train_path.read_text('utf-8').splitlines()]
    row_text, row = next((line, row) for line, row in rows if row['id'] == evidence_id)
    if (row['audio'] != candidate['audio'] or row['text'] != candidate['text']
            or row['metadata']['audio_sha256'] != candidate['audio_sha256']
            or row['metadata']['voice_family'] != 'narrator_baseline'
            or row['metadata']['mood'] != 'soft'
            or row['metadata']['voice_basis'] != 'human' or row['metadata']['mood_basis'] != 'human'):
        raise ValueError('Reference bank and reviewed training row differ')
    book_id, clip_id = evidence_id.split(':', 1)
    archive = ROOT / 'manual-labeling' / book_id
    review_path, latest_path = archive / 'review.json', archive / 'latest.json'
    review, latest = pipeline.read(review_path), pipeline.read(latest_path)
    review_hash = pipeline.file_digest(review_path)
    if latest['review_sha256'] != review_hash:
        raise ValueError('Canonical human review and latest checksum differ')
    decision = review['decisions'][clip_id]
    voice = decision.get('corrected_voice_id') or decision.get('confirmed_voice_id')
    if decision.get('rejected') is True or voice != 'narrator_baseline' or decision.get('corrected_mood') != 'soft':
        raise ValueError('Current canonical human decision does not explicitly select narrator Soft')
    audio_path = dataset / candidate['audio']
    reference_check = check_reference(audio_path, candidate['audio_sha256'])
    if requested.get('audio_sha256', candidate['audio_sha256']) != candidate['audio_sha256']:
        raise ValueError('Explicit checked reference does not match its bank audio')
    provenance = {'bank_path': str(bank_path), 'bank_sha256': pipeline.file_digest(bank_path),
                  'bank_entry': entry, 'selected_candidate': candidate,
                  'reference_check': reference_check, 'train_jsonl_sha256': pipeline.file_digest(train_path),
                  'exact_train_row': row, 'exact_train_row_sha256': pipeline.hashlib.sha256(row_text.encode()).hexdigest(),
                  'canonical_review_path': str(review_path), 'canonical_review_sha256': review_hash,
                  'latest_path': str(latest_path), 'latest_sha256': pipeline.file_digest(latest_path),
                  'latest': latest, 'canonical_decision': decision}
    archive_manifest = archive / 'archive-manifest.json'
    if archive_manifest.exists():
        archived = pipeline.read(archive_manifest)
        if archived.get('review_sha256', archived.get('support_snapshot_review_sha256')) != review_hash:
            raise ValueError('Protected archive does not match the canonical human review')
        provenance['archive_manifest_sha256'] = pipeline.file_digest(archive_manifest)
    labels = archive / 'labels.jsonl'
    if labels.exists():
        protected = [pipeline.json.loads(line) for line in labels.read_text('utf-8').splitlines() if line.strip()]
        provenance['protected_label_rows'] = [r for r in protected if r.get('id') in (clip_id, evidence_id) or r.get('candidate_id') == clip_id]
        provenance['protected_labels_sha256'] = pipeline.file_digest(labels)
        for label in provenance['protected_label_rows']:
            if label.get('review_sha256') != review_hash or label.get('voice_family') != 'narrator_baseline' or label.get('mood') != 'soft':
                raise ValueError('Protected human label does not match current narrator Soft choice')
            media = label.get('audio', {})
            if media.get('archive') and pipeline.file_digest(archive / media['archive']) != media['sha256']:
                raise ValueError('Protected original reference archive audio changed')
    return {'audio': candidate['audio'], 'audio_sha256': candidate['audio_sha256'],
            'path': str(audio_path.resolve()), 'text': candidate['text'], 'mood': 'soft',
            'reviewed': True, 'evidence_id': evidence_id, 'voice_family': 'narrator_baseline',
            'route': 'narration', 'basis': 'Explicit canonical human narrator Soft choice; reviewed audio conditioning',
            'bank_quality': candidate['quality'], 'reference_check': reference_check}, provenance


def prepare(output, register='calm', reference_json=None):
    original = pipeline.read(SOURCE / 'spec.json')
    pin = pipeline.resolve_production_higgs_model(ROOT)
    if pin.sha256 != original['speech_model']['sha256']:
        raise ValueError('Selected production model differs from the original pinned model')
    reference = original['reference']
    if (reference.get('reviewed') is not True or reference.get('mood') != 'calm'
            or pipeline.file_digest(Path(reference['path'])) != reference['audio_sha256']):
        raise ValueError('Selected Calm narrator reference is not intact')
    chosen = next(u for u in original['units'] if u['id'] == '08-en')
    music = pipeline.selected(SOURCE, chosen, 'music')
    speech = pipeline.selected(SOURCE, chosen, 'speech')
    if not music or not speech:
        raise ValueError('Selected English ident is not independently checked')
    original_final_path = SOURCE / 'final/08-en.json'
    original_final = pipeline.read(original_final_path)
    if (original_final['mix_spec']['speech_sha256'] != speech['audio_sha256']
            or original_final['mix_spec']['music_sha256'] != music['audio_sha256']):
        raise ValueError('Selected English mix does not bind to its checked source audio')
    manifest = copy.deepcopy(original)
    unit = {**copy.deepcopy(chosen), 'id': '08-ru', 'language': 'Russian',
            'display': DISPLAY, 'spoken': SPOKEN, 'seed': 202610221}
    manifest['units'] = [unit]
    manifest['purpose'] = 'Selected Dogma Mist refinement'
    manifest['selection'] = {'user_russian_text': DISPLAY, 'user_narrator_request': 'calm narrator tone',
                             'reference_delivery': 'human-reviewed Calm; effects and tags disabled',
                             'selected_english': '08-en Mist', 'selected_music': '08-en Mist',
                             'source_spec_sha256': pipeline.file_digest(SOURCE / 'spec.json')}
    if register == 'soft':
        reference, reference_provenance = soft_reference(reference_json)
        manifest['reference'] = reference
        manifest['units'].append({**copy.deepcopy(chosen), 'seed': 202610920})
        manifest['selection'].update(register='soft', user_narrator_request='narrator soft voice',
                                     reference_delivery='human-reviewed Soft; effects and tags disabled',
                                     soft_reference_provenance=reference_provenance,
                                     english_audio_policy='Synthesize a new English take with the same Soft reference')
    pipeline.write(output / 'spec.json', manifest, immutable=True)
    music_check_path = SOURCE / 'checks' / (music['audio_sha256'] + '.json')
    copied(SOURCE / music['audio'], output / music['audio'], music['audio_sha256'])
    copied(music_check_path, output / 'checks' / music_check_path.name)
    source_receipt_path = SOURCE / 'takes/08-en/music' / f"take-{music['take']:02d}.json"
    imported_music = {**copy.deepcopy(music), 'unit': '08-ru', 'take': 0,
                      'imported_from': {'receipt': str(source_receipt_path),
                                        'receipt_sha256': pipeline.file_digest(source_receipt_path),
                                        'check_sha256': pipeline.file_digest(music_check_path),
                                        'source_unit': '08-en', 'reason': 'User selected Mist; music reused unchanged'}}
    pipeline.write(output / 'takes/08-ru/music/take-00.json', imported_music, immutable=True)
    if register == 'soft':
        pipeline.write(output / 'takes/08-en/music/take-00.json',
                       {**imported_music, 'unit': '08-en'}, immutable=True)
    for receipt in (speech, music):
        copied(SOURCE / receipt['audio'], output / receipt['audio'], receipt['audio_sha256'])
    for ext in ('wav', 'mp3'):
        copied(SOURCE / original_final[ext], output / original_final[ext], original_final[ext + '_sha256'])
    pipeline.write(output / 'english/selection.json', {
        'source_final': str(original_final_path), 'source_final_sha256': pipeline.file_digest(original_final_path),
        'source_spec_sha256': pipeline.file_digest(SOURCE / 'spec.json'),
        'final': original_final, 'speech_receipt': speech, 'music_receipt': music,
        'selection': 'User selected English Mist 08; copied audio unchanged'}, immutable=True)
    page(output)


def page(output):
    spec = pipeline.read(output / 'spec.json')
    unit = spec['units'][0]
    selection = pipeline.read(output / 'english/selection.json')
    register = spec.get('selection', {}).get('register', 'calm')
    english_path = output / 'final/08-en.json'
    english = (pipeline.read(english_path) if english_path.exists() else None) if register == 'soft' else selection['final']
    russian_path = output / 'final/08-ru.json'
    russian = pipeline.read(russian_path) if russian_path.exists() else None
    for final in (english, russian):
        if final:
            for ext in ('wav', 'mp3'):
                if pipeline.file_digest(output / final[ext]) != final[ext + '_sha256']:
                    raise ValueError('Selected playable asset changed')
    cards = []
    for language, text, final in [('Russian', DISPLAY, russian), ('English', 'Narrated with Dogma.', english)]:
        media = f'<p>New {language.lower()} narration is being prepared.</p>'
        disclosure = ''
        if final:
            media = (f'<audio controls preload="metadata" src="{final["mp3"]}"></audio><p>'
                     f'<a href="{final["mp3"]}" download="dogma-mist-{language.lower()}.mp3">MP3</a> · '
                     f'<a href="{final["wav"]}" download="dogma-mist-{language.lower()}.wav">WAV</a></p>')
            check = final['speech_check']
            if check.get('tolerated_deviations'):
                disclosure += ' Brand spelling differs in recognition; guarded name tolerance.'
            if check.get('orthographic_expansions'):
                disclosure += ' Orthographic recognition difference resolved by source-bound spoken equivalence.'
        cards.append(f'<article><small>{language}</small><h2>Mist</h2><p class="tone">{register.title()} narrator</p>'
                     f'<p>{html.escape(text)}</p>{media}<p class="note">'
                     + ('Machine checks passed; listening approval remains yours.' if final else 'Pending machine checks.')
                     + html.escape(disclosure) + '</p></article>')
    archive_link = ''
    if russian and english:
        archive_key = pipeline.sha({'ru': russian, 'en': english})
        archive = output / 'exports' / ('dogma-mist-' + archive_key[:16] + '.zip')
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as packed:
                for language, final in [('ru', russian), ('en', english)]:
                    for ext in ('wav', 'mp3'):
                        packed.write(output / final[ext], f'dogma-mist-{language}.{ext}')
                records = [output / 'spec.json', russian_path, output / 'english/selection.json']
                if register == 'soft':
                    records.append(english_path)
                for path in records:
                    packed.write(path, path.relative_to(output).as_posix())
        archive_link = f'<a class="button" href="{archive.relative_to(output).as_posix()}" download>Download both</a>'
    revision = pipeline.sha({'russian': russian, 'english': english})
    pipeline.write(output / 'paired-status.json', {'ready': bool(russian and english), 'revision': revision})
    markup = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dogma</title><style>
    :root{color-scheme:dark}body{margin:0;background:#141916;color:#eee9dc;font:16px system-ui;line-height:1.6}main{max-width:900px;margin:auto;padding:44px 24px}h1{font:600 56px Georgia;margin:0 0 28px}h2{font:28px Georgia;margin:6px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}article{padding:24px;background:#202820;border:1px solid #344333;border-radius:14px}audio{width:100%}small,.tone,.note,footer{color:#aeb8aa}.note,footer{font-size:13px}a{color:#b9d6a8}.button{display:inline-block;padding:10px 18px;border:1px solid #6d8961;border-radius:8px;margin-top:24px}footer{margin-top:32px}</style><main><h1>Dogma</h1><div class="grid">'''
    markup += ''.join(cards) + '</div>' + archive_link + '<footer>' + html.escape(spec['music_pack']['license']['attribution'])
    markup += '<p><a href="report.json">Russian checks and raw recognition evidence</a> · <a href="spec.json">Generation provenance</a> · <a href="english/selection.json">Original English selection</a></p></footer></main>'
    markup += '''<script>const clips=[...document.querySelectorAll('audio')];clips.forEach(a=>a.addEventListener('play',()=>clips.forEach(b=>{if(a!==b)b.pause()})));setInterval(async()=>{if(clips.some(a=>!a.paused))return;try{const j=await(await fetch('paired-status.json',{cache:'no-store'})).json();if(j.revision!==REVISION)location.reload()}catch(e){}},8000)</script>'''.replace('REVISION', pipeline.json.dumps(revision))
    (output / 'index.html').write_text(markup, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'speech', 'verify', 'witness', 'resolve', 'mix', 'page'], required=True)
    parser.add_argument('--register', choices=['calm', 'soft'], default='calm')
    parser.add_argument('--reference-json', type=Path, help='Explicit checked bank reference id/evidence_id and audio_sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = (args.output or (SOFT_OUTPUT if args.register == 'soft' else OUTPUT)).resolve()
    output.relative_to((ROOT / 'training-work/dogma-intros').resolve())
    if output == SOURCE.resolve():
        raise ValueError('Original audition folder is immutable')
    output.mkdir(parents=True, exist_ok=True)
    if args.stage == 'prepare':
        prepare(output, args.register, args.reference_json)
    else:
        if args.stage != 'page':
            getattr(pipeline, args.stage if args.stage != 'resolve' else 'resolve_witnesses')(output)
        page(output)


if __name__ == '__main__':
    main()
