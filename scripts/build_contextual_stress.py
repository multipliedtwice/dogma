"""Frozen Luna-low contextual stress for dictionary homographs, including unmarked/agreeing words.

CPU/text only. No speech generation, recognition or provider fallback. Call prepare(inputs, output)
with final orthographic source/selected text after YO and before phonetic spellings.
"""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from rimsky import contextual_stress as rules,codex_analysis
from rimsky.cli_process import run_cli
from rimsky.util import digest,file_digest
from build_yo_restoration import immutable,read

PIN=('codex_cli','gpt-6-luna','low')
BATCH=20


def validate_provider(metadata):
    if (any(metadata.get(k)!=v for k,v in zip(('provider','model','effort'),PIN))
            or metadata.get('policy')!=rules.RULE or metadata.get('requested_model')!=PIN[1]
            or metadata.get('model_provenance')!='explicit-cli-model-selection' or not metadata.get('cli_version')
            or not Path(metadata.get('executable','')).is_absolute()
            or not re.fullmatch('[a-f0-9]{64}',metadata.get('executable_sha256',''))
            or metadata.get('structured_call_sha256')!=file_digest(Path(codex_analysis.__file__))):
        raise ValueError('Unpinned Luna-low contextual stress CLI provenance')
    if file_digest(Path(metadata['executable']))!=metadata['executable_sha256']:raise ValueError('Context stress CLI changed')


def call(system,req,contract):
    executable=Path(codex_analysis.codex_executable());version=run_cli([str(executable),'--version'],timeout=20)
    if version.returncode or not version.stdout.strip():raise ValueError('Cannot pin context stress CLI version')
    metadata=dict(zip(('provider','model','effort'),PIN))|{'policy':rules.RULE,'requested_model':PIN[1],
        'model_provenance':'explicit-cli-model-selection','cli_version':version.stdout.strip(),
        'executable':str(executable),'executable_sha256':file_digest(executable),
        'structured_call_sha256':file_digest(Path(codex_analysis.__file__))}
    answer=codex_analysis.structured_call(system,req,contract,model=PIN[1],reasoning_effort=PIN[2])
    if Path(codex_analysis.codex_executable()).resolve()!=executable.resolve():raise ValueError('Context stress CLI selection changed')
    validate_provider(metadata);return answer,metadata


def adjudicate(rows,out):
    decisions=[];evidence=[]
    for start in range(0,len(rows),BATCH):
        subset=rows[start:start+BATCH];req=rules.request(subset);contract=rules.schema([r['id'] for r in req['items']])
        bound={'policy':rules.RULE,'provider':PIN[0],'model':PIN[1],'effort':PIN[2],'system':rules.SYSTEM,
            'request':req,'schema':contract,'bindings':subset,'dictionary_sha256':rules.DICTIONARY_SHA256,
            'module_sha256':file_digest(Path(rules.__file__))}
        key=digest(bound);rp=out/(key+'.request.json');ap=out/(key+'.answer.json');immutable(rp,bound)
        if ap.exists():
            saved=read(ap)
            if saved['request_sha256']!=file_digest(rp):raise ValueError('Context stress cache source binding changed')
        else:
            for attempt in range(3):
                attempt_request=out/(key+f'.attempt-{attempt}.request.json')
                attempt_answer=out/(key+f'.attempt-{attempt}.answer.json')
                failure=out/(key+f'.attempt-{attempt}.failure.json')
                existed=attempt_request.exists();immutable(attempt_request,bound)
                if attempt_answer.exists():
                    saved=read(attempt_answer)
                elif failure.exists():
                    if read(failure).get('request_sha256')!=file_digest(attempt_request):raise ValueError('Context stress failure evidence changed')
                    continue
                else:
                    if existed:raise ValueError('Unfinished context stress attempt; inspect prior worker')
                    try:answer,metadata=call(rules.SYSTEM,req,contract)
                    except Exception as error:
                        immutable(failure,{'request_sha256':file_digest(attempt_request),'error':type(error).__name__+': '+str(error)})
                        if attempt==2:raise
                        continue
                    saved={'request_sha256':file_digest(rp),'answer':answer,'metadata':metadata,
                           'attempt_request_sha256':file_digest(attempt_request),'attempt':attempt}
                    immutable(attempt_answer,saved)
                # A substantive/schema failure is held, never repeatedly persuaded.
                validate_provider(saved['metadata']);rules.validate(saved['answer'],req)
                saved={**saved,'attempt_answer_sha256':file_digest(attempt_answer)};immutable(ap,saved);break
            else:raise ValueError('Context stress transport attempts exhausted')
        raw=out/(key+f".attempt-{saved['attempt']}.answer.json")
        if (file_digest(raw)!=saved['attempt_answer_sha256'] or any(read(raw).get(k)!=saved[k] for k in ('answer','metadata'))):
            raise ValueError('Context stress cached answer differs from immutable attempt')
        validate_provider(saved['metadata']);rules.validate(saved['answer'],req)
        decisions.extend(rules.decisions_from(subset,saved['answer']))
        evidence.append({'request':str(rp),'request_sha256':file_digest(rp),'answer':str(ap),'answer_sha256':file_digest(ap),'metadata':saved['metadata']})
    return decisions,evidence


def prepare(inputs,out):
    rows=[];seen={}
    for item in inputs:
        for row in rules.analyze(item['source'],item['selected'],item.get('names'),item.get('common',())):
            key=(row['source_sha256'],row['word'],row['index'])
            if key in seen:
                if seen[key]!=row:raise ValueError('Same source occurrence has conflicting context preparation')
                continue
            seen[key]=row;rows.append(row)
    immutable(out/'inputs.json',{'policy':rules.RULE,'inputs':inputs,'rows':rows,'dictionary_sha256':rules.DICTIONARY_SHA256,
        'module_sha256':file_digest(Path(rules.__file__)),'builder_sha256':file_digest(Path(__file__))})
    decisions,evidence=adjudicate(rows,out/'batches')
    answer={'rule':rules.RULE,'dictionary_sha256':rules.DICTIONARY_SHA256,'decisions':decisions,'batches':evidence,
        'inputs_sha256':file_digest(out/'inputs.json'),'human_approval':False,'acoustic_approval':False}
    immutable(out/'answer.json',answer)
    block={'rule':rules.RULE,'answer_sha256':file_digest(out/'answer.json'),'dictionary_sha256':rules.DICTIONARY_SHA256,'decisions':decisions}
    for item in inputs:rules.apply(item['source'],item['selected'],block,item.get('names'),item.get('common',()))
    return block


def block(path):
    answer=read(path);inputs_path=path.parent/'inputs.json';inputs=read(inputs_path)
    if (answer.get('rule')!=rules.RULE or answer.get('dictionary_sha256')!=rules.DICTIONARY_SHA256
            or answer.get('inputs_sha256')!=file_digest(inputs_path)
            or inputs.get('module_sha256')!=file_digest(Path(rules.__file__))
            or inputs.get('builder_sha256')!=file_digest(Path(__file__))):raise ValueError('Context stress preparation implementation/input changed')
    decoded=[]
    for evidence in answer['batches']:
        rp=Path(evidence['request']);ap=Path(evidence['answer'])
        if (not rp.resolve().is_relative_to(path.parent.resolve()) or not ap.resolve().is_relative_to(path.parent.resolve())
                or file_digest(rp)!=evidence['request_sha256'] or file_digest(ap)!=evidence['answer_sha256']):
            raise ValueError('Context stress batch evidence changed')
        bound=read(rp);saved=read(ap)
        if (saved['request_sha256']!=file_digest(rp) or saved['metadata']!=evidence['metadata']
                or bound['policy']!=rules.RULE or [bound[k] for k in ('provider','model','effort')]!=list(PIN)):
            raise ValueError('Context stress provider/request changed')
        raw=ap.with_name(ap.name.replace('.answer.json',f".attempt-{saved['attempt']}.answer.json"))
        if file_digest(raw)!=saved['attempt_answer_sha256']:raise ValueError('Context stress raw attempt changed')
        validate_provider(saved['metadata']);rules.validate(saved['answer'],bound['request'])
        decoded.extend(rules.decisions_from(bound['bindings'],saved['answer']))
    if decoded!=answer['decisions']:raise ValueError('Context stress decisions differ from actual batch answers')
    return {'rule':rules.RULE,'answer_sha256':file_digest(path),'dictionary_sha256':rules.DICTIONARY_SHA256,'decisions':decoded}


def private_inputs(generation,chapters,homograph_answer,yo_answer):
    """Reuse the exact existing raw-text reconstruction; no phonetic or audio layer."""
    import build_homograph_stress as hs_builder
    from rimsky import homograph_stress as hs,yo_restoration as yo
    from rimsky.auto_pronunciation import auto_pronunciation,policy_options
    if generation.resolve().parent!=hs_builder.FOLDER.resolve():raise ValueError('Private contextual stress generation must be a canonical sibling')
    texts=hs_builder.native_texts([generation.name],chapters,with_specs=True);inputs=[]
    for source,metadata in texts.items():
        spec={**metadata['pronunciation'],'policy':'stress-rare-v4','homograph_stress':hs.block(homograph_answer),'yo_restoration':yo.yo_block(yo_answer)}
        names={cid:row['stressed'] for cid,row in spec['names'].items()};names.update(spec.get('proper_nouns',{}))
        selected,automatic=auto_pronunciation(source,names,spec.get('common',[]),**policy_options('stress-rare-v4',spec))
        if automatic['yo_restoration']['unbound'] or automatic.get('homograph_overrides',{}).get('unbound'):raise ValueError('Unbound upstream pronunciation in context preparation')
        inputs.append({'source':source,'selected':selected,'names':names,'common':spec.get('common',[]),'chapter':metadata['chapter']})
    return inputs


def main():
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--inputs',type=Path);group.add_argument('--generation',type=Path)
    parser.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003']);parser.add_argument('--homograph-answer',type=Path)
    parser.add_argument('--yo-answer',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.generation:
        hp=args.homograph_answer or args.generation/'preparation/homograph/homograph.answer.json'
        yp=args.yo_answer or args.generation/'preparation/yo/yo.answer.json'
        inputs=private_inputs(args.generation.resolve(),args.chapters,hp,yp)
    else:inputs=read(args.inputs)
    prepare(inputs,args.output.resolve())
if __name__=='__main__':main()
