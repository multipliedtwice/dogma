"""Freeze the user-selected Soft/Mist idents; never synthesizes or edits labels."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import wave

import build_dogma_intros as trial

SOURCE = trial.ROOT / 'training-work/dogma-intros/20261022-mist-soft-v3'
DESTINATION = trial.ROOT / 'resources/branding/dogma-opening-v1'
MODEL = '78f09d20145ab79b8a6d425bc68fe104e3c624a7aea687bb0ff31c3a186374d1'
REFERENCE = '58a4f3284b0312cee9834a6753bc63168de4c3fffc962a992fa79516de5d29b6'
MUSIC = '98a63f324303c74fe2f16359e901019be98448b84d9ec23478a40fa14fcfdee4'


def main():
    spec_path = SOURCE / 'spec.json'
    spec = trial.read(spec_path)
    pin = trial.resolve_production_higgs_model(trial.ROOT)
    if pin.sha256 != MODEL or spec['speech_model']['sha256'] != MODEL or spec['reference']['audio_sha256'] != REFERENCE:
        raise ValueError('Selected brand assets have different production model/reference provenance')
    if spec['reference'].get('reviewed') is not True or spec['reference']['mood'] != 'soft' or spec['effects_policy'] != 'disabled':
        raise ValueError('Brand assets must use the reviewed Soft performance without effects')
    if trial.file_digest(Path(spec['reference']['path'])) != REFERENCE:
        raise ValueError('Selected Soft reference bytes changed')
    DESTINATION.mkdir(parents=True, exist_ok=True)
    languages = {}
    for language in ('ru', 'en'):
        unit = next(u for u in spec['units'] if u['id'] == '08-' + language)
        speech = trial.selected(SOURCE, unit, 'speech')
        music = trial.selected(SOURCE, unit, 'music')
        final_path = SOURCE / 'final' / (unit['id'] + '.json')
        final = trial.read(final_path)
        if not speech or not music or music['audio_sha256'] != MUSIC:
            raise ValueError('Brand speech/music has an unresolved quality flag')
        if (speech['spec']['model']['sha256'] != MODEL
                or speech['spec']['reference']['audio_sha256'] != REFERENCE
                or speech['spec']['reference'].get('reviewed') is not True
                or speech['spec']['reference'].get('mood') != 'soft'):
            raise ValueError('Speech take was not synthesized with the pinned model and reviewed Soft reference')
        if (final['mix_spec']['speech_sha256'] != speech['audio_sha256']
                or final['mix_spec']['music_sha256'] != MUSIC
                or final['speech_check'] != trial.audit(SOURCE, speech)
                or final['music_check'] != trial.audit(SOURCE, music)):
            raise ValueError('Published mix differs from checked speech/music evidence')
        if language == 'ru' and final['speech_check']['status'] != 'independent_witness_exact_match':
            raise ValueError('Selected Russian brand needs its exact independent Whisper witness')
        if language == 'en' and final['speech_check']['status'] != 'asr_exact_match':
            raise ValueError('Selected English brand needs its exact Qwen check')
        variants = {}
        for variant, source, expected in [('speech', SOURCE / speech['audio'], speech['audio_sha256']),
                                          ('music', SOURCE / final['wav'], final['wav_sha256'])]:
            if trial.file_digest(source) != expected:
                raise ValueError('Approved source WAV changed')
            name = f'{language}-{variant}.wav'
            destination = DESTINATION / name
            if destination.exists() and trial.file_digest(destination) != expected:
                raise ValueError('Immutable branding asset already differs: ' + str(destination))
            if not destination.exists():
                shutil.copyfile(source, destination)
            with wave.open(str(destination), 'rb') as wav:
                actual = {'file': name, 'audio_hash': expected, 'duration': wav.getnframes() / wav.getframerate(),
                          'samples': wav.getnframes(), 'sample_rate': wav.getframerate(),
                          'channels': wav.getnchannels(), 'sample_width': wav.getsampwidth()}
                if actual['sample_rate'] != 24000 or actual['sample_width'] != 2 or (variant == 'music' and (actual['duration'] != 8 or actual['channels'] != 2)):
                    raise ValueError('Approved branding WAV format changed')
            variants[variant] = actual
        languages[language] = {'text': unit['display'], 'spoken_text': unit['spoken'], 'variants': variants,
                               'provenance': {'speech_receipt': speech, 'music_receipt': music,
                                              'final_receipt_sha256': trial.file_digest(final_path), 'final_receipt': final}}
    manifest = {'schema': 1, 'id': 'dogma-opening-v1', 'music_policy': 'always_musical_branding',
                'approval': {'basis': 'User selected narrator Soft and Mist, then requested automatic musical branded splash',
                             'scope': 'Exact branded assets only', 'human_corpus_labels_changed': False},
                'attribution': spec['music_pack']['license']['attribution'],
                'source_spec_sha256': trial.file_digest(spec_path), 'source_spec': spec,
                'model_sha256': MODEL, 'reference_sha256': REFERENCE, 'music_sha256': MUSIC,
                'languages': languages}
    trial.write(DESTINATION / 'manifest.json', manifest, immutable=True)
    print(json.dumps({'manifest_sha256': trial.file_digest(DESTINATION / 'manifest.json'),
                      'files': {k: {name: item['audio_hash'] for name, item in v['variants'].items()}
                                for k, v in languages.items()}}, indent=2))


if __name__ == '__main__':
    main()
