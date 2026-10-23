"""Pinned five-seed paragraph audition; never changes a book or its assignment."""
from __future__ import annotations
import argparse,copy,html,json,random,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import digest,file_digest,write_json,atomic_write
from rimsky.audio_join import boundary_check
from rimsky.spoken_pronunciation import unmarked
from render_higgs_book_trial import render,spoken_text
from regenerate_higgs_book_trial import wait_for_audio_resources,cached_audit
from normalize_higgs_word_audit import normalize
from higgs_trial_io import read_json as read
PAGE=ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/comma-comparison'
MEDIA=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp'
SEEDS=[2026101401+i for i in range(5)]


def prepare(folder):
    source=ROOT/'training-work/elirm-iv-higgs-mvp/source/book.json'
    bank_path=ROOT/'training-work/higgs-production/reference-bank/development-20261014-v2.json'
    book=read(source)
    candidates=[(c['id'],p) for c in book['chapters'] for p in c['blocks']
                if p.get('kind')=='paragraph' and p['text'].count(',')>=7
                and 55<=len(p['text'].split())<=100 and '—' not in p['text'] and '«' not in p['text']]
    if not candidates:raise ValueError('No eligible narration paragraph')
    chapter,paragraph=random.Random(20261014).choice(candidates)
    entry=next(r for r in read(bank_path)['entries'] if r['voice_family']=='narrator_baseline'
               and r['character']=='narrator' and r['mood']=='calm')
    ref=next(r for r in entry['candidates'] if not r['quality']['edges']['unsafe_start']
             and not r['quality']['edges']['unsafe_end'] and r['voice_basis']=='human' and r['mood_basis']=='human')
    original=spoken_text(paragraph['text']);removed=original.replace(',','')
    if unmarked(original).replace(',','')!=unmarked(removed):raise ValueError('Words changed')
    manifest={'schema':1,'purpose':'comma_only_paragraph_comparison','production_approved':False,
              'paragraph_request_exception':'Whole paragraph for this audition only; production units remain sentence sized.',
              'source_sha256':file_digest(source),'source_id':paragraph['id'],'chapter_id':chapter,
              'text':paragraph['text'],'variants':{'original':original,'no_commas':removed},
              'commas_removed':original.count(','),'selection_seed':20261014,
              'seed_pairs':SEEDS,'bank_sha256':file_digest(bank_path),'reference':ref,
              'effects':'disabled','postprocessing':'none; no music or loudness normalization'}
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'manifest.json'
    if target.exists() and read(target)!=manifest:raise ValueError('Experiment inputs changed; use a new folder')
    write_json(target,manifest)
    if not (folder/'progress.json').exists():write_json(folder/'progress.json',{'status':'prepared','pairs':[]})
    publish(folder)
    return manifest


def publish(folder):
    manifest=read(folder/'manifest.json');progress=read(folder/'progress.json')
    PAGE.mkdir(parents=True,exist_ok=True);MEDIA.mkdir(parents=True,exist_ok=True)
    out=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>Higgs — сравнение запятых</title><style>body{font:19px system-ui;max-width:950px;margin:auto;padding:20px;background:#f6f8f3}audio{width:100%}article{background:white;padding:20px;margin:20px 0;border-radius:16px}p{line-height:1.5}</style>',
         '<h1>Один абзац: с запятыми и без</h1>',
         '<p>Пять пар с одинаковыми seed, моделью, голосом и эталоном Calm рассказчика. Только запятые различаются. Без эффектов, музыки и склейки предложений. Этот эксперимент не меняет текст книги или готовые главы.</p>',
         '<blockquote>'+html.escape(manifest['text'])+'</blockquote>',
         f'<p>Удалено запятых: {manifest["commas_removed"]}. Готово пар: {len(progress["pairs"])}/5. '+html.escape(progress['status'])+'</p>']
    for pair in progress['pairs']:
        out.append(f'<article><h2>Пара {pair["number"]}</h2>')
        order=['original','no_commas'] if pair['number']%2 else ['no_commas','original']
        for kind in order:
            row=pair['variants'][kind]
            label='Исходные запятые' if kind=='original' else 'Без запятых'
            flags=[]
            if row['word_status']!='asr_exact_match':flags.append('есть расхождения ASR — не подтверждены слова')
            if row['edge']['unsafe_start'] or row['edge']['unsafe_end']:flags.append('небезопасный край аудио')
            out.append('<h3>'+label+'</h3><audio controls preload="none" src="'+html.escape(row['stream'])+'"></audio>'
                       +'<p>'+html.escape('; '.join(flags) or 'Слова совпали с ASR; края проверены. Естественность ещё не оценена.')+'</p>')
        out.append('</article>')
    out.append('<p><a href="../">Вернуться к главам</a></p>')
    if progress['status']!='complete':out.append('<script>setTimeout(()=>{if(![...document.querySelectorAll("audio")].some(a=>!a.paused))location.reload()},20000)</script>')
    out.append('</html>');atomic_write(PAGE/'index.html','\n'.join(out).encode('utf8'))


def run(folder):
    import soundfile as sf
    manifest=prepare(folder);progress=read(folder/'progress.json')
    progress['status']='rendering';write_json(folder/'progress.json',progress);publish(folder)
    for number,seed in enumerate(SEEDS,1):
        if any(p['number']==number for p in progress['pairs']):continue
        ref=manifest['reference'];palette={'template_id':'comma-demo-narrator-calm','source_character':'narrator',
            'allowed_reference_sha256':[ref['audio_sha256']],'effects_policy':'disabled'}
        units=[{'id':f'pair{number}-{kind}','chapter_id':'demo','source_id':manifest['source_id'],
                'text':text,'speaker':'narrator','mode':'narrator','voice':'narrator_baseline',
                'mood':'calm','render_seed':seed,'palette_reference':copy.deepcopy(ref),
                'palette_assignment':palette} for kind,text in manifest['variants'].items()]
        pair_folder=folder/f'pair-{number}';pair_folder.mkdir(exist_ok=True)
        assignment={'units':units,'experiment_sha256':file_digest(folder/'manifest.json')}
        path=pair_folder/'assignment.json'
        if path.exists() and read(path)!=assignment:raise ValueError('Pair changed')
        write_json(path,assignment)
        render(path,pair_folder,['demo'],execution_guard=wait_for_audio_resources)
        plans=read(pair_folder/'render-plan.json')['units']
        a,b=[r['synthesis'] for r in plans]
        excluded={'text','prompt','phrase_pronunciation'}
        if {k:v for k,v in a.items() if k not in excluded}!={k:v for k,v in b.items() if k not in excluded}:
            raise ValueError('Comparison changed more than punctuation')
        audit=cached_audit(pair_folder)
        if audit is None:
            wait_for_audio_resources()
            subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(pair_folder)],cwd=ROOT,check=True)
            audit=normalize(pair_folder)
        receipts=read(pair_folder/'receipts.json');variants={}
        for unit in plans:
            kind=unit['id'].split('-',1)[1];take=receipts['takes'][unit['id']]
            wav=pair_folder/take['audio'];sha=file_digest(wav);check=audit['checks'][unit['id']]
            if check['audio_sha256']!=sha or check['synthesis_key']!=take['key']:raise ValueError('Unbound audit')
            audio,rate=sf.read(wav,dtype='float32');filename='comma-demo-'+sha[:20]+'.wav'
            target=MEDIA/filename
            if not target.exists():shutil.copyfile(wav,target)
            if file_digest(target)!=sha:raise ValueError('Published audio mismatch')
            variants[kind]={'audio_sha256':sha,'synthesis_key':take['key'],'seed':seed,
                'seconds':len(audio)/rate,'word_status':check['status'],'edge':boundary_check(audio,rate),
                'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+filename}
        progress['pairs'].append({'number':number,'variants':variants})
        write_json(folder/'progress.json',progress);publish(folder)
        print('PUBLISHED COMMA PAIR',number,flush=True)
    progress['status']='complete';write_json(folder/'progress.json',progress);publish(folder)


def report(folder):
    """Pin actual completed takes and audits, including every failed check."""
    manifest=read(folder/'manifest.json');progress=read(folder/'progress.json')
    if progress['status']!='complete' or len(progress['pairs'])!=5:raise ValueError('Comparison not complete')
    pairs=[]
    for pair in progress['pairs']:
        p=folder/f'pair-{pair["number"]}'
        plan=read(p/'render-plan.json');receipts=read(p/'receipts.json');audit=read(p/'word-checks-1.7B-normalized.json')
        binding=file_digest(p/'render-plan.json')
        if receipts['plan_sha256']!=binding or audit['plan_sha256']!=binding:raise ValueError('Changed comparison binding')
        for unit in plan['units']:
            take=receipts['takes'][unit['id']]
            if take['key']!=unit['key'] or digest(unit['synthesis'])!=take['key'] or file_digest(p/take['audio'])!=take['audio_sha256']:
                raise ValueError('Changed comparison take')
            if unit['synthesis']['prompt']!=unit['synthesis']['text'] or unit['synthesis']['effects_policy']!='disabled':
                raise ValueError('Effects entered punctuation comparison')
        pairs.append({'number':pair['number'],'plan_sha256':binding,
            'receipts_sha256':file_digest(p/'receipts.json'),
            'word_audit_sha256':file_digest(p/'word-checks-1.7B-normalized.json'),'variants':pair['variants']})
    value={'manifest_sha256':file_digest(folder/'manifest.json'),'pairs':pairs,
           'production_approved':False,'source_unchanged':file_digest(ROOT/'training-work/elirm-iv-higgs-mvp/source/book.json')==manifest['source_sha256'],
           'listening_verdict':None,'comma_removal_enabled_for_books':False,
           'limits':'Pinned raw takes, not selected best takes; ASR discrepancies and unsafe edges retained. Duration alone does not establish phrasing quality.'}
    write_json(folder/'report.json',value);return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--run',action='store_true');p.add_argument('--report',action='store_true');args=p.parse_args()
    if args.report:report(args.folder.resolve());sys.exit(0)
    if args.run:run(args.folder.resolve())
    else:prepare(args.folder.resolve())
