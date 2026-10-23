"""Snapshot explicit mood answers with text context; never change human labels.

This is descriptive evidence, not a training export or an acoustic evaluation.
Enabled mixed parents are replaced by explicitly mood-labelled child parts.
The original Book 2 pilot is kept separate to avoid double-counting migrations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from review_label_resolution import resolved_review_mood, resolved_review_speaker

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ('reality-benders-1', 'reality-benders-2-full', 'reality-benders-2')


def build():
    hashes, units, populations = {}, [], {}
    for path in (Path(__file__), ROOT / 'scripts/review_label_resolution.py',
                 ROOT / 'scripts/book1_review_shared.js'):
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

    def read(path, lines=False):
        raw = path.read_bytes()
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(raw).hexdigest()
        return ([json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
                if lines else json.loads(raw))

    for book in BOOKS:
        folder = ROOT / 'manual-labeling' / book
        for _ in range(20):
            review = read(folder / 'review.json')
            latest = read(folder / 'latest.json')
            if hashes[(folder / 'review.json').relative_to(ROOT).as_posix()] == latest['review_sha256']:
                break
        else:
            raise ValueError('Review and latest checksum do not match: ' + book)
        rows = read(folder / 'metadata/candidates.jsonl', lines=True)
        assert hashes[(folder / 'metadata/candidates.jsonl').relative_to(ROOT).as_posix()] == review['candidates_sha256']
        source_path = folder / 'metadata' / ('paragraph-sources.json' if book.endswith('-1') else 'source-context.json')
        sources = read(source_path)['sources'] if source_path.exists() else {}
        overlay_path = folder / 'metadata/voice-proposal-overrides.json'
        overlay = read(overlay_path).get('clips', {}) if overlay_path.exists() else {}
        counts = Counter(candidates=len(rows))
        for row in rows:
            cid = row['id']
            d = review['decisions'].get(cid, {})
            if d.get('rejected'):
                counts['rejected'] += 1
                continue
            source = sources.get(row.get('source_id'), {})
            voice_proposed = overlay.get(cid, {}).get('base_voice_id_proposed') or row.get('base_voice_id_proposed')
            voice = d.get('corrected_voice_id') or d.get('confirmed_voice_id') or voice_proposed
            voice_basis = 'human' if d.get('corrected_voice_id') or d.get('confirmed_voice_id') or d.get('voice_verdict') else 'proposal'
            speaker = resolved_review_speaker(row.get('character_id'), d.get('speaker_verdict'), d.get('corrected_character_id'))
            common = dict(book=book, parent_id=cid, source_id=row.get('source_id'),
                          chapter_id=row.get('chapter_id'), recording_id=row.get('recording_id'),
                          chapter=source.get('chapter_title') or source.get('chapter'),
                          paragraph=source.get('text') or source.get('current') or row['source_text'],
                          before=source.get('before'), after=source.get('after'),
                          role_proposed=row.get('role'), speaker_proposed=row.get('character_id'),
                          speaker=speaker,
                          speaker_basis='human' if d.get('speaker_verdict') or d.get('corrected_character_id') else 'proposal',
                          voice=voice, voice_basis=voice_basis,
                          boundary_held=bool(d.get('boundary_start') or d.get('boundary_end')))
            split = d.get('split_review', {})
            if split.get('enabled'):
                counts['mixed_parents_excluded'] += 1
                assert split['parent_audio_sha256'] == row['audio_sha256']
                for pid, part in split.get('parts', {}).items():
                    mood = resolved_review_mood(None, part.get('mood_verdict'), part.get('corrected_mood'))
                    if not mood or part.get('rejected'):
                        counts['children_without_explicit_mood'] += 1
                        continue
                    edge = part['split_source']
                    a, b = edge['text_start'], edge['text_end']
                    assert 0 <= a < b <= len(row['source_text'])
                    units.append({**common, 'id': pid, 'unit': 'split_part', 'mood': mood,
                                  'text': row['source_text'][a:b], 'audio_sha256': edge['audio_sha256'],
                                  'voice': part.get('corrected_voice_id') or part.get('confirmed_voice_id'),
                                  'voice_basis': 'human' if part.get('corrected_voice_id') or part.get('confirmed_voice_id') else 'unresolved_child',
                                  'parent_voice': voice,
                                  'origin': part.get('review_origin'), 'provenance': part.get('label_provenance'),
                                  'reviewed_at': part.get('reviewed_at')})
                    counts['explicit_mood_parts'] += 1
                continue
            proposed = row.get('mood_proposed')
            proposed = proposed[0] if isinstance(proposed, list) and proposed else proposed
            mood = resolved_review_mood(proposed, d.get('mood_verdict'), d.get('corrected_mood'))
            if not mood:
                counts['without_explicit_mood'] += 1
                continue
            units.append({**common, 'id': cid, 'unit': 'clip', 'mood': mood, 'text': row['source_text'],
                          'audio_sha256': row.get('audio_sha256'), 'origin': d.get('review_origin'),
                          'reviewed_at': d.get('reviewed_at')})
            counts['explicit_mood_clips'] += 1
        populations[book] = dict(counts)

    main = [u for u in units if u['book'] != 'reality-benders-2']
    groups = defaultdict(list)
    for u in main:
        groups[u['mood']].append(u)
    summary = {}
    for mood, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        summary[mood] = dict(count=len(rows), books=dict(Counter(u['book'] for u in rows)),
                             voices=dict(Counter(u['voice'] or 'unresolved' for u in rows)),
                             voice_bases=dict(Counter(u['voice_basis'] for u in rows)),
                             split_parts=sum(u['unit'] == 'split_part' for u in rows),
                             origins=dict(Counter(u['origin'] for u in rows)))
    return dict(schema=1, captured_at=datetime.now(timezone.utc).isoformat(), input_sha256=hashes,
                method='Explicit mood choices only; no passive playback or model-prefilled moods. '
                       'Mixed parents excluded; explicit child labels retained. Pilot separate. '
                       'Counts are label units, not independent acoustic observations.',
                populations=populations, moods=summary, units=units)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output':str(args.output), 'populations':report['populations'],
                      'moods':{m:s['count'] for m,s in report['moods'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
