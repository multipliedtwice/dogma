"""A/B test: does a doubled one-letter preposition (Сс потолка, вв сторону) stop Higgs swallowing it?

Word recognizers fill a swallowed с/в/к back in from context, so the phonetic
model (sounds only) judges presence. For each sentence the published take is
compared with fresh renders of the plain text and of a synthesis-only
respelling that doubles the preposition's consonant; the printed text and the
expected spoken words never change. Same voice, reference and mood; two seeds
per variant. Changes no chapter audio and approves nothing.
Run with training-work/higgs-production/.venv (GPU must be free).
"""
import argparse
import copy
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import digest, file_digest, write_json, atomic_write
from render_higgs_book_trial import MODEL, RUNNER, quality, sequence_request

FOLDER = ROOT/'training-work/elirm-iv-higgs-mvp/preposition-doubling-audition-v1'
PAGE = ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/prepositions'
MEDIA = ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp/prepositions'
SEEDS = (0, 1)


def doubled(text, targets):
    """Double the consonant of each targeted preposition (word index among Cyrillic words)."""
    words = list(re.finditer(r'[А-Яа-яЁё́]+', text)); out = text
    for index in sorted(targets, reverse=True):
        m = words[index]; w = m[0]
        if w.casefold() not in ('с', 'в', 'к'):raise ValueError('Target is not a one-letter preposition')
        out = out[:m.start()] + w + w.lower() + out[m.end():]
    return out


def main():
    from preposition_check import check
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe', type=Path, required=True, help='preposition probe rows (id, spoken, path, missing)')
    p.add_argument('--extra', nargs='*', default=[], help='unit ids to include besides flagged rows')
    a = p.parse_args()
    probe = {r['id']: r for r in json.loads(a.probe.read_text('utf8'))}
    merge = {'с': 'сзшжщч', 'в': 'вф', 'к': 'кгх'}
    picks = {uid: r for uid, r in probe.items()
             if any(len(ctx.split()) > 1 and ctx.split()[1][:1] not in merge[w] for _, w, ctx in r['missing']) or uid in a.extra}
    FOLDER.mkdir(parents=True, exist_ok=True)
    units, requests, meta = [], [], {}
    for uid, r in picks.items():
        take = Path(r['path']); plan = json.loads((take.parent/'render-plan.json').read_text('utf8'))
        unit = next(u for u in plan['units'] if u['id'] == uid); base = unit['synthesis']
        targets = [t for t, w, ctx in r['missing'] if len(ctx.split()) > 1 and ctx.split()[1][:1] not in merge[w]] or \
                  [t for t, w, _ in r['missing']]
        for variant in ('plain', 'doubled'):
            for seed in SEEDS:
                s = copy.deepcopy(base)
                if variant == 'doubled':s['text'] = s['prompt'] = doubled(base['text'], targets)
                s['sampling']['seed'] = int(digest({'unit': uid, 'variant': variant, 'seed': seed})[:8], 16) % 2**31
                ident = f'{uid}--{variant}-{seed}'
                units.append({'id': ident, 'unit': uid, 'variant': variant, 'spoken': r['spoken'], 'synthesis': s,
                              'key': digest(s), 'audio': ident+'.wav'})
                requests.append(sequence_request(ident, s))
        meta[uid] = {'targets': targets, 'published': str(take)}
    plan_path = FOLDER/'render-plan.json'
    write_json(plan_path, {'purpose': 'preposition_doubling_audition', 'production_approved': False, 'units': units})
    for n in range(0, len(requests), 25):
        batch = [r for r in requests[n:n+25] if not (FOLDER/(r['id']+'.wav')).exists()]
        if not batch:continue
        seq = FOLDER/f'sequence-{n:04d}.json'; write_json(seq, {'requests': batch})
        with (FOLDER/f'sequence-{n:04d}.log').open('w', encoding='utf8') as log:
            subprocess.run([str(RUNNER), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(MODEL), '--backend', 'cuda',
                            '--device', '0', '--threads', '4', '--request-sequence', str(seq), '--out-dir', str(FOLDER),
                            '--out-format', 'pcm16', '--metrics'], cwd=RUNNER.parent, stdout=log, stderr=subprocess.STDOUT, check=False)
    results = {}
    for u in units:
        wav = FOLDER/u['audio']
        if not wav.exists():results[u['id']] = {'rendered': False};continue
        missing = check(u['spoken'], wav)
        results[u['id']] = {'rendered': True, 'present': not any(t in meta[u['unit']]['targets'] for t, _, _ in missing),
                            'audio_sha256': file_digest(wav)}
    for uid in picks:
        missing = check(probe[uid]['spoken'], meta[uid]['published'])
        results[uid+'--published'] = {'present': not any(t in meta[uid]['targets'] for t, _, _ in missing)}
    summary = {v: f"{sum(r.get('present', False) for k, r in results.items() if f'--{v}-' in k)}/"
                  f"{sum(1 for k, r in results.items() if f'--{v}-' in k and r.get('rendered'))}" for v in ('plain', 'doubled')}
    summary['published'] = f"{sum(r['present'] for k, r in results.items() if k.endswith('--published'))}/{len(picks)}"
    write_json(FOLDER/'results.json', {'summary': summary, 'results': results, 'meta': meta, 'listener_verdict': None})
    PAGE.mkdir(parents=True, exist_ok=True); MEDIA.mkdir(parents=True, exist_ok=True)
    def player(path):
        name = file_digest(path)[:16]+'.wav'; shutil.copyfile(path, MEDIA/name)
        return f'<audio controls preload="none" src="/character-rb2/full/clips/elirm-iv-mvp/prepositions/{name}"></audio>'
    def mark(ok):return '✓' if ok else '✗ предлог не слышен (фонетическая проверка)'
    parts = []
    for uid in picks:
        rows = [('Опубликовано', Path(meta[uid]['published']), results[uid+'--published']['present'], probe[uid]['spoken'])]
        for u in units:
            if u['unit'] == uid and results[u['id']].get('rendered'):
                rows.append((('Как есть' if u['variant'] == 'plain' else 'Удвоенный предлог') + f' · seed {u["id"][-1]}',
                             FOLDER/u['audio'], results[u['id']]['present'], u['synthesis']['text']))
        parts.append('<section><h3>' + html.escape(probe[uid]['spoken']) + '</h3>' + ''.join(
            f'<p>{html.escape(label)} — {mark(ok)}<br><small>{html.escape(text)}</small></p>{player(path)}' for label, path, ok, text in rows) + '</section>')
    atomic_write(PAGE/'index.html', ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Предлоги с, в, к</title><style>:root{color-scheme:light dark}body{font:17px system-ui;max-width:820px;margin:auto;padding:16px;line-height:1.4}'
        'audio{width:100%}section{border-top:1px solid #8886;padding:6px 0}small{opacity:.7}</style>'
        f'<h1>Проглоченные предлоги: удвоение согласной</h1><p>Фонетическая проверка: опубликовано {summary["published"]}, '
        f'как есть {summary["plain"]}, удвоенный предлог {summary["doubled"]} (предлог слышен). Печатный текст книги не меняется.</p>'
        + ''.join(parts) + '</html>').encode('utf8'))
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), '->', PAGE)


if __name__ == '__main__':main()
