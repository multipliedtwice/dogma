from __future__ import annotations
import contextlib
import gc
import json
import os
import sys
import traceback
from itertools import zip_longest
from pathlib import Path

os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['DO_NOT_TRACK']='1'

from rimsky.util import ERROR_CODES, AppError, AnalysisCapacityError, AnalysisInvalidError, GenerationCapError, atomic_write, inside, reached_generation_cap
from rimsky.cli_process import trusted_executable
from rimsky.analysis import PROMPT_MARGIN, SYSTEM_PROMPT, analysis_request, analysis_schema, known_ids, output_estimate, output_limit
from rimsky.cli_analysis import RECONCILE_SCHEMA, RECONCILE_SYSTEM
from rimsky.staged_analysis import staged_proposal_with_details, unit_answer
from rimsky.speech_backend import backend_for

text_model=None
speech_model=None
voice_prompts={}
loaded_pack=None
audio_verifier=None
verifier_pack=None
DEVICE_UNAVAILABLE='The selected device is not available for speech synthesis. Choose another device in Settings and resume.'
CAPACITY='Scene plus character registry exceeds the local model context. Use a larger-context pack; no text was discarded.'
CRITIC_TOKENS=128
STRUCTURE_TOKENS=64
STRUCTURE_CHUNK_TOKENS=8
STRUCTURE_TARGET_TOKENS=24
CRITIC_PART_TOKENS=96
INSTRUCTIONS={'neutral':'Read naturally and clearly, with restrained expression.','soft':'Speak softly and gently, preserving this speaker identity.','whisper':'Whisper clearly, preserving this speaker identity.','firm':'Speak firmly and with controlled emphasis.','excited':'Speak with excitement, without changing the speaker identity.','sad':'Speak with restrained sadness.','angry':'Speak with controlled anger, without shouting excessively.','questioning':'Speak with a natural questioning intonation.',
              'enumeration':'Read the connected list of items slightly more briskly than surrounding prose, preserving clarity.',
              'stunned':'Speak with a brief, subdued sense of shock and a deliberate pause, preserving this speaker identity.'}


PACES={'pace':{'brisk':'Keep a brisk, lively pace without rushing the words.','deliberate':'Use a slower, deliberate pace with clear phrasing.'},
       'ending_pace':{'deliberate':'Slow down slightly at the end, as if trailing off.'}}


def resolved_analysis_mode(requested):
    if requested != 'auto':
        return requested
    for provider, names, label in (
            ('codex_cli', ('codex.exe', 'codex.cmd', 'codex'), 'Codex CLI'),
            ('claude_cli', ('claude.exe', 'claude.cmd', 'claude'), 'Claude Code CLI')):
        try:
            trusted_executable(names, label=label)
            return provider
        except AppError:
            pass
    return 'local'


def hold_unverified_local_resolutions(records):
    """Keep local model speaker guesses visible without silently accepting them."""
    for record in records:
        if record.get('tier') != 'local':
            continue
        record['status'] = 'review'
        triggers = record.setdefault('triggers', [])
        if 'local_fallback_review' not in triggers:
            triggers.append('local_fallback_review')
    return records

def device_config(preference):
    import torch
    if preference=='auto':
        device='cuda:0' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    else:
        device='cuda:0' if preference=='cuda' else preference
    dtype=torch.bfloat16 if device.startswith('cuda') and torch.cuda.is_bf16_supported() else torch.float16 if device.startswith('cuda') else torch.float32
    return device,dtype

def probe(payload):
    backend = backend_for(payload['pack'])
    if backend is not None:
        return backend.probe(payload)
    pack=payload['pack']
    if not inside(Path(pack['_root']),pack['speech']['path']).is_dir():
        raise AppError('Missing speech model files.')
    import torch
    available={'auto':True,'cpu':True,'cuda':torch.cuda.is_available(),'mps':torch.backends.mps.is_available()}
    if not available.get(payload['device']):
        raise AppError(DEVICE_UNAVAILABLE)
    return {'device':payload['device']}

def release_text():
    global text_model
    if text_model is not None:
        text_model.close()
        text_model=None
    gc.collect()

def load_text(pack,device):
    global text_model
    if text_model is None:
        from llama_cpp import Llama
        text_model=Llama(model_path=str(inside(Path(pack['_root']),pack['text_model']['path'])), n_ctx=int(pack['text_model'].get('context',8192)), n_gpu_layers=0 if device=='cpu' else -1, verbose=False, seed=42)
    return text_model

def reply_json(result):
    content=result['choices'][0]['message']['content']
    try:
        return json.loads(content)
    except (TypeError,ValueError):
        return content

def largest(low,high,fits):
    while low<high:
        middle=(low+high+1)//2
        low,high=(middle,high) if fits(middle) else (low,middle-1)
    return low

def requested_mode(payload):
    return resolved_analysis_mode(payload.get('analysis_mode') or payload['pack']['text_model'].get('analysis_mode','staged'))

def cli_module(mode):
    if mode=='claude_cli':
        from rimsky import claude_analysis
        return claude_analysis
    from rimsky import codex_analysis
    return codex_analysis

def staged_infer(model,context):
    def infer(system, request, schema, max_tokens):
        def run(active_request):
            messages=[{'role':'system','content':system},
                      {'role':'user','content':json.dumps(active_request,ensure_ascii=False)}]
            def complete():
                tokens=len(model.tokenize(''.join(message['role']+message['content'] for message in messages).encode()))
                if tokens > context-max_tokens-256:
                    raise AnalysisCapacityError('A staged analysis request exceeds this pack context. No text or character state was discarded.')
                response=model.create_chat_completion(messages=messages,
                    response_format={'type':'json_object','schema':schema},
                    temperature=0,max_tokens=max_tokens)
                if response['choices'][0].get('finish_reason')=='length':
                    raise AnalysisCapacityError('A staged analysis response reached its output limit.')
                return response['choices'][0]['message']['content']
            raw=complete()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                messages.append({'role':'user','content':json.dumps({
                    'repair':'Return a complete JSON object matching the same schema.',
                    'validation_error':f'Invalid JSON syntax: {exc}',
                    'rejected_response':raw},ensure_ascii=False)})
                try:
                    return json.loads(complete())
                except json.JSONDecodeError as repaired:
                    raise AnalysisInvalidError(
                        'Local character analysis returned invalid JSON after repair.') from repaired
        first=run(request)
        if 'speech' not in request or 'repair' in request:
            return first
        verification={**request,'verification':{
            'instruction':('Independently check the first-pass speaker identity '
                           'against the supplied text and character registry. Keep '
                           'it only when supported; otherwise return the corrected '
                           'identity. Do not choose the person being addressed. If '
                           'the evidence is insufficient, return unknown.'),
            'first_pass_proposal':first}}
        second=run(verification)
        if first.get('speaker') == second.get('speaker'):
            return second
        return {'speaker':'unknown','name':'','evidence_id':'',
                'evidence_quote':''}
    return infer

def repaired(infer,payload):
    if not payload.get('retry_error'):
        return infer
    note={'previous_validation_error':payload['retry_error'],
          **({'previous_rejected_answer':payload['retry_response']} if payload.get('retry_response') is not None else {})}
    def ask(system, request, schema, max_tokens):
        try:
            return infer(system, {**request, **note}, schema, max_tokens)
        except AnalysisCapacityError:
            if 'previous_rejected_answer' not in note:
                raise
            return infer(system, {**request, 'previous_validation_error':note['previous_validation_error'],
                                  'previous_rejected_answer_omitted':True}, schema, max_tokens)
    return ask

def staged_analysis(payload,model):
    tie_breaker=payload.get('tie_breaker','none')
    if tie_breaker not in ('none','codex_cli','claude_cli'):
        raise AppError('Unsupported speaker tie-breaker.')
    tie_infer=None
    if tie_breaker!='none':
        structured_call=cli_module(tie_breaker).structured_call
        def tie_infer(system, request, schema, max_tokens):
            return structured_call(system, request, schema)
    resolution_log=[]
    answer,speech_structure=staged_proposal_with_details(
        payload['segments'], payload['cast'], payload.get('before',[]),
        payload.get('after',[]), repaired(staged_infer(model,int(payload['pack']['text_model'].get('context',8192))),payload),
        payload['pack']['text_model'].get('dialogue_style','auto'),
        tie_breaker_infer=tie_infer, resolution_log=resolution_log,
        narrative_frames=payload.get('narrative_frames'))
    hold_unverified_local_resolutions(resolution_log)
    return {'reply':unit_answer(answer,payload['segments']),'known':known_ids(payload),'speech_structure':speech_structure,
            'speaker_resolution':resolution_log,'staged':True}

def local_call(model,context):
    def call(system, request, schema):
        max_tokens=STRUCTURE_TOKENS+sum(STRUCTURE_TARGET_TOKENS+STRUCTURE_CHUNK_TOKENS*len(case['chunks']) for case in request.get('dash_targets',[]))
        content=json.dumps(request,ensure_ascii=False)
        room=context-len(model.tokenize((system+content).encode()))-PROMPT_MARGIN
        if room<max_tokens:
            raise AnalysisCapacityError('A speech-structure request exceeds this pack context; no text was discarded.')
        result=model.create_chat_completion(messages=[{'role':'system','content':system},{'role':'user','content':content}],response_format={'type':'json_object','schema':schema},temperature=0,max_tokens=max_tokens)
        if result['choices'][0].get('finish_reason')=='length':
            raise AnalysisCapacityError('A speech-structure reply reached its output limit.')
        return reply_json(result)
    return call

def local_structure(payload,model):
    from rimsky.cli_analysis import structure
    return structure(payload,local_call(model,int(payload['pack']['text_model'].get('context',8192))))

def single_pass_analysis(payload,model):
    segments=payload['segments']
    fixed=local_structure(payload,model)
    estimate,limit=output_estimate(segments),output_limit(segments)
    cast=payload['cast']
    def request(size,listed,rejected):
        content=analysis_request(payload if rejected else {**payload,'retry_response':None},cast[:size],listed,fixed)
        if not rejected and payload.get('retry_response') is not None:
            content['previous_rejected_answer_omitted']=True
        raw=json.dumps(content,ensure_ascii=False)
        return raw,int(payload['pack']['text_model'].get('context',8192))-len(model.tokenize((SYSTEM_PROMPT+raw).encode()))-PROMPT_MARGIN
    floor=min(payload.get('required',0),len(cast)) if len(segments)>1 else 0
    size=listed=len(cast)
    raw,room=request(size,listed,False)
    need=limit
    if room<limit:
        least=request(0,floor,False)[1]
        need=limit if len(segments)>1 else min(limit,least)
        if least<max(need,estimate):
            raise AnalysisCapacityError(CAPACITY)
        if room<need:
            listed=largest(floor,len(cast),lambda count:request(0,count,False)[1]>=need)
            size=largest(0,listed,lambda count:request(count,listed,False)[1]>=need)
            raw,room=request(size,listed,False)
    if payload.get('retry_response') is not None:
        fuller=request(size,listed,True)
        if fuller[1]>=need:
            raw,room=fuller
    known=known_ids(payload,listed)
    result=model.create_chat_completion(messages=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':raw}], response_format={'type':'json_object','schema':analysis_schema(segments,payload.get('before',[])+payload.get('after',[]),known)}, temperature=0.1, max_tokens=min(room,limit))
    if result['choices'][0].get('finish_reason')=='length':
        raise AnalysisCapacityError('Character analysis reached its output limit.')
    return {'reply':reply_json(result),'known':known,'speech_structure':fixed,'local_unverified':True}

def analyze(payload):
    mode=requested_mode(payload)
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).analyze(payload)
    model=load_text(payload['pack'],payload['device'])
    if payload['pack']['text_model'].get('analysis_mode','staged')=='staged':
        return staged_analysis(payload,model)
    return single_pass_analysis(payload,model)

def critique(payload):
    mode=requested_mode(payload)
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).critique(payload)
    from rimsky.critic import CRITIC_SYSTEM
    model=load_text(payload['pack'],payload['device'])
    content=json.dumps(payload['request'],ensure_ascii=False)
    room=int(payload['pack']['text_model'].get('context',8192))-len(model.tokenize((CRITIC_SYSTEM+content).encode()))-PROMPT_MARGIN
    limit=CRITIC_TOKENS+CRITIC_PART_TOKENS*sum(len(target['proposal']) for target in payload['request']['targets'])
    if room<limit:
        raise AnalysisCapacityError('Critic request exceeds this pack context; no text was discarded.')
    result=model.create_chat_completion(messages=[{'role':'system','content':CRITIC_SYSTEM},{'role':'user','content':content}],response_format={'type':'json_object','schema':payload['schema']},temperature=0,max_tokens=min(room,limit))
    if result['choices'][0].get('finish_reason')=='length':
        raise AnalysisCapacityError('Critic reply reached its output limit.')
    return reply_json(result)

def reconcile(payload):
    mode=requested_mode(payload)
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).reconcile(payload)
    model=load_text(payload['pack'],payload['device'])
    first,second=(list(payload['evidence'][character['id']].items()) for character in payload['characters'])
    passages=[item for pair in zip_longest(first,second) for item in pair if item]
    def content(count):
        chosen=dict(passages[:count])
        request={'characters':[{**character,'evidence_passages':{pid:text for pid,text in payload['evidence'][character['id']].items() if pid in chosen}} for character in payload['characters']]}
        if payload.get('retry_error'):
            request['previous_validation_error']=payload['retry_error']
        return json.dumps(request,ensure_ascii=False)
    budget=int(payload['pack']['text_model'].get('context',8192))-2000
    count=next((count for count in range(len(passages),1,-1) if len(model.tokenize((RECONCILE_SYSTEM+content(count)).encode()))<=budget),0)
    if not count:
        raise AnalysisCapacityError('Alias comparison exceeds this pack context.')
    result=model.create_chat_completion(messages=[{'role':'system','content':RECONCILE_SYSTEM},{'role':'user','content':content(count)}],response_format={'type':'json_object','schema':RECONCILE_SCHEMA},temperature=0,max_tokens=1800)
    if result['choices'][0].get('finish_reason')=='length':
        raise AnalysisCapacityError('Character reconciliation reached its output limit.')
    return reply_json(result)

def render(payload):
    backend = backend_for(payload['pack'])
    if backend is not None:
        release_text()
        return backend.render(payload)
    return render_qwen(payload)


def render_qwen(payload):
    global speech_model,loaded_pack
    release_text()
    import numpy as np
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    pack=payload['pack']
    if speech_model is None:
        device,dtype=device_config(payload['device'])
        speech_model=Qwen3TTSModel.from_pretrained(str(inside(Path(pack['_root']),pack['speech']['path'])),device_map=device,dtype=dtype,attn_implementation='sdpa',local_files_only=True)
        loaded_pack=pack['_fingerprint']
    if loaded_pack!=pack['_fingerprint']:
        raise AppError('Model revision changed inside a pinned generation.')
    voice=next((v for v in pack['voices'] if v['id']==payload['voice_id']),None)
    if not voice:
        raise AppError('Assigned voice is not in the pinned pack.')
    mode=pack['speech'].get('language_mode','book')
    if mode not in ('book','auto'):
        raise AppError('Unsupported speech language-conditioning mode.')
    kwargs={'text':payload['text'],'language':'Auto' if mode=='auto' else payload['language'],'max_new_tokens':int(pack['speech'].get('max_new_tokens',2048))}
    warning=''
    if pack['speech']['variant']=='custom':
        if voice['speaker'].lower() not in [s.lower() for s in speech_model.get_supported_speakers()]:
            raise AppError('Pack speaker is not supported by the loaded model.')
        kwargs['speaker']=voice['speaker']
        if pack['speech'].get('instruction_control',False):
            kwargs['instruct']=' '.join([INSTRUCTIONS[payload['delivery']],*(PACES[key][value] for key,value in (payload.get('controls') or {}).items())])
        wavs,sr=speech_model.generate_custom_voice(**kwargs)
    else:
        refs=voice['references']
        selected=payload['delivery'] if payload['delivery'] in refs else 'neutral'
        if selected!=payload['delivery']:
            warning='Requested delivery has no reference variant; neutral reference was used.'
        key=(voice['id'],selected)
        ref=refs[selected]
        if key not in voice_prompts:
            voice_prompts[key]=speech_model.create_voice_clone_prompt(ref_audio=str(inside(Path(pack['_root']),ref['audio'])),ref_text=ref['text'],x_vector_only_mode=False)
        wavs,sr=speech_model.generate_voice_clone(voice_clone_prompt=voice_prompts[key],**kwargs)
    audio=np.asarray(wavs[0],dtype=np.float32).reshape(-1)
    duration=len(audio)/sr
    if reached_generation_cap(len(audio),sr,int(pack['speech'].get('max_new_tokens',2048))):
        raise GenerationCapError('Speech may have reached its generation cap; output was not accepted and the text was not clipped.')
    if not np.isfinite(audio).all() or not 0.1<duration<300 or np.max(np.abs(audio))<0.00001:
        raise AppError('Speech output is empty, invalid, silent, or too long.')
    import io
    output=io.BytesIO()
    sf.write(output,audio,sr,format='WAV',subtype='PCM_16')
    atomic_write(Path(payload['output']),output.getvalue())
    return {'duration':duration,'sample_rate':sr,'samples':len(audio),'warning':warning}


def verify_audio(payload):
    global audio_verifier, verifier_pack
    from rimsky.speech_quality import transcript_check
    from rimsky.util import file_digest, digest, SpeechHoldError
    import torch
    from qwen_asr import Qwen3ASRModel
    pack = payload['pack']
    config = pack['speech'].get('verifier')
    if not config or config.get('backend') != 'qwen3_asr':
        raise SpeechHoldError('No independent spoken-word verifier is configured; take remains provisional.')
    root = Path(pack['_root'])
    model = inside(root, config['path'])
    signature = digest({k: v for k, v in pack['files'].items() if k.startswith(config['path'].rstrip('/') + '/')})
    if audio_verifier is None or verifier_pack != signature:
        # CPU keeps the 6 GB GPU available to Higgs. This model hears only audio,
        # with no expected-text prompt or transcript conditioning.
        audio_verifier = Qwen3ASRModel.from_pretrained(str(model), device_map='cpu', dtype=torch.float32,
                                                     attn_implementation='sdpa', local_files_only=True)
        verifier_pack = signature
    audio = Path(payload['audio'])
    before = file_digest(audio)
    language = payload.get('language', 'Russian')
    heard = audio_verifier.transcribe(str(audio), language=None if language == 'Auto' else language)
    if len(heard) != 1 or file_digest(audio) != before:
        raise SpeechHoldError('Audio changed or independent transcription was incomplete.')
    return transcript_check(payload['text'], heard[0].text, before, signature)

def narrative_frame(payload):
    from rimsky.narrative_frames import SYSTEM
    phase=payload.get('phase','primary')
    if phase not in ('primary','critic','adjudication'):raise AppError('Unknown narrative-frame phase.')
    system=SYSTEM+({'primary':'','critic':' Independently check every proposed range against the source and challenge erroneous boundaries or registers.',
        'adjudication':' Resolve disagreements from source evidence. Preserve already checked ranges. This is a distinct tie-break, not a vote.'}[phase])
    request=payload['request']
    if payload.get('retry_error'):
        request={**request,'previous_validation_error':payload['retry_error']}
    mode=resolved_analysis_mode(payload.get('analysis_mode','local'))
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).structured_call(system,request,payload['schema'])
    model=load_text(payload['pack'],payload['device'])
    content=json.dumps(request,ensure_ascii=False)
    room=int(payload['pack']['text_model'].get('context',8192))-len(model.tokenize((system+content).encode()))-PROMPT_MARGIN
    needed=min(4096,300+120*len(payload['request']['targets']))
    if room<needed:raise AnalysisCapacityError('Narrative-frame request exceeds local context; no source was discarded.')
    result=model.create_chat_completion(messages=[{'role':'system','content':system},{'role':'user','content':content}],
        response_format={'type':'json_object','schema':payload['schema']},temperature=0,max_tokens=needed)
    if result['choices'][0].get('finish_reason')=='length':raise AnalysisCapacityError('Narrative-frame response reached its output limit.')
    return reply_json(result)

def performance_direction(payload):
    from rimsky.analysis import provider_schema
    from rimsky.character_direction import SYSTEM as PROFILE_SYSTEM
    from rimsky.scene_direction import SYSTEM as SCENE_SYSTEM
    from rimsky.name_stress_analysis import SYSTEM as NAME_STRESS_SYSTEM
    from rimsky.hard_e import SYSTEM as HARD_E_SYSTEM
    kind=payload.get('kind')
    if kind not in ('profile','scene','name_stress','hard_e'):raise AppError('Unknown performance-direction stage.')
    system={'profile':PROFILE_SYSTEM,'scene':SCENE_SYSTEM,'name_stress':NAME_STRESS_SYSTEM,'hard_e':HARD_E_SYSTEM}[kind]
    phase=payload.get('phase','primary')
    if phase not in ('primary','critic','adjudication'):raise AppError('Unknown direction phase.')
    if phase=='critic':system+=' Independently inspect every target from source; no primary answer is supplied.'
    if phase=='adjudication':system+=' Resolve only disputed targets. Check evidence relevance. Preserve uncertainty where unresolved.'
    request=payload['request']
    if payload.get('retry_error'):request={**request,'previous_validation_error':payload['retry_error']}
    mode=resolved_analysis_mode(payload.get('analysis_mode','auto'))
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).structured_call(system,request,provider_schema(payload['schema']))
    model=load_text(payload['pack'],payload['device'])
    content=json.dumps(request,ensure_ascii=False)
    room=int(payload['pack']['text_model'].get('context',8192))-len(model.tokenize((system+content).encode()))-PROMPT_MARGIN
    count=len(request.get('characters',request.get('turns',request.get('items',[]))))
    needed=min(8192,400+300*count)
    if room<needed:raise AnalysisCapacityError('Performance direction exceeds local context; no source was discarded.')
    result=model.create_chat_completion(messages=[{'role':'system','content':system},{'role':'user','content':content}],
        response_format={'type':'json_object','schema':payload['schema']},temperature=0,max_tokens=needed)
    if result['choices'][0].get('finish_reason')=='length':raise AnalysisCapacityError('Performance direction reached its output limit.')
    return reply_json(result)

def music_direction(payload):
    from rimsky.analysis import provider_schema
    from rimsky.music import DIRECTION_SYSTEM
    if payload.get('phase') not in DIRECTION_SYSTEM:raise AppError('Unknown music-direction phase.')
    system=DIRECTION_SYSTEM[payload['phase']]
    request=payload['request']
    if payload.get('retry_error'):
        request={**request,'previous_validation_error':payload['retry_error']}
    mode=resolved_analysis_mode(payload.get('analysis_mode','local'))
    if mode in ('codex_cli','claude_cli'):
        return cli_module(mode).structured_call(system,request,provider_schema(payload['schema']))
    model=load_text(payload['pack'],payload['device'])
    content=json.dumps(request,ensure_ascii=False)
    room=int(payload['pack']['text_model'].get('context',8192))-len(model.tokenize((system+content).encode()))-PROMPT_MARGIN
    if room<600:raise AnalysisCapacityError('Music-direction request exceeds local context; no source was discarded.')
    result=model.create_chat_completion(messages=[{'role':'system','content':system},{'role':'user','content':content}],
        response_format={'type':'json_object','schema':payload['schema']},temperature=0,max_tokens=600)
    if result['choices'][0].get('finish_reason')=='length':raise AnalysisCapacityError('Music-direction response reached its output limit.')
    return reply_json(result)

def main():
    for handle,errors in ((sys.stdin,'strict'),(sys.stdout,'replace')):
        if hasattr(handle,'reconfigure'):
            handle.reconfigure(encoding='utf-8',errors=errors)
    stream=sys.stdout
    for line in sys.stdin:
        request={}
        try:
            if len(line)>4*1024*1024:
                raise AppError('Worker message too large.')
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                operation=request['method']
                if operation=='analyze':
                    result=analyze(request['data'])
                elif operation=='narrative_frame':
                    result=narrative_frame(request['data'])
                elif operation=='performance_direction':
                    result=performance_direction(request['data'])
                elif operation=='music_direction':
                    result=music_direction(request['data'])
                elif operation=='reconcile':
                    result=reconcile(request['data'])
                elif operation=='critique':
                    result=critique(request['data'])
                elif operation=='render':
                    result=render(request['data'])
                elif operation=='probe':
                    result=probe(request['data'])
                elif operation=='verify_audio':
                    result=verify_audio(request['data'])
                elif operation=='word_recovery':
                    from rimsky.word_recovery import diagnose
                    result=diagnose(Path(request['data']['folder']),request['data']['request'])
                elif operation=='pronounce':
                    from rimsky.auto_pronunciation import auto_pronunciation
                    data=request['data'];spec=data['pronunciation']
                    names={cid:row['stressed'] for cid,row in spec.get('names',{}).items()}
                    result={'texts':[auto_pronunciation(text,names,spec.get('common',[]))[0] for text in data['texts']]}
                elif operation=='release_text':
                    release_text()
                    result=True
                else:
                    raise AppError('Unknown local inference operation.')
            stream.write(json.dumps({'id':request['id'],'result':result},ensure_ascii=False)+'\n')
            stream.flush()
        except Exception as error:
            response = {'id': request.get('id'), 'error': str(error)[:2000] if isinstance(error, AppError) else f'{type(error).__name__}: {str(error)[:2000]}'}
            code = next((code for code, kind in ERROR_CODES.items() if isinstance(error, kind)), None)
            if code:
                response['error_code'] = code
            stream.write(json.dumps(response, ensure_ascii=False)+'\n')
            stream.flush()
    release_text()

if __name__=='__main__':
    main()
