"""Replace the bad cached Book 1 scene-break render with a timed pause."""
from __future__ import annotations

import io
import json
import shutil
import sqlite3
import wave
from pathlib import Path

from runtime.rimsky.integrity import read_audio
from runtime.rimsky.util import atomic_write, write_json

ROOT = Path(__file__).resolve().parents[1] / 'training-work' / 'book1-listening-app'
GENERATION = '1b15b833cc8ee135b527c30c'
SEGMENT = 'c0005-p00007-0-5'


def main():
    db = sqlite3.connect(ROOT / 'library.sqlite3')
    db.row_factory = sqlite3.Row
    status = db.execute('SELECT status FROM generations WHERE id=?', (GENERATION,)).fetchone()
    if not status or status['status'] != 'paused':
        raise RuntimeError('Pause Book 1 generation before repairing its cache.')
    row = db.execute('SELECT * FROM segments WHERE generation_id=? AND id=?', (GENERATION, SEGMENT)).fetchone()
    if not row or row['text'] != '* * *' or row['spoken'] != '* * *' or row['status'] != 'ready':
        raise RuntimeError('The expected scene-break segment is missing or already changed.')
    key = row['audio_hash']
    uses = db.execute('SELECT count(*) FROM segments WHERE audio_hash=?', (key,)).fetchone()[0]
    if uses != 1:
        raise RuntimeError(f'The scene-break cache is shared by {uses} segments.')
    wav = ROOT / 'audio' / f'{key}.wav'
    metadata = wav.with_suffix('.json')
    backup = ROOT / 'work' / f'{key}.bad-scene-break.wav'
    if not backup.exists():
        shutil.copyfile(wav, backup)
    rate, seconds = 24000, 1.4
    frames = round(rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b'\x00\x00' * frames)
    atomic_write(wav, buffer.getvalue())
    result = {**read_audio(wav), 'warning': 'Scene pause.', 'reset_cues': False}
    write_json(metadata, result)
    with db:
        db.execute("UPDATE segments SET duration=?,cues='[]',warning=? WHERE generation_id=? AND id=?",
                   (seconds, 'Scene pause.', GENERATION, SEGMENT))
    print(json.dumps({'segment': SEGMENT, 'duration': seconds, 'backup': str(backup)}))


if __name__ == '__main__':
    main()
