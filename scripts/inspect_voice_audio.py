from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.references import inspect_reference
from rimsky.util import AppError, file_digest


def main():
    parser = argparse.ArgumentParser(description='Inspect local narrator recordings without inference or transcription.')
    parser.add_argument('recordings', type=Path, nargs='+')
    args = parser.parse_args()
    results = []
    try:
        for recording in args.recordings:
            results.append({'file': recording.name, 'sha256': file_digest(recording), **inspect_reference(recording),
                            'transcript_verified': False, 'voice_identity_verified': False,
                            'selected_for_cloning': False})
    except (AppError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps({'recordings': results,
                      'note': 'Signal and decoder checks only. No transcript, character, delivery or voice-model assignment is inferred.'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
