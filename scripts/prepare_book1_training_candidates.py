"""Package Book 1 sentence-bounded audio as review candidates, never accepted training data."""
from __future__ import annotations

import hashlib
import json
import wave
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'training-work/character-rb1'
OUTPUT = SOURCE / 'fine-tune-candidates'
INPUTS = (('sentence', SOURCE / 'sentence-audio-candidates.jsonl'),
          ('primary', SOURCE / 'audio-candidates.jsonl'))
REVIEW = SOURCE / 'voice-evidence-atlas/book1-review-import.json'
HELDOUT = {'c0019', 'c0038'}
CHANNELS = {'direct', 'radio', 'public_address', 'intercom', 'telepathic', 'recorded'}
DELIVERIES = {'neutral', 'soft', 'whisper', 'firm', 'excited', 'sad', 'angry',
              'questioning', 'enumeration', 'stunned'}
# Only families explicitly distinguished in the listener's Book 1 notes are
# proposed here. Similar adjectives alone never merge two character voices.
VOICE_PROPOSALS = {
    'rb1_kirill': ('masculine_4', 'Reusable masculine family with Kirill as the first reference'),
    'rb1_imran': ('masculine_1', 'First masculine voice (Imran anchor)'),
    'rb1_kisly': ('masculine_2', 'Second masculine voice (Kisly anchor)'),
    'rb1_male_harpy_c0026': ('masculine_2', 'Listener compared this to Kisly'),
    'rb1_rupor': ('masculine_2', 'Listener described second masculine voice'),
    'rb1_vadim_driver': ('masculine_2', 'One reviewed clip described second masculine voice'),
    'rb1_pier_intercom_voice_c0017': ('masculine_3', 'Listener suggested a third masculine voice'),
    'rb1_runner_343': ('masculine_5', 'Listener described a higher-pitched masculine sound, distinct from Kisly and Kirill; provisional'),
    'rb1_san_sanych': ('masculine_6', 'Listener described a mature male sound; provisional'),
    'rb1_minno': ('feminine_1', 'First feminine voice (Minno anchor)'),
    'rb1_tamara': ('feminine_2', 'Second feminine voice, explicitly contrasted with Minno'),
    'rb1_uline': ('feminine_3', 'Provisional soft feminine family; direct comparison with Tamara remains open'),
    'rb1_broadcast_envoy': ('mechanical_envoy', 'Powerful envoy mechanical voice'),
}
MOOD_WORDS = {
    'cheerful': 'cheerful', 'excited': 'excited', 'worried': 'worried',
    'sarcastic': 'sarcastic', 'indignant': 'indignant', 'threatening': 'threatening',
    'perplexed': 'perplexed', 'distressed': 'distressed', 'shocked': 'shocked',
    'irritated': 'irritated', 'curious': 'curious',
}
VOICE_ANCHORS = {
    'masculine_4': 'sentence_41bd7ccfb08553d4ee05',
    'masculine_1': 'clip_4748f86f06a2e09c6f4b',
    'masculine_2': 'recovered_bb19f5614ee551e5e3e0',
    'masculine_3': 'rescue_24c82707a4591c69740a',
    'masculine_5': 'clip_8980d07f0d9964562bc7',
    'masculine_6': 'sentence_b184638065d380dc649b',
    'feminine_1': 'clip_e46c3f8ed9067dc2b22f',
    'feminine_2': 'clip_212a58a1e759bf7b0c28',
    'feminine_3': 'clip_597c2a8f41b0e4d663a5',
    'mechanical_stats': 'game_ui_recovered_22234bd7df78e39878d2',
    'mechanical_envoy': 'channel_recorded',
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def lines(path: Path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def eligible(origin: str, row: dict) -> bool:
    length = row['audio_end'] - row['audio_start']
    return (not row.get('flags') and 1 <= length <= 30
            and (origin == 'sentence' and row.get('alignment_kind') == 'complete_sentence_exact_tokens'
                 or origin == 'primary' and row.get('sentence_start') is True
                 and row.get('sentence_end') is True))


def overlaps(a: dict, b: dict) -> bool:
    return (a['recording_id'] == b['recording_id']
            and a['audio_start'] < b['audio_end'] - .02
            and b['audio_start'] < a['audio_end'] - .02)


def main() -> None:
    reviewed = json.loads(REVIEW.read_text(encoding='utf-8'))
    evidence = {case['case_id']: case for case in reviewed['reviewed_cases']}
    atlas = json.loads((SOURCE / 'voice-evidence-atlas/manifest.json').read_text(encoding='utf-8'))
    atlas_cases = {case['id']: case for case in atlas['cases']}
    anchor_paths = {voice: str((SOURCE / 'voice-evidence-atlas' /
                               atlas_cases[case_id]['source_audio']).relative_to(ROOT))
                    for voice, case_id in VOICE_ANCHORS.items()}
    baseline = next(case['comparison']['audio'] for case in atlas['cases'] if case['comparison'])
    anchor_paths['narrator_baseline'] = str((SOURCE / 'voice-evidence-atlas' / baseline).relative_to(ROOT))
    presentation = json.loads((SOURCE / 'reviewed-character-presentation.json').read_text(encoding='utf-8'))
    manual_ui = set(json.loads((SOURCE / 'game-ui-overrides.json').read_text(encoding='utf-8'))['source_ids'])
    context_reviewed_ui = {item['source_id'] for item in presentation['game_ui']
                           if item.get('basis') == 'manual_context_review' or item['source_id'] in manual_ui}
    recording_hashes = {}
    for recording_id in {row['recording_id'] for _, path in INPUTS for row in lines(path)}:
        number = recording_id.rsplit('-', 1)[-1]
        paths = list((ROOT / 'content/reality-benders-1').glob(number + '_*.mp3'))
        if len(paths) != 1:
            raise ValueError(f'Expected one source recording for {recording_id}')
        recording_hashes[recording_id] = digest(paths[0])
    pool = []
    for origin, path in INPUTS:
        pool.extend((origin, row) for row in lines(path) if eligible(origin, row))
    pool.sort(key=lambda item: (item[0] != 'sentence',
                                -(item[1]['audio_end'] - item[1]['audio_start']),
                                item[1]['recording_id'], item[1]['audio_start'], item[1]['id']))
    selected: list[tuple[str, dict]] = []
    for item in pool:
        if not any(overlaps(item[1], other) for _, other in selected):
            selected.append(item)

    seen_ids = set()
    records = []
    for origin, row in sorted(selected, key=lambda item: (item[1]['recording_id'], item[1]['audio_start'])):
        if row['id'] in seen_ids:
            raise ValueError(f'Duplicate clip ID: {row["id"]}')
        seen_ids.add(row['id'])
        audio = Path(row['audio']).resolve()
        if not audio.is_relative_to(ROOT) or not audio.is_file() or digest(audio) != row['audio_sha256']:
            raise ValueError(f'Missing or changed audio: {row["id"]}')
        with wave.open(str(audio), 'rb') as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                raise ValueError(f'Wrong clip audio format: {row["id"]}')
            if abs(wav.getnframes() / wav.getframerate() - (row['audio_end'] - row['audio_start'])) > .025:
                raise ValueError(f'Clip duration differs from source interval: {row["id"]}')
        role = row['role']
        speaker = row.get('speaker')
        if role == 'narration':
            voice, voice_basis = 'narrator_baseline', 'narration role; delivery still needs listening review'
        elif role == 'game_ui':
            if row['source_id'] not in context_reviewed_ui:
                raise ValueError(f"Game role lacks contextual review: {row['source_id']}. Rebuild the source routing; prefix matches cannot select a voice.")
            voice, voice_basis = 'mechanical_stats', 'listener confirmed softer mechanical stats voice'
        elif role == 'character':
            voice, voice_basis = VOICE_PROPOSALS.get(speaker, (None, None))
        else:
            voice, voice_basis = None, None
        item = evidence.get(row['id'])
        note = item['observed_features'] if item else None
        moods = [label for word, label in MOOD_WORDS.items()
                 if note and word in note.casefold()]
        if not moods:
            moods = ['neutral']
            mood_basis = 'weak default from text; confirm or correct by listening'
        else:
            mood_basis = 'listener note for this exact clip'
        raw_delivery = row.get('delivery') or None
        records.append({
            'id': row['id'], 'book_id': 'reality-benders-1', 'narrator_id': 'vlad-rimsky',
            'split': 'validation' if row['chapter_id'] in HELDOUT else 'train',
            'origin': origin, 'recording_id': row['recording_id'],
            'recording_sha256': recording_hashes[row['recording_id']],
            'chapter_id': row['chapter_id'], 'source_id': row['source_id'],
            'text_start': row['text_start'], 'text_end': row['text_end'],
            'source_text': row.get('source_text', row.get('text')),
            'asr_text': row.get('asr_text'),
            'audio': str(audio.relative_to(ROOT)), 'audio_sha256': row['audio_sha256'],
            'audio_start': row['audio_start'], 'audio_end': row['audio_end'],
            'role': role, 'character_id': speaker if role == 'character' else None,
            'base_voice_id_proposed': voice,
            'base_voice_proposal_basis': voice_basis,
            'base_voice_anchor_audio': anchor_paths.get(voice),
            'mood_proposed': moods,
            'mood_proposal_basis': mood_basis,
            'presentation_channel_proposed': raw_delivery if raw_delivery in CHANNELS else None,
            'delivery_proposed': raw_delivery if raw_delivery in DELIVERIES else 'neutral',
            'voice_evidence_verdict': item['verdict'] if item else None,
            'voice_evidence_note': note,
            'review': {'text_checked': False, 'boundaries_checked': False,
                       'speaker_checked': False, 'clean_speech': False,
                       'base_voice_checked': False, 'mood_checked': False},
            'training_accepted': False,
        })
    OUTPUT.mkdir(parents=True, exist_ok=True)
    candidates = OUTPUT / 'candidates.jsonl'
    candidates.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
    summary = {
        'schema': 1, 'book': 'reality-benders-1',
        'purpose': 'review candidates; not a trainable dataset',
        'source_sha256': {path.name: digest(path) for _, path in INPUTS},
        'recording_sha256': recording_hashes,
        'voice_review_import_sha256': digest(REVIEW),
        'voice_atlas_manifest_sha256': digest(SOURCE / 'voice-evidence-atlas/manifest.json'),
        'candidates_sha256': digest(candidates),
        'heldout_chapters': sorted(HELDOUT),
        'overlap_alternatives_removed': len(pool) - len(records),
        'counts': dict(Counter(row['split'] for row in records)),
        'roles': dict(Counter(row['role'] for row in records)),
        'origins': dict(Counter(row['origin'] for row in records)),
        'minutes': {split: round(sum(row['audio_end'] - row['audio_start']
                                     for row in records if row['split'] == split) / 60, 2)
                    for split in ('train', 'validation')},
        'voice_evidence_linked': sum(row['voice_evidence_verdict'] is not None for row in records),
        'base_voice_unassigned': sum(row['base_voice_id_proposed'] is None for row in records),
        'mood_defaulted': sum(row['mood_proposal_basis'].startswith('weak default') for row in records),
        'explicit_voice_families_proposed': dict(Counter(row['base_voice_id_proposed']
                                                       for row in records if row['base_voice_id_proposed'])),
        'training_accepted': 0,
        'next_gate': 'Listen to source/ASR/text and boundaries; assign base voice and mood; then accept clean clips individually.',
    }
    (OUTPUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
