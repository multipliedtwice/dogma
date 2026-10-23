"""Build an isolated full Book 2 bucket board after audio and context finish."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'training-work/character-rb2/full'
TTS=ROOT/'training-work/.venv-tts/Scripts/python.exe'
EMOTION=ROOT/'training-work/.venv-emotion-benchmark/Scripts/python.exe'
PREDICTOR=ROOT/'training-work/character-rb1/label-predictor-3a743ea3d029'
BOARD=ROOT/'training-work/character-rb2-full/fine-tune-all'
LOG=OUT/'finalize.log'


def note(message):
    value=f'{datetime.now(timezone.utc).isoformat()} {message}'
    with LOG.open('a',encoding='utf-8') as f:f.write(value+'\n')
    print(value,flush=True)


def command(*args, python=TTS):
    note('Start: '+' '.join(str(x) for x in args))
    with LOG.open('a',encoding='utf-8') as f:
        subprocess.run([str(python),'-X','utf8','-u',*map(str,args)],cwd=ROOT,
                       stdout=f,stderr=subprocess.STDOUT,check=True)
    note('Finished: '+str(args[0]))


def wait_for_audio():
    while True:
        path=OUT/'audio-run-progress.json'
        if path.exists():
            state=json.loads(path.read_text(encoding='utf-8'))
            if state['stage']=='audit' and state['state']=='finished':return
        time.sleep(30)


def wait_for_context():
    while not (OUT/'context.json').exists():time.sleep(30)


def main():
    note('Waiting for the complete audio transcription and clip audit')
    wait_for_audio()
    if not (OUT/'checked-clips.jsonl').is_file():raise ValueError('Missing checked clips')
    command(ROOT/'scripts/extract_label_pilot_features.py','wave','--out',OUT)
    command(ROOT/'scripts/extract_label_pilot_features.py','emotion','--out',OUT,python=EMOTION)
    command(ROOT/'scripts/extract_label_pilot_features.py','combine','--out',OUT)
    command(ROOT/'scripts/predict_book_labels.py',
            '--model-directory',PREDICTOR,'--features',OUT/'features.npz',
            '--manifest',OUT/'checked-clips.jsonl','--output',OUT/'suggestions-v2.jsonl')
    command(ROOT/'scripts/train_hybrid_mood_suggestions.py','--out',OUT,
            '--frozen-report',ROOT/'training-work/character-rb2/pilot/hybrid/report.json')
    note('Waiting for complete-book GPT text context')
    wait_for_context()
    command(ROOT/'scripts/build_label_pilot_buckets.py',
            '--source-folder',OUT,'--board-folder',BOARD,
            '--manual-book-id','reality-benders-2-full',
            '--suggestions','suggestions-v2.jsonl')
    command(ROOT/'scripts/migrate_book2_pilot_labels.py')
    command(ROOT/'scripts/build_series_voice_prefill.py','--apply')
    note('Full Book 2 bucket board is prepared; start server on port 1439')


if __name__=='__main__':main()
