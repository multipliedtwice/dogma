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
import independent_luna_passes as independent

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
    if not metadata.get('cli_evidence'):raise ValueError('Missing audited single-execution context CLI evidence')
    if metadata.get('cli_evidence'):
        path=Path(metadata['cli_evidence'])
        if file_digest(path/'manifest.json')!=metadata.get('cli_evidence_sha256'):raise ValueError('Context raw CLI evidence changed')
        proof=read(path/'manifest.json')
        if proof.get('max_attempts')!=1:raise ValueError('Context CLI execution was not single-attempt')
        for relative,checksum in proof['files'].items():
            if not (path/relative).resolve().is_relative_to(path.resolve()) or file_digest(path/relative)!=checksum:raise ValueError('Context raw CLI artifact changed')


def call(system,req,contract,*,evidence_dir=None):
    executable=Path(codex_analysis.codex_executable());version=run_cli([str(executable),'--version'],timeout=20)
    if version.returncode or not version.stdout.strip():raise ValueError('Cannot pin context stress CLI version')
    metadata=dict(zip(('provider','model','effort'),PIN))|{'policy':rules.RULE,'requested_model':PIN[1],
        'model_provenance':'explicit-cli-model-selection','cli_version':version.stdout.strip(),
        'executable':str(executable),'executable_sha256':file_digest(executable),
        'structured_call_sha256':file_digest(Path(codex_analysis.__file__))}
    answer=codex_analysis.structured_call(system,req,contract,model=PIN[1],reasoning_effort=PIN[2],max_attempts=1,evidence_dir=evidence_dir)
    if evidence_dir is not None:metadata.update(cli_evidence=str(evidence_dir),cli_evidence_sha256=file_digest(evidence_dir/'manifest.json'))
    if Path(codex_analysis.codex_executable()).resolve()!=executable.resolve():raise ValueError('Context stress CLI selection changed')
    validate_provider(metadata);return answer,metadata


def selected_decisions(rows,selections):
    items=rules.request(rows)['items'];lookup={s['id']:s for s in selections}
    answer={'decisions':[{'id':i['id'],'word':i['word'],'stressed':lookup[i['id']]['choice'],
        'basis':lookup[i['id']]['basis']} for i in items]}
    rules.validate(answer,{'items':items})
    return [{**decision,'two_luna':lookup[item['id']]} for decision,item in zip(rules.decisions_from(rows,answer),items,strict=True)]


def adjudicate(rows,out):
    decisions=[];evidence=[]
    for start in range(0,len(rows),BATCH):
        subset=rows[start:start+BATCH];items=rules.request(subset)['items']
        key=digest({'rule':rules.RULE,'rows':subset,'pin':PIN,'system':rules.SYSTEM,
            'module_sha256':file_digest(Path(rules.__file__)),'helper_sha256':file_digest(Path(independent.__file__))})
        folder=out/key
        try:manifest=independent.run(items,subset,folder,policy=rules.RULE,system=rules.SYSTEM,pin=PIN,call=call,validate_provider=validate_provider)
        except ValueError:
            immutable(out.parent/'unresolved.json',{'rule':rules.RULE,'status':'invalid_or_missing_independent_vote',
                'bindings':rows,'batch':str(folder),'human_approval':False});raise
        selections=independent.verify(manifest,folder,items,subset,policy=rules.RULE,system=rules.SYSTEM,pin=PIN,validate_provider=validate_provider)
        decisions.extend(selected_decisions(subset,selections))
        evidence.append({'bindings':subset,'consensus':str(folder/'consensus.json'),
                         'consensus_sha256':file_digest(folder/'consensus.json')})
    return decisions,evidence


def prepare(inputs,out):
    rows=[];seen={}
    for item in inputs:
        for row in rules.analyze(item['source'],item['selected'],item.get('names'),item.get('common',())):
            if item.get('upstream_evidence'):row['upstream_evidence']=item['upstream_evidence']
            key=(row['source_sha256'],row['word'],row['index'])
            if key in seen:
                if seen[key]!=row:raise ValueError('Same source occurrence has conflicting context preparation')
                continue
            seen[key]=row;rows.append(row)
    immutable(out/'inputs.json',{'policy':rules.RULE,'inputs':inputs,'rows':rows,'dictionary_sha256':rules.DICTIONARY_SHA256,
        'module_sha256':file_digest(Path(rules.__file__)),'builder_sha256':file_digest(Path(__file__))})
    decisions,evidence=adjudicate(rows,out/'batches')
    answer={'rule':rules.RULE,'selection_policy':independent.SELECTION,'dictionary_sha256':rules.DICTIONARY_SHA256,'decisions':decisions,'batches':evidence,
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
    if answer.get('selection_policy')!=independent.SELECTION:raise ValueError('Context stress requires the source-bound conditional second-check policy')
    expected_rows=[];seen={}
    for item in inputs['inputs']:
        for row in rules.analyze(item['source'],item['selected'],item.get('names'),item.get('common',())):
            if item.get('upstream_evidence'):row['upstream_evidence']=item['upstream_evidence']
            key=(row['source_sha256'],row['word'],row['index'])
            if key not in seen:seen[key]=row;expected_rows.append(row)
            elif seen[key]!=row:raise ValueError('Conflicting contextual input witnesses')
    if expected_rows!=inputs['rows']:raise ValueError('Context source candidate coverage changed')
    decoded=[];covered=[]
    for evidence in answer['batches']:
        cp=Path(evidence['consensus']);subset=evidence['bindings'];items=rules.request(subset)['items']
        if not cp.resolve().is_relative_to(path.parent.resolve()) or file_digest(cp)!=evidence['consensus_sha256']:
            raise ValueError('Context stress independent consensus evidence changed')
        selections=independent.verify(read(cp),cp.parent,items,subset,policy=rules.RULE,system=rules.SYSTEM,pin=PIN,validate_provider=validate_provider)
        decoded.extend(selected_decisions(subset,selections));covered.extend(subset)
    if covered!=inputs['rows']:raise ValueError('Context stress batch coverage differs from expected source occurrences')
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
        inputs.append({'source':source,'selected':selected,'names':names,'common':spec.get('common',[]),'chapter':metadata['chapter'],
            'upstream_evidence':{'stage':'after Silero plus frozen HS/YO and automatic guards','pure_silero_snapshot_available':False,
                                 'automatic_pronunciation':automatic}})
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
