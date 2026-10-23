"""Resume Book 2 audio transcription, alignment, cuts, and independent audit."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'training-work/character-rb2/full'
EPUB = ROOT / 'content/reality-benders-2.epub'
SOURCE_SHA256 = '4ed8157413044ddc27e1c5a31d4f89b2cda617fde43755e5ec56e4811b133785'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def checkpoint(stage, track, state):
    data = {'stage': stage, 'track': track, 'state': state,
            'updated_utc': datetime.now(timezone.utc).isoformat(),
            'epub_sha256': SOURCE_SHA256}
    path = OUT / 'audio-run-progress.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    temp.replace(path)
    print(f'{state}: {stage} {track}', flush=True)


def run(stage, track=None):
    if digest(EPUB) != SOURCE_SHA256:
        raise ValueError('Book 2 EPUB changed during the full audio run')
    args = [sys.executable, '-X', 'utf8', '-u',
            str(ROOT/'scripts/prepare_book_label_pilot.py'), stage,
            '--out', str(OUT)]
    if track is not None:
        args += ['--tracks', track]
    checkpoint(stage, track, 'started')
    log = OUT/'audio-run.log'
    with log.open('a', encoding='utf-8') as stream:
        stream.write(f'\n=== {stage} {track} ===\n')
        stream.flush()
        subprocess.run(args, cwd=ROOT, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
    checkpoint(stage, track, 'finished')


def main():
    if digest(EPUB) != SOURCE_SHA256:
        raise ValueError('Expected the complete Book 2 EPUB')
    for number in range(3, 30):
        track = f'{number:02d}'
        for stage in ('asr', 'align', 'clips'):
            run(stage, track)
    run('audit')


if __name__ == '__main__':
    main()
