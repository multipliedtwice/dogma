"""A new primary-evidence review after an immutable lexical rejection.

This namespace never retries the rejected request. It preserves the candidate,
stress decision and three native slots; only genuinely new primary facts may
justify two new independent lexical evaluations, either of which may reject.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import pronounce_public_audiobook_word_recovery as original
from rimsky.production_higgs_model import resolve_production_higgs_model

renderer=original.renderer
isolated=original.isolated
read=original.read
immutable=original.immutable
file_digest=original.file_digest
POLICY='public-demo-word-pronunciation-supplement-v1'
FACT_POLICY='public-demo-additive-primary-word-facts-v1'
MARKER='word_pronunciation_supplement'
SELF=Path(__file__).resolve()


def home(folder,unit):return Path(folder)/'word-pronunciation-supplement-v1'/unit['id']
def attempt_home(folder,unit,attempt):
    if type(attempt) is not int or attempt not in (1,2,3):raise ValueError('Only three total native word attempts are permitted')
    return home(folder,unit)/('attempt-%02d'%attempt)
def receipt_path(folder,unit,attempt):return attempt_home(folder,unit,attempt)/'native-receipt.json'
def dependencies():return {**original.dependencies(),SELF.relative_to(ROOT).as_posix():file_digest(SELF)}


def no_prior_native(folder,unit,allow_supplement=False):
    for attempt in (1,2,3):
        if original.attempt_home(folder,unit,attempt).exists():
            raise ValueError('Supplement requires zero original word native attempts')
        base=attempt_home(folder,unit,attempt)
        if not allow_supplement and base.exists():raise ValueError('New supplement plan requires zero native attempts')
        canonical=Path(folder)/'takes'/unit['id']/('take-%02d.json'%(attempt+14))
        if canonical.exists() and (not allow_supplement or not receipt_path(folder,unit,attempt).is_file()
                                  or canonical.read_bytes()!=receipt_path(folder,unit,attempt).read_bytes()):
            raise ValueError('Canonical word slots already belong to another strategy')


def validate_facts(folder,unit,data,old):
    required={'schema','policy','unit','frozen_generation','source','source_text_sha256','prior_rejection',
              'candidate_repair_sha256','candidate_repair_unchanged','facts','limitations','audio_approval'}
    if (set(data)!=required or data['schema']!=1 or data['policy']!=FACT_POLICY or data['unit']!=unit['id']
            or data['frozen_generation']!=old['generation']
            or data['source']!={k:unit[k] for k in ('id','source_id','start','end','text')}
            or data['source_text_sha256']!=renderer.sha(unit['text']) or data['candidate_repair_sha256']!=renderer.sha(old['repair'])
            or data['candidate_repair_unchanged'] is not True or data['audio_approval'] is not False):
        raise ValueError('Supplement does not bind the exact rejected frozen campaign/request')
    from urllib.parse import urlparse
    import re
    if not isinstance(data['facts'],list) or len(data['facts'])<2:raise ValueError('Materially new primary evidence is required')
    primary=[];seen=set();old_urls={f['url'] for f in old['dictionary']}
    for fact in data['facts']:
        required_fact={'id','url','authority','retrieval_date','retrieval','locator','direct_fact','excerpts','audio_approval'}
        if (set(fact)-{'application_inference'}!=required_fact or fact['id'] in seen
                or any(not isinstance(fact[k],str) or not fact[k].strip() for k in ('id','authority','retrieval_date','locator','direct_fact'))
                or fact['audio_approval'] is not False):raise ValueError('New primary fact metadata is incomplete')
        url=urlparse(fact['url']);retrieval=fact['retrieval'];seen.add(fact['id'])
        if (url.scheme!='https' or url.hostname not in original.PRIMARY_HOSTS|{'feb-web.ru','lib.ru'} or url.username or url.password
                or set(retrieval)!={'method','retrieved_utc','response_bytes','response_sha256'}
                or type(retrieval['response_bytes']) is not int or retrieval['response_bytes']<=0
                or not re.fullmatch('[a-f0-9]{64}',retrieval['response_sha256']) or not retrieval['method'] or not retrieval['retrieved_utc']):
            raise ValueError('New evidence must retain explicit primary retrieval hashes')
        if not isinstance(fact['excerpts'],list) or not fact['excerpts']:raise ValueError('Primary excerpt evidence is missing')
        for excerpt in fact['excerpts']:
            if (set(excerpt)!={'text','utf8_sha256','whitespace'} or not excerpt['text'] or not excerpt['whitespace']
                    or hashlib.sha256(excerpt['text'].encode('utf-8')).hexdigest()!=excerpt['utf8_sha256']):
                raise ValueError('New primary excerpt hash changed')
        if url.hostname in original.PRIMARY_HOSTS and fact['url'] not in old_urls:primary.append(fact)
    if len(primary)<2 or not any(urlparse(f['url']).hostname=='oross.ruslang.ru' for f in primary):
        raise ValueError('At least two new primary sources, including lexical dictionary evidence, are required')
    prior=data['prior_rejection']
    if (set(prior)!={'campaign','proposal_request','provider_request','provider_answer','failure','retained_byte_exact','rejection_remains_in_force'}
            or prior['retained_byte_exact'] is not True or prior['rejection_remains_in_force'] is not True):
        raise ValueError('Old rejection must remain immutable and in force')
    for key in ('campaign','proposal_request','provider_request','provider_answer','failure'):
        row=prior[key]
        if set(row)!={'path','sha256'}:raise ValueError('Prior rejection file binding is incomplete')
        isolated.relative_checked(folder,row['path'],row['sha256'])
    if (prior['campaign']['path']!=(original.home(folder,unit)/'campaign.json').relative_to(folder).as_posix()
            or prior['proposal_request']['path']!=(original.home(folder,unit)/'proposal-request.json').relative_to(folder).as_posix()):
        raise ValueError('Prior campaign/request namespace changed')
    rejection=prior['provider_answer']
    rejected=isolated.relative_checked(folder,rejection['path'],rejection['sha256'])
    old_home=original.home(folder,unit)
    if rejected.parent!=old_home or not rejected.name.endswith('-attempt-1.answer.json'):
        raise ValueError('Only an actual first substantive rejection may start a supplementary review')
    saved=read(rejected);metadata=saved['metadata'];provider=next((p for p in isolated.PROVIDERS if p[0]==metadata.get('provider')),None)
    request=rejected.with_name(rejected.name.replace('.answer.json','.request.json'))
    if (provider is None or saved['request_sha256']!=file_digest(request)
            or prior['provider_request']['path']!=request.relative_to(folder).as_posix()
            or read(request)!={**original.proposal_request(old),'provider':provider[0],'model':provider[1],'effort':provider[2]}
            or any(metadata.get(k)!=v for k,v in [('model_used',provider[1]),('effort',provider[2]),('fallback',False)])
            or not str(metadata.get('version','')).strip() or file_digest(Path(metadata['cli']))!=metadata['cli_sha256']
            or saved.get('audio_approval') is not False or saved.get('answer',{}).get('accepted') is not False
            or not str(saved['answer'].get('reason','')).strip()):
        raise ValueError('Old substantive rejection or exact original provider request changed')
    try:original.validate_answer(saved['answer'],old)
    except original.ProposalRejected:pass
    else:raise ValueError('Prior review was not a substantive rejection')
    failure=read(isolated.relative_checked(folder,prior['failure']['path'],prior['failure']['sha256']))
    if (prior['failure']['path']!=rejected.with_name(rejected.name.replace('.answer.json','.failure.json')).relative_to(folder).as_posix()
            or failure.get('kind')!='candidate_rejected' or failure.get('request_sha256')!=saved['request_sha256']
            or failure.get('answer_sha256')!=rejection['sha256'] or failure.get('audio_approval') is not False):
        raise ValueError('Old recorded rejection failure changed')
    return saved


def plan(folder,unit,supplement_path):
    folder=Path(folder);old=original.campaign(folder,unit);data=read(supplement_path)
    if (home(folder,unit)/'campaign.json').exists():
        bound,_=campaign(folder,unit)
        if bound['supplement']!=data:raise ValueError('Supplement plan changed; never replace an existing review')
        return bound
    rejection=validate_facts(folder,unit,data,old);no_prior_native(folder,unit)
    stress=original.validated_stress(folder,old)
    value={'schema':1,'policy':POLICY,'language':'Russian','generation':old['generation'],'target':unit,
           'old_campaign':(original.home(folder,unit)/'campaign.json').relative_to(folder).as_posix(),
           'old_campaign_sha256':file_digest(original.home(folder,unit)/'campaign.json'),
           'supplement':data,'supplement_sha256':renderer.sha(data),'dependencies':dependencies(),
           'repair':old['repair'],'stress':stress,'max_takes':3,'providers':isolated.PROVIDERS,'audio_approval':False}
    immutable(home(folder,unit)/'campaign.json',value)
    print('SUPPLEMENT PLAN',unit['id'],'new primary facts',len(data['facts']),flush=True);return value


def campaign(folder,unit):
    value=read(home(folder,unit)/'campaign.json');old=original.campaign(folder,unit)
    if (value.get('policy')!=POLICY or value['target']!=unit or value['generation']!=old['generation']
            or value['dependencies']!=dependencies() or value['repair']!=old['repair']
            or value['old_campaign']!=(original.home(folder,unit)/'campaign.json').relative_to(folder).as_posix()
            or value['old_campaign_sha256']!=file_digest(original.home(folder,unit)/'campaign.json')
            or value['supplement_sha256']!=renderer.sha(value['supplement']) or value['max_takes']!=3
            or value['providers']!=[list(p) for p in isolated.PROVIDERS] or value['audio_approval'] is not False
            or value['stress']!=original.validated_stress(folder,old)):
        raise ValueError('Supplement source/candidate/stress/code/provider bindings changed')
    validate_facts(folder,unit,value['supplement'],old);no_prior_native(folder,unit,True)
    return value,old


def proposal_request(bound,old):
    request=copy.deepcopy(original.proposal_request(old));request['policy']=POLICY
    request['request'].update(dictionary=[*old['dictionary'],*bound['supplement']['facts']],
         supplementary_review={'policy':POLICY,'old_request_sha256':bound['supplement']['prior_rejection']['proposal_request']['sha256'],
          'old_rejection_sha256':bound['supplement']['prior_rejection']['provider_answer']['sha256'],
          'prior_rejection_reason':read_rejection_reason(bound),
          'material_new_primary_evidence':bound['supplement']['facts'],
          'instruction':'This is a new evidence set, not a request to reverse a rejection. Independently accept OR reject. The candidate and stress are unchanged; no audio approval.'})
    return request


def read_rejection_reason(bound):
    # The retained campaign includes only the source-safe reason in the request;
    # the caller fills it from the hash-verified answer before freezing it.
    return bound['rejection_reason']


def prepared_bound(folder,unit):
    bound,old=campaign(folder,unit)
    prior=bound['supplement']['prior_rejection']['provider_answer']
    rejected=read(isolated.relative_checked(folder,prior['path'],prior['sha256']))
    return {**bound,'rejection_reason':rejected['answer']['reason']},old


def propose(folder,unit):
    bound,old=prepared_bound(folder,unit);base=home(folder,unit);request=proposal_request(bound,old)
    immutable(base/'proposal-request.json',request)
    if (base/'proposal.json').exists():return validated_proposal(folder,unit,bound,old)
    rows=[]
    for provider,model,effort in isolated.PROVIDERS:
        prefix=base/(provider+'-attempt-1');rp=prefix.with_suffix('.request.json');ap=prefix.with_suffix('.answer.json')
        immutable(rp,{**request,'provider':provider,'model':model,'effort':effort})
        transports=[]
        for number in (1,2):
            tp=base/(provider+'-transport-'+str(number)+'.json')
            if tp.exists():
                transport=read(tp)
            elif ap.exists():
                # A response is written before its success journal. A process
                # interrupted between those writes may recover the journal,
                # without asking the provider again.
                transport={'request_sha256':file_digest(rp),'provider':provider,'model':model,'effort':effort,'number':number,
                           'status':'answered','answer_sha256':file_digest(ap)}
                immutable(tp,transport)
            else:
                try:
                    saved=original.call_provider(provider,model,effort,request,rp);immutable(ap,saved)
                    transport={'request_sha256':file_digest(rp),'provider':provider,'model':model,'effort':effort,'number':number,
                               'status':'answered','answer_sha256':file_digest(ap)}
                except Exception as error:
                    transport={'request_sha256':file_digest(rp),'provider':provider,'model':model,'effort':effort,'number':number,
                               'status':'failed','transient':original.transient_failure(error),'error_type':type(error).__name__,'error':str(error)}
                immutable(tp,transport)
            transports.append({'path':tp.relative_to(folder).as_posix(),'sha256':file_digest(tp)})
            if transport['status']=='answered':break
            if not transport.get('transient'):raise ValueError('Supplement provider failed permanently; immutable failure retained')
        if not ap.exists():raise OSError('Two bounded same-request transport attempts exhausted; evidence retained')
        saved=read(ap)
        # Rejections persist in the new namespace, without persuasion retries.
        try:original.validate_answer(saved['answer'],old)
        except (original.ProposalRejected,ValueError) as error:
            immutable(prefix.with_suffix('.failure.json'),{'kind':'candidate_rejected' if isinstance(error,original.ProposalRejected) else 'invalid_answer',
                'request_sha256':file_digest(rp),'answer_sha256':file_digest(ap),'error':str(error),'audio_approval':False})
            raise
        rows.append({'provider':provider,'request':rp.relative_to(folder).as_posix(),'request_sha256':file_digest(rp),
                     'answer':ap.relative_to(folder).as_posix(),'answer_sha256':file_digest(ap),'transports':transports})
    result={'policy':POLICY,'campaign_sha256':file_digest(base/'campaign.json'),'request_sha256':file_digest(base/'proposal-request.json'),
            'providers':rows,'synthesis_text':bound['repair']['synthesis_text'],'audio_approval':False}
    immutable(base/'proposal.json',result);return validated_proposal(folder,unit,bound,old)


def validated_proposal(folder,unit,bound,old):
    base=home(folder,unit);proposal=read(base/'proposal.json');request=proposal_request(bound,old)
    if (proposal.get('policy')!=POLICY or proposal['campaign_sha256']!=file_digest(base/'campaign.json')
            or proposal['request_sha256']!=file_digest(base/'proposal-request.json') or read(base/'proposal-request.json')!=request
            or proposal['synthesis_text']!=old['repair']['synthesis_text'] or proposal.get('audio_approval') is not False
            or len(proposal['providers'])!=2):raise ValueError('New supplementary proposal binding changed')
    for row,(provider,model,effort) in zip(proposal['providers'],isolated.PROVIDERS):
        rp=base/(provider+'-attempt-1.request.json');ap=base/(provider+'-attempt-1.answer.json')
        if (row.get('provider')!=provider or row['request']!=rp.relative_to(folder).as_posix() or row['answer']!=ap.relative_to(folder).as_posix()
                or row['request_sha256']!=file_digest(rp) or row['answer_sha256']!=file_digest(ap)
                or read(rp)!={**request,'provider':provider,'model':model,'effort':effort}):
            raise ValueError('Only two independent first-attempt reviews of the new evidence are permitted')
        transports=row.get('transports',[])
        if not 1<=len(transports)<=2:raise ValueError('Unbounded supplementary provider transport attempts')
        for number,transport_row in enumerate(transports,1):
            tp=base/(provider+'-transport-'+str(number)+'.json')
            if transport_row['path']!=tp.relative_to(folder).as_posix():raise ValueError('Provider transport namespace changed')
            transport=read(isolated.relative_checked(folder,transport_row['path'],transport_row['sha256']))
            if (any(transport.get(k)!=v for k,v in [('request_sha256',row['request_sha256']),('provider',provider),('model',model),('effort',effort),('number',number)])
                    or number==len(transports) and (transport.get('status')!='answered' or transport.get('answer_sha256')!=row['answer_sha256'])
                    or number<len(transports) and (transport.get('status')!='failed' or transport.get('transient') is not True)):
                raise ValueError('Supplement transport retries changed request/provider or retried a substantive decision')
        saved=read(ap);metadata=saved['metadata']
        if (saved['request_sha256']!=row['request_sha256'] or saved.get('audio_approval') is not False
                or any(metadata.get(k)!=v for k,v in [('provider',provider),('model_used',model),('effort',effort),('fallback',False)])
                or not str(metadata.get('version','')).strip() or file_digest(Path(metadata['cli']))!=metadata['cli_sha256']
                or any(not name.startswith(model) for name in metadata.get('model_usage',{}))):
            raise ValueError('Supplement reviewer model/executable/request provenance changed')
        original.validate_answer(saved['answer'],old)
    return proposal


def native_spec(folder,unit,bound,old,proposal,attempt):
    attempt_home(folder,unit,attempt);profile_name=tuple(original.PROFILES)[attempt-1];profile=original.PROFILES[profile_name]
    spec=copy.deepcopy(old['original_spec']);spec['request'].update({k:v for k,v in profile.items() if k!='seed_offset'})
    spec['request'].update(id=unit['id']+'-word-supplement-'+str(attempt),text=old['repair']['synthesis_text'],
                          seed=(old['original_spec']['request']['seed']+profile['seed_offset']+300000*attempt)%2**31)
    spec[MARKER]={'policy':POLICY,'attempt':attempt,'limit':3,'profile':profile_name,'repair':old['repair'],
                 'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'proposal_sha256':file_digest(home(folder,unit)/'proposal.json'),
                 'implementation_sha256':file_digest(SELF),'old_rejection_sha256':bound['supplement']['prior_rejection']['provider_answer']['sha256'],
                 'supplement_sha256':bound['supplement_sha256']}
    return spec


def native_command(base,spec):
    return [str(Path(spec['runner']['path'])),'--task','tts','--family','higgs_audio_tts','--model',str(Path(spec['model']['path'])),
            '--backend','cuda','--device','0','--threads','4','--request-sequence',str(base/'native-request.json'),
            '--out-dir',str(base/'native'),'--out-format','pcm16','--metrics']


def native_receipt(folder,unit,attempt):
    bound,old=prepared_bound(folder,unit);proposal=validated_proposal(folder,unit,bound,old)
    spec=native_spec(folder,unit,bound,old,proposal,attempt);base=attempt_home(folder,unit,attempt);receipt=read(receipt_path(folder,unit,attempt))
    if (receipt.get('unit')!=unit['id'] or receipt.get('take')!=attempt+14 or receipt['spec']!=spec
            or receipt['spec_sha256']!=renderer.sha(spec) or read(base/'native-request.json')!={'requests':[spec['request']]}
            or read(base/'native-session.json')!={'command':native_command(base,spec),'spec':spec,'sequence_sha256':file_digest(base/'native-request.json')}):
        raise ValueError('Supplement native source/request/session changed')
    if receipt.get('quality_passed'):
        if (receipt.get('schema')!=1 or receipt.get('audio_approval') is not False or receipt.get('generation_error')
                or not (base/'native.log').is_file() or not (base/'native.log').stat().st_size):raise ValueError('Missing successful native execution evidence')
        audio=isolated.relative_checked(folder,receipt['audio'],receipt['audio_sha256'])
        if (receipt['audio']!=(base/(receipt['audio_sha256']+'.wav')).relative_to(folder).as_posix()
                or file_digest(base/'native'/(spec['request']['id']+'.wav'))!=receipt['audio_sha256']
                or renderer.quality(audio,spec['request']['max_tokens'])!=receipt['quality'] or receipt['quality']['duration']>30):
            raise ValueError('Supplement audio/native quality binding changed')
    return bound,old,spec,receipt


def render(folder,unit,attempt):
    bound,old=prepared_bound(folder,unit);proposal=validated_proposal(folder,unit,bound,old)
    spec=native_spec(folder,unit,bound,old,proposal,attempt);base=attempt_home(folder,unit,attempt)
    if receipt_path(folder,unit,attempt).exists():native_receipt(folder,unit,attempt);return
    renderer.wait_for_audio_resources();pin=resolve_production_higgs_model(ROOT)
    model=spec['model']
    if (pin.sha256!=model['sha256'] or pin.checkpoint!=model['checkpoint'] or pin.provenance!=model['provenance']
            or file_digest(pin.provenance_path)!=model['provenance_sha256'] or file_digest(pin.manifest_path)!=model['manifest_sha256']):
        raise ValueError('Production Higgs provenance changed')
    runner=renderer.checked(spec['runner']['path'],spec['runner']['sha256'])
    if {p.name:file_digest(p) for p in sorted(runner.parent.glob('*.dll'))}!=spec['runner']['dlls']:raise ValueError('Native runner dependencies changed')
    renderer.checked(spec['reference']['path'],spec['reference']['audio_sha256'])
    immutable(base/'native-request.json',{'requests':[spec['request']]});(base/'native').mkdir(exist_ok=True)
    command=native_command(base,spec);immutable(base/'native-session.json',{'command':command,'spec':spec,'sequence_sha256':file_digest(base/'native-request.json')})
    source=base/'native'/(spec['request']['id']+'.wav');error=None
    if not source.exists():
        try:
            with (base/'native.log').open('ab') as stream:subprocess.run(command,cwd=runner.parent,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=900,**renderer.HIDDEN)
        except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:error=str(exc)
    receipt={'schema':1,'unit':unit['id'],'take':attempt+14,'spec':spec,'spec_sha256':renderer.sha(spec),'quality_passed':False,'audio_approval':False}
    if source.exists():
        checksum=file_digest(source);audio=base/(checksum+'.wav')
        if not audio.exists():shutil.copyfile(source,audio)
        receipt.update(audio=audio.relative_to(folder).as_posix(),audio_sha256=checksum)
        try:
            receipt['quality']=renderer.quality(audio,spec['request']['max_tokens'])
            if receipt['quality']['duration']>30:raise ValueError('Supplement exceeds 30 seconds; do not truncate')
            receipt['quality_passed']=True
        except Exception as exc:receipt['quality_error']=str(exc)
    if error:receipt.update(generation_error=error,quality_passed=False)
    immutable(receipt_path(folder,unit,attempt),receipt)
    if not receipt['quality_passed']:raise ValueError('Supplement native quality remains held')


def verification_audio(folder,unit,attempt,kind='native'):
    if kind!='native':raise ValueError('Only the entire original source unit is verified')
    bound,old,_,receipt=native_receipt(folder,unit,attempt)
    if receipt.get('quality_passed') is not True:raise ValueError('Supplement native quality remains held')
    return bound,old,unit['spoken'],receipt['audio'],receipt['audio_sha256']


def verifier_config(bound,old,recognizer,torch_version,transformers_version):
    return {**original.verifier_config(old,recognizer,torch_version,transformers_version),
            'supplement_policy':POLICY,'supplement_implementation_sha256':file_digest(SELF),
            'supplement_sha256':bound['supplement_sha256']}


def check_record(check,expected,checksum,bound,old,recognizer):
    config=check['verifier']
    installed_torch=importlib.metadata.version('torch');installed_transformers=importlib.metadata.version('transformers')
    if (config.get('torch')!=installed_torch or config.get('transformers')!=installed_transformers
            or config!=verifier_config(bound,old,recognizer,installed_torch,installed_transformers)):
        raise ValueError('Supplement source-blind recognizer configuration changed')
    if original.checked_inventory(original.ASR_ROOT)!=old['qwen_files']:raise ValueError('Qwen snapshot changed')
    import witness_public_demo_turbo as turbo
    if recognizer=='turbo' and original.checked_inventory(turbo.MODEL_PATH)!=turbo.FILES:raise ValueError('Whisper snapshot changed')
    raw=original.transcript_check(expected,check['heard'],checksum,renderer.sha(config))
    if any(check.get(k)!=v for k,v in raw.items()):raise ValueError('Supplement raw source comparison changed')
    return raw


def verify(folder,unit,attempt,recognizer):
    bound,old,expected,relative,checksum=verification_audio(folder,unit,attempt);path=isolated.relative_checked(folder,relative,checksum)
    target=attempt_home(folder,unit,attempt)/('native-'+recognizer+'.json')
    if target.exists():check_record(read(target),expected,checksum,bound,old,recognizer);return
    import witness_public_demo_turbo as turbo
    turbo.wait_for_resources();os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    import torch
    import transformers
    torch.set_num_threads(4);config=verifier_config(bound,old,recognizer,torch.__version__,transformers.__version__)
    if recognizer=='qwen':
        if original.checked_inventory(original.ASR_ROOT)!=old['qwen_files']:raise ValueError('Qwen snapshot changed')
        from qwen_asr import Qwen3ASRModel
        from asr_batching import last_position_logits
        model=last_position_logits(Qwen3ASRModel.from_pretrained(str(original.ASR_ROOT),device_map='cuda:0',dtype=torch.bfloat16,
            attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=1,max_new_tokens=512))
        try:heard=model.transcribe(str(path),language='Russian')[0].text
        finally:del model;torch.cuda.empty_cache()
    else:
        from transformers import WhisperProcessor,WhisperForConditionalGeneration
        import soundfile as sf
        from scipy.signal import resample_poly
        audio,rate=sf.read(path,dtype='float32')
        if len(audio)>30*rate:raise ValueError('Audio exceeds verifier window; never truncate')
        divisor=math.gcd(rate,16000);mono=resample_poly(audio,16000//divisor,rate//divisor);root=turbo.verify_model()
        processor=WhisperProcessor.from_pretrained(str(root),local_files_only=True)
        model=WhisperForConditionalGeneration.from_pretrained(str(root),local_files_only=True,torch_dtype=torch.float16,attn_implementation='sdpa').to('cuda:0').eval()
        try:
            inputs={k:v.to('cuda:0',dtype=torch.float16) if v.is_floating_point() else v.to('cuda:0') for k,v in processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).items()}
            with torch.inference_mode():tokens=model.generate(**inputs,language='ru',task='transcribe',do_sample=False,max_new_tokens=256)
            if tokens.shape[-1]>=256:raise ValueError('Verifier token cap reached')
            heard=processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
        finally:del model;torch.cuda.empty_cache()
    renderer.checked(path,checksum);check=original.transcript_check(expected,heard,checksum,renderer.sha(config))
    check.update(verifier=config,human_approval=False,alignment_used_for_recognition=False);immutable(target,check)


def validate_supplement_receipt(folder,unit,receipt):
    marker=receipt.get('spec',{}).get(MARKER,{});attempt=marker.get('attempt')
    bound,old,spec,native=native_receipt(folder,unit,attempt)
    if receipt!=native or receipt.get('quality_passed') is not True:raise ValueError('Supplement receipt is not its immutable quality-passed native result')
    evidence=[]
    for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
        path=attempt_home(folder,unit,attempt)/('native-'+recognizer+'.json');check=read(path)
        evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':file_digest(path),'role':role,'family':family,
                         'verifier':check['verifier'],'raw':check_record(check,unit['spoken'],native['audio_sha256'],bound,old,recognizer)})
    decision=original.strict_decision(unit,evidence,old['source_paragraph'],old['repair'])
    if decision.get('passed') is not True:raise ValueError('BOTH original-source blind checks must pass unchanged strict word rules')
    proposal=validated_proposal(folder,unit,bound,old)
    providers=[{k:read(isolated.relative_checked(folder,r['answer'],r['answer_sha256']))['metadata'][k] for k in ('provider','model_used','effort')}|
               {'request_sha256':r['request_sha256'],'answer_sha256':r['answer_sha256'],'audio_approval':False} for r in proposal['providers']]
    return {'policy':POLICY,'receipt_sha256':file_digest(receipt_path(folder,unit,attempt)),'native_audio_sha256':native['audio_sha256'],
            'target_decision':decision,'source_spans':[{k:unit[k] for k in ('id','source_id','start','end','text')}],
            'pronunciation_provenance':{'repair':old['repair'],'stress':bound['stress'],'dictionary':old['dictionary'],
               'supplementary_primary_facts':bound['supplement']['facts'],
               'old_campaign_sha256':bound['old_campaign_sha256'],'old_request_sha256':bound['supplement']['prior_rejection']['proposal_request']['sha256'],
               'old_substantive_rejection_sha256':bound['supplement']['prior_rejection']['provider_answer']['sha256'],'supplement_sha256':bound['supplement_sha256'],
               'providers':providers,'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'proposal_sha256':file_digest(home(folder,unit)/'proposal.json'),
               'native_text_sha256':renderer.sha(spec['request']['text']),'original_text':unit['text'],'original_spoken':unit['spoken'],
               'original_text_unchanged':True,'original_spoken_unchanged':True,'captions_unchanged':True,'acoustic_stress_approval':False,
               'implementation_sha256':file_digest(SELF)},'human_approval':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--unit',required=True)
    parser.add_argument('--stage',choices=['plan','propose','render','verify','validate'],required=True);parser.add_argument('--supplement-data',type=Path)
    parser.add_argument('--attempt',type=int,choices=[1,2,3]);parser.add_argument('--recognizer',choices=['qwen','turbo'])
    args=parser.parse_args();folder=args.folder.resolve();assignment,_,_=isolated.frozen_context(folder)
    unit=next((u for u in assignment['units'] if u['id']==args.unit),None)
    if unit is None:parser.error('Unknown frozen unit')
    if args.stage=='plan':
        if args.supplement_data is None:parser.error('--supplement-data required')
        plan(folder,unit,args.supplement_data)
    elif args.stage=='propose':propose(folder,unit)
    else:
        if args.attempt is None:parser.error('--attempt required')
        if args.stage=='render':render(folder,unit,args.attempt)
        elif args.stage=='verify':
            if args.recognizer is None:parser.error('--recognizer required')
            verify(folder,unit,args.attempt,args.recognizer)
        else:immutable(attempt_home(folder,unit,args.attempt)/'validation.json',validate_supplement_receipt(folder,unit,read(receipt_path(folder,unit,args.attempt))))


if __name__=='__main__':main()
