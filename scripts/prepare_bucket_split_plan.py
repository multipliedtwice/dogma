"""Versioned word boundaries for review splits; never rewrite corpus candidates.

Alignment proposes listening boundaries, not approved training cuts. Context
parts only suggest where delivery may change; the listener chooses the labels.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import wave
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'training'), str(ROOT / 'runtime')]
from rimsky_training.pairing import tokens
from rimsky.util import atomic_write


DELIVERY_PUNCTUATION = frozenset(',.;:!?\u2026\u00bb\u201d"')
FOLD = str.maketrans({'\u0451': '\u0438', '\u0435': '\u0438', '\u043e': '\u0430'})
NARRATOR_PLACEHOLDER = re.compile(
    r'\(\s*(?:нецензурно|непонятно)\s*\)', re.IGNORECASE)


def dash_boundaries(text):
    """Return internal em-dash boundaries and their delivery classification."""
    result = []
    # Some editions typeset Russian dialogue with U+2013 rather than U+2014.
    # Treat both as structural dash signals; a leading dialogue dash is still
    # excluded below and punctuation on the left determines delivery status.
    for match in re.finditer(r'[\u2013\u2014]\s*', text):
        if match.start() == 0 or match.end() >= len(text):
            continue
        before = text[:match.start()].rstrip()
        result.append({'start': match.start(), 'offset': match.end(),
                       'delivery_transition': bool(before and before[-1] in DELIVERY_PUNCTUATION)})
    return result


def structural_dash_offsets(text):
    """Return all internal em-dash boundaries, including grammatical dashes."""
    return [row['offset'] for row in dash_boundaries(text)]


def delivery_dash_offsets(text):
    """Return dashes that follow dialogue/attribution punctuation."""
    return [row['offset'] for row in dash_boundaries(text) if row['delivery_transition']]


def narrator_placeholder_spans(text):
    """Return printed speech placeholders performed in the narrator register."""
    return [{'start': match.start(), 'end': match.end(),
             'voice_family_id': 'narrator_baseline', 'kind': 'spoken_placeholder'}
            for match in NARRATOR_PLACEHOLDER.finditer(text)]


def parenthetical_spans(text):
    """Balanced outer parentheses are delivery-review spans, not voice verdicts."""
    spans, stack = [], []
    for offset, character in enumerate(text):
        if character == '(':
            stack.append(offset)
        elif character == ')' and stack:
            start = stack.pop()
            if not stack and tokens(text[start + 1:offset]):
                spans.append({'start': start, 'end': offset + 1,
                              'kind': 'parenthetical_aside'})
    return spans


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tolerant_key(value):
    """Fold only the explicitly allowed Russian ASR spelling variations."""
    return value.replace('\u0451', '\u0435').translate(FOLD)


def anchor_equal(expected, heard):
    if expected == heard or tolerant_key(expected) == tolerant_key(heard):
        return True
    # Vlad inflects the printed placeholder to fit the sentence, for example
    # (\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u043e) -> \u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d\u0443\u044e.  This narrow stem match is only
    # for a known spoken placeholder; ordinary words remain strict.
    return expected.startswith('\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d') and heard.startswith('\u043d\u0435\u0446\u0435\u043d\u0437\u0443\u0440\u043d')


def token_boundary(entries, offset):
    """Return the first lexical token starting after a punctuation boundary."""
    for index, (_, start, _) in enumerate(entries):
        if start >= offset:
            return index
    return None


def anchored_dash_cut(entries, audio, offset, audio_start, audio_end):
    """Locate one dash from local ASR anchors without aligning the whole clip.

    The adjacent words are mandatory. Up to three words on each side confirm
    the location. A single-word side is allowed only with a clear acoustic gap.
    Taking lexical anchors naturally extends across a neighbouring dash.
    """
    split = token_boundary(entries, offset)
    if split is None or split == 0 or split == len(entries):
        return None
    expected = [item[0] for item in entries]
    left_count, right_count = min(3, split), min(3, len(entries) - split)
    left = expected[split-left_count:split]
    right = expected[split:split+right_count]
    local = [(word, data) for word, data in audio
             if data['end'] >= audio_start - .25 and data['start'] <= audio_end + .25]
    candidates = []
    for cursor in range(1, len(local)):
        if not anchor_equal(left[-1], local[cursor-1][0]) or not anchor_equal(right[0], local[cursor][0]):
            continue
        heard_left = [word for word, _ in local[max(0, cursor-left_count):cursor]]
        heard_right = [word for word, _ in local[cursor:cursor+right_count]]
        if len(heard_left) != left_count or len(heard_right) != right_count:
            continue
        left_matches = sum(anchor_equal(a, b) for a, b in zip(left, heard_left))
        right_matches = sum(anchor_equal(a, b) for a, b in zip(right, heard_right))
        if left_matches < min(2, left_count) or right_matches < min(2, right_count):
            continue
        before, after = local[cursor-1][1], local[cursor][1]
        gap = after['start'] - before['end']
        if gap < -.02 or gap > 2.0 or (min(left_count, right_count) == 1 and gap < .05):
            continue
        midpoint = (before['end'] + after['start']) / 2
        if not audio_start < midpoint < audio_end:
            continue
        score = left_matches + right_matches
        exact = sum(a == b for a, b in zip(left, heard_left)) + sum(a == b for a, b in zip(right, heard_right))
        candidates.append({'time': midpoint, 'pause_seconds': round(gap, 3),
                           'anchor_words': left_count + right_count,
                           'anchor_matches': score, 'exact_anchor_matches': exact,
                           'adjacent_match': ('exact' if left[-1] == local[cursor-1][0]
                                              and right[0] == local[cursor][0] else 'vowel_folded')})
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row['anchor_matches'], -row['exact_anchor_matches'],
                                     abs(row['time'] - (audio_start + audio_end) / 2)))
    best = candidates[0]
    if len(candidates) > 1:
        other = candidates[1]
        if (best['anchor_matches'], best['exact_anchor_matches']) == (other['anchor_matches'], other['exact_anchor_matches']):
            return None
    return best


def forced_dash_cut(entries, items, offset):
    split = token_boundary(entries, offset)
    if split is None or split == 0 or split >= len(items) or len(items) != len(entries):
        return None
    expected = [item[0] for item in entries]
    heard = [part for item in items for part, _, _ in tokens(item['text'])]
    if heard != expected:
        return None
    before, after = items[split-1], items[split]
    gap = float(after['start']) - float(before['end'])
    if gap < -.02 or gap > 2.0:
        return None
    return {'time': (float(before['end']) + float(after['start'])) / 2,
            'pause_seconds': round(gap, 3), 'anchor_words': 0,
            'anchor_matches': 0, 'exact_anchor_matches': 0,
            'adjacent_match': 'forced_text_alignment'}


def cut_record(row, offset, timing, rate, frames, method, absolute=True):
    seconds = timing['time'] - row['audio_start'] if absolute else timing['time']
    frame = round(seconds * rate)
    if not 0 < frame < frames:
        return None
    return {'offset': offset, 'frame': frame,
            'before': row['source_text'][:offset].strip()[-55:],
            'after': row['source_text'][offset:].strip()[:55:],
            'proposal_method': method,
            'pause_seconds': timing.get('pause_seconds'),
            'anchor_words': timing.get('anchor_words'),
            'anchor_matches': timing.get('anchor_matches'),
            'adjacent_match': timing.get('adjacent_match')}


def load_context(pilot):
    context = {}
    folders = [path for path in pilot.glob('context*') if path.is_dir()
               and path.name != 'context-duplicate-checkpoints']
    for folder in sorted(folders):
        for path in sorted(folder.glob('*.json')):
            document = json.loads(path.read_text(encoding='utf-8'))
            normalized = []
            for row in document.get('rows', []):
                parts = row.get('performance', {}).get('analysis_parts', [])
                if parts and all('start' in part for part in parts):
                    normalized.append({'id': row['id'], 'parts': parts})
            # Older checkpoints stored character offsets directly in raw.parts.
            # New production analysis keeps unit indices in raw.parts and the
            # validated character offsets in rows[].performance.analysis_parts.
            normalized_ids = {passage['id'] for passage in normalized}
            normalized.extend(passage for passage in document.get('raw', {}).get('passages', [])
                              if passage['id'] not in normalized_ids
                              and all('start' in part for part in passage.get('parts', [])))
            for passage in normalized:
                if passage['id'] in context:
                    raise ValueError('Duplicate context passage across analysis lanes')
                context[passage['id']] = passage['parts']
    return context


def load_forced(pilot, candidate_hash):
    path = pilot / 'forced-split-alignments.json'
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema') != 1 or document.get('candidates_sha256') != candidate_hash:
        raise ValueError('Forced split alignments belong to a different candidate set')
    return document.get('clips', {})


def load_evaluation_baseline(pilot, candidate_hash):
    path = pilot / 'dash-split-evaluation-baseline.json'
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    if (document.get('schema') != 1 or document.get('candidates_sha256') != candidate_hash
            or document.get('count') != len(document.get('blocked_clip_ids', []))):
        raise ValueError('Dash split evaluation baseline is invalid or belongs to another candidate set')
    return document


def plan(board, pilot):
    candidate_hash = sha(board / 'candidates.jsonl')
    rows = [json.loads(line) for line in (board / 'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    context, forced = load_context(pilot), load_forced(pilot, candidate_hash)
    baseline = load_evaluation_baseline(pilot, candidate_hash)
    frozen = {}
    if (board / 'split-plan.json').exists() and (board / 'review-draft.json').exists():
        previous = json.loads((board / 'split-plan.json').read_text(encoding='utf-8'))
        review = json.loads((board / 'review-draft.json').read_text(encoding='utf-8'))
        if previous.get('candidates_sha256') != candidate_hash or review.get('candidates_sha256') != candidate_hash:
            if review.get('decisions'):
                raise ValueError('Saved splits belong to a different candidate set')
        else:
            frozen = {clip_id: previous['clips'][clip_id]
                      for clip_id, decision in review['decisions'].items()
                      if decision.get('split_review') and clip_id in previous['clips']}
    recordings, clips = {}, {}
    metrics = Counter()
    unresolved = []
    unresolved_delivery = []
    for row in rows:
        track = row['recording_id'].split('-')[-1]
        if track not in recordings:
            source = json.loads((pilot / 'recordings' / track / 'align.json').read_text(encoding='utf-8'))
            if source['source']['sha256'] != row['recording_sha256']:
                raise ValueError('Alignment belongs to a different recording')
            recordings[track] = [(token, word) for window in source['windows']
                                 for word in window['words'] for token, _, _ in tokens(word['text'])]
        audio = recordings[track]
        entries = tokens(row['source_text'])
        text_tokens = [item[0] for item in entries]
        all_dashes = structural_dash_offsets(row['source_text'])
        delivery_dashes = delivery_dash_offsets(row['source_text'])
        placeholder_spans = narrator_placeholder_spans(row['source_text'])
        placeholder_ranges = {(span['start'], span['end']) for span in placeholder_spans}
        aside_spans = [span for span in parenthetical_spans(row['source_text'])
                      if (span['start'], span['end']) not in placeholder_ranges]
        placeholder_offsets = sorted({offset for span in placeholder_spans
                                      for offset in (span['start'], span['end'])
                                      if token_boundary(entries, offset) not in (None, 0, len(entries))})
        aside_offsets = {offset for span in aside_spans for offset in (span['start'], span['end'])
                         if token_boundary(entries, offset) not in (None, 0, len(entries))}
        parenthetical_offsets = sorted(set(placeholder_offsets) | aside_offsets)
        matches = [index for index in range(len(audio) - len(entries) + 1)
                   if [token for token, _ in audio[index:index+len(entries)]] == text_tokens]
        path = ROOT / row['exact_audio']
        if sha(path) != row['audio_sha256']:
            raise ValueError('Original clip changed')
        with wave.open(str(path), 'rb') as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError('Split playback needs mono PCM16')
        cuts, methods = [], set()
        if matches:
            index = min(matches, key=lambda value: abs(audio[value][1]['start'] - row['audio_start']))
            words = [word for _, word in audio[index:index+len(entries)]]
            for position in range(1, len(entries)):
                previous_end, next_start = entries[position-1][2], entries[position][1]
                gap_text = row['source_text'][previous_end:next_start]
                dash = re.search(r'\s[\u2013\u2014-]\s+', gap_text)
                offset = previous_end + dash.end() if dash else next_start
                # Printed placeholders belong wholly to their narrator part,
                # including the parentheses. Keep the same word/audio boundary.
                for boundary in parenthetical_offsets:
                    if token_boundary(entries, boundary) == position:
                        offset = boundary
                        break
                timing = {'time': (words[position-1]['end'] + words[position]['start']) / 2,
                          'pause_seconds': round(words[position]['start'] - words[position-1]['end'], 3),
                          'anchor_words': len(entries), 'anchor_matches': len(entries),
                          'adjacent_match': 'exact'}
                item = cut_record(row, offset, timing, rate, frames, 'exact_full_transcript')
                if item and (not cuts or item['frame'] > cuts[-1]['frame']):
                    cuts.append(item)
            methods.add('exact_full_transcript')
        elif all_dashes or parenthetical_offsets:
            if delivery_dashes:
                metrics['blocked_delivery_clips'] += 1
            elif all_dashes:
                metrics['grammatical_dash_clips'] += 1
            forced_row = forced.get(row['id'])
            if forced_row and forced_row.get('audio_sha256') != row['audio_sha256']:
                raise ValueError('Forced alignment audio changed')
            recovered_offsets = set()
            for offset in sorted(set(all_dashes) | set(parenthetical_offsets)):
                timing = anchored_dash_cut(entries, audio, offset, row['audio_start'], row['audio_end'])
                method = 'anchored_local_alignment'
                absolute = True
                if timing is None and forced_row:
                    timing = forced_dash_cut(entries, forced_row.get('items', []), offset)
                    method = 'forced_text_alignment'
                    absolute = False
                if timing:
                    item = cut_record(row, offset, timing, rate, frames, method, absolute)
                    if item:
                        cuts.append(item)
                        recovered_offsets.add(offset)
                        methods.add(method)
                        metrics[f'{method}_boundaries'] += 1
                        if offset not in delivery_dashes:
                            metrics[f'{method}_manual_only_boundaries'] += 1
            missing_offsets = (set(all_dashes) | set(parenthetical_offsets)) - recovered_offsets
            if missing_offsets:
                unresolved.append(row['id'])
            if missing_offsets.intersection(delivery_dashes):
                unresolved_delivery.append(row['id'])

        raw = context.get(f"{row['source_id']}-{row['text_start']}-{row['text_end']}", [])
        hints = [part['start'] for part in raw[1:]]
        hints += [match.end() for match in re.finditer(r'[.!?\u2026][\u00bb\u201d"]?\s+(?=[\u0410-\u042fA-Z\u2013\u2014])', row['source_text'])]
        hints += delivery_dashes
        hints += parenthetical_offsets
        manual_only_offsets = set(all_dashes) - set(delivery_dashes)
        suggested = sorted({min(cuts, key=lambda cut: abs(cut['offset'] - hint))['offset']
                            for hint in hints if cuts and min(abs(cut['offset'] - hint) for cut in cuts) <= 4
                            and min(cuts, key=lambda cut: abs(cut['offset'] - hint))['offset'] not in manual_only_offsets})
        tags = ['structural_dash_transition'] if all_dashes else []
        if placeholder_spans:
            tags.append('speech_internal_placeholder')
        if aside_spans:
            tags.append('parenthetical_aside')
        if not all_dashes:
            review = 'not_applicable'
        elif not delivery_dashes:
            review = 'grammatical_dash_not_a_delivery_split'
        elif 'forced_text_alignment' in methods:
            review = 'forced_aligned_split_available'
        elif 'anchored_local_alignment' in methods:
            review = 'anchored_split_available'
        elif matches:
            review = 'word_aligned_split_available'
        else:
            review = 'requires_forced_alignment'
        if suggested:
            for method in {cut['proposal_method'] for cut in cuts if cut['offset'] in suggested}:
                metrics[f'{method}_proposal_clips'] += 1
        clips[row['id']] = {
            'audio_sha256': row['audio_sha256'], 'sample_rate': rate, 'frames': frames,
            'cuts': sorted(cuts, key=lambda cut: cut['frame']), 'suggested': suggested,
            'basis': ('word alignment with method-specific provenance; contextual, sentence, and '
                      'reviewable em-dash boundaries; not checked training cuts'),
            'complexity_tags': tags,
            'training_priority': ('high_after_independent_edge_review'
                                  if delivery_dashes or placeholder_spans or aside_spans else 'normal'),
            'structural_dash_review': review,
            'manual_only_cuts': sorted(cut['offset'] for cut in cuts if cut['offset'] in manual_only_offsets),
        }
        if placeholder_spans:
            clips[row['id']]['voice_prefill_spans'] = placeholder_spans
        if aside_spans:
            clips[row['id']]['delivery_review_spans'] = aside_spans
        if not cuts:
            clips[row['id']]['reason'] = ('No delivery split applies' if all_dashes and not delivery_dashes
                                          else 'No reliable word boundaries for a split yet')
        if row['id'] in frozen:
            if frozen[row['id']]['audio_sha256'] != row['audio_sha256']:
                raise ValueError('Saved split audio changed')
            clips[row['id']] = frozen[row['id']]
    metrics['unresolved_delivery_clips'] = len(unresolved_delivery)
    metrics['unresolved_manual_boundary_clips'] = len(unresolved)
    metrics['proposal_clips'] = sum(bool(item['suggested']) for item in clips.values())
    baseline_result = None
    if baseline:
        baseline_counts = Counter()
        for clip_id in baseline['blocked_clip_ids']:
            clip = clips[clip_id]
            methods = {cut['proposal_method'] for cut in clip['cuts'] if cut['offset'] in clip['suggested']}
            if methods:
                for method in methods:
                    baseline_counts[f'{method}_proposal_clips'] += 1
                baseline_counts['proposal_clips'] += 1
            elif clip['structural_dash_review'] == 'grammatical_dash_not_a_delivery_split':
                baseline_counts['grammatical_dash_not_a_delivery_split'] += 1
                manual_methods = {cut['proposal_method'] for cut in clip['cuts']
                                  if cut['offset'] in clip.get('manual_only_cuts', [])}
                if manual_methods:
                    baseline_counts['manual_split_available'] += 1
                    for method in manual_methods:
                        baseline_counts[f'{method}_manual_split_clips'] += 1
            else:
                baseline_counts['unresolved'] += 1
        baseline_result = {'clips': baseline['count'],
                           'source_split_plan_sha256': baseline['source_split_plan_sha256'],
                           'counts': dict(sorted(baseline_counts.items()))}
    return {'schema': 1, 'candidates_sha256': candidate_hash, 'clips': clips,
            'training_approved': False,
            'evaluation': {
                'blocked_exact_match_baseline': baseline_result,
                'counts': dict(sorted(metrics.items())),
                'unresolved_for_forced_alignment': unresolved,
                'acceptance_measure': ('For each proposal_method, count saved splits that retain the suggested '
                                       'offset; do not combine anchored and forced-alignment acceptance rates.'),
            }}


def write_plan(result, board, manual_book_id, pilot=None):
    if __package__:
        from .manual_labeling_archive import archive_lock
    else:
        from manual_labeling_archive import archive_lock
    archive = ROOT / 'manual-labeling' / manual_book_id
    with archive_lock(archive):
        _write_plan(result, board, manual_book_id, pilot)


def _write_plan(result, board, manual_book_id, pilot=None):
    data = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    target = board / 'split-plan.json'
    review = json.loads((board / 'review-draft.json').read_text(encoding='utf-8'))
    if target.exists() and target.read_bytes() != data:
        previous_document = json.loads(target.read_text(encoding='utf-8'))
        changed_saved = [clip_id for clip_id, answer in review['decisions'].items()
                         if answer.get('split_review')
                         and previous_document.get('clips', {}).get(clip_id) != result.get('clips', {}).get(clip_id)]
        if changed_saved:
            raise ValueError('Saved split boundaries are frozen for: ' + ', '.join(sorted(changed_saved)))
    archive = ROOT / 'manual-labeling' / manual_book_id / 'metadata'
    archive.mkdir(parents=True, exist_ok=True)
    atomic_write(target, data)
    atomic_write(archive / 'split-plan.json', data)
    support = [pilot / name for name in ('forced-split-alignments.json',
                                         'dash-split-evaluation-baseline.json')] if pilot else []
    for source in support:
        if source.exists():
            atomic_write(archive / source.name, source.read_bytes())
    atomic_write(board / 'split-plan.json.gz', gzip.compress(data, compresslevel=6, mtime=0))
    manifest = archive.parent / 'archive-manifest.json'
    if manifest.exists():
        document = json.loads(manifest.read_text(encoding='utf-8'))
        document['metadata']['split-plan.json'] = hashlib.sha256(data).hexdigest()
        for source in support:
            if source.exists():
                document['metadata'][source.name] = sha(source)
        atomic_write(manifest, (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def build(board, pilot, manual_book_id='reality-benders-2'):
    result = plan(board, pilot)
    write_plan(result, board, manual_book_id, pilot)
    print(f"Prepared {len(result['clips'])} immutable-parent split plans, "
          f"{result['evaluation']['counts']['proposal_clips']} with suggested changes.")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', type=Path, default=ROOT / 'training-work/character-rb2/fine-tune-all')
    parser.add_argument('--source', type=Path, default=ROOT / 'training-work/character-rb2/pilot')
    parser.add_argument('--manual-book-id', default='reality-benders-2')
    args = parser.parse_args()
    build(args.board, args.source, args.manual_book_id)


if __name__ == '__main__':
    main()
