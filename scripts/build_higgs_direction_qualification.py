"""Six native-backend auditions; no chapter publication or human verdicts.

These are explicit control experiments, not hand-edited generation answers.
Use the same Higgs planner/renderer as app jobs, with disclosed evaluation
qualification bypass. Model, reference, worker, seed and outputs are pinned.
"""
import argparse
import html
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.higgs_backend import HiggsBackend,synthesis_plan
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.util import digest,file_digest,write_json,SpeechHoldError,GenerationCapError
from prepare_higgs_listening import candidate_pack
from rimsky.cli_process import run_cli
from rimsky.higgs_reference import check_reference
from regenerate_higgs_book_trial import wait_for_audio_resources

def build(output,run=False,suite='controls-v1'):
    base=ROOT/'training-work/higgs-production';dataset=base/'datasets/reviewed-20261012-v1'
    pinned=resolve_production_higgs_model(ROOT);model=pinned.path
    runner=ROOT/'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
    references=json.loads((dataset/'references.json').read_text('utf-8'))
    model_sha=file_digest(model)
    proof=pinned.provenance
    if proof['model_sha256']!=model_sha:raise ValueError('Custom model changed')
    version=run_cli([str(runner),'--version'],cwd=runner.parent,timeout=30).stdout.strip()
    # Named reported cases are a frozen listening experiment, never casting
    # rules in the production engine. There are three signatures, six takes.
    specs=[('gentle','masculine_2',0,'Хороший… хороший…','ordinary',[],
            [('ordinary',[]),('warm',['affection','expressive_low'])]),
           ('rough','masculine_6',1,'Оставь меня в покое, трутень…','hoarse',[],
            [('ordinary',[]),('subdued',['expressive_low'])]),
           ('low','masculine_6',1,'Ладно-ладно. Я потерплю. Только заткнись!','hoarse',['pitch_low'],
            [('ordinary',['pitch_low']),('intense',['pitch_low','expressive_high','shouting'])])]
    if suite=='references-v2':
        # Development recordings only; this is an anchor audition, not a rule
        # to change a character's reference whenever their mood changes.
        neutral=next((r for r in references['masculine_2'] if reference_safe(dataset,r)),None)
        pool=[json.loads(line) for line in (dataset/'train.jsonl').read_text('utf-8').splitlines() if line.strip()]
        warm=[];neutral_candidates=[]
        for row in pool:
            meta=row['metadata']
            if (meta['voice_family']!='masculine_2' or meta['mood'] not in ('neutral','warm','gentle')
                or meta['voice_basis']!='human' or meta['mood_basis']!='human' or not 2<=meta['seconds']<=8):continue
            ref={'audio':row['audio'],'audio_sha256':meta['audio_sha256'],'text':row['text'],
                 'mood':meta['mood'],'evidence_id':row['id'],'reviewed':True,'seconds':meta['seconds'],
                 'basis':'Human-reviewed development performance; anchor audition only; no held-out accuracy claim.'}
            if reference_safe(dataset,ref):
                (neutral_candidates if meta['mood']=='neutral' else warm).append(ref)
        if neutral is None and neutral_candidates:
            neutral=sorted(neutral_candidates,key=lambda r:(abs(r['seconds']-5),r['evidence_id']))[0]
        if neutral is None or not warm:raise ValueError('No complete reviewed neutral/warm references for the comparison')
        warm=sorted(warm,key=lambda r:(r['mood']!='warm',abs(r['seconds']-5),r['evidence_id']))[0]
        references={'masculine_2':[neutral,warm]}
        specs=[('gentle-safe-neutral','masculine_2',0,'Хороший… хороший…','ordinary',[],
                [('ordinary',[]),('warm',['affection'])]),
               ('gentle-reviewed-warm','masculine_2',1,'Хороший… хороший…','ordinary',[],
                [('ordinary',[]),('warm',['affection'])])]
    results=[];output.mkdir(parents=True,exist_ok=True)
    for actor,family,index,text,baseline,permanent,variants in specs:
        reference=references[family][index]
        if suite=='references-v2':check_reference(dataset/reference['audio'],reference['audio_sha256'])
        pack=candidate_pack(dataset,model,{'voice':family,'reference':reference,'candidate_tags':[]},runner,version,False)
        combinations=[]
        for register,tags in variants:
            technique='shouting' if 'shouting' in tags else baseline
            combinations.append({'id':actor+'-'+register,'register':register,'technique':technique,
                'pace':'normal','tags':tags,'reference':'neutral'})
            if register=='warm':combinations[-1]['warmth']='reassuring'
        for combination in combinations:combination['permanent_tags']=permanent
        pack['voices'][0]['higgs_controls']['combinations']=combinations
        pack['_fingerprint']=digest({k:v for k,v in pack.items() if not k.startswith('_')})
        backend=HiggsBackend();resolved=backend.probe({'pack':pack,'device':'cuda'})
        for combination in combinations:
            cid=combination['id'];wav=output/(cid+'.wav')
            direction={'turn_id':cid,'register':combination['register'],'technique':combination['technique'],
                'warmth':combination.get('warmth','none'),
                'audio_kind':'speech','event_kind':'none','needs_adjudication':False,
                'audible_stance':'Qualification contrast; expected intent is to be judged by listening.'}
            controls={'direction':direction,'character_profile':{'baseline_technique':baseline},
                'voice_signature':{'character_id':actor,'voice_family':family,'reference':'neutral',
                                   'permanent_tags':permanent,'gain':1.},'pace':'normal'}
            request={'pack':pack,'device':'cuda','resolved_speech':resolved,'voice_id':family,
                     'text':text,'delivery':'neutral','controls':controls,'output':str(wav)}
            attempts=[];selected=None
            for take in range(3):
                planned=synthesis_plan(pack,pack['voices'][0],text,'neutral',controls,resolved,take)
                frozen=output/(cid+f'-take-{take}.plan.json')
                if frozen.exists() and json.loads(frozen.read_text('utf-8'))!=planned:raise ValueError('Qualification plan changed; use a fresh folder')
                write_json(frozen,planned)
                if not run:break
                wait_for_audio_resources()
                try:
                    result=backend.render({**request,'take':take})
                    if result['synthesis']!={**planned,'plan_sha256':digest(planned)}:raise ValueError('Native plan receipt mismatch')
                    selected={**combination,'id':cid,'audio':wav.name,'audio_sha256':file_digest(wav),
                        'model_sha256':model_sha,'voice_family':family,'reference_sha256':reference['audio_sha256'],
                        'native_plan_sha256':digest(planned),'qualification_bypassed':True,'text':text,
                        'reference_evidence':reference,
                        'controls_pending_listening':True,'word_check':'not_run','attempts':attempts}
                    selected['permanent_tags']=permanent
                    selected['vocal_traits']={'pitch':'low' if 'pitch_low' in permanent else 'ordinary','baseline_technique':baseline}
                    write_json(output/(cid+'.receipt.json'),result);break
                except (SpeechHoldError,GenerationCapError) as exc:
                    attempts.append({'take':take,'error':str(exc)})
            results.append(selected or {'id':cid,'error':'Not rendered' if not run else 'Automatic edge/format retakes exhausted',
                                         'attempts':attempts,'voice_family':family})
            write_json(output/'results.json',results)
    cases=[r for r in results if r.get('audio')]
    if cases:audit_inputs(output,cases)
    write_json(output/'cases.json',{'schema':1,'model_sha256':model_sha,'cases':cases,
                                  'suite':suite,'reference_source_sha256':file_digest(dataset/'references.json'),
                                  'development_rows_sha256':file_digest(dataset/'train.jsonl'),
                                  'purpose':'bounded_candidate_listening','production_approved':False})
    page=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
          '<title>Higgs — проверка исполнения</title><style>body{font:19px system-ui;max-width:800px;margin:auto;padding:22px}audio{width:100%}article{margin:28px 0}</style>',
          '<h1>Короткие сравнения Higgs</h1><p>Шесть экспериментальных вариантов. Это не новая глава. Проверены формат и края аудио; слова и естественность ещё не утверждены. Проверка управлений временно отключена только для этого эксперимента.</p>']
    for result in results:
        page.append('<article><h2>'+html.escape(result['id'])+'</h2>')
        if result.get('audio'):page.append('<p>'+html.escape(result['text'])+'</p><audio controls preload="none" src="'+html.escape(result['audio'])+'"></audio>')
        else:page.append('<p>'+html.escape(result['error'])+'</p>')
        page.append('</article>')
    (output/'index.html').write_text(''.join(page)+'</html>',encoding='utf-8')
    return results


def reference_safe(dataset,reference):
    try:check_reference(dataset/reference['audio'],reference['audio_sha256'])
    except SpeechHoldError:return False
    return True


def audit_inputs(output,cases):
    """Derive verifier inputs from real native receipts, never edited answers."""
    units=[];takes={}
    for case in cases:
        receipt=json.loads((output/(case['id']+'.receipt.json')).read_text('utf-8'))
        synthesis=receipt['synthesis']
        native={k:v for k,v in synthesis.items() if k!='plan_sha256'}
        if (digest(native)!=case['native_plan_sha256'] or synthesis['plan_sha256']!=case['native_plan_sha256']
            or file_digest(output/case['audio'])!=case['audio_sha256']):raise ValueError('Native qualification receipt changed')
        units.append({'id':case['id'],'spoken':case['text'],'text':case['text'],
                      'key':case['native_plan_sha256'],'synthesis':native})
        takes[case['id']]={'audio':case['audio'],'audio_sha256':case['audio_sha256'],'key':case['native_plan_sha256']}
    plan={'schema':1,'purpose':'control_qualification','production_approved':False,'units':units}
    path=output/'render-plan.json'
    if path.exists() and json.loads(path.read_text('utf-8'))!=plan:raise ValueError('Qualification audit plan changed')
    write_json(path,plan)
    value={'plan_sha256':file_digest(path),'takes':takes}
    path=output/'receipts.json'
    if path.exists() and json.loads(path.read_text('utf-8'))!=value:raise ValueError('Qualification audit receipts changed')
    write_json(path,value)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--run',action='store_true')
    p.add_argument('--suite',choices=('controls-v1','references-v2'),default='controls-v1')
    a=p.parse_args();results=build(a.output.resolve(),a.run,a.suite)
    print(json.dumps({'cases':len(results),'rendered':sum(bool(r.get('audio')) for r in results),'output':str(a.output)},ensure_ascii=False))
