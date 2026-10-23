"""Make local A/B listening clips for terminal ellipses in book one."""
from __future__ import annotations

import html
import json
import sqlite3
import subprocess
from pathlib import Path

import soundfile as sf

from analyze_ellipsis_acoustics import ROOT, match_last_word

REPORT = ROOT / 'training-work/higgs-tts-3-pilot/ellipsis-acoustics.json'
OUT = ROOT / 'training-work/higgs-tts-3-pilot/ellipsis-pilot/review'
DB = ROOT / 'training-work/book1-listening-app/library.sqlite3'
CHOSEN = ('устройства', 'живого', 'замер', 'существовало', 'еще', 'чемпион')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = json.loads(REPORT.read_text(encoding='utf-8'))['cases']
    with sqlite3.connect(DB) as db:
        items = {row[0]: (row[1], row[2]) for row in db.execute(
            'SELECT id,text,audio_hash FROM segments WHERE generation_id=?',
            ('d25eec1c040193e489281531',))}
    blocks = []
    for wanted in CHOSEN:
        row = next((x for x in report if x['last_word_source'].casefold() == wanted), None)
        if row is None or row['id'] not in items:
            continue
        text, audio_hash = items[row['id']]
        chapter = row['recording']
        transcript = json.loads((ROOT / f'training-work/full-book-aligned-recovered/rb1-{chapter}/transcript.json')
                                .read_text(encoding='utf-8'))['words']
        match = match_last_word(text, transcript)
        if match is None:
            continue
        word_end = float(transcript[match[0]]['end'])
        next_start = float(transcript[match[0]+1]['start']) if match[0]+1 < len(transcript) else word_end+1.3
        source = next((ROOT / 'content/reality-benders-1').glob(chapter + '_*.mp3'))
        original_file = OUT / f'{wanted}-original.wav'
        start = max(0, word_end - 8.5)
        duration = min(word_end + min(1.2, max(.4, next_start-word_end)) - start, 10)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(start),
                        '-t', str(duration), '-i', str(source), '-ar', '24000', '-ac', '1',
                        str(original_file)], check=True)
        rendered = ROOT / 'training-work/book1-listening-app/audio' / f'{audio_hash}.wav'
        generated_file = OUT / f'{wanted}-generated.wav'
        data, rate = sf.read(rendered, dtype='float32')
        sf.write(generated_file, data[max(0, len(data)-round(9.7*rate)):], rate)
        original_shape = row['source_final_word']
        generated_shape = row['generated_final_window']
        blocks.append(f'<section><h2>{html.escape(wanted)}…</h2>'
                      f'<p>{html.escape(text)}</p>'
                      f'<div><label>Original narrator<audio controls preload="none" src="{original_file.name}"></audio></label>'
                      f'<label>Higgs draft<audio controls preload="none" src="{generated_file.name}"></audio></label></div>'
                      f'<small>Final word: original loudness change {original_shape["loudness_change_db"]} dB; '
                      f'draft final 0.7 s {generated_shape["loudness_change_db"]} dB. '
                      f'Original pause {row["source_pause_after_seconds"]} s; '
                      f'draft pause {row["generated_pause_after_seconds"]} s.</small></section>')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Ellipsis A/B review</title><style>body{font:18px/1.5 system-ui;max-width:900px;'
            'margin:40px auto;padding:0 20px;background:#f7f6f2;color:#26312d}'
            'section{background:white;border-radius:14px;padding:22px;margin:24px 0;box-shadow:0 2px 12px #0001}'
            'section div{display:grid;grid-template-columns:1fr 1fr;gap:18px}audio{display:block;width:100%;margin-top:8px}'
            'small{color:#53615b}@media(max-width:650px){section div{grid-template-columns:1fr}}</style>'
            '<h1>Vlad Rimsky: ellipsis delivery</h1><p>Listen to the final word in each pair. '
            'These clips come from different sentence starts but end on the same text. '
            'The draft is unchanged; this page is for diagnosis.</p>' + ''.join(blocks) + '</html>')
    (OUT / 'index.html').write_text(page, encoding='utf-8')
    print(f'{len(blocks)} pairs: {OUT / "index.html"}')


if __name__ == '__main__':
    main()
