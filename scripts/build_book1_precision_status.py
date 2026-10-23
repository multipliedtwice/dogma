"""Publish a complete Book 1 clip precision audit for the review interface."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections import Counter, defaultdict
from pathlib import Path

from finalize_book1_training_review import BASIC_AUDIO_FAILURES, precision_clip, records

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'training-work/character-rb1/fine-tune-all'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    candidates = FOLDER / 'candidates.jsonl'
    rows = list(records(candidates))
    checksum = digest(candidates)
    quality = json.loads((FOLDER / 'quality-screen.json').read_text(encoding='utf-8'))
    repairs = json.loads((FOLDER / 'precision-v2/repairs.json').read_text(encoding='utf-8'))
    if quality['candidates_sha256'] != checksum or repairs['candidates_sha256'] != checksum:
        raise ValueError('Precision inputs belong to a different candidate manifest')
    audit_rows = list(records(FOLDER / 'precision-v2/clip-asr-audit.jsonl'))
    audits = {row['id']: row for row in audit_rows}
    if len(audits) != len(audit_rows):
        raise ValueError('Duplicate independent clip ASR result')
    required = {row['id'] for row in rows if repairs['clips'][row['id']]['status'] == 'recut_unverified'}
    missing = required - set(audits)
    if missing:
        raise ValueError(f'Independent clip ASR is incomplete: {len(missing)} clips remain')
    clips = {}
    for row in rows:
        cid = row['id']
        repair = repairs['clips'][cid]
        audit = audits.get(cid)
        if BASIC_AUDIO_FAILURES.intersection(quality['clips'][cid]['reasons']):
            status = 'hold_basic_audio'
        elif row['role'] == 'mixed':
            status = 'hold_mixed_voices'
        elif repair['status'] != 'recut_unverified':
            status = 'hold_no_quiet_edge'
        elif not audit or audit['audio_sha256'] != repair['audio_sha256']:
            status = 'hold_asr_missing_or_stale'
        elif audit['expected'] != row['source_text'] or audit['edits']:
            status = 'hold_spoken_text_mismatch'
        elif precision_clip(row, repair, audit) is None:
            status = 'hold_boundary_or_integrity'
        else:
            status = 'precision_pass'
        item = {'status': status}
        if repair['status'] == 'recut_unverified':
            item.update(audio=repair['audio'], audio_start=repair['audio_start'],
                        audio_end=repair['audio_end'], audio_sha256=repair['audio_sha256'])
        if audit and status == 'hold_spoken_text_mismatch':
            item['heard'] = audit['heard']
            item['edits'] = audit['edits']
        clips[cid] = item
    by_recording = defaultdict(list)
    for row in rows:
        item = clips[row['id']]
        if item['status'] == 'precision_pass':
            by_recording[row['recording_id']].append((item['audio_start'], item['audio_end'], row['id']))
    overlapping = set()
    for group in by_recording.values():
        group.sort()
        for previous, current in zip(group, group[1:]):
            if current[0] < previous[1] - .001:
                overlapping.update((previous[2], current[2]))
    for cid in overlapping:
        clips[cid]['status'] = 'hold_overlap'
    corpus = json.loads((ROOT / 'training-work/corpus-rb1-current/corpus.json').read_text(encoding='utf-8'))
    book = json.loads((ROOT / 'training-work/corpus-rb1-current' /
                       corpus['books'][0]['manifest']).read_text(encoding='utf-8'))
    text_order = {block['id']: (chapter_index, block_index)
                  for chapter_index, chapter in enumerate(book['chapters'])
                  for block_index, block in enumerate(chapter['blocks'])}
    out_of_order = set()
    by_recording = defaultdict(list)
    for row in rows:
        if clips[row['id']]['status'] == 'precision_pass':
            by_recording[row['recording_id']].append(row)
    for group in by_recording.values():
        group.sort(key=lambda row: clips[row['id']]['audio_start'])
        for previous, current in zip(group, group[1:]):
            prev_key = (*text_order[previous['source_id']], previous['text_start'])
            curr_key = (*text_order[current['source_id']], current['text_start'])
            same_source_overlap = (previous['source_id'] == current['source_id']
                                   and current['text_start'] < previous['text_end'])
            if curr_key < prev_key or same_source_overlap:
                out_of_order.update((previous['id'], current['id']))
    for cid in out_of_order:
        clips[cid]['status'] = 'hold_text_order'
    accepted = [row for row in rows if clips[row['id']]['status'] == 'precision_pass']
    model_dir = ROOT / 'training-work/models/Qwen3-ASR-0.6B'
    model_files = {str(path.relative_to(model_dir)).replace('\\', '/'): digest(path)
                   for path in sorted(model_dir.rglob('*')) if path.is_file()}
    report = {'schema': 1, 'candidates_sha256': checksum,
              'method': 'quiet-gap recut plus independent ASR of each finished WAV',
              'asr_provenance': {'model': 'Qwen3-ASR-0.6B',
                                 'model_files_sha256': model_files,
                                 'qwen_asr_version': importlib.metadata.version('qwen-asr'),
                                 'repairs_sha256': digest(FOLDER / 'precision-v2/repairs.json'),
                                 'clip_asr_audit_sha256': digest(FOLDER / 'precision-v2/clip-asr-audit.jsonl')},
              'limits': 'An exact ASR match does not prove the narrator, absence of noise, or emotional label.',
              'counts': dict(Counter(item['status'] for item in clips.values())),
              'precision_minutes': round(sum(clips[row['id']]['audio_end']-
                                             clips[row['id']]['audio_start'] for row in accepted)/60, 2),
              'by_recording': dict(Counter(row['recording_id'] for row in accepted)),
              'by_split': dict(Counter(row['split'] for row in accepted)),
              'by_role': dict(Counter(row['role'] for row in accepted)),
              'held_first_word_mismatch': sum(bool(audits.get(row['id']) and
                                              not audits[row['id']]['first_word_match']) for row in rows),
              'held_last_word_mismatch': sum(bool(audits.get(row['id']) and
                                             not audits[row['id']]['last_word_match']) for row in rows),
              'clips': clips}
    target = FOLDER / 'precision-v2/status.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['counts'], ensure_ascii=True))


if __name__ == '__main__':
    main()
