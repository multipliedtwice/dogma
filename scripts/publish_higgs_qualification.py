"""Publish read-only qualification audio with UTF-8 labels from file-based code."""
import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import atomic_write,file_digest,inside


def publish(folder,slug):
    if not re.fullmatch(r'[a-z0-9-]+',slug):raise ValueError('Invalid qualification URL slug')
    cases=json.loads((folder/'cases.json').read_text('utf-8'))['cases']
    audit=json.loads((folder/'word-checks-1.7B.json').read_text('utf-8'))
    if audit['plan_sha256']!=file_digest(folder/'render-plan.json'):raise ValueError('Stale word report')
    page=ROOT/'training-work/character-rb2-full/fine-tune-all'/slug
    media=ROOT/'training-work/character-rb2/full/clips'/slug
    page.mkdir(parents=True,exist_ok=True);media.mkdir(parents=True,exist_ok=True)
    url='/character-rb2/full/clips/'+slug+'/'
    parts=['<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>Higgs — проверка подачи</title><style>body{font:19px system-ui;line-height:1.5;max-width:800px;margin:auto;padding:22px}audio{width:100%;margin:10px 0}section{padding:18px 0;border-bottom:1px solid #ccc}small{color:#555}</style></head><body>',
           '<h1>Хороший… хороший…</h1><p>Это сравнение исполнения, а не новая глава. Слова проверены. Решение о конкретном варианте показано ниже.</p>',
           '<p>Контекст: Окрус опустился перед псом на колени и принялся ласково чесать его за ушами. Нужна спокойная, ободряющая речь к собаке, без соблазняющей или интимной интонации.</p>']
    seen=set()
    for case in cases:
        wav=inside(folder,case['audio']);check=audit['checks'][case['id']]
        if (file_digest(wav)!=case['audio_sha256'] or check['audio_sha256']!=case['audio_sha256']
            or check['synthesis_key']!=case['native_plan_sha256'] or check['status']!='asr_exact_match'):
            raise ValueError('Qualification audio or word check changed')
        reference=case['reference_evidence'];ref_hash=reference['audio_sha256']
        if ref_hash not in seen:
            if seen:parts.append('</section>')
            seen.add(ref_hash)
            source=inside(ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1',reference['audio'])
            if file_digest(source)!=ref_hash:raise ValueError('Reviewed reference changed')
            name='reference-'+ref_hash+'.wav';shutil.copyfile(source,media/name)
            title='Опора на тёплое исполнение Влада' if reference['mood']=='warm' else 'Опора на обычный голос'
            parts+=['<section><h2>'+title+'</h2><details><summary>Исходный голос Влада</summary><p>'+html.escape(reference['text'])+'</p><audio controls preload="none" src="'+url+name+'"></audio></details>']
        shutil.copyfile(wav,media/case['audio'])
        label='Без дополнительного управления' if not case['tags'] else 'С управлением тёплой подачи'
        parts+=['<h3>'+label+'</h3><audio controls preload="none" src="'+url+html.escape(case['audio'],quote=True)+'"></audio>']
    parts.append('</section>')
    answers=sorted(folder.glob('answers-listener-*.json'))
    result_path=folder/'qualification-results.json'
    if result_path.exists():
        qualification=json.loads(result_path.read_text('utf-8'))
        if qualification['cases_sha256']!=file_digest(folder/'cases.json'):raise ValueError('Stale qualification result')
        answers=[p for p in answers if file_digest(p)==qualification['answers_sha256']]
        if len(answers)!=1:raise ValueError('Latest qualification result has no matching listener answers')
    if answers:
        for answer in json.loads(answers[-1].read_text('utf-8')).get('answers',[]):
            if answer.get('source')=='explicit_listener' and answer.get('verbatim'):
                parts.append('<p>Сохранённый отзыв слушателя: «'+html.escape(answer['verbatim'])+'».</p>')
                if answer.get('verdict')=='rejected':parts.append('<p>Этот вариант не принят для выпуска.</p>')
                elif answer.get('verdict')=='approved' and answer.get('approval_scope')=='take':
                    parts.append('<p>Этот конкретный вариант принят для MVP с оговорками. Это не общее одобрение управления для других реплик.</p>')
    parts+=['<small>Ваши метки не меняются. Здесь сравниваются опорные записи; в книге голос персонажа должен оставаться постоянным.</small></body></html>']
    raw=''.join(parts).encode('utf-8')
    if b'????' in raw:raise ValueError('Broken text encoding')
    atomic_write(page/'index.html',raw)
    return page/'index.html'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--slug',required=True)
    args=parser.parse_args();print(publish(args.folder.resolve(),args.slug))
