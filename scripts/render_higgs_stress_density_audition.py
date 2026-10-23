"""Stress-mark density audition: sparse, every word, and sparse plus rare words.

A single explicit mark (по́том) changed Higgs' stress as intended, while a mark
on every word made pronunciation worse. This audition keeps voice, reference,
seed and words fixed and changes only which words carry U+0301, so a listener
can compare densities on the same sentences. It never touches chapter audio.

Run with training-work/higgs-production/.venv/Scripts/python.exe.
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
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import digest,file_digest,write_json,atomic_write
from rimsky.spoken_pronunciation import ACUTE,VOWELS
from render_higgs_book_trial import MODEL,RUNNER,quality,sequence_request

BASE=ROOT/'training-work/elirm-iv-higgs-mvp'
SPARSE=BASE/'audio-palette-v3-descriptors';DENSE=BASE/'audio-palette-v3-stress-all'
NAME='stress-density-audition-v1';FOLDER=BASE/NAME
PAGE=ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/stress-density'
MEDIA=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp/stress-density'
RARE_ZIPF=3.5  # wordfreq Zipf scale; 3.5 ~ a few occurrences per million words
OK=('asr_exact_match','nonverbal_not_spoken_text')


def rare(token):
    from wordfreq import zipf_frequency
    parts=re.findall(r'[А-Яа-яЁё]+',token.replace(ACUTE,''))
    return any(sum(c in VOWELS for c in p)>=2 and zipf_frequency(p.lower(),'ru')<RARE_ZIPF for p in parts)


def rare_marks(sparse,dense):
    """Sparse text with the dense mark applied to rare words only."""
    a,b=sparse.split(' '),dense.split(' ')
    if len(a)!=len(b) or any(x.replace(ACUTE,'')!=y.replace(ACUTE,'') for x,y in zip(a,b)):return None
    return ' '.join(y if rare(y) and ACUTE in y and x!=y else x for x,y in zip(a,b))


def selections(generation):
    rows={}
    for path in sorted((generation/'c0002').glob('chunk-00[01]/selected.json')):
        rows.update(json.loads(path.read_text('utf8')))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=10);args=parser.parse_args()
    sparse,dense=selections(SPARSE),selections(DENSE)
    picks=[]
    for uid,row in dense.items():
        other=sparse.get(uid)
        if not other or row['word_check']['status'] not in OK or other['word_check']['status'] not in OK:continue
        if row['receipt']['duration']>12:continue
        text=rare_marks(other['unit']['synthesis']['text'],row['unit']['synthesis']['text'])
        if text is None:continue
        added=sum(x!=y for x,y in zip(other['unit']['synthesis']['text'].split(' '),text.split(' ')))
        if added>=2:picks.append((added,uid,text))
    picks=sorted(sorted(picks,reverse=True)[:args.count],key=lambda p:p[1])
    if not picks:raise ValueError('No audition sentences')
    FOLDER.mkdir(exist_ok=True);units=[];requests=[]
    for _,uid,text in picks:
        row=dense[uid];before=row['unit']['synthesis']
        if file_digest(Path(row['path']))!=row['receipt']['audio_sha256']:raise ValueError('Dense take changed: '+uid)
        if file_digest(MODEL)!=before['model_sha256'] or file_digest(RUNNER)!=before['runner_sha256']:
            raise ValueError('Model or native runner changed; not a marks-only comparison')
        if before['prompt']!=before['text']:raise ValueError('Unexpected prompt prefix')
        after=copy.deepcopy(before);after['text']=after['prompt']=text
        if {k for k in before if before[k]!=after[k]}!={'text','prompt'}:raise ValueError('Comparison changed more than marks')
        unit={**copy.deepcopy(row['unit']),'synthesis':after,'key':digest(after),'audio':uid+'.wav'}
        units.append(unit);requests.append(sequence_request(uid,after))
    plan={'purpose':'stress_mark_density_audition','production_approved':False,'rare_zipf_below':RARE_ZIPF,'units':units}
    path=FOLDER/'render-plan.json'
    if path.exists() and json.loads(path.read_text('utf8'))!=plan:raise ValueError('Frozen audition changed')
    write_json(path,plan);sequence=FOLDER/'sequence.json';write_json(sequence,{'requests':requests})
    if any(not (FOLDER/u['audio']).exists() for u in units):
        with (FOLDER/'native.log').open('w',encoding='utf8') as log:
            subprocess.run([str(RUNNER),'--task','tts','--family','higgs_audio_tts','--model',str(MODEL),
                '--backend','cuda','--device','0','--threads','4','--request-sequence',str(sequence),
                '--out-dir',str(FOLDER),'--out-format','pcm16','--metrics'],cwd=RUNNER.parent,
                stdout=log,stderr=subprocess.STDOUT,timeout=1800,check=True)
    takes={u['id']:{**quality(FOLDER/u['audio'],u['synthesis']),'key':u['key'],'audio':u['audio'],
                    'synthesis_sha256':digest(u['synthesis'])} for u in units}
    write_json(FOLDER/'receipts.json',{'plan_sha256':file_digest(path),'takes':takes})
    from regenerate_higgs_book_trial import cached_audit
    from normalize_higgs_word_audit import normalize
    report=cached_audit(FOLDER)
    if report is None:
        subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
            str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(FOLDER)],cwd=ROOT,check=True)
        report=normalize(FOLDER)
    PAGE.mkdir(parents=True,exist_ok=True);MEDIA.mkdir(parents=True,exist_ok=True)
    def show(text,base=None):
        words=text.split(' ');ref=(base or text).split(' ')
        return ' '.join('<b>'+html.escape(w)+'</b>' if base and w!=r else html.escape(w) for w,r in zip(words,ref))
    def player(wav):
        target=file_digest(wav)[:16]+'.wav';shutil.copyfile(wav,MEDIA/target)
        return '<audio controls preload="none" src="/character-rb2/full/clips/elirm-iv-mvp/stress-density/'+target+'"></audio>'
    parts=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Плотность ударений</title><style>:root{color-scheme:light dark}body{font:18px system-ui;max-width:820px;margin:auto;padding:16px;line-height:1.5}',
        'audio{width:100%}section{border-top:1px solid #8886;padding:8px 0}h3{margin:10px 0 2px;font-size:15px;opacity:.75}b{color:#c60}</style>',
        '<h1>Плотность отметок ударения</h1><p><b>A</b> — как в главах сейчас: без автоматических ударений (другой seed). ',
        '<b>B</b> — ударение на каждом слове. <b>C</b> — ударения на омографах, именах и редких словах (выделены). ',
        'B и C: одинаковые голос, опора и seed — меняются только отметки. Проверка слов не проверяет ударение.</p>']
    results={}
    for unit in units:
        uid=unit['id'];a,b=sparse[uid],dense[uid];check=report['checks'][uid]
        results[uid]={'c_audio_sha256':takes[uid]['audio_sha256'],'c_word_check':check['status'],
                      'a_audio_sha256':a['receipt']['audio_sha256'],'b_audio_sha256':b['receipt']['audio_sha256']}
        parts.append('<section><p>'+html.escape(a['unit']['text'].strip())+'</p>')
        for label,text,wav,base in [('A — сейчас',a['unit']['synthesis']['text'],Path(a['path']),None),
                                    ('B — каждое слово',b['unit']['synthesis']['text'],Path(b['path']),None),
                                    ('C — редкие слова',unit['synthesis']['text'],FOLDER/unit['audio'],a['unit']['synthesis']['text'])]:
            parts.append('<h3>'+label+'</h3><div>'+show(text,base)+'</div>'+player(wav))
        if check['status'] not in OK:parts.append('<p>C: расхождение распознавания — '+html.escape(check.get('heard',''))+'</p>')
        parts.append('</section>')
    atomic_write(PAGE/'index.html',(''.join(parts)+'</html>').encode('utf8'))
    write_json(FOLDER/'comparison.json',{'units':results,'listener_approved':False})
    print('PUBLISHED',PAGE,flush=True)
    print(json.dumps({u:r['c_word_check'] for u,r in results.items()}),flush=True)


if __name__=='__main__':main()
