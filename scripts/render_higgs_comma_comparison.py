"""One-variable punctuation audition, separate from chapter and human labels.

Run with training-work/higgs-production/.venv/Scripts/python.exe, which carries
the pinned spoken-text normalizer dependencies used by the chapter renderer.
"""
import argparse
import copy
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json,atomic_write
from rimsky.audio_join import boundary_check
from render_higgs_book_trial import MODEL,RUNNER,quality,sequence_request
from regenerate_higgs_book_trial import cached_audit
from normalize_higgs_word_audit import normalize


def main():
    import numpy as np
    import soundfile as sf
    import psutil
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stress-potom',action='store_true',help='Audition the contextual noun по́том, without effect tags.')
    parser.add_argument('--recover-ay-vsyo',action='store_true',help='Recover both words of the reported short remark, with a completion period.')
    args=parser.parse_args();stress=args.stress_potom;recovery=args.recover_ay_vsyo
    if stress and recovery:raise ValueError('Choose one audition')
    base=ROOT/'training-work/elirm-iv-higgs-mvp'
    unit_id='c0001-u0244' if recovery else 'c0001-u0227' if stress else 'c0001-u0228'
    source_chunk='chunk-001' if stress else 'chunk-002'
    selected=json.loads((base/'audio-palette-v1/c0001'/source_chunk/'selected.json').read_text('utf8'))[unit_id]
    original=selected['unit'];source=Path(selected['path']);before=original['synthesis']
    if (file_digest(source)!=selected['receipt']['audio_sha256'] or digest(before)!=selected['receipt']['key']):
        raise ValueError('Original audition changed')
    if file_digest(MODEL)!=before['model_sha256'] or file_digest(RUNNER)!=before['runner_sha256']:
        raise ValueError('Model or native runner changed; not a punctuation-only comparison')
    if file_digest(ROOT/before['reference']['audio'])!=before['reference']['sha256']:
        raise ValueError('Reference changed')
    after=copy.deepcopy(before)
    if before['prompt']!=before['text']:raise ValueError('Unexpected effect prefix')
    if recovery:
        if before['text']!='Ай, всё':raise ValueError('Unexpected reported short remark')
        after['text']=before['text']+'.';expected=after['text']
    elif stress:
        if before['text']!='Кровью, потом и немытыми задницами.':raise ValueError('Unexpected homograph context')
        after['text']=before['text'].replace('потом','по\u0301том')
        expected=before['text']
    else:
        if before['text'].count(',')!=1:raise ValueError('Expected exactly one comma')
        after['text']=before['text'].replace(',','');expected=after['text']
    after['prompt']=after['text']
    if {k for k in before if before[k]!=after[k]}!={'text','prompt'}:
        raise ValueError('Comparison changed more than punctuation')
    folder=base/('word-recovery-ay-vsyo-v1' if recovery else 'stress-potom-comparison-v1' if stress else 'comma-comparison-v1');folder.mkdir(exist_ok=True)
    # ASR checks the audible words, not a Unicode pronunciation marker. Its
    # success does not verify the stress; the accent audition needs listening.
    unit={**copy.deepcopy(original),'spoken':expected,'synthesis':after,'key':digest(after)}
    plan={'purpose':'missing_word_recovery' if recovery else 'stress_marker_audition' if stress else 'punctuation_only_audition','production_approved':False,
          'original_audio_sha256':file_digest(source),'units':[unit]}
    path=folder/'render-plan.json'
    if path.exists() and json.loads(path.read_text('utf8'))!=plan:raise ValueError('Frozen comparison changed')
    write_json(path,plan)
    sequence=folder/'sequence.json';write_json(sequence,{'requests':[sequence_request(unit['id'],after)]})
    paused=[]
    try:
        # Give the tiny audition priority without restarting the renderer or
        # repeating its expensive validation. Only our chapter orchestrators
        # are paused; existing native/ASR work and independent music finish.
        for process in psutil.process_iter(['pid','name','cmdline']):
            cmd=process.info.get('cmdline') or []
            if (process.pid!=psutil.Process().pid and process.info['name'].lower()=='python.exe'
                and any(Path(arg).name=='render_higgs_palette_preview.py' for arg in cmd)
                and any('elirm-iv-higgs-mvp' in arg for arg in cmd)):
                process.suspend();paused.append(process)
        write_json(folder/'paused-processes.json',{'pids':[p.pid for p in paused]})
        while True:
            busy=[]
            for process in psutil.process_iter(['name','cmdline']):
                name=(process.info['name'] or '').lower();cmd=process.info['cmdline'] or []
                if name=='audiocpp_cli.exe' or (name=='python.exe' and any(
                    Path(arg).name=='audit_higgs_book_cuda.py' or Path(arg).name.startswith('music_') and arg.endswith('.py') for arg in cmd)):
                    busy.append(process.pid)
            if not busy:break
            print('WAITING FOR EXISTING AUDIO WORK',busy,flush=True);time.sleep(5)
        wav=folder/unit['audio']
        if not wav.exists():
            print('GENERATING STRESS AUDITION' if stress else 'GENERATING WITHOUT COMMA',after['text'],flush=True)
            with (folder/'native.log').open('w',encoding='utf8') as log:
                subprocess.run([str(RUNNER),'--task','tts','--family','higgs_audio_tts','--model',str(MODEL),
                    '--backend','cuda','--device','0','--threads','4','--request-sequence',str(sequence),
                    '--out-dir',str(folder),'--out-format','pcm16','--metrics'],cwd=RUNNER.parent,
                    stdout=log,stderr=subprocess.STDOUT,timeout=900,check=True)
        receipt={**quality(wav,after),'key':unit['key'],'audio':unit['audio'],'synthesis_sha256':digest(after)}
        write_json(folder/'receipts.json',{'plan_sha256':file_digest(path),'takes':{unit['id']:receipt}})
        report=cached_audit(folder)
        if report is None:
            subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(folder)],cwd=ROOT,check=True)
            report=normalize(folder)
    finally:
        for process in reversed(paused):
            try:process.resume()
            except psutil.NoSuchProcess:pass
        write_json(folder/'paused-processes.json',{'pids':[]})
        print('CHAPTER RENDERER RESUMED',flush=True)
    metrics={}
    variant='complete-remark' if recovery else 'stress-marker' if stress else 'without-comma'
    for name,wav in [('original',source),(variant,folder/unit['audio'])]:
        x,sr=sf.read(wav);width=round(sr*.01);padded=np.pad(x,(0,(-len(x))%width))
        rms=np.sqrt(np.mean(padded.reshape(-1,width)**2,axis=1));quiet=rms<max(.001,float(rms.max())*.035)
        start=None;pauses=[]
        for i,flag in enumerate(list(quiet)+[False]):
            if flag and start is None:start=i
            if not flag and start is not None:
                if i-start>=8 and start>0 and i<len(quiet):pauses.append({'start':start*.01,'end':i*.01,'seconds':(i-start)*.01})
                start=None
        metrics[name]={'seconds':len(x)/sr,'internal_low_energy_intervals':pauses,'edge':boundary_check(x,sr),'audio_sha256':file_digest(wav)}
    result={'changed_fields':['text','prompt'],'seed':after['sampling']['seed'],
        'effects':0,'metrics':metrics,'word_check':report['checks'][unit['id']],'listener_approved':False,
        'pronunciation_verified':False if stress else None}
    answers=list(folder.glob('answers-listener-*.json'))
    for answer_path in answers:
        answer=json.loads(answer_path.read_text('utf8'))
        if (answer.get('source')=='explicit_listener' and answer.get('verdict')=='approved'
            and answer.get('audio_sha256')==metrics[variant]['audio_sha256']):
            result.update(listener_approved=True,listener_answer_sha256=file_digest(answer_path))
    write_json(folder/'comparison.json',result)
    slug='elirm-ay-vsyo-recovery-v1' if recovery else 'elirm-stress-potom-v1' if stress else 'elirm-comma-comparison-v1';page=ROOT/'training-work/character-rb2-full/fine-tune-all'/slug
    media=ROOT/'training-work/character-rb2/full/clips'/slug;page.mkdir(exist_ok=True);media.mkdir(exist_ok=True)
    parts=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Проверка произношения</title><style>body{font:20px system-ui;max-width:760px;margin:auto;padding:24px;line-height:1.5}audio{width:100%}</style>',
        '<h1>Ай, всё</h1><p>Восстанавливаем оба слова короткой реплики. В синтез добавлена завершающая точка; печатный текст и голос сохранены.</p>' if recovery else
        '<h1>По́том: запах пота</h1><p>В этом перечислении нужен творительный падеж слова «пот», не наречие «пото́м». Проверяем явную отметку ударения на первом слоге. Поддержка такого указания ещё не подтверждена на слух.</p>' if stress else
        '<h1>Одна фраза: с запятой и без</h1><p>Голос, опорная запись, модель и seed одинаковые. Эффектов нет. Меняется только запятая.</p>']
    for label,name,wav,text in [('Исходный вариант','original',source,before['text']),
                                ('Завершённая реплика' if recovery else 'Отметка ударения: по́том' if stress else 'Без запятой',variant,folder/unit['audio'],after['text'])]:
        target=name+'-'+file_digest(wav)[:12]+'.wav';shutil.copyfile(wav,media/target)
        parts.append('<h2>'+label+'</h2><p>'+html.escape(text)+'</p><audio controls preload="metadata" src="/character-rb2/full/clips/'+slug+'/'+target+'"></audio>')
    if report['checks'][unit['id']]['status']!='asr_exact_match':
        parts.append('<p>Есть расхождение распознавания слов: '+html.escape(report['checks'][unit['id']].get('heard',''))+'. Это флаг для проверки, не подтверждение правильного ударения.</p>')
    parts.append('<p>Голос, опора и seed сохранены. Эффектов нет. Проверка слов не проверяет ударение. Этот новый вариант пока не установлен в главу.</p></html>' if stress else
                 '<p>Это проверка пунктуации; исходный текст книги не изменён. Оценка связности остаётся за прослушиванием.</p></html>')
    atomic_write(page/'index.html',''.join(parts).encode('utf8'))
    print('PUBLISHED',page,flush=True);print(json.dumps(metrics,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
