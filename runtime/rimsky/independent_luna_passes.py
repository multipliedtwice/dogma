"""Luna checks each ambiguity; independently recheck every proposed change.

Requests never contain the other answer. Transport retries repeat the same pass;
malformed output is retained and held, not a reason to ask again.
"""
from __future__ import annotations
import json,uuid,subprocess,unicodedata
from pathlib import Path
from rimsky.util import digest,file_digest,write_json,ProviderTransportError,ProviderUnavailableError
from .russian_preparation_providers import module_digest
TRANSPORT_ERRORS=(OSError,subprocess.TimeoutExpired,ProviderTransportError,ProviderUnavailableError)

SELECTION='recheck-every-proposed-change-second-authority-v2'


def read(path):return json.loads(Path(path).read_text('utf8'))


def immutable(path,value):
    if path.exists():
        if read(path)!=value:raise ValueError('Independent-pass evidence changed: '+str(path))
    else:write_json(path,value)


def specification(items,bindings,policy,system,pin,pass_id):
    if pass_id not in ('pass-1','pass-2'):raise ValueError('Exactly two named passes are supported')
    second=pass_id=='pass-2'
    ordered=[{**item,'candidates':list(reversed(item['candidates'])) if second else list(item['candidates'])}
             for item in (reversed(items) if second else items)]
    instructions=('Independently interpret the sentence and select its intended reading. Report confidence as an integer '
        '0 to 100 expressing your own uncertainty, not a calibrated probability. The upstream proposal is advisory.' if not second else
        'Make a fresh independent grammatical and semantic reading. Check negation, case, tense and who acts on whom. '
        'Determine stress for THIS INFLECTED FORM in this context, not the dictionary lemma. Verify that the selected '
        'candidate stress actually corresponds to the case/number/tense used in your explanation. '
        'No earlier answer is provided; select only from the supplied alternatives.')
    request={'task':policy,'pass_id':pass_id,'instruction':instructions,'items':ordered}
    properties={item['id']:{'type':'object','additionalProperties':False,'required':['choice','basis']+([] if second else ['confidence']),
        'properties':{'choice':{'type':'string','enum':item['candidates']},'basis':{'type':'string','minLength':1,'maxLength':300},
                      **({} if second else {'confidence':{'type':'integer','minimum':0,'maximum':100}})}}
        for item in ordered}
    schema={'type':'object','additionalProperties':False,'required':['decisions'],'properties':{'decisions':{
        'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}}}
    return {'policy':policy,'selection_policy':SELECTION,'pass_id':pass_id,'provider':pin[0],'model':pin[1],'effort':pin[2],
            'system':system+' '+instructions,'request':request,'schema':schema,'bindings':bindings,
            'independent_helper_sha256':module_digest(__name__)}


def validate(answer,items,pass_id):
    if not isinstance(answer,dict) or set(answer)!={'decisions'} or not isinstance(answer['decisions'],dict):raise ValueError('Malformed independent-pass schema')
    expected={item['id']:item for item in items}
    if set(answer['decisions'])!=set(expected):raise ValueError('Independent-pass coverage changed')
    for key,decision in answer['decisions'].items():
        if (not isinstance(decision,dict) or set(decision)!=({'choice','basis','confidence'} if pass_id=='pass-1' else {'choice','basis'}) or decision['choice'] not in expected[key]['candidates']
                or not isinstance(decision['basis'],str) or not decision['basis'].strip() or len(decision['basis'])>300):
            raise ValueError('Independent-pass choice is not a supplied exact candidate')
        if pass_id=='pass-1' and (type(decision['confidence']) is not int or not 0<=decision['confidence']<=100):
            raise ValueError('First-opinion confidence must be a self-rated integer 0..100')


def transport_failure(folder,attempt,bound):
    rp=folder/'request.json';start=folder/f'attempt-{attempt}.start.json'
    failure=folder/f'attempt-{attempt}.failure.json';cli=folder/f'attempt-{attempt}.cli'
    failed=read(failure);invocation=read(start);manifest=read(cli/'manifest.json')
    if (failed.get('request_sha256')!=file_digest(rp) or failed.get('pass_id')!=bound['pass_id']
            or any(failed.get(k)!=invocation.get(k) for k in ('invocation_id','request_sha256','pass_id'))
            or not invocation.get('invocation_id') or failed.get('cli_evidence')!=str(cli)
            or file_digest(cli/'manifest.json')!=failed.get('cli_evidence_sha256')
            or manifest.get('max_attempts')!=1 or (folder/f'attempt-{attempt}.answer.json').exists()):
        raise ValueError('Independent transport failure changed')
    for relative,checksum in manifest['files'].items():
        if not (cli/relative).resolve().is_relative_to(cli.resolve()) or file_digest(cli/relative)!=checksum:
            raise ValueError('Independent failure CLI artifact changed')
    request_path=cli/'attempt-0/request.json'
    if request_path.exists():
        request=read(request_path)
        if (any(request.get(k)!=bound[k] for k in ('system','request','schema','model'))
                or request.get('reasoning_effort')!=bound['effort'] or request.get('max_attempts')!=1):
            raise ValueError('Failed CLI request differs from independent pass')
    elif (manifest['files'].keys()!={'transport-before-execution.json'}
            or read(cli/'transport-before-execution.json').get('cli_execution_started') is not False):
        raise ValueError('Transport failure lacks audited execution or pre-execution proof')
    return {'attempt':attempt,'start_sha256':file_digest(start),'failure_sha256':file_digest(failure),
            'cli_evidence_sha256':file_digest(cli/'manifest.json')}


def verify_pass(path,bound,items,validate_provider):
    folder=path.parent;rp=folder/'request.json';saved=read(path)
    if read(rp)!=bound or saved.get('request_sha256')!=file_digest(rp) or saved.get('pass_id')!=bound['pass_id']:
        raise ValueError('Independent-pass source/request identity changed')
    attempt=saved.get('attempt')
    if type(attempt) is not int or not 0<=attempt<3:raise ValueError('Invalid independent attempt budget')
    if saved.get('preceding_transport_failures')!=[transport_failure(folder,n,bound) for n in range(attempt)]:
        raise ValueError('Independent preceding transport history changed')
    permitted={f'attempt-{n}.{suffix}' for n in range(attempt+1) for suffix in
               (('start.json','failure.json','cli') if n<attempt else ('start.json','answer.json','cli'))}
    if any(p.name not in permitted for p in folder.glob('attempt-*')):
        raise ValueError('Orphan or later independent attempt artifacts')
    raw=folder/f"attempt-{saved['attempt']}.answer.json";start=folder/f"attempt-{saved['attempt']}.start.json"
    if (file_digest(raw)!=saved.get('raw_answer_sha256') or file_digest(start)!=saved.get('start_sha256')
            or read(raw)!=saved['raw'] or read(start).get('invocation_id')!=saved.get('invocation_id')
            or read(start).get('request_sha256')!=file_digest(rp) or read(start).get('pass_id')!=bound['pass_id']):
        raise ValueError('Independent-pass invocation/raw evidence changed')
    if (saved['raw'].get('invocation_id')!=saved['invocation_id'] or saved['raw'].get('request_sha256')!=file_digest(rp)
            or saved['raw'].get('pass_id')!=bound['pass_id']):raise ValueError('Duplicate or unbound independent response')
    if Path(saved['raw']['metadata'].get('cli_evidence','')).resolve()!=(folder/f"attempt-{saved['attempt']}.cli").resolve():
        raise ValueError('Independent response does not bind its own audited CLI execution')
    cli=folder/f"attempt-{saved['attempt']}.cli";request=read(cli/'attempt-0/request.json')
    if (any(request.get(k)!=bound[k] for k in ('system','request','schema','model'))
            or request.get('reasoning_effort')!=bound['effort'] or request.get('max_attempts')!=1
            or read(cli/'attempt-0/raw-answer.json')!=saved['raw']['answer']):
        raise ValueError('Audited CLI request/raw output differs from this independent pass')
    validate_provider(saved['raw']['metadata']);validate(saved['raw']['answer'],items,bound['pass_id'])
    return saved


def one_pass(folder,bound,items,call,validate_provider):
    rp=folder/'request.json';done=folder/'answer.json';immutable(rp,bound)
    if done.exists():return verify_pass(done,bound,items,validate_provider)
    for attempt in range(3):
        start=folder/f'attempt-{attempt}.start.json';raw=folder/f'attempt-{attempt}.answer.json';failure=folder/f'attempt-{attempt}.failure.json'
        if raw.exists():value=read(raw)
        elif failure.exists():
            transport_failure(folder,attempt,bound)
            continue
        else:
            if start.exists():raise ValueError('Unknown unfinished independent Luna invocation; inspect old worker')
            invocation={'invocation_id':uuid.uuid4().hex,'request_sha256':file_digest(rp),'pass_id':bound['pass_id']}
            immutable(start,invocation)
            try:answer,metadata=call(bound['system'],bound['request'],bound['schema'],evidence_dir=folder/f'attempt-{attempt}.cli')
            except TRANSPORT_ERRORS as error:
                cli=folder/f'attempt-{attempt}.cli'
                if not (cli/'manifest.json').exists():
                    immutable(cli/'transport-before-execution.json',{'error':type(error).__name__+': '+str(error),'cli_execution_started':False})
                    immutable(cli/'manifest.json',{'max_attempts':1,'files':{'transport-before-execution.json':file_digest(cli/'transport-before-execution.json')}})
                immutable(failure,{**invocation,'error':type(error).__name__+': '+str(error),
                    'cli_evidence':str(cli),'cli_evidence_sha256':file_digest(cli/'manifest.json')})
                if attempt==2:raise
                continue
            value={**invocation,'answer':answer,'metadata':metadata};immutable(raw,value)
        # Never ask again after a syntactically or substantively invalid answer.
        validate_provider(value['metadata']);validate(value['answer'],items,bound['pass_id'])
        saved={'pass_id':bound['pass_id'],'request_sha256':file_digest(rp),'attempt':attempt,
            'invocation_id':value['invocation_id'],'start_sha256':file_digest(start),'raw_answer_sha256':file_digest(raw),'raw':value,
            'preceding_transport_failures':[transport_failure(folder,n,bound) for n in range(attempt)]}
        immutable(done,saved);return verify_pass(done,bound,items,validate_provider)
    raise ValueError('Independent Luna transport attempts exhausted')


def baseline(item):
    value=item.get('baseline')
    normalized=lambda word:unicodedata.normalize('NFC',word)
    matches=[c for c in item['candidates'] if isinstance(value,str) and normalized(c)==normalized(value)]
    return matches[0] if len(matches)==1 else None


def changed_items(items,first):
    return [item for item in items if baseline(item) is None or first['decisions'][item['id']]['choice']!=baseline(item)]


def record(folder,saved):
    return {'pass_id':saved['pass_id'],'request':str(folder/'request.json'),'request_sha256':saved['request_sha256'],
        'answer':str(folder/'answer.json'),'answer_sha256':file_digest(folder/'answer.json'),
        'invocation_id':saved['invocation_id'],'decisions':saved['raw']['answer']['decisions'],'metadata':saved['raw']['metadata']}


def selections(items,records):
    first=records[0];second=records[1] if len(records)==2 else None;selected=[]
    for item in items:
        initial=first['decisions'][item['id']];old=baseline(item)
        changed=old is None or initial['choice']!=old
        applicable=[first,second] if changed else [first]
        votes=[r['decisions'][item['id']] for r in applicable]
        selected.append({'id':item['id'],'choice':votes[-1]['choice'],'basis':votes[-1]['basis'],
            'baseline':old,'route':'proposed-change-rechecked' if changed else 'baseline-kept',
            'first_confidence':initial['confidence'],'confidence_scope':'self-rated, not calibrated; never a routing threshold',
            'agreement':votes[0]['choice']==votes[1]['choice'] if changed else None,
            'selected_pass':'pass-2' if changed else 'pass-1','selection_policy':SELECTION,
            'passes':[{'pass_id':r['pass_id'],**vote,'request_sha256':r['request_sha256'],
                'answer_sha256':r['answer_sha256'],'invocation_id':r['invocation_id'],'metadata':r['metadata']}
                for r,vote in zip(applicable,votes,strict=True)]})
    return selected


def totals(selected):
    return {'occurrences':len(selected),'baseline_kept':sum(s['route']=='baseline-kept' for s in selected),
        'changed':sum(s['route']=='proposed-change-rechecked' for s in selected),
        'second_checked':sum(s['selected_pass']=='pass-2' for s in selected),
        'agreements':sum(s['agreement'] is True for s in selected),
        'disagreements_second_selected':sum(s['agreement'] is False for s in selected)}


def run(items,bindings,out,*,policy,system,pin,call,validate_provider):
    if len({item['id'] for item in items})!=len(items):raise ValueError('Duplicate canonical item IDs')
    records=[]
    try:
        for pass_id in ('pass-1','pass-2'):
            subset=items if pass_id=='pass-1' else changed_items(items,records[0])
            if not subset:
                if (out/'pass-2').exists():raise ValueError('Unexpected second pass for unchanged baseline choices')
                break
            bound=specification(subset,bindings,policy,system,pin,pass_id);folder=out/pass_id
            saved=one_pass(folder,bound,subset,call,validate_provider);records.append(record(folder,saved))
    except Exception as error:
        immutable(out/'unresolved.json',{'policy':policy,'selection_policy':SELECTION,'bindings':bindings,
            'status':'unresolved_invalid_or_missing_vote','passes':records,
            'errors':[{'error':type(error).__name__+': '+str(error)}],'human_approval':False})
        raise ValueError('Valid conditional Luna opinions required; unresolved evidence: '+str(out/'unresolved.json')) from error
    if len(records)==2 and records[0]['invocation_id']==records[1]['invocation_id']:
        raise ValueError('Independent passes reused one invocation/cache')
    selected=selections(items,records)
    manifest={'policy':policy,'selection_policy':SELECTION,'status':'valid_conditional_decisions',
              'passes':records,'selected':selected,'counts':totals(selected)}
    immutable(out/'consensus.json',manifest);return manifest


def verify(manifest,out,items,bindings,*,policy,system,pin,validate_provider):
    records=manifest.get('passes',[])
    if (manifest.get('policy')!=policy or manifest.get('selection_policy')!=SELECTION
            or manifest.get('status')!='valid_conditional_decisions' or not records or records[0].get('pass_id')!='pass-1'):
        raise ValueError('Missing conditional Luna opinions')
    for n,entry in enumerate(records):
        if n>1 or entry.get('pass_id')!=f'pass-{n+1}':raise ValueError('Unexpected independent pass')
        subset=items if n==0 else changed_items(items,records[0])
        if not subset:raise ValueError('Unexpected second pass for unchanged baseline choices')
        folder=out/entry['pass_id'];bound=specification(subset,bindings,policy,system,pin,entry['pass_id'])
        saved=verify_pass(folder/'answer.json',bound,subset,validate_provider)
        if entry!=record(folder,saved):raise ValueError('Independent consensus differs from raw receipts')
    changes=changed_items(items,records[0])
    if (bool(changes)!=(len(records)==2) or (not changes and (out/'pass-2').exists())):
        raise ValueError('Missing required or unexpected second check')
    if len(records)==2 and records[0]['invocation_id']==records[1]['invocation_id']:raise ValueError('Duplicate independent Luna invocation')
    expected=selections(items,records)
    if manifest.get('selected')!=expected:raise ValueError('Conditional selection/disagreement evidence changed')
    if manifest.get('counts')!=totals(expected):raise ValueError('Conditional agreement totals changed')
    return expected
