"""Build read-only acoustic evidence for a narrator's evolving voice registry.

Human labels are inputs only.  The report deliberately does not edit the
registry: measured sound cannot establish perceived age, gender or character
use without listening review.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re

import numpy as np
from joblib import Parallel, delayed
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


ROOT = Path(__file__).resolve().parents[1]
BOOK1 = ROOT / 'manual-labeling/reality-benders-1'
BOOK2 = ROOT / 'manual-labeling/reality-benders-2-full'
BOOK1_FEATURES = ROOT / 'training-work/character-rb1/label-audit-20260929/audio-features.jsonl'
BOOK2_FEATURES = ROOT / 'training-work/character-rb2/full/audio-features.jsonl'
OUT = ROOT / 'training-work/voice-family-registry-evidence/vlad-rimsky-v1'
TAXONOMY = ROOT / 'training/voice_families.json'
ACOUSTICS = ('median_pitch_hz', 'pitch_range_semitones',
             'envelope_peaks_per_second', 'internal_pause_fraction')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def jsonl(path: Path):
    with path.open(encoding='utf-8') as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def character_name(character_id: str) -> str:
    value = character_id.removeprefix('rb1_')
    value = re.sub(r'_c\d+$', '', value)
    special = {'timor_ankhu': 'Timor-Ankhu', 'runner_343': 'Runner 343'}
    return special.get(value, value.replace('_', ' ').title())


def feature_index(path: Path, key: str):
    # Cache files can contain a later enriched record for the same clip/hash.
    # The last record is authoritative within this derived cache.
    result = {}
    for row in jsonl(path):
        if row.get('speaker_embedding'):
            result[row[key]] = row
    return result


def verified_archive(folder: Path):
    review = folder / 'review.json'
    latest = load(folder / 'latest.json')
    manifest = load(folder / 'archive-manifest.json')
    digest = sha(review)
    if digest != latest['review_sha256']:
        raise ValueError(f'{folder.name} review does not match latest.json')
    manifest_hash = manifest.get('review_sha256', manifest.get('support_snapshot_review_sha256'))
    if digest != manifest_hash:
        raise ValueError(f'{folder.name} review does not match archive manifest')
    return review.read_bytes(), latest, manifest


def book1_rows(families, aliases, features):
    rows = []
    for label in jsonl(BOOK1 / 'labels.jsonl'):
        family = aliases.get(label.get('voice_family'), label.get('voice_family'))
        if family not in families:
            continue
        audio = label.get('audio', {})
        feature = features.get(audio.get('sha256'))
        character = label.get('character_id') or (
            '__narrator__' if family.startswith('narrator_') else '__unattributed__')
        rows.append({'book': 'reality-benders-1', 'id': label['id'],
                     'family': family, 'character': character,
                     'chapter': label.get('chapter_id'), 'source_id': label.get('source_id'),
                     'text': label.get('text', ''),
                     'seconds': max(0.0, label.get('audio_end', 0) - label.get('audio_start', 0)),
                     'audio_sha256': audio.get('sha256'),
                     'audio_url': ('/manual-labeling/reality-benders-1/' + audio['archive'])
                     if audio.get('archive') else None,
                     'feature': feature,
                     'label_basis': 'protected_resolved_human_label'})
    return rows


def book2_rows(families, aliases, features, review):
    candidates = {row['id']: row for row in jsonl(BOOK2 / 'metadata/candidates.jsonl')}
    rows = []
    for clip_id, decision in review['decisions'].items():
        candidate = candidates.get(clip_id)
        if not candidate:
            continue
        split = decision.get('split_review', {})
        if split.get('enabled'):
            for part_id, part in split.get('parts', {}).items():
                family = aliases.get(part.get('corrected_voice_id'), part.get('corrected_voice_id'))
                if part.get('voice_verdict') != 'no' or family not in families:
                    continue
                source = part.get('split_source', {})
                frames = max(0, source.get('last_frame', 0) - source.get('first_frame', 0))
                character = ('__narrator__' if family.startswith('narrator_')
                             else candidate.get('character_id') or '__unattributed__')
                rows.append({'book': 'reality-benders-2-full', 'id': part_id,
                             'family': family, 'character': character,
                             'chapter': candidate.get('chapter_id'),
                             'source_id': candidate.get('source_id'),
                             'seconds': frames / max(1, source.get('sample_rate', 24000)),
                             'audio_sha256': source.get('audio_sha256'), 'feature': None,
                             'label_basis': 'explicit_listener_split_override',
                             'measurement_hold': 'split child has no cached speaker embedding'})
            continue
        family = aliases.get(decision.get('corrected_voice_id'),
                             decision.get('corrected_voice_id'))
        if decision.get('voice_verdict') != 'no' or family not in families:
            continue
        feature = features.get(clip_id)
        rows.append({'book': 'reality-benders-2-full', 'id': clip_id,
                     'family': family,
                     'character': candidate.get('character_id') or (
                         '__narrator__' if family.startswith('narrator_') else '__unattributed__'),
                     'chapter': candidate.get('chapter_id'),
                     'source_id': candidate.get('source_id'),
                     'seconds': max(0.0, candidate.get('audio_end', 0) - candidate.get('audio_start', 0)),
                     'audio_sha256': candidate.get('audio_sha256'), 'feature': feature,
                     'label_basis': 'explicit_listener_voice_override'})
    return rows


def center(rows):
    """Equalize characters, then source paragraphs, before averaging clips."""
    characters = defaultdict(lambda: defaultdict(list))
    for row in rows:
        feature = row.get('feature')
        if feature and row['seconds'] >= 1.5:
            characters[row['character']][row['source_id']].append(
                unit(feature['speaker_embedding']))
    character_centers = []
    for paragraphs in characters.values():
        paragraph_centers = [unit(np.mean(items, axis=0)) for items in paragraphs.values()]
        if paragraph_centers:
            character_centers.append(unit(np.mean(paragraph_centers, axis=0)))
    return unit(np.mean(character_centers, axis=0)) if character_centers else None


def medians(rows):
    result = {}
    for key in ACOUSTICS:
        values = [row['feature'].get(key) for row in rows if row.get('feature')
                  and row['feature'].get(key) is not None and row['seconds'] >= 1.5]
        result[key] = round(float(np.median(values)), 4) if values else None
    return result


def heldout_separability(left, right, rows):
    """Pairwise Book 1 test with whole chapters or characters unseen in fit."""
    selected = [row for row in rows if row['book'] == 'reality-benders-1'
                and row['family'] in (left, right) and row.get('feature')
                and not row['character'].startswith('__') and row['seconds'] >= 1.5]
    if not selected:
        return None
    x = np.asarray([unit(row['feature']['speaker_embedding']) for row in selected],
                   dtype=np.float32)
    y = np.asarray([int(row['family'] == right) for row in selected])

    def evaluate(group_field):
        actual, predicted, scores, used = [], [], [], []
        for group in sorted({row[group_field] for row in selected if row[group_field]}):
            test = np.asarray([row[group_field] == group for row in selected])
            train = ~test
            if len(set(y[train])) < 2:
                continue
            model = make_pipeline(
                StandardScaler(),
                LogisticRegression(C=.1, class_weight='balanced', max_iter=1500),
            )
            with threadpool_limits(limits=1):
                model.fit(x[train], y[train])
                predicted.extend(model.predict(x[test]).tolist())
                scores.extend(model.decision_function(x[test]).tolist())
            actual.extend(y[test].tolist())
            used.append(group)
        if len(set(actual)) < 2:
            return {'roc_auc': None, 'balanced_accuracy': None, 'folds': len(used),
                    'heldout_groups': used, 'scored_clips': len(actual)}
        return {'roc_auc': round(float(roc_auc_score(actual, scores)), 6),
                'balanced_accuracy': round(float(
                    balanced_accuracy_score(actual, predicted)), 6),
                'folds': len(used), 'heldout_groups': used,
                'scored_clips': len(actual)}

    characters = {family: sorted({row['character'] for row in selected
                                  if row['family'] == family})
                  for family in (left, right)}
    return {
        'left': left, 'right': right, 'clips': len(selected),
        'characters': characters,
        'heldout_chapters': evaluate('chapter'),
        # Holding one character out removes all of that character's clips from
        # both labels when a performer used more than one family for the role.
        'heldout_characters': evaluate('character'),
    }


def main():
    book1_review, book1_latest, book1_manifest = verified_archive(BOOK1)
    book2_review_bytes, book2_latest, book2_manifest = verified_archive(BOOK2)
    taxonomy = load(TAXONOMY)
    families = {item['id']: item for item in taxonomy['families']}
    aliases = taxonomy.get('legacy_aliases', {})
    book1_features = feature_index(BOOK1_FEATURES, 'audio_sha256')
    book2_features = feature_index(BOOK2_FEATURES, 'id')
    book2_review = json.loads(book2_review_bytes)
    rows = [*book1_rows(families, aliases, book1_features),
            *book2_rows(families, aliases, book2_features, book2_review)]

    by_family = defaultdict(list)
    by_book_family = defaultdict(list)
    for row in rows:
        by_family[row['family']].append(row)
        by_book_family[(row['book'], row['family'])].append(row)
    centers = {(book, family): center(group)
               for (book, family), group in by_book_family.items()}
    centers = {key: value for key, value in centers.items() if value is not None}

    pairwise = []
    family_ids = sorted(families)
    for index, left in enumerate(family_ids):
        for right in family_ids[index + 1:]:
            by_book = {}
            for book in ('reality-benders-1', 'reality-benders-2-full'):
                a, b = centers.get((book, left)), centers.get((book, right))
                if a is not None and b is not None:
                    by_book[book] = round(float(1 - np.dot(a, b)), 6)
            if by_book:
                pairwise.append({'left': left, 'right': right,
                                 'distance': round(float(np.median(list(by_book.values()))), 6),
                                 'by_book': by_book})
    pairwise.sort(key=lambda item: item['distance'])

    # Centre distance mostly recognizes the common narrator.  This stronger
    # diagnostic asks whether a family distinction survives unseen text and,
    # separately, unseen fictional characters.
    book1_character_counts = Counter(
        row['family'] for row in rows
        if row['book'] == 'reality-benders-1' and row.get('feature')
        and not row['character'].startswith('__') and row['seconds'] >= 1.5)
    separability_families = sorted(family for family, count in book1_character_counts.items()
                                  if count >= 5)
    with threadpool_limits(limits=1):
        separability = Parallel(n_jobs=4, prefer='threads')(
            delayed(heldout_separability)(left, right, rows)
            for left, right in combinations(separability_families, 2))
    separability = [item for item in separability if item]

    family_report = {}
    arrays = {}
    for family_id, family in families.items():
        group = by_family.get(family_id, [])
        measured_group = [row for row in group
                          if row.get('feature') is not None and row['seconds'] >= 1.5]
        measured_paragraphs = len({(row['book'], row['source_id'])
                                   for row in measured_group})
        if len(measured_group) >= 8 and measured_paragraphs >= 4:
            acoustic_status = 'measured'
        elif len(measured_group) >= 3 and measured_paragraphs >= 2:
            acoustic_status = 'limited'
        else:
            acoustic_status = 'insufficient'
        usage_by_book = {}
        for book in ('reality-benders-1', 'reality-benders-2-full'):
            subset = [row for row in group if row['book'] == book]
            if subset:
                usage_by_book[book] = {
                    'labeled_clips': len(subset),
                    'measured_clips': sum(row.get('feature') is not None and row['seconds'] >= 1.5
                                          for row in subset),
                    'characters': len({row['character'] for row in subset}),
                    'chapters': len({row['chapter'] for row in subset if row['chapter']}),
                    'seconds': round(sum(row['seconds'] for row in subset), 2),
                    'acoustic_medians': medians(subset),
                }
                value = centers.get((book, family_id))
                if value is not None:
                    arrays[f'{book}::{family_id}'] = value.astype(np.float32)
        nearest = []
        for item in pairwise:
            if family_id in (item['left'], item['right']):
                other = item['right'] if item['left'] == family_id else item['left']
                nearest.append({'family': other, 'distance': item['distance'],
                                'by_book': item['by_book']})
        family_report[family_id] = {
            'label': family['label'],
            'usage': {
                'labeled_clips': len(group),
                'measured_clips': len(measured_group),
                'measured_source_paragraphs': measured_paragraphs,
                'characters': len({(row['book'], row['character']) for row in group}),
                'books': len({row['book'] for row in group}),
                'seconds': round(sum(row['seconds'] for row in group), 2),
            },
            'by_book': usage_by_book,
            'exploratory_center_neighbors': sorted(nearest, key=lambda item: item['distance'])[:5],
            'heldout_separability': sorted([
                item for item in separability if family_id in (item['left'], item['right'])
            ], key=lambda item: (
                item['heldout_characters']['roc_auc'] is None,
                item['heldout_characters']['roc_auc']
                if item['heldout_characters']['roc_auc'] is not None else 2,
                item['heldout_chapters']['roc_auc']
                if item['heldout_chapters']['roc_auc'] is not None else 2,
            )),
            'book1_sample_size_note': (
                f"Only {usage_by_book.get('reality-benders-1', {}).get('measured_clips', 0)} "
                'measured Book 1 clips; family evidence is limited until at least 30.'
                if usage_by_book.get('reality-benders-1', {}).get('measured_clips', 0) < 30
                else None
            ),
            'acoustic_evidence_status': acoustic_status,
            'descriptor_status': 'unreviewed',
        }

    created = datetime.now(timezone.utc).isoformat()
    report = {
        'schema': 1,
        'narrator_id': 'vlad-rimsky',
        'created_at': created,
        'read_only_evidence_pass': True,
        'taxonomy_sha256': sha(TAXONOMY),
        'sources': {
            'reality-benders-1': {
                'review_sha256': book1_latest['review_sha256'],
                'labels_sha256': sha(BOOK1 / 'labels.jsonl'),
                'feature_cache_sha256': sha(BOOK1_FEATURES),
            },
            'reality-benders-2-full': {
                'review_sha256': book2_latest['review_sha256'],
                'candidates_sha256': book2_manifest['candidates_sha256'],
                'feature_cache_sha256': sha(BOOK2_FEATURES),
                'label_scope': 'explicit listener voice overrides only; playback acceptance excluded',
            },
        },
        'families': family_report,
        'exploratory_center_distances': pairwise,
        'heldout_pairwise_separability': separability,
        'methods': {
            'embedding': 'Local Qwen3-TTS speaker encoder cache; cosine distance.',
            'center': 'Equal weight per character, then source paragraph, then clip.',
            'cross_book_distance': 'Median of within-book distances; raw embeddings are never compared across recordings.',
            'heldout_separability': 'Book 1 normalized speaker embeddings; balanced logistic regression. Each fold excludes a whole chapter or a whole fictional character. Scores are pooled across folds. ROC AUC is the primary distinctness measure because it evaluates ranking without depending on a fixed cut-off; balanced accuracy is retained as a prefill-threshold diagnostic. For both, 0.5 is chance and AUC below 0.5 can indicate character-specific reversal.',
            'pitch_and_tempo': 'Median of clips at least 1.5 seconds; tempo is waveform envelope peaks per second, not words per second.',
        },
        'limits': [
            'These measurements do not assign perceived gender, age, weight, species or story role.',
            'Speaker embeddings can merge different performances by the same narrator and can react to phonetic content.',
            'Book 2 includes only explicit listener voice changes; playback-only evidence is excluded.',
            'Split children count toward usage but stay out of acoustic centers until separately measured.',
            'A small acoustic distance is a merge-review hint, never permission to merge labels.',
            'Held-out separability is indicative with one book. A reusable-family verdict requires several unseen characters and listening review.',
            'Families with fewer than 30 measured Book 1 clips carry an explicit limited-evidence note.',
        ],
    }

    # The labeling UI may save while this read-only pass is running.  The rows
    # above come from the verified bytes captured at startup, so publish that
    # internally consistent point-in-time snapshot and say whether a newer
    # review appeared meanwhile.  Do not force the listener to pause labeling.
    current_review_hashes = {
        'reality-benders-1': sha(BOOK1 / 'review.json'),
        'reality-benders-2-full': sha(BOOK2 / 'review.json'),
    }
    report['source_changed_during_run'] = {
        'reality-benders-1': (
            current_review_hashes['reality-benders-1'] != book1_latest['review_sha256']),
        'reality-benders-2-full': (
            current_review_hashes['reality-benders-2-full'] != book2_latest['review_sha256']),
    }
    report['current_review_sha256_at_publish'] = current_review_hashes

    priority = ['masculine_3', 'feminine_3', 'masculine_1']
    character_family_ids = [item['id'] for item in taxonomy['families']
                            if item['group'] in ('Masculine voices', 'Feminine voices',
                                                 'Special voices')]
    review_order = priority + [family_id for family_id in character_family_ids
                               if family_id not in priority]
    review_families = []
    for family_id in review_order:
        family = families[family_id]
        book1 = [row for row in by_family.get(family_id, [])
                 if row['book'] == 'reality-benders-1' and row.get('audio_url')
                 and not row['character'].startswith('__')]
        by_character = defaultdict(list)
        for row in book1:
            by_character[row['character']].append(row)
        examples = []
        per_character = 2 if len(by_character) <= 2 else 1
        for character_id in sorted(by_character):
            choices = sorted(by_character[character_id], key=lambda row: (
                row.get('feature') is None,
                not 2.0 <= row['seconds'] <= 10.0,
                abs(row['seconds'] - 5.0),
                row['id'],
            ))[:per_character]
            for row in choices:
                examples.append({
                    'id': row['id'],
                    'character_id': character_id,
                    'character_name': character_name(character_id),
                    'text': row['text'].strip(),
                    'seconds': round(row['seconds'], 3),
                    'audio_url': row['audio_url'],
                })
        comparisons = []
        for item in family_report[family_id]['heldout_separability']:
            other = item['right'] if item['left'] == family_id else item['left']
            if families[other]['group'] != family['group']:
                continue
            comparisons.append({
                'other_family_id': other,
                'other_family_label': families[other]['label'],
                'heldout_chapters': item['heldout_chapters'],
                'heldout_characters': item['heldout_characters'],
            })
        proposed_gender = ('masculine' if family['group'] == 'Masculine voices' else
                           'feminine' if family['group'] == 'Feminine voices' else 'neutral')
        review_families.append({
            'id': family_id,
            'label': family['label'],
            'group': family['group'],
            'priority_reason': (
                'Review first: cross-character reuse is doubtful.' if family_id in priority[:2]
                else 'Review first: only one Book 1 character is available.'
                if family_id == priority[2] else None),
            'usage': family_report[family_id]['usage'],
            'book1_metrics': family_report[family_id]['by_book'].get(
                'reality-benders-1', {}).get('acoustic_medians', {}),
            'sample_size_note': family_report[family_id]['book1_sample_size_note'],
            'comparisons': comparisons,
            'examples': examples,
            'proposed_descriptors': {
                'perceived_gender': proposed_gender,
                'perceived_age': 'adult',
                'voice_weight': 'medium',
                'voice_quality': 'normal',
                'otherness': 'human',
            },
        })
    review_data = {
        'schema': 1,
        'narrator_id': 'vlad-rimsky',
        'registry_version': 'vlad-rimsky-v1',
        'report_created_at': created,
        'families': review_families,
        'instructions': (
            'Listen across characters. Decide whether the sound is one reusable family, '
            'a character-specific performance, or several voices that need splitting.'),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
    np.savez_compressed(OUT / 'family-centers.npz', **arrays)
    (OUT / 'measured-label-index.jsonl').write_text(''.join(
        json.dumps({key: value for key, value in row.items() if key != 'feature'},
                   ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    (OUT / 'review-data.json').write_text(
        json.dumps(review_data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (OUT / 'index.html').write_bytes(
        (ROOT / 'scripts/voice_family_descriptor_review.html').read_bytes())
    (OUT / 'review.js').write_bytes(
        (ROOT / 'scripts/voice_family_descriptor_review.js').read_bytes())

    print(json.dumps({
        'output': str(OUT), 'families': len(families), 'labeled_clips': len(rows),
        'measured_clips': sum(row.get('feature') is not None and row['seconds'] >= 1.5
                              for row in rows),
        'center_distances': len(pairwise),
        'heldout_separability_pairs': len(separability),
        'book1_review_sha256': hashlib.sha256(book1_review).hexdigest(),
        'book2_review_sha256': hashlib.sha256(book2_review_bytes).hexdigest(),
        'source_changed_during_run': report['source_changed_during_run'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
