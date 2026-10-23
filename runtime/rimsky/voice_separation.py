"""Relative voice-confusion diagnostics, not an identity or listening verdict."""
from itertools import combinations
import numpy as np


def unit_vector(value):
    vector = np.asarray(value, dtype=np.float64)
    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
        raise ValueError('Invalid speaker embedding')
    norm = np.linalg.norm(vector)
    if norm <= 1e-12:
        raise ValueError('Empty speaker embedding')
    return vector / norm


def separation_report(groups):
    """Compare between-character distance with each character's actual spread.

    Nearest-centroid measurements leave the tested utterance out of its own
    centroid. Small samples and same-actor embeddings cannot certify that two
    performed voices are perceptually distinct.
    """
    vectors = {key: np.stack([unit_vector(v) for v in values])
               for key, values in groups.items() if values}
    if not vectors:
        return {'characters': {}, 'pairs': []}
    if len({v.shape[1] for v in vectors.values()}) != 1:
        raise ValueError('Speaker embedding dimensions differ')
    centers = {key: unit_vector(v.mean(axis=0)) for key, v in vectors.items()}
    spread = {}
    for key, values in vectors.items():
        distances = [float(1 - v @ unit_vector(np.delete(values, i, axis=0).mean(axis=0)))
                     for i, v in enumerate(values)] if len(values) > 1 else []
        spread[key] = {'samples': len(values), 'within_p95_cosine_distance':
                       float(np.percentile(distances, 95)) if distances else None}
    pairs = []
    for a, b in combinations(sorted(vectors), 2):
        distance = float(1 - centers[a] @ centers[b])
        radii = [spread[k]['within_p95_cosine_distance'] for k in (a, b)]
        errors = total = 0
        margins = []
        for own, other in ((a, b), (b, a)):
            if len(vectors[own]) < 2:
                continue
            for i, vector in enumerate(vectors[own]):
                center = unit_vector(np.delete(vectors[own], i, axis=0).mean(axis=0))
                margin = float(vector @ center - vector @ centers[other])
                margins.append(margin)
                errors += margin <= 0
                total += 1
        pairs.append({'characters': [a, b], 'centroid_cosine_distance': distance,
                      'within_spread_overlap_warning': all(r is not None for r in radii)
                      and distance <= max(radii),
                      'leave_one_utterance_out_confusions': errors, 'evaluated': total,
                      'median_own_reference_margin': float(np.median(margins)) if margins else None})
    return {'characters': spread, 'pairs': sorted(pairs, key=lambda p: p['centroid_cosine_distance'])}
