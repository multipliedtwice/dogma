"""Validate locally reviewed Book 1 clips and emit official dataset review rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import wave
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'training-work/character-rb1'
CORPUS = ROOT / 'training-work/corpus-rb1-current'
DELIVERIES = {'neutral', 'soft', 'whisper', 'firm', 'excited', 'sad', 'angry',
              'questioning', 'enumeration', 'stunned'}
VOICE_TAXONOMY = json.loads((ROOT / 'training/voice_families.json').read_text(encoding='utf-8'))
VOICE_FAMILIES = {family['id'] for family in VOICE_TAXONOMY['families']}
LEGACY_VOICE_ALIASES = VOICE_TAXONOMY.get('legacy_aliases', {})
BASIC_AUDIO_FAILURES = {'missing_or_changed_audio', 'unexpected_pcm_format', 'empty_audio',
                        'duration_mismatch', 'clipping', 'level_outlier'}
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]
from rimsky_training.pairing import compound_tokens  # noqa: E402
from review_label_resolution import canonical_review_mood, resolved_review_mood  # noqa: E402


def voice_family(value):
    value = LEGACY_VOICE_ALIASES.get(value, value)
    return value if value in VOICE_FAMILIES else None


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def records(path: Path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def resolved(proposed, verdict, correction):
    if verdict == 'yes' and proposed:
        return proposed
    return correction.strip() if isinstance(correction, str) else None


def canonical_mood(value):
    return canonical_review_mood(value)


def bucket_review_matches(decision, voice, mood, audio_sha256):
    check = decision.get('bucket_review', {})
    return (isinstance(check, dict) and check.get('verdict') == 'consistent'
            and check.get('voice_family_id') == voice and canonical_mood(check.get('mood')) == canonical_mood(mood)
            and check.get('audio_sha256') == audio_sha256)


def precision_clip(candidate: dict, repair: dict | None, audit: dict | None) -> dict | None:
    """Require a new quiet-gap cut and independent exact WAV transcription."""
    if not repair or repair.get('status') != 'recut_unverified' or not audit:
        return None
    if (repair.get('id') != candidate['id'] or audit.get('id') != candidate['id']
            or repair.get('original_audio_sha256') != candidate['audio_sha256']
            or audit.get('audio_sha256') != repair.get('audio_sha256')
            or audit.get('expected') != candidate['source_text']
            or [word for word, _, _ in compound_tokens(audit.get('heard', ''))] !=
               [word for word, _, _ in compound_tokens(candidate['source_text'])]):
        return None
    audio = ROOT / repair['audio']
    if (not audio.resolve().is_relative_to((BASE / 'fine-tune-all/precision-v2/clips').resolve())
            or not audio.is_file() or digest(audio) != repair['audio_sha256']):
        return None
    if not 1 <= repair['audio_end'] - repair['audio_start'] <= 30:
        return None
    if repair['audio_start'] > candidate['audio_start'] + .12 or repair['audio_end'] < candidate['audio_end'] - .12:
        return None
    with wave.open(str(audio), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
            return None
        if abs(wav.getnframes()/24000 - (repair['audio_end']-repair['audio_start'])) > .01:
            return None
        edge_frames = 960
        beginning = wav.readframes(edge_frames)
        wav.setpos(max(0, wav.getnframes()-edge_frames))
        ending = wav.readframes(edge_frames)
    for chunk in (beginning, ending):
        samples = struct.unpack('<' + str(len(chunk)//2) + 'h', chunk)
        level = math.sqrt(sum(value*value for value in samples)/len(samples))/32768
        if 20*math.log10(max(level, 1e-9)) > -34:
            return None
    return repair


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expanded', action='store_true', help='Validate the supplemental sentence pool')
    parser.add_argument('--all', action='store_true', help='Validate the combined whole-book queue')
    parser.add_argument('--review', type=Path, help='Review JSON (defaults to the selected board draft)')
    parser.add_argument('--output', type=Path, help='New directory for reviewed JSONL and report')
    parser.add_argument('--require-bucket-review', action='store_true',
                        help='Require Step 2 consistency confirmation for the current voice, mood, and exact WAV')
    args = parser.parse_args()
    if args.expanded and args.all:
        parser.error('Select one candidate pool')
    if not args.all:
        parser.error('The older candidate exports are retired after the boundary audit; use --all.')
    folder = BASE / ('fine-tune-all' if args.all else
                     'fine-tune-expanded' if args.expanded else 'fine-tune-candidates')
    review_path = args.review or folder / 'review-draft.json'
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    candidates_file = folder / 'candidates.jsonl'
    if digest(candidates_file) != summary['candidates_sha256']:
        raise ValueError('Candidate file changed')
    candidates = list(records(candidates_file))
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.is_file() else {
        'schema': 1, 'candidates_sha256': summary['candidates_sha256'], 'decisions': {}}
    if (review.get('schema') != 1 or review.get('candidates_sha256') != summary['candidates_sha256']
            or not isinstance(review.get('decisions'), dict)):
        raise ValueError('Review does not match current candidates')
    decisions = review['decisions']
    indexed = {row['id']: row for row in candidates}
    if set(decisions) - set(indexed):
        raise ValueError('Review contains unknown clip IDs')
    quality = {}
    overrides = {}
    repairs, clip_audits, precision_status = {}, {}, {}
    precision_complete = False
    role_proposals_path = folder / 'role-voice-proposals.json'
    role_proposals = (json.loads(role_proposals_path.read_text(encoding='utf-8'))['roles']
                      if role_proposals_path.is_file() else {})
    if args.all:
        quality_report = json.loads((folder / 'quality-screen.json').read_text(encoding='utf-8'))
        if quality_report['candidates_sha256'] != summary['candidates_sha256']:
            raise ValueError('Audio quality screen is stale')
        quality = quality_report['clips']
        override_report = json.loads((folder / 'voice-proposal-overrides.json').read_text(encoding='utf-8'))
        if override_report.get('candidates_sha256', summary['candidates_sha256']) != summary['candidates_sha256']:
            raise ValueError('Context proposals are stale')
        overrides = override_report['clips']
        precision_dir = folder / 'precision-v2'
        repair_path = precision_dir / 'repairs.json'
        audit_path = precision_dir / 'clip-asr-audit.jsonl'
        if repair_path.is_file():
            repair_report = json.loads(repair_path.read_text(encoding='utf-8'))
            if repair_report['candidates_sha256'] != summary['candidates_sha256']:
                raise ValueError('Precision repairs are stale')
            repairs = repair_report['clips']
        status_path = precision_dir / 'status.json'
        if status_path.is_file():
            if not audit_path.is_file() or not repair_path.is_file():
                raise ValueError('Precision audit inputs are missing')
            status_report = json.loads(status_path.read_text(encoding='utf-8'))
            if (status_report['candidates_sha256'] != summary['candidates_sha256']
                    or status_report['asr_provenance']['clip_asr_audit_sha256'] != digest(audit_path)
                    or status_report['asr_provenance']['repairs_sha256'] != digest(repair_path)):
                raise ValueError('Precision audit status is stale')
            clip_audits = {row['id']: row for row in records(audit_path)}
            precision_status = status_report['clips']
            precision_complete = True
    corpus = json.loads((CORPUS / 'corpus.json').read_text(encoding='utf-8'))
    book = json.loads((CORPUS / corpus['books'][0]['manifest']).read_text(encoding='utf-8'))
    sources = {block['id']: block for chapter in book['chapters'] for block in chapter['blocks']}
    recordings = {row['id']: row for row in corpus['recordings']}
    if corpus['narrator']['id'] != 'vlad-rimsky':
        raise ValueError('Wrong narrator corpus')
    reviewer = review.get('reviewer', '').strip() or 'local-listener'
    ready, pending, rejected = [], [], []
    unresolved_voice_labels = Counter()
    boundary_repairs = []
    bucket_verified, bucket_pending = [], []
    for candidate in candidates:
        cid = candidate['id']
        decision = decisions.get(cid, {})
        if not isinstance(decision, dict):
            raise ValueError(f'Invalid decision for {cid}')
        if decision.get('rejected') is True:
            rejected.append(cid)
            continue
        if decision.get('split_review', {}).get('enabled') is True:
            # A whole-clip approval predating a split cannot approve its mixed
            # delivery or any newly cut part. Separate part audits are required.
            pending.append(cid)
            continue
        bad_edges = [edge for edge, field in (('start', 'boundary_start'), ('end', 'boundary_end'))
                     if decision.get(field) is True]
        if bad_edges:
            boundary_repairs.append({'candidate_id': cid, 'source_id': candidate['source_id'],
                                     'edges': bad_edges,
                                     'note': decision.get('group_comment', decision.get('comment', ''))})
        proposal = overrides.get(cid, {}).get('base_voice_id_proposed',
                    role_proposals.get(candidate['character_id'], {}).get('base_voice_id_proposed',
                    candidate['base_voice_id_proposed']))
        # Older saved Yes answers refer to the original immutable candidate proposal.
        if decision.get('voice_verdict') == 'yes':
            proposal = decision.get('confirmed_voice_id') or candidate['base_voice_id_proposed']
        raw_voice = resolved(proposal, decision.get('voice_verdict'),
                             decision.get('corrected_voice_id'))
        voice = voice_family(raw_voice)
        if raw_voice and not voice:
            unresolved_voice_labels[raw_voice] += 1
        mood = resolved_review_mood(', '.join(candidate['mood_proposed'] or []),
                                    decision.get('mood_verdict'),
                                    decision.get('corrected_mood'))
        precise = precision_clip(candidate, repairs.get(cid), clip_audits.get(cid)) if args.all else None
        bucket_checked = bool(precise and bucket_review_matches(decision, voice, mood, precise['audio_sha256']))
        gates = (precision_complete and precision_status.get(cid, {}).get('status') == 'precision_pass'
                 and not BASIC_AUDIO_FAILURES.intersection(quality.get(cid, {}).get('reasons', []))
                 and quality.get(cid, {}).get('audio_sha256') == candidate['audio_sha256']
                 and precise is not None) if args.all else all(
                     decision.get(key) is True for key in
                     ('text_checked', 'boundaries_checked', 'speaker_checked', 'clean_speech'))
        delivery = candidate.get('delivery_proposed') if args.all else decision.get('delivery')
        if args.all and delivery not in DELIVERIES:
            delivery = 'neutral'
        if (decision.get('voice_verdict') == 'unsure' or decision.get('mood_verdict') == 'unsure'
                or bad_edges or not voice or not mood or delivery not in DELIVERIES or not gates):
            pending.append(cid)
            continue
        (bucket_verified if bucket_checked else bucket_pending).append(cid)
        if args.require_bucket_review and not bucket_checked:
            pending.append(cid)
            continue
        source = sources.get(candidate['source_id'])
        if source is None or source['text'][candidate['text_start']:candidate['text_end']] != candidate['source_text']:
            raise ValueError(f'Source text changed: {cid}')
        recording = recordings.get(candidate['recording_id'])
        if recording is None or recording['sha256'] != candidate['recording_sha256']:
            raise ValueError(f'Source recording changed: {cid}')
        audio = ROOT / candidate['audio']
        if not audio.is_file() or digest(audio) != candidate['audio_sha256']:
            raise ValueError(f'Candidate audio changed: {cid}')
        if candidate['role'] == 'mixed':
            pending.append(cid)
            continue
        row = {
            'id': cid, 'corpus_revision': corpus['revision'], 'narrator_id': 'vlad-rimsky',
            'book_id': 'reality-benders-1', 'chapter_id': candidate['chapter_id'],
            'source_id': candidate['source_id'], 'text_start': candidate['text_start'],
            'text_end': candidate['text_end'], 'source_text': candidate['source_text'],
            'spoken_text': candidate['source_text'],
            'recording_id': candidate['recording_id'], 'recording_sha256': candidate['recording_sha256'],
            'audio_start': precise['audio_start'] if args.all else candidate['audio_start'],
            'audio_end': precise['audio_end'] if args.all else candidate['audio_end'],
            'performance': voice, 'voice_family_id': voice,
            'character_id': candidate['character_id'],
            'mood': mood, 'presentation_channel': overrides.get(cid, {}).get(
                'presentation_channel_proposed', candidate['presentation_channel_proposed']),
            'context_suggestion': overrides.get(cid, {}).get('context_suggestion'),
            'delivery': delivery, 'speaker': candidate['character_id'] or 'narrator',
            'review': {'accepted': True, 'reviewer': reviewer, 'text_checked': True,
                       'boundaries_checked': True, 'speaker_checked': True, 'clean_speech': True,
                       'performance_checked': True, 'performance_cues': [],
                       'quality_method': 'quiet_gap_recut_independent_exact_clip_asr' if args.all else 'listener_check',
                       'quality_screen_limits': ('Independent ASR and quiet-gap cuts reduce text and edge errors; they cannot prove speaker identity or absence of background noise.'
                                                 if args.all else None)},
            'candidate_audio_sha256': precise['audio_sha256'] if args.all else candidate['audio_sha256'],
            'candidate_review_comment': decision.get('group_comment', decision.get('comment', '')),
            'bucket_review': decision.get('bucket_review') if bucket_checked else None,
            'bucket_review_comment': decision.get('bucket_comment', ''),
        }
        ready.append(row)
    # Quiet-gap correction can enlarge two neighboring clips into the same
    # pause. Keep both out if they overlap rather than repeat spoken audio.
    overlapping = set()
    by_recording = {}
    for row in ready:
        by_recording.setdefault(row['recording_id'], []).append(row)
    for group in by_recording.values():
        group.sort(key=lambda row: row['audio_start'])
        for previous, current in zip(group, group[1:]):
            if current['audio_start'] < previous['audio_end'] - .001:
                overlapping.update((previous['id'], current['id']))
    if overlapping:
        ready = [row for row in ready if row['id'] not in overlapping]
        pending.extend(sorted(overlapping))
    report = {'schema': 1, 'book': 'reality-benders-1',
              'candidates_sha256': summary['candidates_sha256'],
              'review_sha256': digest(review_path) if review_path.is_file() else None,
              'corpus_revision': corpus['revision'], 'reviewer': reviewer or None,
              'accepted': len(ready), 'pending': len(pending), 'rejected': len(rejected),
              'boundary_holds': len(boundary_repairs), 'boundary_repairs': boundary_repairs,
              'bucket_review_required': args.require_bucket_review,
              'bucket_verified': len(bucket_verified), 'bucket_pending': len(bucket_pending),
              'precision_recuts': len(repairs), 'precision_asr_checked': len(clip_audits),
              'precision_complete': precision_complete,
              'precision_exact': sum(precision_clip(candidate, repairs.get(candidate['id']),
                                     clip_audits.get(candidate['id'])) is not None
                                     for candidate in candidates) if args.all else None,
              'precision_overlap_holds': len(overlapping),
              'performance_counts': dict(Counter(row['performance'] for row in ready)),
              'voice_family_taxonomy_schema': VOICE_TAXONOMY['schema'],
              'unresolved_voice_labels': dict(unresolved_voice_labels),
              'accepted_minutes': round(sum(row['audio_end'] - row['audio_start'] for row in ready)/60, 2),
              'train': sum(row['chapter_id'] not in summary['heldout_chapters'] for row in ready),
              'validation': sum(row['chapter_id'] in summary['heldout_chapters'] for row in ready),
              'limitation': 'Only the chosen performance can be passed to the official single-speaker trainer. Rich mood/channel labels are preserved as metadata.'}
    if args.output:
        if args.output.exists():
            raise FileExistsError(args.output)
        args.output.mkdir(parents=True)
        (args.output / 'reviewed.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n'
                                                       for row in ready), encoding='utf-8')
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
