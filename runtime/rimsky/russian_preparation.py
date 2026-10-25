"""Frozen Russian first-take preparation shared by the packaged worker and app.

Source sentences are adjudicated only during preparation, never during synthesis.
Legacy auto-pronunciation-v1 artifacts retain their original interpretation.
"""
from __future__ import annotations
import copy
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from .util import AppError, digest, file_digest, write_json
from . import independent_luna_passes as independent
from . import russian_preparation_providers as providers

POLICY='russian-preparation-v1'
YO_POLICY='yo-all-ambiguous-luna-change-check-v3'
BATCH=24
RUACCENT_REQUIRED_FILES=('nn/nn_omograph/turbo3.1/model.onnx','nn/nn_yo_homograph_resolver/model.onnx',
    'nn/nn_accent/model.onnx','nn/nn_stress_usage_predictor/model.onnx','dictionary/accents.json.gz',
    'dictionary/yo_words.json.gz','dictionary/yo_homographs.json.gz','dictionary/omographs.json.gz',
    'dictionary/rule_engine/accents.json','dictionary/rule_engine/forms.json')


def read(path):return json.loads(Path(path).read_text('utf8'))


def book_binding(book):
    """Only immutable imported source, never playback/UI/generation metadata."""
    return digest({'source_hash':book.get('source_hash'),'chapters':[
        {'id':c['id'],'blocks':[{k:b.get(k)for k in ('id','text','kind','exclude_from_narration')}for b in c['blocks']]}
        for c in book['chapters']]})


def proper_inventory(book):
    """Compact book-wide candidate sets; no tagger or online provider runs here."""
    from .proper_noun_stress import _candidates,_coinages
    from .name_stress import ACUTE,parse
    blocks=[b for c in book['chapters']for b in c['blocks']if b.get('text')]
    texts=[b['text']for b in blocks];capitalized=_candidates(texts,True,acute_transparent=True)
    explicit=[]
    for block in blocks:
        for match in re.finditer('[А-Яа-яЁё́]+',block['text']):
            if ACUTE not in match[0]or match[0].replace(ACUTE,'')not in capitalized:continue
            plain,index=parse(match[0])
            explicit.append({'word':match[0],'plain':plain,'stress':index,'source_id':block['id'],
                'source_start':match.start(),'source_end':match.end(),'source_text_sha256':digest(block['text'].encode('utf8'))})
    return {'capitalized':sorted(capitalized),'coinages':sorted(_coinages([t.replace(ACUTE,'')for t in texts])),
        'explicit_stress':explicit}


def prepare_names(data,*,model_pins=None):
    from .proper_noun_stress import proper_noun_lexicon,RULE_SOURCE
    models=local_model_pins()if model_pins is None else model_pins
    inventory=data['inventory'];names=data['names']
    texts=['. '.join(inventory['capitalized']),' '.join(inventory['coinages'])]
    explicit_evidence=[]
    lexicon=proper_noun_lexicon(texts,names,True,explicit_stress=inventory.get('explicit_stress',[]),source_evidence=explicit_evidence)
    result={'rule':RULE_SOURCE,'book_sha256':data['book_sha256'],'input_sha256':digest({'inventory':inventory,'names':names}),
        'proper_nouns':lexicon,'explicit_source_families':explicit_evidence,'model_pins':models,'module_pins':module_pins()}
    result['sha256']=digest(result);return result


def source_binding(value):
    required=('source_id','source_text','source_start','source_end')
    if not isinstance(value,dict) or any(k not in value for k in required):raise ValueError('Preparation requires an exact source binding')
    start,end=value['source_start'],value['source_end'];text=value['source_text']
    if (not isinstance(value['source_id'],str) or not value['source_id'] or not isinstance(text,str)
        or type(start)is not int or type(end)is not int or not 0<=start<end<=len(text)):
        raise ValueError('Invalid preparation source range')
    return {'source_id':value['source_id'],'source_text_sha256':digest(text.encode('utf8')),'source_start':start,'source_end':end}


def entry_key(text,source):return digest({'text':text,'source':source_binding(source)})


def sound_binding(entry):
    return {k:entry[k] for k in ('policy','book_sha256','source','text_sha256','automatic_text','module_pins','model_pins')}


def validate_entry(entry):
    if entry.get('policy')!=POLICY or entry.get('sha256')!=digest({k:v for k,v in entry.items() if k!='sha256'}):
        raise ValueError('Frozen Russian preparation evidence changed')
    if entry.get('sound_sha256')!=digest(sound_binding(entry)):raise ValueError('Prepared sound identity changed')
    from .spoken_pronunciation import validate_marks
    validate_marks(entry['automatic_text'])
    return entry


def resolve_entry(text,spec):
    source=spec.get('listener_source');key=entry_key(text,source)
    entry=(spec.get('prepared_units')or{}).get(key)
    if entry is None:raise ValueError('Missing frozen Russian preparation for this source part; prepare it before synthesis')
    validate_entry(entry)
    if not spec.get('book_sha256') or entry['book_sha256']!=spec['book_sha256']:
        raise ValueError('Frozen Russian preparation belongs to a different source book')
    if entry['source']!=source_binding(source) or entry['text_sha256']!=digest(text.encode('utf8')):
        raise ValueError('Frozen Russian preparation belongs to a different source occurrence')
    from .respelling import validate_respelling
    validate_respelling(text,entry['automatic_text'])
    return copy.deepcopy(entry)


def module_pins():
    names=('russian_preparation','russian_preparation_providers','independent_luna_passes','auto_pronunciation',
        'homograph_stress','yo_restoration','proper_noun_stress','name_stress','spoken_pronunciation','respelling',
        'cli_process','claude_analysis','codex_analysis')
    return {name:providers.module_digest('rimsky.'+name) for name in names}


def local_model_pins():
    """Only existing local assets; never let RUAccent download a missing model."""
    import importlib.util
    from . import homograph_stress as hs
    if getattr(sys,'frozen',False):
        hs.MODEL_DIR=Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent))/'russian-preparation'/'ruaccent-1.5.8.3'
    package=importlib.util.find_spec('silero_stress')
    silero=Path(package.origin).parent/'data'/'accentor.pt' if package and package.origin else Path()
    root=Path(hs.MODEL_DIR)
    if not silero.is_file() or not root.is_dir():
        raise AppError('Russian preparation requires installed local Silero and RUAccent turbo3.1 models; nothing was downloaded.')
    if any(not(root/name).is_file()for name in RUACCENT_REQUIRED_FILES):
        raise AppError('RUAccent turbo3.1 / YO model assets are missing; preparation is held without downloading.')
    package=importlib.util.find_spec('ruaccent')
    if package is None or not package.origin:raise AppError('RUAccent is not installed; Russian preparation is held.')
    koziev=Path(package.origin).parent/'koziev'
    if not koziev.is_dir():raise AppError('RUAccent bundled rule models are missing; preparation is held without downloading.')
    files={p.relative_to(root).as_posix():file_digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    return {'silero_sha256':file_digest(silero),'ruaccent_files':files,
        'ruaccent_rule_files':{p.relative_to(koziev).as_posix():file_digest(p)for p in sorted(koziev.rglob('*'))if p.is_file()and'__pycache__'not in p.parts},
        'packages':{p:importlib.metadata.version(p) for p in ('silero-stress','ruaccent','wordfreq','pymorphy3','pymorphy3-dicts-ru')}}


def config(provider):
    return {'policy':POLICY,'online_allowed':provider in ('codex_cli','claude_cli'),
        'stress':dict(zip(('provider','model','effort'),providers.STRESS_PIN)),
        'yo':dict(zip(('provider','model','effort'),providers.YO_PIN)),
        'yo_selection':independent.SELECTION,'postprocessing':'unchanged; not enabled by preparation'}


def validate_config(value):
    expected=config('codex_cli' if value.get('online_allowed') is True else 'local')
    if value!=expected:raise ValueError('Russian preparation recipe/provider pins changed')


def _stress(folder,request,call,validate_provider):
    from . import homograph_stress as hs
    answer={'decisions':[]};receipts=[]
    for start in range(0,len(request['items']),15):
        part={'task':request['task'],'items':request['items'][start:start+15]}
        bound={'pin':list(providers.STRESS_PIN),'system':hs.SYSTEM,'request':part,'schema':hs.schema([i['id']for i in part['items']])}
        output=folder/'stress'/digest(bound);done=output/'answer.json';independent.immutable(output/'request.json',bound)
        if done.exists():saved=read(done)
        else:
            for attempt in range(3):
                target=output/f'attempt-{attempt}'
                if (target/'failure.json').exists():continue
                if (target/'start.json').exists():raise ValueError('Unknown unfinished Opus invocation; preparation held for inspection')
                independent.immutable(target/'start.json',{'request_sha256':digest(bound),'attempt':attempt})
                try:raw,metadata=call(providers.STRESS_PIN,hs.SYSTEM,part,bound['schema'],evidence_dir=target/'cli')
                except independent.TRANSPORT_ERRORS as error:
                    independent.immutable(target/'failure.json',{'error':type(error).__name__+': '+str(error)})
                    if attempt==2:raise
                    continue
                # Invalid semantic answers are retained, never retried as new opinions.
                independent.immutable(target/'raw.json',{'answer':raw,'metadata':metadata})
                validate_provider(metadata,providers.STRESS_PIN);_validate_stress_answer(raw,part)
                providers.validate_stress_binding(metadata,bound,target/'cli',raw)
                saved={'answer':raw,'metadata':metadata,'request_sha256':digest(bound),'attempt':attempt,
                    'raw_sha256':file_digest(target/'raw.json')}
                independent.immutable(done,saved);break
            else:raise ValueError('Opus transport retry budget exhausted')
        target=output/f"attempt-{saved['attempt']}"
        if (saved['request_sha256']!=digest(bound) or saved['raw_sha256']!=file_digest(target/'raw.json')
            or read(target/'raw.json')!={'answer':saved['answer'],'metadata':saved['metadata']}):raise ValueError('Frozen Opus answer changed')
        validate_provider(saved['metadata'],providers.STRESS_PIN)
        providers.validate_stress_binding(saved['metadata'],bound,target/'cli',saved['answer']);_validate_stress_answer(saved['answer'],part)
        answer['decisions']+=saved['answer']['decisions'];receipts.append(saved)
    hs.validate(answer,request)
    return answer,receipts


def _validate_stress_answer(answer,request):
    from .homograph_stress import validate
    if (not isinstance(answer,dict)or set(answer)!={'decisions'}or not isinstance(answer['decisions'],list)
        or any(not isinstance(d,dict)or set(d)!={'id','choice','stressed','basis'}or not isinstance(d['basis'],str)
               or not d['basis'].strip()or len(d['basis'])>200 for d in answer['decisions'])):
        raise ValueError('Malformed bounded Opus stress decision')
    validate(answer,request)


def _yo(folder,rows,call,validate_provider):
    from . import yo_restoration as yo
    request=yo.request(rows);by_id={i['id']:row for i,row in zip(request['items'],rows,strict=True)}
    selections=[];receipts=[]
    system=yo.SYSTEM+' Select the FULL exact candidate spelling in choice; return only the supplied decision schema.'
    for start in range(0,len(request['items']),25):
        part={'task':request['task'],'items':request['items'][start:start+25]};items=[]
        for item in part['items']:
            witnesses=by_id[item['id']]['witnesses'];witness=witnesses.get('silero')
            items.append({'id':item['id'],'sentence':item['sentence'],'word':item['word'],
                'candidates':[item['candidate_e'],item['candidate_yo']],
                'baseline':item['candidate_'+witness] if witness in ('e','yo') else None,
                'baseline_stage':'categorical Silero YO witness; unknown is not an agreement','witnesses':witnesses})
        output=folder/'yo'/digest({'items':items,'policy':YO_POLICY,'pin':providers.YO_PIN})
        done=output/'consensus.json'
        kwargs={'policy':YO_POLICY,'system':system,'pin':providers.YO_PIN,
            'validate_provider':lambda metadata:validate_provider(metadata,providers.YO_PIN)}
        if done.exists():
            manifest=read(done);chosen=independent.verify(manifest,output,items,part,**kwargs)
        else:
            manifest=independent.run(items,part,output,call=lambda a,b,c,**kw:call(providers.YO_PIN,a,b,c,**kw),**kwargs)
            chosen=manifest['selected']
        selections+=chosen;receipts.append(manifest)
    chosen={s['id']:s for s in selections};answer={'decisions':[
        {'id':i['id'],'choice':'e' if chosen[i['id']]['choice']==i['candidate_e'] else 'yo',
            'word':chosen[i['id']]['choice'],'basis':chosen[i['id']]['basis'][:200]} for i in request['items']]}
    yo.validate(answer,request)
    return yo.decisions_from(rows,answer),receipts


def prepare(data,*,call=providers.call,validate_provider=providers.validate,model_pins=None):
    """Bounded worker batch, cached on all source/preparation inputs; no TTS."""
    from . import auto_pronunciation as automatic,homograph_stress as hs,yo_restoration as yo
    from .spoken_pronunciation import phrase_stress
    from .respelling import respell_text
    validate_config(data['recipe'])
    units=data['units']
    if not units or len(units)>BATCH:raise ValueError('Preparation requires 1–24 source parts per worker batch')
    for unit in units:source_binding(unit['source'])
    pins=module_pins();models=local_model_pins() if model_pins is None else model_pins
    spec=data['pronunciation'];names={key:row['stressed'] for key,row in spec.get('names',{}).items()}
    if not isinstance(spec.get('proper_nouns'),dict):raise ValueError('A frozen book-wide proper-noun lexicon is required')
    names.update(spec.get('proper_nouns')or{})
    request={'policy':POLICY,'book_sha256':data['book_sha256'],'units':units,
        'pronunciation':{k:spec.get(k)for k in ('names','common','proper_nouns','terms','respelling_sources')},
        'recipe':data['recipe'],'module_pins':pins,'model_pins':models}
    folder=Path(data['cache_dir'])/digest(request);done=folder/'prepared.json'
    independent.immutable(folder/'request.json',request)
    if done.exists():
        saved=read(done)
        if (saved.get('sha256')!=digest({k:v for k,v in saved.items()if k!='sha256'})
            or saved.get('request_sha256')!=digest(request)):raise ValueError('Frozen preparation batch changed')
        for relative,sha in saved['artifacts'].items():
            path=(folder/relative).resolve()
            if not path.is_relative_to(folder.resolve()) or file_digest(path)!=sha:raise ValueError('Frozen preparation provider evidence changed')
        witnesses=read(folder/'witnesses.json')
        _,stress_receipts=_stress(folder,hs.request(witnesses['homograph']),call,validate_provider)
        _,yo_receipts=_yo(folder,witnesses['yo'],call,validate_provider)
        if saved['evidence']!={'homograph':stress_receipts,'yo':yo_receipts}:raise ValueError('Frozen adjudication evidence changed')
        for entry in saved['prepared_units'].values():validate_entry(entry)
        return saved
    try:
        inputs=[];transforms=[]
        for unit in units:
            text,phrase=phrase_stress(unit['text'])
            text,respelling=respell_text(text,names,spec.get('common',[]),spec.get('terms'),True,spec.get('respelling_sources'))
            inputs.append(text);transforms.append({'phrase':phrase,'respelling':respelling})
        hs_rows=hs.disagreements([{'text':t,'names':names,'common':spec.get('common',[]),'implicit_single':True}for t in dict.fromkeys(inputs)])
        yo_rows=[];yo_auto=[]
        for text in dict.fromkeys(inputs):
            normalized,_=yo.normalize_marked_e(text)
            spans,silero,dense=automatic.yo_witnesses(normalized,names,spec.get('common',[]),True)
            result=yo.analyze(normalized,spans,silero,dense,yo.ruaccent_yo(normalized),force_all=True)
            yo_rows+=result['rows'];yo_auto+=result['decisions']
        independent.immutable(folder/'witnesses.json',{'homograph':hs_rows,'yo':yo_rows,'yo_automatic':yo_auto,'proper_nouns':names})
        if (hs_rows or yo_rows) and not data['recipe']['online_allowed']:
            raise AppError('Offline Russian preparation is held: stress/е/ё adjudication needs online providers. No text was sent online.')
        hs_answer,hs_receipts=_stress(folder,hs.request(hs_rows),call,validate_provider)
        yo_decisions,yo_receipts=_yo(folder,yo_rows,call,validate_provider)
        hs_block={'rule':hs.RULE,'answer_sha256':digest(hs_answer),'overrides':hs.overrides(hs_rows,hs_answer)}
        yo_block={'rule':yo.RULE,'answer_sha256':digest(yo_receipts),'decisions':yo_auto+yo_decisions}
        prepared={}
        for unit,text,transform in zip(units,inputs,transforms,strict=True):
            native,evidence=automatic.auto_pronunciation(text,names,spec.get('common',[]),rare_words=True,
                implicit_single=True,homograph=hs_block,yo=yo_block)
            for key in ('homograph_overrides','yo_restoration'):
                if (evidence or{}).get(key,{}).get('unbound'):raise ValueError('Prepared automatic decision was not applied to its exact occurrence')
            entry={'policy':POLICY,'book_sha256':data['book_sha256'],'source':source_binding(unit['source']),
                'text_sha256':digest(unit['text'].encode('utf8')),'automatic_text':native,'module_pins':pins,'model_pins':models,
                'preautomatic':transform,'automatic_evidence':evidence}
            entry['sound_sha256']=digest(sound_binding(entry));entry['sha256']=digest(entry)
            key=entry_key(unit['text'],unit['source'])
            if key in prepared and prepared[key]!=entry:raise ValueError('Conflicting preparation for one source occurrence')
            prepared[key]=entry
        saved={'policy':POLICY,'request_sha256':digest(request),'prepared_units':prepared,
            'evidence':{'homograph':hs_receipts,'yo':yo_receipts},'human_approval':False,
            'artifacts':{p.relative_to(folder).as_posix():file_digest(p)for p in folder.rglob('*')if p.is_file() and p.name not in ('prepared.json','held.json')}}
        saved['sha256']=digest(saved);independent.immutable(done,saved);return saved
    except Exception as error:
        write_json(folder/'held.json',{'policy':POLICY,'status':'held','error':str(error),'human_approval':False})
        raise
