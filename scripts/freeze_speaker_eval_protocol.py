"""Freeze the model-selection and untouched-test partitions for speaker scoring."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / 'manual-labeling/speaker-resolution-gold-v1/gold.json'
TARGET = ROOT / 'manual-labeling/speaker-resolution-gold-v1/evaluation-protocol.json'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def main() -> None:
    if TARGET.exists():
        saved = json.loads(TARGET.read_text(encoding='utf-8'))
        expected = digest({key: value for key, value in saved.items()
                           if key != 'content_sha256'})
        if saved.get('content_sha256') != expected:
            raise ValueError('Frozen evaluation protocol checksum is invalid.')
        print(json.dumps({'verified': True,
                          'content_sha256': expected,
                          'selection_cases': len(saved['selection_case_ids']),
                          'test_cases': len(saved['test_case_ids'])}, indent=2))
        return
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    ids = [case['id'] for case in gold['cases']]
    payload = {
        'schema': 1,
        'frozen_at': datetime.now(timezone.utc).isoformat(),
        'gold_content_sha256': gold['content_sha256'],
        'selection_case_ids': ids[:70],
        'test_case_ids': ids[70:],
        'selection_note': (
            'The first 70 passages were used once to compare the installed Bonsai '
            'and Qwen 3.5 4B local models. Do not report them as untouched test data.'),
        'test_note': (
            'The remaining cases were frozen before any Qwen results for them were '
            'generated or inspected.'),
        'selected_local_model': {
            'name': 'qwen3.5:4b-q4_K_M',
            'ollama_digest': ('2a654d98e6fba55d452b7043684e9b57'
                              'a947e393bbffa62485a7aac05ee4eefd'),
        },
        'rejected_selection_candidate': {
            'name': 'digitsflow/bonsai-8b:latest',
            'ollama_digest': ('6de1d548dd04fa7986bf363e90f4b611'
                              'ef1bef48d34f8c6603106fa0f756048f'),
        },
    }
    payload['content_sha256'] = digest(payload)
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    print(json.dumps({'frozen': True,
                      'content_sha256': payload['content_sha256'],
                      'selection_cases': 70,
                      'test_cases': len(ids) - 70}, indent=2))


if __name__ == '__main__':
    main()
