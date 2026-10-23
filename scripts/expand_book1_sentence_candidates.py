"""Propose additional Book 1 sentence clips from timed ASR; never auto-approve them."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training'), str(ROOT / 'scripts')]
from rimsky.books import sentence_spans  # noqa: E402
from rimsky.preparation import narratable  # noqa: E402
from rimsky_training.pairing import tokens  # noqa: E402
from prepare_book1_training_candidates import VOICE_PROPOSALS, VOICE_ANCHORS  # noqa: E402

SOURCE = ROOT / 'training-work/character-rb1'
CORPUS = ROOT / 'training-work/corpus-rb1-current'
EXISTING = SOURCE / 'fine-tune-candidates/candidates.jsonl'
OUTPUT = SOURCE / 'fine-tune-expanded'
HELDOUT = {'c0019', 'c0038'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def classify(source_id: str, start: int, end: int, reviewed: dict, game_ui: set) -> tuple[str, str | None]:
    item = reviewed.get(source_id)
    if source_id in game_ui and not item:
        return 'game_ui', 'mechanical_game_voice'
    if not item:
        return 'narration', 'narrator'
    parts = {(part['mode'], part.get('speaker')) for part in item.get('parts', [])
             if part['start'] < end and part['end'] > start}
    if not parts or parts == {('narrator', 'narrator')}:
        return 'narration', 'narrator'
    if len(parts) == 1:
        mode, speaker = next(iter(parts))
        if mode == 'speech' and speaker and speaker != 'unknown':
            return 'character', speaker
    return 'mixed', None


def overlaps(a: dict, b: dict) -> bool:
    return (a['recording_id'] == b['recording_id']
            and a['audio_start'] < b['audio_end'] - .02
            and b['audio_start'] < a['audio_end'] - .02)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cut-audio', action='store_true', help='Decode each source recording once and write WAV clips')
    args = parser.parse_args()
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    corpus = json.loads((CORPUS / 'corpus.json').read_text(encoding='utf-8'))
    book = json.loads((CORPUS / corpus['books'][0]['manifest']).read_text(encoding='utf-8'))
    chapters = {chapter['id']: chapter for chapter in book['chapters']}
    analysis = json.loads((SOURCE / 'reviewed-character-analysis.json').read_text(encoding='utf-8'))
    reviewed = {item['source_id']: item for item in analysis['passages']}
    presentation = json.loads((SOURCE / 'reviewed-character-presentation.json').read_text(encoding='utf-8'))
    manual_ui = set(json.loads((SOURCE / 'game-ui-overrides.json').read_text(encoding='utf-8'))['source_ids'])
    game_ui = {item['source_id'] for item in presentation['game_ui']
               if item.get('basis') == 'manual_context_review' or item['source_id'] in manual_ui}
    channels = {item['source_id']: item['delivery'] for item in presentation['speech_delivery']}
    atlas = json.loads((SOURCE / 'voice-evidence-atlas/manifest.json').read_text(encoding='utf-8'))
    atlas_cases = {item['id']: item for item in atlas['cases']}
    anchors = {voice: str((SOURCE / 'voice-evidence-atlas' /
                           atlas_cases[case]['source_audio']).relative_to(ROOT))
               for voice, case in VOICE_ANCHORS.items()}
    baseline = next(item['comparison']['audio'] for item in atlas['cases'] if item['comparison'])
    anchors['narrator_baseline'] = str((SOURCE / 'voice-evidence-atlas' / baseline).relative_to(ROOT))
    existing = [json.loads(line) for line in EXISTING.read_text(encoding='utf-8').splitlines() if line]
    by_recording = defaultdict(list)
    for row in existing:
        by_recording[row['recording_id']].append(row)
    proposals = []
    counts = Counter()
    for recording in corpus['recordings']:
        recording_id = recording['id']
        chapter_id = f'c{int(recording_id.rsplit("-", 1)[-1])+4:04d}'
        chapter = chapters[chapter_id]
        timed = json.loads((ROOT / 'training-work/full-book-aligned' / recording_id /
                            'transcript.json').read_text(encoding='utf-8'))
        if timed['recording_sha256'] != recording['sha256']:
            raise ValueError(f'Changed source recording: {recording_id}')
        words = timed['words']
        source_tokens, spans = [], []
        for block in chapter['blocks']:
            if not narratable(block) or 'knigolub.net' in block['text'].casefold():
                continue
            for left, right in sentence_spans(block['text']):
                text = block['text'][left:right]
                lexical = tokens(text)
                if len(lexical) < 3:
                    continue
                first = len(source_tokens)
                source_tokens.extend(token[0] for token in lexical)
                spans.append((block, left, right, first, len(lexical)))
        asr_tokens = [tokens(word['text'])[0][0] for word in words]
        mapping = {}
        for block in SequenceMatcher(None, source_tokens, asr_tokens, autojunk=False).get_matching_blocks():
            if block.size >= 2:
                mapping.update((block.a + offset, block.b + offset) for offset in range(block.size))
        for block, left, right, first, length in spans:
            indices = [mapping.get(first + offset) for offset in range(length)]
            matched = [index for index in indices if index is not None]
            ratio = len(matched) / length
            if (ratio < .7 or indices[0] is None or indices[-1] is None
                    or indices[-1] - indices[0] > length + 4):
                counts['no_safe_alignment'] += 1
                continue
            start, end = words[indices[0]]['start'], words[indices[-1]]['end']
            if not 1 <= end - start <= 30:
                counts['duration'] += 1
                continue
            role, speaker = classify(block['id'], left, right, reviewed, game_ui)
            if role == 'narration':
                voice, basis = 'narrator_baseline', 'narration role; still needs audio review'
            elif role == 'game_ui':
                voice, basis = 'mechanical_stats', 'listener-confirmed stats voice family; this clip unreviewed'
            elif role == 'character':
                voice, basis = VOICE_PROPOSALS.get(speaker, (None, None))
            else:
                voice, basis = None, None
            clip_id = 'expanded_' + hashlib.sha256(
                f'{recording_id}:{block["id"]}:{left}:{right}:{start:.3f}:{end:.3f}'.encode()).hexdigest()[:20]
            proposals.append({'id': clip_id, 'book_id': 'reality-benders-1', 'narrator_id': 'vlad-rimsky',
                              'split': 'validation' if chapter_id in HELDOUT else 'train',
                              'origin': 'expanded_sentence_alignment', 'recording_id': recording_id,
                              'recording_sha256': recording['sha256'], 'chapter_id': chapter_id,
                              'source_id': block['id'], 'text_start': left, 'text_end': right,
                              'source_text': block['text'][left:right],
                              'asr_text': ' '.join(word['text'] for word in words[indices[0]:indices[-1]+1]),
                              'asr_anchor_ratio': round(ratio, 3),
                              'audio_start': start, 'audio_end': end,
                              'role': role, 'character_id': speaker if role == 'character' else None,
                              'base_voice_id_proposed': voice, 'base_voice_proposal_basis': basis,
                              'base_voice_anchor_audio': anchors.get(voice),
                              'mood_proposed': ['neutral'],
                              'mood_proposal_basis': 'weak default from text; confirm or correct by listening',
                              'presentation_channel_proposed': channels.get(block['id']),
                              'delivery_proposed': 'neutral',
                              'voice_evidence_verdict': None, 'voice_evidence_note': None,
                              'alignment_kind': 'sentence_boundary_exact_anchors',
                              'flags': ([] if ratio >= .9 else ['asr_mismatch']) +
                                       (['mixed_performance'] if role == 'mixed' else []),
                              'review': {'text_checked': False, 'boundaries_checked': False,
                                         'speaker_checked': False, 'clean_speech': False,
                                         'base_voice_checked': False, 'mood_checked': False},
                              'training_accepted': False})
    proposals.sort(key=lambda row: (row['recording_id'], row['audio_start'], row['id']))
    chosen = []
    chosen_by_recording = defaultdict(list)
    for row in proposals:
        if any(overlaps(row, other) for other in by_recording[row['recording_id']]):
            counts['overlaps_existing'] += 1
            continue
        if any(overlaps(row, other) for other in chosen_by_recording[row['recording_id']]):
            counts['overlaps_expanded'] += 1
            continue
        chosen.append(row)
        chosen_by_recording[row['recording_id']].append(row)
    OUTPUT.mkdir(parents=True)
    clips = OUTPUT / 'clips'
    if args.cut_audio:
        clips.mkdir()
        grouped = defaultdict(list)
        for row in chosen:
            grouped[row['recording_id']].append(row)
        for recording in corpus['recordings']:
            rows = grouped.get(recording['id'], [])
            if not rows:
                continue
            with tempfile.TemporaryDirectory(prefix='rb1-expanded-', dir=OUTPUT) as temp:
                decoded = Path(temp) / 'recording.wav'
                source = CORPUS / recording['path']
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                                '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '24000',
                                '-c:a', 'pcm_s16le', str(decoded)], check=True)
                with wave.open(str(decoded), 'rb') as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (24000, 1, 2):
                        raise ValueError('Unexpected decoded audio format')
                    for row in rows:
                        first = round(row['audio_start'] * 24000)
                        last = round(row['audio_end'] * 24000)
                        wav.setpos(first)
                        audio = wav.readframes(last - first)
                        target = clips / f'{row["id"]}.wav'
                        with wave.open(str(target), 'wb') as out:
                            out.setnchannels(1)
                            out.setsampwidth(2)
                            out.setframerate(24000)
                            out.writeframes(audio)
                        row['audio'] = str(target.relative_to(ROOT))
                        row['audio_sha256'] = digest(target)
            print(f'{recording["id"]}: {len(rows)} new sentence clips', flush=True)
    manifest = OUTPUT / 'candidates.jsonl'
    manifest.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in chosen), encoding='utf-8')
    summary = {'schema': 1, 'book': 'reality-benders-1', 'purpose': 'additional unreviewed sentence candidates',
               'source_hours': round(sum(r['audio']['duration'] for r in corpus['recordings'])/3600, 2),
               'candidate_count': len(chosen),
               'candidate_hours': round(sum(r['audio_end']-r['audio_start'] for r in chosen)/3600, 2),
               'train_count': sum(r['split'] == 'train' for r in chosen),
               'validation_count': sum(r['split'] == 'validation' for r in chosen),
               'heldout_chapters': sorted(HELDOUT),
               'by_role': dict(Counter(r['role'] for r in chosen)),
               'by_flag': dict(Counter(flag for r in chosen for flag in r['flags'])),
               'exclusions': dict(counts), 'audio_cut': args.cut_audio,
               'candidates_sha256': digest(manifest), 'training_accepted': 0}
    (OUTPUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
