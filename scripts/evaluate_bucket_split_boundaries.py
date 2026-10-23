"""Report split proposal coverage and unchanged-cut acceptance by method."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', type=Path, default=ROOT / 'training-work/character-rb2-full/fine-tune-all')
    args = parser.parse_args()
    plan = json.loads((args.board / 'split-plan.json').read_text(encoding='utf-8'))
    review = json.loads((args.board / 'review-draft.json').read_text(encoding='utf-8'))
    proposals = Counter()
    for clip in plan['clips'].values():
        for method in {cut.get('proposal_method', 'legacy_word_alignment')
                       for cut in clip['cuts'] if cut['offset'] in clip['suggested']}:
            proposals[method] += 1
    outcomes = {method: Counter() for method in proposals}
    for clip_id, decision in review['decisions'].items():
        split = decision.get('split_review')
        if not split or not split.get('enabled'):
            continue
        clip = plan['clips'].get(clip_id)
        if not clip:
            continue
        for offset in split.get('cuts', []):
            cut = next((item for item in clip['cuts'] if item['offset'] == offset), None)
            if not cut:
                continue
            method = cut.get('proposal_method', 'legacy_word_alignment')
            if offset in clip.get('manual_only_cuts', []):
                outcome = 'manual_only_cut_selected'
            elif offset in clip['suggested']:
                outcome = 'accepted_unchanged'
            else:
                outcome = 'different_boundary'
            outcomes.setdefault(method, Counter())[outcome] += 1
    result = {'schema': 1,
              'blocked_exact_match_baseline': plan.get('evaluation', {}).get('blocked_exact_match_baseline'),
              'proposal_clips_by_method': dict(sorted(proposals.items())),
              'reviewed_boundaries_by_method': {method: dict(sorted(values.items()))
                                                for method, values in sorted(outcomes.items())},
              'note': 'Anchored recovery and forced text alignment are intentionally reported separately.'}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
