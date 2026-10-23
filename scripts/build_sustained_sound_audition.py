"""Listening test for hyphenated sustained sounds (У-у-у, Не-е-ет, М-м-м, О-оу).

The word gate can now confirm these lines (a recognizer heard the right sound),
but it cannot judge how Higgs performed them. This cuts every such line out of
the published chapter audio, exactly as a listener hears it, with how it was
verified and a duration warning, and publishes one page for a listening
verdict. It changes no chapter audio and records no approval.
Run with training-work/higgs-production/.venv.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, atomic_write
from higgs_published_timeline import read_timeline

PAGE = ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/sustained'
MEDIA = ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp/sustained'
STYLIZED = re.compile(r'(?<!\w)[А-Яа-яЁё]+(?:[-‐‑–][А-Яа-яЁё]+)+(?!\w)')
SECONDS_PER_PART = 0.18  # a sustained part shorter than this is unlikely to sound held


def stylized(text):
    """Hyphenated words with repeated letters or one-letter parts (not Ар-Хакон or кто-то)."""
    found = []
    for match in STYLIZED.finditer(text):
        parts = [p.casefold() for p in re.split('[-‐‑–]', match[0])]
        # Ordinary compounds (из-за, северо-запад, крюк-кошка, я-то, рок-н-ролл) are not sustained sounds.
        if parts[-1] in ('то', 'ка', 'либо', 'нибудь', 'таки', 'де') or parts[0] == 'кое' or 'н' in parts[1:-1]:continue
        if all(len(p) > 1 and _morph().word_is_known(p) for p in parts):continue
        if any(len(p) == 1 for p in parts) or any(re.search(r'(.)\1', p) for p in parts):found.append((match[0], len(parts)))
    return found


def _morph():
    import pymorphy3
    global _MORPH
    if '_MORPH' not in globals():_MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def verification(generation, chapter, uid):
    """How the published unit's take was verified, from its section selection."""
    for path in sorted((generation/chapter).glob('chunk-*/**/selected.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        if any(part.startswith(('word-', 'take-', 'prior', 'batched', 'delegated')) for part in path.relative_to(generation).parts[:-1]):continue
        row = json.loads(path.read_text('utf8')).get(uid)
        if row:
            check = row['word_check']; report = check.get('independent_report', '')
            how = 'primary recognizer' if not report else ('listener verdict' if 'listener' in report else
                  'glossary recognizer' if 'glossary' in report else 'padded recognizer' if 'padded' in report else 'independent recognizer')
            return how, check.get('heard_raw') or check.get('heard', '')
    return 'unknown', ''


def main():
    import numpy as np
    import soundfile as sf
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generation', type=Path, required=True); p.add_argument('--chapters', nargs='+', required=True)
    a = p.parse_args(); state = json.loads((a.generation/'progress.json').read_text('utf8'))
    PAGE.mkdir(parents=True, exist_ok=True); MEDIA.mkdir(parents=True, exist_ok=True)
    rows = []
    for chapter in a.chapters:
        ready = state['chapters'][chapter]
        audio, rate = sf.read(ROOT/('training-work'+ready['stream']), dtype='float32')
        for unit in read_timeline(a.generation, chapter, ready):
            found = stylized(unit.get('text', ''))
            if not found:continue
            clip = audio[int(unit['start']*rate):int(unit['end']*rate)]
            name = unit['id']+'.wav'; sf.write(MEDIA/name, clip, rate, subtype='PCM_16')
            how, heard = verification(a.generation, chapter, unit['id'])
            seconds = len(clip)/rate; parts = sum(n for _, n in found)
            only = re.fullmatch(r'[\W_]*' + r'[\W_]+'.join(re.escape(w) for w, _ in found) + r'[\W_]*', unit['text'].strip()) is not None
            short = only and seconds < SECONDS_PER_PART*parts
            rows.append({'id': unit['id'], 'chapter': chapter, 'text': unit['text'].strip(), 'sounds': [w for w, _ in found],
                         'speaker': unit.get('speaker'), 'seconds': round(seconds, 2), 'word_status': unit.get('word_status'),
                         'verified_by': how, 'heard': heard, 'too_short_warning': short,
                         'clip': name, 'clip_sha256': file_digest(MEDIA/name)})
    write_json(PAGE/'audition.json', {'generation': a.generation.name, 'chapters': a.chapters, 'rows': rows,
               'listener_verdict': None, 'note': 'Listening test only; no chapter audio changed, nothing approved.'})
    items = ''.join(
        f'<section{" class=warn" if r["too_short_warning"] else ""}><p><b>{html.escape(r["text"])}</b> '
        f'<small>{r["id"]} · {html.escape(str(r["speaker"]))} · {r["seconds"]} s · {html.escape(r["verified_by"])}'
        f'{" · распознано: «" + html.escape(r["heard"]) + "»" if r["heard"] else ""}'
        f'{" · <em>слишком коротко для протяжного звука</em>" if r["too_short_warning"] else ""}</small></p>'
        f'<audio controls preload="none" src="/character-rb2/full/clips/elirm-iv-mvp/sustained/{r["clip"]}?v={r["clip_sha256"][:10]}"></audio></section>'
        for r in rows)
    atomic_write(PAGE/'index.html', ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Протяжные звуки</title><style>:root{color-scheme:light dark}body{font:17px system-ui;max-width:820px;margin:auto;padding:16px;line-height:1.45}'
        'audio{width:100%}section{border-top:1px solid #8886;padding:6px 0}small{opacity:.75}.warn{background:#f804}</style>'
        f'<h1>Протяжные звуки — проверка на слух</h1><p>{len(rows)} фраз из опубликованных глав ({", ".join(a.chapters)}), '
        'вырезаны из того же аудио, что слышит слушатель. Распознавание подтверждает только сам звук, а не качество исполнения. '
        f'Отмечено предупреждением: {sum(r["too_short_warning"] for r in rows)}.</p>{items}</html>').encode('utf8'))
    print('AUDITION', len(rows), 'lines;', sum(r['too_short_warning'] for r in rows), 'too-short warnings ->', PAGE)


if __name__ == '__main__':main()
