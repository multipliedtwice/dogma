"""Stock Qwen3-TTS first pass of one chapter: speed and an error profile, no quality gates.

The base model is not fine-tuned on the narrator, so holding it to the
production zero-flag gate would only measure how long retakes chase quality it
cannot reach. This renders every unit once with the exact assignments (voice,
mood, reference, text) of an existing Higgs generation, word-checks each take
once for information, and publishes the whole chapter for listening. Nothing
here is verified, approved or reused by production generations.

Run with RIMSKY_TTS_ENGINE=qwen and training-work/higgs-production/.venv.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, atomic_write
from higgs_trial_io import read_json as read

PAGE_ROOT = ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp'
MEDIA_ROOT = ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp'
GAP = {'sentence': .24, 'paragraph': .34}


def assignments(generation, chapter):
    """The section assignments a generation published from, in chunk order."""
    found = []
    for selected in sorted((generation/chapter).glob('chunk-*/**/selected.json')):
        rel = selected.relative_to(generation)
        if any(p.startswith(('word-', 'prior-take', 'take-', 'batched', 'delegated')) or 'diagnosis' in p for p in rel.parts[:-1]):
            continue
        if (selected.parent/'assignment.json').exists():found.append(selected.parent/'assignment.json')
    return found


def main():
    import numpy as np
    import soundfile as sf
    from render_higgs_book_trial import ENGINE, render
    from higgs_exhausted_word_recovery import audit_candidate
    if ENGINE != 'qwen':raise ValueError('Set RIMSKY_TTS_ENGINE=qwen')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-generation', type=Path, required=True)
    p.add_argument('--chapter', default='c0002'); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--page', default='qwen')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    started = time.time(); timing = {'render_seconds': 0.0, 'audit_seconds': 0.0}
    sections = []
    for n, path in enumerate(assignments(a.source_generation, a.chapter)):
        folder = a.output/f'section-{n:02d}'; folder.mkdir(exist_ok=True)
        target = folder/'assignment.json'
        if not target.exists():target.write_bytes(path.read_bytes())
        t = time.time(); render(target, folder, [a.chapter]); timing['render_seconds'] += time.time()-t
        t = time.time(); report = audit_candidate(folder); timing['audit_seconds'] += time.time()-t
        sections.append((folder, report))
        print('SECTION', n, 'rendered and checked', flush=True)
    # Assemble in source order with the pipeline's sentence/paragraph gaps.
    pieces = []; rows = []; previous = None
    for folder, report in sections:
        plan = read(folder/'render-plan.json'); receipts = read(folder/'receipts.json')['takes']
        for unit in plan['units']:
            receipt = receipts.get(unit['id'])
            if not receipt:rows.append({'id': unit['id'], 'status': 'not_rendered'});continue
            x, rate = sf.read(folder/receipt['audio'], dtype='float32')
            if previous is not None:
                gap = GAP['sentence'] if unit.get('source_id') == previous else GAP['paragraph']
                pieces.append(np.zeros(round(gap*rate), dtype='float32'))
            pieces.append(x); previous = unit.get('source_id')
            check = (report or {}).get('checks', {}).get(unit['id'], {})
            rows.append({'id': unit['id'], 'status': check.get('status', 'unchecked'), 'text': unit.get('spoken', ''),
                         'heard': check.get('heard', ''), 'seconds': receipt['duration']})
    audio = np.concatenate(pieces); seconds = len(audio)/24000
    exact = sum(r['status'] in ('asr_exact_match', 'nonverbal_not_spoken_text') for r in rows)
    summary = {'engine': 'qwen3_tts 1.7B base (not fine-tuned)', 'chapter': a.chapter,
               'audio_minutes': round(seconds/60, 1), 'wall_minutes': round((time.time()-started)/60, 1),
               'render_minutes': round(timing['render_seconds']/60, 1), 'audit_minutes': round(timing['audit_seconds']/60, 1),
               'render_x_realtime': round(timing['render_seconds']/seconds, 2),
               'units': len(rows), 'word_exact': exact, 'word_flagged': len(rows)-exact,
               'stress_marks': 'stripped (Qwen voices U+0301 as an extra vowel)', 'quality_gates': 'none; informational',
               'production_approved': False}
    write_json(a.output/'summary.json', {**summary, 'units_detail': rows})
    page = PAGE_ROOT/a.page; media = MEDIA_ROOT/a.page; page.mkdir(parents=True, exist_ok=True); media.mkdir(parents=True, exist_ok=True)
    wav = media/f'{a.chapter}-qwen-first-pass.wav'; sf.write(wav, audio, 24000, subtype='PCM_16')
    src = '/character-rb2/full/clips/elirm-iv-mvp/'+a.page+'/'+wav.name+'?v='+file_digest(wav)[:12]
    import html
    flagged = ''.join('<li><b>'+html.escape(r['id'])+'</b>: '+html.escape(r.get('text', ''))+'<br><i>'+html.escape(r.get('heard', ''))+'</i></li>'
                      for r in rows if r['status'] not in ('asr_exact_match', 'nonverbal_not_spoken_text'))
    atomic_write(page/'index.html', ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Qwen first pass</title><style>:root{color-scheme:light dark}body{font:17px system-ui;max-width:820px;margin:auto;padding:16px;line-height:1.5}audio{width:100%}li{margin:6px 0}</style>'
        '<h1>Глава 2 — Qwen3-TTS (без дообучения)</h1>'
        f'<p>Один проход, без перезаписей и без проверок качества. Аудио {summary["audio_minutes"]} мин; синтез {summary["render_minutes"]} мин '
        f'({summary["render_x_realtime"]}× от длительности); всего с проверкой слов {summary["wall_minutes"]} мин. '
        f'Слова совпали в {exact} из {len(rows)} фраз. Отметки ударения убраны: Qwen произносит их как лишний звук. Без музыки.</p>'
        f'<audio controls preload="metadata" src="{src}"></audio><h2>Фразы с расхождением распознавания</h2><ol>{flagged}</ol></html>').encode('utf8'))
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':main()
