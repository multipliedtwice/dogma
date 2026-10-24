"""V17 development-only source-ellipsis phrase assembly; no production default.

Two separately executed child requests retain original ellipses and approved
pronunciation. Selected child PCM is preserved byte-exact. A parent is honestly
an assembly, never a fictitious native full-source execution.
"""
from __future__ import annotations
import argparse
import copy
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
import context_public_demo_historical_word_recovery as historical
import context_public_demo_line_wrap_recovery as line_wrap
import context_public_audiobook_recovery as context
import pronounce_public_audiobook_word_recovery as words
import recover_public_audiobook_demo as isolated
import render_public_audiobook_demo as renderer
from rimsky import audio_join
from rimsky.integrity import validate_cues
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.speech_quality import transcript_check,word_tokens
from rimsky.util import file_digest

SELF=Path(__file__).resolve()
POLICY='public-demo-source-ellipsis-pcm-assembly-v1'
PROFILES=isolated.PROFILES
BOUNDARY=context.BOUNDARY
ASR_ROOT=context.ASR_ROOT
ALIGN_ROOT=context.ALIGN_ROOT
read=renderer.read
immutable=isolated.immutable
language=historical.language


def dependencies():
    return {**line_wrap.dependencies(),SELF.relative_to(ROOT).as_posix():file_digest(SELF),
        Path(historical.__file__).relative_to(ROOT).as_posix():file_digest(Path(historical.__file__)),
        Path(audio_join.__file__).relative_to(ROOT).as_posix():file_digest(Path(audio_join.__file__))}


def home(folder,unit):return Path(folder)/'ellipsis-phrase-assembly-v1'/unit['id']
def bounded(number):
    if type(number) is not int or number not in (1,2,3):raise ValueError('Three fixed physical/assembly attempts only')
    return number
def child_home(folder,unit,child,attempt):
    if child not in (1,2) or type(child) is not int:raise ValueError('Exactly two source phrases')
    return home(folder,unit)/('phrase-'+str(child))/('attempt-%02d'%bounded(attempt))
def attempt_home(folder,unit,attempt):return home(folder,unit)/('assembly-%02d'%bounded(attempt))
def receipt_path(folder,unit,attempt):return attempt_home(folder,unit,attempt)/'assembly-receipt.json'


def partition(unit,repair):
    historical.validate_repair(unit,repair)
    source=unit['text'];native=repair['synthesis_text']
    # The original punctuation supplies the only split; nothing is deleted.
    if '…' not in source:raise ValueError('No original internal ellipsis')
    boundary=source.index('…')+1
    if not source[boundary:].strip() or not source[:boundary].strip():raise ValueError('Ellipsis is not internal')
    after=boundary
    while after<len(source) and source[after].isspace():after+=1
    gap=source[boundary:after]
    if not gap or any(c not in ' \t' for c in gap):raise ValueError('Source ellipsis gap must be preserved horizontal whitespace')
    if native.count('…')!=source.count('…'):raise ValueError('Approved repair changed deliberate punctuation')
    nb=native.index('…')+1;nr=nb
    while nr<len(native) and native[nr].isspace():nr+=1
    if native[nb:nr]!=gap:raise ValueError('Approved native source boundary whitespace changed')
    intervals=[(0,boundary,0,nb),(after,len(source),nr,len(native))];children=[];word_offset=0
    for number,(a,b,c,d) in enumerate(intervals,1):
        text=source[a:b];spoken=text.strip();synthesis=native[c:d].strip()
        if not spoken or not synthesis:raise ValueError('Empty child phrase')
        count=len(word_tokens(spoken));patches=[]
        for patch in repair['patches']:
            if unit['start']+a<=patch['source_start'] and patch['source_end']<=unit['start']+b:
                row=copy.deepcopy(patch);row['spoken_index']-=word_offset;patches.append(row)
            elif patch['source_start']<unit['start']+b and patch['source_end']>unit['start']+a:
                raise ValueError('Source split intersects a reviewed word')
        projection=repair['source_stress_projection']
        pb=projection.index('…')+1;pr=pb
        while pr<len(projection) and projection[pr].isspace():pr+=1
        child_projection=(projection[:pb] if number==1 else projection[pr:]).strip()
        child_repair={**copy.deepcopy(repair),'patches':patches,'synthesis_text':synthesis,'source_stress_projection':child_projection}
        children.append({'id':'phrase-'+str(number),'child':number,'source_id':unit['source_id'],'start':unit['start']+a,
            'end':unit['start']+b,'text':text,'spoken':spoken,'synthesis_text':synthesis,'source_stress_projection':child_projection,
            'target_word_start':0,'target_word_end':count,'parent_word_start':word_offset,'parent_word_end':word_offset+count,
            'repair':child_repair})
        word_offset+=count
    if children[0]['text']+gap+children[1]['text']!=source or word_offset!=len(word_tokens(unit['spoken'])):
        raise ValueError('Child source reconstruction/word sequence differs')
    if children[0]['synthesis_text']+gap+children[1]['synthesis_text']!=native:
        raise ValueError('Child native reconstruction differs from approved spelling/stress')
    return {'children':children,'gap':{'source_id':unit['source_id'],'start':unit['start']+boundary,'end':unit['start']+after,'text':gap},
        'source_reconstruction_sha256':renderer.sha(source),'native_reconstruction_sha256':renderer.sha(native),
        'original_ellipses_preserved':True,'source_and_captions_unchanged':True}


def negative_context_history(folder,unit):
    rows=[]
    for attempt in (1,2,3):
        _,spec,native=historical.native_receipt(folder,unit,attempt);base=historical.attempt_home(folder,unit,attempt)
        failure=historical.execution_proof(folder,base,spec,native)
        if failure is None:
            decision=historical.blind_decision(folder,unit,attempt,'context')
            if decision['passed']:raise ValueError('Prior full-context pass requires separate failure proof; do not reset its budget')
            failure={'failed_stage':'full-context-recognition','decision':decision}
        files=[{'path':p.relative_to(folder).as_posix(),'sha256':file_digest(p)} for p in sorted(base.rglob('*'))
               if p.is_file() and p.suffix in ('.json','.wav','.log')]
        rows.append({'attempt':attempt,'native_receipt_sha256':file_digest(base/'native-receipt.json'),**failure,'files':files})
    return rows


def join_policy():
    return {'module_sha256':file_digest(Path(audio_join.__file__)),'join_policy':copy.deepcopy(audio_join.JOIN_POLICY),
        'selected_gap_key':'sentence_gap','target_seconds':audio_join.JOIN_POLICY['sentence_gap'],
        'scope':'Explicit experiment pause, not a measured source ellipsis duration','preserve_selected_pcm':True,
        'trim':False,'fade':False,'resample':False,'gain':False,'assembly_budget':3,'native_budget_per_child':3,'native_budget_total':6,
        'final_verification_pairs':3,'child_verification':'Each physical native: one full and one crop Qwen/Turbo pair, immutable checks reused'}


def check_slots(folder,unit):
    for take in (30,31,32):
        p=Path(folder)/'takes'/unit['id']/('take-%02d.json'%take)
        if not p.exists():continue
        value=read(p);marker=value.get('spec',{}).get('ellipsis_phrase_assembly',{})
        if (marker.get('policy')!=POLICY or marker.get('attempt')!=take-29 or value.get('unit')!=unit['id']
                or marker.get('implementation_sha256')!=file_digest(SELF)
                or marker.get('campaign_sha256')!=file_digest(home(folder,unit)/'campaign.json')
                or p.read_bytes()!=receipt_path(folder,unit,take-29).read_bytes()):raise ValueError('Composite canonical slot is occupied or differs from private mirror')


def plan(folder,unit):
    if unit['id']!='u0153':raise ValueError('Only the authorized held development unit')
    old=historical.campaign(folder,unit);check_slots(folder,unit)
    value={'schema':1,'policy':POLICY,'language':'Russian','target':unit,'generation':old['generation'],'original_spec':old['original_spec'],
        'dependencies':dependencies(),'bindings':old['bindings'],'partition':partition(unit,old['word_repair']),
        'word_campaign_sha256':old['word_campaign_sha256'],'word_proposal_sha256':old['word_proposal_sha256'],
        'word_repair':old['word_repair'],'word_stress':old['word_stress'],'normative_evidence':old['normative_evidence'],
        'prior_context_campaign_sha256':file_digest(historical.home(folder,unit)/'campaign.json'),
        'prior_context_failures':negative_context_history(folder,unit),'profiles':PROFILES,'join':join_policy(),
        'qwen_files':old['qwen_files'],'aligner_files':old['aligner_files'],'source_paragraph':old['source_paragraph'],
        'boundary':BOUNDARY,'processing_mode':'development-only','audio_approval':False,'human_approval':False}
    immutable(home(folder,unit)/'campaign.json',value);return value


def campaign(folder,unit):
    value=read(home(folder,unit)/'campaign.json');old=historical.campaign(folder,unit);check_slots(folder,unit)
    if (unit['id']!='u0153' or value.get('policy')!=POLICY or value['target']!=unit or value['generation']!=old['generation']
            or value['dependencies']!=dependencies() or value['bindings']!=old['bindings'] or value['original_spec']!=old['original_spec']
            or value['partition']!=partition(unit,old['word_repair']) or value['join']!=join_policy() or value['profiles']!=PROFILES
            or value['prior_context_campaign_sha256']!=file_digest(historical.home(folder,unit)/'campaign.json')
            or value['prior_context_failures']!=negative_context_history(folder,unit)
            or any(value[k]!=old[k] for k in ('word_campaign_sha256','word_proposal_sha256','word_repair','word_stress','normative_evidence',
                                             'qwen_files','aligner_files','source_paragraph'))
            or value.get('processing_mode')!='development-only' or value.get('audio_approval') is not False):
        raise ValueError('Ellipsis source/approved repair/prior failures/assembly budget changed')
    return value


def native_spec(folder,unit,bound,child,attempt):
    child_home(folder,unit,child,attempt);chosen=bound['partition']['children'][child-1];profile=PROFILES[list(PROFILES)[attempt-1]]
    spec=copy.deepcopy(bound['original_spec']);spec['request'].update({k:v for k,v in profile.items() if k!='seed_offset'})
    spec['request'].update(id=unit['id']+'-ellipsis-phrase-'+str(child)+'-'+str(attempt),text=chosen['synthesis_text'],
        seed=(bound['original_spec']['request']['seed']+profile['seed_offset']+1100000*attempt+7000*child)%2**31,
        max_tokens=min(2048,math.ceil((6+.35*sum(c.isalpha() for c in chosen['synthesis_text']))*25)+8))
    spec['ellipsis_child_recovery']={'policy':POLICY,'child':child,'attempt':attempt,'limit_per_child':3,'total_limit':6,
        'context':chosen,'profile':list(PROFILES)[attempt-1],'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),
        'implementation_sha256':file_digest(SELF),'word_proposal_sha256':bound['word_proposal_sha256']}
    return spec


def native_receipt(folder,unit,child,attempt):
    bound=campaign(folder,unit);spec=native_spec(folder,unit,bound,child,attempt);base=child_home(folder,unit,child,attempt)
    value=read(base/'native-receipt.json')
    if value.get('spec')!=spec or value.get('spec_sha256')!=renderer.sha(spec) or value.get('audio_approval') is not False:
        raise ValueError('Physical child native request changed')
    start=read(base/'physical-native-start.json')
    if start!={'spec_sha256':renderer.sha(spec),'session_sha256':file_digest(base/'native-session.json'),
               'physical_native_calls':1,'child':child,'attempt':attempt}:raise ValueError('Physical native count/request provenance changed')
    historical.execution_proof(folder,base,spec,value);return bound,spec,value


def verification_audio(folder,unit,attempt,kind,child=None):
    if child is None:
        if kind!='parent':raise ValueError('Composite parent requires parent verification kind')
        bound,receipt=assembly_receipt(folder,unit,attempt)
        if receipt.get('quality_passed') is not True:raise ValueError('Composite original final quality remains held')
        return bound,unit['spoken'],receipt['audio'],receipt['audio_sha256']
    bound,spec,native=native_receipt(folder,unit,child,attempt)
    if native.get('quality_passed') is not True:raise ValueError('Child native quality remains held')
    expected=spec['ellipsis_child_recovery']['context']['spoken']
    if kind=='context':return bound,expected,native['audio'],native['audio_sha256']
    if kind!='crop':raise ValueError('Child full/crop verification only')
    value=read(child_home(folder,unit,child,attempt)/'child-crop.json');isolated.relative_checked(folder,value['audio'],value['audio_sha256'])
    return bound,expected,value['audio'],value['audio_sha256']


def verifier_config(bound,recognizer,torch_version,transformers_version):
    return {**words.verifier_config(bound,recognizer,torch_version,transformers_version),'policy':POLICY,'implementation_sha256':file_digest(SELF)}


def check_record(check,expected,checksum,bound,recognizer):
    config=check['verifier']
    if config!=verifier_config(bound,recognizer,config.get('torch'),config.get('transformers')):raise ValueError('Source-blind verifier changed')
    if recognizer=='qwen':
        if words.checked_inventory(ASR_ROOT)!=bound['qwen_files']:raise ValueError('Qwen inventory changed')
    else:
        import witness_public_demo_turbo as turbo
        if words.checked_inventory(turbo.MODEL_PATH)!=turbo.FILES:raise ValueError('Whisper inventory changed')
    raw=transcript_check(expected,check['heard'],checksum,renderer.sha(config))
    if any(check.get(k)!=v for k,v in raw.items()):raise ValueError('Raw audio-only check changed')
    return raw


def blind_decision(folder,unit,attempt,kind,child=None):
    bound,expected,_,checksum=verification_audio(folder,unit,attempt,kind,child);evidence=[]
    base=attempt_home(folder,unit,attempt) if child is None else child_home(folder,unit,child,attempt)
    for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
        path=base/(kind+'-'+recognizer+'.json');check=read(path)
        start=read(base/(kind+'-'+recognizer+'.started.json'))
        if start!={'audio_sha256':checksum,'verifier_sha256':renderer.sha(check['verifier']),'recognizer':recognizer,
                   'kind':kind,'child':child,'attempt':attempt,'physical_recognizer_calls':1}:
            raise ValueError('Physical recognition count/source binding changed')
        evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':file_digest(path),'raw':check_record(check,expected,checksum,bound,recognizer),
                         'verifier':check['verifier'],'role':role,'family':family})
    repair=bound['word_repair'] if child is None else bound['partition']['children'][child-1]['repair']
    target=unit if child is None else {'spoken':expected,'text':bound['partition']['children'][child-1]['text']}
    return words.strict_decision(target,evidence,bound['source_paragraph'],repair)


def render_child(folder, unit, child, attempt):
    bound = campaign(folder, unit)
    spec = native_spec(folder, unit, bound, child, attempt)
    base = child_home(folder, unit, child, attempt)
    if (base/'native-receipt.json').exists():
        native_receipt(folder, unit, child, attempt)
        return
    for prior in range(1,attempt):
        old=child_home(folder,unit,child,prior)
        if (old/'child-crop.json').exists() and all((old/('crop-'+r+'.json')).exists() for r in ('qwen','turbo')):
            try:validate_child(folder,unit,child,prior)
            except (ValueError,KeyError,FileNotFoundError):pass
            else:raise ValueError('Reuse the earlier independently verified child; no redundant native call')
    renderer.wait_for_audio_resources()
    pin = resolve_production_higgs_model(ROOT)
    model = spec['model']
    if (pin.sha256 != model['sha256'] or pin.checkpoint != model['checkpoint'] or pin.provenance != model['provenance']
            or file_digest(pin.provenance_path) != model['provenance_sha256'] or file_digest(pin.manifest_path) != model['manifest_sha256']):
        raise ValueError('Context synthesis production model/provenance changed')
    runner = renderer.checked(spec['runner']['path'], spec['runner']['sha256'])
    if {p.name:file_digest(p) for p in sorted(runner.parent.glob('*.dll'))} != spec['runner']['dlls']:
        raise ValueError('Context native runner DLLs changed')
    renderer.checked(spec['reference']['path'], spec['reference']['audio_sha256'])
    sequence = {'requests': [spec['request']]}
    sequence_path = base/'native-request.json'
    immutable(sequence_path, sequence)
    staging = base/'native'
    staging.mkdir(exist_ok=True)
    source = staging/(spec['request']['id']+'.wav')
    command = [str(runner), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(pin.path),
               '--backend', 'cuda', '--device', '0', '--threads', '4', '--request-sequence', str(sequence_path),
               '--out-dir', str(staging), '--out-format', 'pcm16', '--metrics']
    immutable(base/'native-session.json', {'command': command, 'spec': spec, 'sequence_sha256': file_digest(sequence_path)})
    error = None
    if not source.exists():
        start_record=base/'physical-native-start.json'
        if start_record.exists():raise ValueError('Physical child native already started without a durable result; never replay this slot')
        immutable(start_record,{'spec_sha256':renderer.sha(spec),'session_sha256':file_digest(base/'native-session.json'),
                                'physical_native_calls':1,'child':child,'attempt':attempt})
        try:
            with (base/'native.log').open('ab') as stream:
                subprocess.run(command, cwd=runner.parent, stdout=stream, stderr=subprocess.STDOUT,
                               check=True, timeout=900, **renderer.HIDDEN)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
    receipt = {'spec': spec, 'spec_sha256': renderer.sha(spec), 'quality_passed': False, 'audio_approval': False}
    if source.exists():
        checksum = file_digest(source)
        audio = base/(checksum+'.wav')
        if not audio.exists():
            shutil.copyfile(source, audio)
        receipt.update(audio=audio.relative_to(folder).as_posix(), audio_sha256=checksum)
        try:
            receipt['quality'] = renderer.quality(audio, spec['request']['max_tokens'])
            if receipt['quality']['duration'] > 30:
                raise ValueError('Context exceeds 30 seconds; do not truncate')
            receipt['quality_passed'] = True
        except Exception as exc:
            receipt['quality_error'] = str(exc)
    if error:
        receipt['generation_error'] = error
    immutable(base/'native-receipt.json', receipt)


def verify(folder, unit, attempt, kind, recognizer, child=None):
    bound, expected, relative, checksum = verification_audio(folder, unit, attempt, kind, child)
    path = isolated.relative_checked(folder, relative, checksum)
    base = attempt_home(folder, unit, attempt) if child is None else child_home(folder, unit, child, attempt)
    target = base/(kind+'-'+recognizer+'.json')
    if target.exists():
        check_record(read(target), expected, checksum, bound, recognizer)
        return
    import witness_public_demo_turbo as turbo
    turbo.wait_for_resources()
    import os
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import torch
    import transformers
    torch.set_num_threads(4)
    config = verifier_config(bound, recognizer, torch.__version__, transformers.__version__)
    start_record=base/(kind+'-'+recognizer+'.started.json')
    if start_record.exists():raise ValueError('Blind ASR call already started without a durable result; do not reset its count')
    immutable(start_record,{'audio_sha256':checksum,'verifier_sha256':renderer.sha(config),'recognizer':recognizer,
                            'kind':kind,'child':child,'attempt':attempt,'physical_recognizer_calls':1})
    if recognizer == 'qwen':
        if renderer.inventory(ASR_ROOT) != bound['qwen_files']:
            raise ValueError('Context Qwen verifier files changed')
        from qwen_asr import Qwen3ASRModel
        from asr_batching import last_position_logits
        model = last_position_logits(Qwen3ASRModel.from_pretrained(str(ASR_ROOT), device_map='cuda:0', dtype=torch.bfloat16,
                attn_implementation='sdpa', local_files_only=True, max_inference_batch_size=1, max_new_tokens=512))
        try:
            heard = model.transcribe(str(path), language=config['decoder_language'])[0].text
        finally:
            del model
            torch.cuda.empty_cache()
    else:
        from transformers import WhisperProcessor, WhisperForConditionalGeneration
        import soundfile as sf
        from scipy.signal import resample_poly
        model_root = turbo.verify_model()
        audio, rate = sf.read(path, dtype='float32')
        if len(audio) > 30*rate:
            raise ValueError('Full context/crop exceeds turbo window; do not truncate')
        divisor = math.gcd(rate, 16000)
        mono = resample_poly(audio, 16000//divisor, rate//divisor)
        processor = WhisperProcessor.from_pretrained(str(model_root), local_files_only=True)
        model = WhisperForConditionalGeneration.from_pretrained(str(model_root), local_files_only=True,
                 torch_dtype=torch.float16, attn_implementation='sdpa').to('cuda:0').eval()
        try:
            inputs = {k: v.to('cuda:0', dtype=torch.float16) if v.is_floating_point() else v.to('cuda:0')
                      for k,v in processor(mono, sampling_rate=16000, return_tensors='pt', return_attention_mask=True).items()}
            with torch.inference_mode():
                tokens = model.generate(**inputs, language=config['decoder_language'], task='transcribe', do_sample=False, max_new_tokens=256)
            if tokens.shape[-1] >= 256:
                raise ValueError('Turbo output token cap reached')
            heard = processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()
        finally:
            del model
            torch.cuda.empty_cache()
    renderer.checked(path, checksum)
    check = transcript_check(expected, heard, checksum, renderer.sha(config))
    check.update(verifier=config, human_approval=False, alignment_used_for_recognition=False)
    immutable(target, check)


def alignment_config(bound, torch_version, transformers_version):
    if (torch_version!=importlib.metadata.version('torch') or transformers_version!=importlib.metadata.version('transformers')):
        raise ValueError('Aligner runtime differs from the installed pinned environment')
    distribution = importlib.metadata.distribution('qwen-asr')
    implementation = Path(distribution.locate_file('qwen_asr/inference/qwen3_forced_aligner.py'))
    return {'policy': POLICY, 'implementation_sha256': file_digest(Path(__file__)),
            'model': 'Qwen3-ForcedAligner-0.6B', 'files': bound['aligner_files'],
            'language': language(bound), 'device': 'cuda:0', 'dtype': 'float16', 'attention': 'sdpa', 'threads': 4,
            'torch': torch_version, 'transformers': transformers_version, 'qwen_asr_version': distribution.version,
            'aligner_implementation_sha256': file_digest(implementation), 'local_files_only': True,
            'expected_text_given_to_aligner': True, 'alignment_is_audio_approval': False}


def align_child(folder, unit, child, attempt):
    bound, spec, native = native_receipt(folder, unit, child, attempt)
    if not blind_decision(folder, unit, attempt, 'context', child)['passed']:
        raise ValueError('Full context has unresolved blind recognition disagreements')
    target = child_home(folder, unit, child, attempt)/'alignment.json'
    if target.exists():
        validate_alignment(folder, unit, child, attempt)
        return
    import witness_public_demo_turbo as turbo
    turbo.wait_for_resources()
    import os
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import torch
    import transformers
    from qwen_asr import Qwen3ForcedAligner
    if words.checked_inventory(ALIGN_ROOT) != bound['aligner_files']:
        raise ValueError('Pinned forced-aligner files changed')
    torch.set_num_threads(4)
    config = alignment_config(bound, torch.__version__, transformers.__version__)
    model = Qwen3ForcedAligner.from_pretrained(str(ALIGN_ROOT), device_map='cuda:0', dtype=torch.float16,
                                             attn_implementation='sdpa', local_files_only=True)
    try:
        result = model.align(audio=str(folder/native['audio']), text=spec['ellipsis_child_recovery']['context']['spoken'], language=config['language'])[0]
        raw = [{'text': item.text, 'start': float(item.start_time), 'end': float(item.end_time)} for item in result.items]
    finally:
        del model
        torch.cuda.empty_cache()
    cues = validate_cues(raw, spec['ellipsis_child_recovery']['context']['spoken'], native['quality']['duration'])
    immutable(target, {'audio_sha256': native['audio_sha256'], 'native_receipt_sha256': file_digest(target.parent/'native-receipt.json'),
                       'expected_text_sha256': renderer.sha(spec['ellipsis_child_recovery']['context']['spoken']),
                       'raw_cues': raw, 'cues': cues, 'files': bound['aligner_files'], 'model': 'Qwen3-ForcedAligner-0.6B',
                       'expected_text_given_to_aligner': True, 'alignment_is_audio_approval': False,
                       'implementation_sha256': file_digest(Path(__file__)), 'configuration': config})


def validate_alignment(folder, unit, child, attempt):
    bound, spec, native = native_receipt(folder, unit, child, attempt)
    path = child_home(folder, unit, child, attempt)/'alignment.json'
    value = read(path)
    if words.checked_inventory(ALIGN_ROOT)!=bound['aligner_files']:raise ValueError('Actual aligner inventory changed')
    expected = spec['ellipsis_child_recovery']['context']['spoken']
    cues = validate_cues(value['raw_cues'], expected, native['quality']['duration'])
    config = value.get('configuration', {})
    if (config != alignment_config(bound, config.get('torch'), config.get('transformers'))
            or not all(isinstance(config.get(k), str) and config[k] for k in ('torch', 'transformers'))):
        raise ValueError('Aligner runtime/model/attention/language provenance changed')
    if (value['cues'] != cues or value['audio_sha256'] != native['audio_sha256'] or value['files'] != bound['aligner_files']
            or value['expected_text_sha256'] != renderer.sha(expected) or value.get('alignment_is_audio_approval') is not False
            or value['native_receipt_sha256'] != file_digest(path.parent/'native-receipt.json')
            or value['implementation_sha256'] != file_digest(Path(__file__))):
        raise ValueError('Full-coverage alignment is not bound to native source/audio')
    return cues


def crop_child(folder,unit,child,attempt):
    import soundfile as sf
    import numpy as np
    bound,spec,native=native_receipt(folder,unit,child,attempt)
    if not blind_decision(folder,unit,attempt,'context',child)['passed']:raise ValueError('Child full-native words remain held')
    chosen=spec['ellipsis_child_recovery']['context'];audio,rate=sf.read(folder/native['audio'],dtype='int16')
    bounds=context.crop_bounds(audio,rate,validate_alignment(folder,unit,child,attempt),chosen)
    base=child_home(folder,unit,child,attempt);output=base/'selected-child.wav'
    if not output.exists():sf.write(output,audio[bounds['start_sample']:bounds['end_sample']],rate,subtype='PCM_16')
    existing,existing_rate=sf.read(output,dtype='int16')
    if existing_rate!=rate or not np.array_equal(existing,audio[bounds['start_sample']:bounds['end_sample']]):raise ValueError('Child crop PCM changed')
    value={'schema':1,'role':'source-phrase-PCM-crop','unit':unit['id'],'child':child,'attempt':attempt,
        'native_receipt_sha256':file_digest(base/'native-receipt.json'),'alignment_sha256':file_digest(base/'alignment.json'),
        'audio':output.relative_to(folder).as_posix(),'audio_sha256':file_digest(output),'bounds':bounds,
        'quality':renderer.quality(output),'quality_passed':True,'human_approval':False,'canonical_registration':False}
    immutable(base/'child-crop.json',value);return value


def validate_child(folder,unit,child,attempt):
    import numpy as np
    import soundfile as sf
    bound,spec,native=native_receipt(folder,unit,child,attempt);base=child_home(folder,unit,child,attempt)
    if native.get('quality_passed') is not True:raise ValueError('Original child native quality must pass before any crop')
    full=blind_decision(folder,unit,attempt,'context',child)
    if not full['passed']:raise ValueError('Both full-native child recognizers must pass')
    value=read(base/'child-crop.json');chosen=spec['ellipsis_child_recovery']['context']
    raw,rate=sf.read(isolated.relative_checked(folder,native['audio'],native['audio_sha256']),dtype='int16')
    bounds=context.crop_bounds(raw,rate,validate_alignment(folder,unit,child,attempt),chosen)
    expected={'schema':1,'role':'source-phrase-PCM-crop','unit':unit['id'],'child':child,'attempt':attempt,
        'native_receipt_sha256':file_digest(base/'native-receipt.json'),'alignment_sha256':file_digest(base/'alignment.json'),
        'audio':(base/'selected-child.wav').relative_to(folder).as_posix(),'audio_sha256':value['audio_sha256'],'bounds':bounds,
        'quality':value['quality'],'quality_passed':True,'human_approval':False,'canonical_registration':False}
    path=isolated.relative_checked(folder,value['audio'],value['audio_sha256']);selected,selected_rate=sf.read(path,dtype='int16')
    if value!=expected or selected_rate!=rate or not np.array_equal(selected,raw[bounds['start_sample']:bounds['end_sample']]):
        raise ValueError('Selected child is not exact aligned native PCM')
    if renderer.quality(path)!=value['quality']:raise ValueError('Child crop quality changed')
    cropped=blind_decision(folder,unit,attempt,'crop',child)
    if not cropped['passed']:raise ValueError('Both fresh child-crop recognizers must pass')
    return {'policy':POLICY,'unit':unit['id'],'child':child,'attempt':attempt,'source_span':{k:chosen[k] for k in ('source_id','start','end','text','spoken')},
        'native_spec_sha256':renderer.sha(spec),'native_receipt_sha256':file_digest(base/'native-receipt.json'),
        'native_audio_sha256':native['audio_sha256'],'child_crop_receipt_sha256':file_digest(base/'child-crop.json'),
        'audio':value['audio'],'audio_sha256':value['audio_sha256'],'sample_count':len(selected),'sample_rate':rate,
        'bounds':bounds,'full_native_decision':full,'crop_decision':cropped,'human_approval':False,'alignment_is_approval':False}


def assemble_pcm(children,rate,policy):
    """Only append zero samples; preserve every selected child sample."""
    import numpy as np
    if len(children)!=2 or any(np.asarray(x).dtype!=np.int16 or np.asarray(x).ndim!=1 or not len(x) for x in children):
        raise ValueError('Two nonempty mono PCM16 children required')
    edges=[audio_join.boundary_check(x.astype(np.float64)/32768,rate,policy['join_policy']) for x in children]
    if any(e['unsafe_start'] or e['unsafe_end'] for e in edges):raise ValueError('Active child edge cannot be hidden by assembly')
    tail=round(edges[0]['trailing_padding']*rate);head=round(edges[1]['leading_padding']*rate)
    target=round(rate*policy['target_seconds']);zeros=max(0,target-tail-head)
    output=np.concatenate([children[0],np.zeros(zeros,dtype=np.int16),children[1]])
    details={'target_samples':target,'natural_left_tail_samples':tail,'natural_right_head_samples':head,
        'inserted_zero_samples':zeros,'natural_silence_met_target':tail+head>=target,'sample_rate':rate,
        'child_ranges':[{'child':1,'start_sample':0,'end_sample':len(children[0])},
                        {'child':2,'start_sample':len(children[0])+zeros,'end_sample':len(output)}],
        'pause_policy_sha256':renderer.sha(policy),'all_selected_pcm_preserved':True,'trimmed_samples':0,'fades':False,'resampled':False,'gain_applied':False}
    return output,details


def execution_inventory(folder,unit):
    rows=[]
    for child in (1,2):
        for attempt in (1,2,3):
            base=child_home(folder,unit,child,attempt)
            if not base.exists():continue
            record={'child':child,'attempt':attempt,'physical_native_started':(base/'physical-native-start.json').exists(),
                'native_receipt':file_digest(base/'native-receipt.json') if (base/'native-receipt.json').exists() else None,'checks':{}}
            for kind in ('context','crop'):
                for recognizer in ('qwen','turbo'):
                    check=base/(kind+'-'+recognizer+'.json');start=base/(kind+'-'+recognizer+'.started.json')
                    if start.exists() or check.exists():record['checks'][kind+'-'+recognizer]={'started':file_digest(start) if start.exists() else None,
                        'result':file_digest(check) if check.exists() else None}
            rows.append(record)
    return rows


def assemble(folder,unit,attempt,left_attempt,right_attempt):
    import numpy as np
    import soundfile as sf
    bounded(attempt);bound=campaign(folder,unit);base=attempt_home(folder,unit,attempt)
    if (base/'assembly-receipt.json').exists():
        existing=assembly_receipt(folder,unit,attempt)[1]
        if existing['spec']['ellipsis_phrase_assembly']['child_attempts']!=[left_attempt,right_attempt]:raise ValueError('Do not replace a bounded assembly child selection')
        return existing
    children=[validate_child(folder,unit,n,a) for n,a in ((1,left_attempt),(2,right_attempt))]
    audio=[];rates=[]
    for child in children:
        x,r=sf.read(isolated.relative_checked(folder,child['audio'],child['audio_sha256']),dtype='int16');audio.append(x);rates.append(r)
    if rates[0]!=rates[1]:raise ValueError('Child sample rates differ; never resample')
    assembled,gap=assemble_pcm(audio,rates[0],bound['join'])
    for prior in (1,2,3):
        if prior==attempt:continue
        p=receipt_path(folder,unit,prior)
        if p.exists() and read(p)['spec']['ellipsis_phrase_assembly']['child_attempts']==[left_attempt,right_attempt]:
            raise ValueError('Same child pair cannot reset parent assembly/verification budget')
    base.mkdir(parents=True,exist_ok=True);output=base/'assembled-source.wav'
    if not output.exists():sf.write(output,assembled,rates[0],subtype='PCM_16')
    saved,rate=sf.read(output,dtype='int16')
    if rate!=rates[0] or not np.array_equal(saved,assembled):raise ValueError('Existing assembled PCM differs')
    # No parent native request, seed, or fake native execution receipt exists.
    spec={k:copy.deepcopy(bound['original_spec'][k]) for k in ('source','generation','model','runner','reference','effects')}
    spec['audio_origin']='exact-pcm-composite'
    spec['ellipsis_phrase_assembly']={'policy':POLICY,'attempt':attempt,'limit':3,'child_attempts':[left_attempt,right_attempt],
        'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'implementation_sha256':file_digest(SELF),
        'source_partition_sha256':renderer.sha(bound['partition']),'child_proofs':children,'gap':gap,
        'execution_inventory_at_assembly':execution_inventory(folder,unit),'physical_parent_native_calls':0,
        'native_budget_per_child':3,'native_budget_total':6,'assembly_budget':3,'final_recognizer_pairs_budget':3}
    quality=None;passed=False;error=None
    try:
        quality=renderer.quality(output)
        if quality['duration']>30:raise ValueError('Assembly exceeds recognizer window; never truncate')
        passed=True
    except Exception as exc:error=str(exc)
    value={'schema':1,'unit':unit['id'],'take':attempt+29,'spec':spec,'spec_sha256':renderer.sha(spec),
        'audio':output.relative_to(folder).as_posix(),'audio_sha256':file_digest(output),'quality':quality,'quality_passed':passed,
        'quality_error':error,'canonical_registration':False,'human_approval':False,'audio_origin':'exact-pcm-composite'}
    immutable(base/'assembly-receipt.json',value);return value


def assembly_receipt(folder,unit,attempt):
    import numpy as np
    import soundfile as sf
    bound=campaign(folder,unit);value=read(receipt_path(folder,unit,attempt));spec=value['spec'];marker=spec.get('ellipsis_phrase_assembly',{})
    expected_base={k:bound['original_spec'][k] for k in ('source','generation','model','runner','reference','effects')}
    if (set(spec)!=set(expected_base)|{'audio_origin','ellipsis_phrase_assembly'} or any(spec[k]!=v for k,v in expected_base.items())
            or spec.get('audio_origin')!='exact-pcm-composite' or value.get('audio_origin')!='exact-pcm-composite'
            or value.get('spec_sha256')!=renderer.sha(spec) or marker.get('policy')!=POLICY or marker.get('attempt')!=bounded(attempt)
            or value.get('take')!=attempt+29 or value.get('unit')!=unit['id'] or marker.get('physical_parent_native_calls')!=0
            or marker.get('limit')!=3 or marker.get('assembly_budget')!=3 or marker.get('native_budget_total')!=6
            or marker.get('native_budget_per_child')!=3 or marker.get('final_recognizer_pairs_budget')!=3
            or marker.get('implementation_sha256')!=file_digest(SELF) or marker.get('campaign_sha256')!=file_digest(home(folder,unit)/'campaign.json')
            or marker.get('source_partition_sha256')!=renderer.sha(bound['partition']) or value.get('canonical_registration') is not False
            or value.get('human_approval') is not False):raise ValueError('Composite falsely claims native execution or changed provenance/budgets')
    selected=marker['child_attempts']
    if len(selected)!=2:raise ValueError('Exactly two independently verified child phrases required')
    children=[validate_child(folder,unit,n,a) for n,a in enumerate(selected,1)]
    if marker['child_proofs']!=children:raise ValueError('Selected child execution/ASR/PCM provenance changed')
    # The inventory snapshot is historical: later bounded child work may append,
    # but every pinned earlier file remains byte-exact.
    current={(r['child'],r['attempt']):r for r in execution_inventory(folder,unit)}
    for row in marker['execution_inventory_at_assembly']:
        now=current.get((row['child'],row['attempt']))
        if (now is None or row['physical_native_started'] and not now['physical_native_started']
                or row['native_receipt'] is not None and now['native_receipt']!=row['native_receipt']
                or any(any(digest is not None and now['checks'].get(name,{}).get(key)!=digest for key,digest in checks.items())
                       for name,checks in row['checks'].items())):
            raise ValueError('Bound physical execution/check inventory changed after assembly')
    audio=[];rates=[]
    for child in children:
        x,r=sf.read(isolated.relative_checked(folder,child['audio'],child['audio_sha256']),dtype='int16');audio.append(x);rates.append(r)
    if rates[0]!=rates[1]:raise ValueError('Composite child rate mismatch')
    expected,gap=assemble_pcm(audio,rates[0],bound['join'])
    path=isolated.relative_checked(folder,value['audio'],value['audio_sha256']);actual,rate=sf.read(path,dtype='int16')
    if (value['audio']!=(attempt_home(folder,unit,attempt)/'assembled-source.wav').relative_to(folder).as_posix()
            or rate!=rates[0] or not np.array_equal(actual,expected) or marker['gap']!=gap):raise ValueError('Composite PCM/gap/sample proof differs')
    quality=None;error=None;passed=False
    try:
        quality=renderer.quality(path)
        if quality['duration']>30:raise ValueError('Assembly exceeds recognizer window; never truncate')
        passed=True
    except Exception as exc:error=str(exc)
    if value.get('quality_passed')!=passed or value.get('quality')!=quality or value.get('quality_error')!=error:raise ValueError('Parent original quality cannot be reproduced')
    return bound,value


def validate_ellipsis_assembly_receipt(folder,unit,receipt):
    marker=receipt['spec']['ellipsis_phrase_assembly'];bound,value=assembly_receipt(folder,unit,marker['attempt'])
    if receipt!=value or value.get('quality_passed') is not True:raise ValueError('Composite parent is not quality-valid private receipt')
    decision=blind_decision(folder,unit,marker['attempt'],'parent')
    if not decision['passed']:raise ValueError('Both final full-source parent recognizers must pass; child success is insufficient')
    return {'policy':POLICY,'receipt_sha256':file_digest(receipt_path(folder,unit,marker['attempt'])),
        'audio_origin':'exact-pcm-composite','assembled_audio_sha256':receipt['audio_sha256'],'physical_parent_native_calls':0,
        'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},'source_partition':bound['partition'],
        'campaign_sha256':marker['campaign_sha256'],'child_proofs':marker['child_proofs'],'gap':marker['gap'],
        'decision':decision,'normative_evidence':bound['normative_evidence'],'word_campaign_sha256':bound['word_campaign_sha256'],
        'word_proposal_sha256':bound['word_proposal_sha256'],'word_stress_sha256':renderer.sha(bound['word_stress']),
        'execution_inventory_at_assembly':marker['execution_inventory_at_assembly'],'human_approval':False,'acoustic_stress_approval':False,
        'source_and_captions_unchanged':True,'alignment_is_approval':False,'canonical_registration':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--unit',required=True)
    parser.add_argument('--stage',choices=['plan','render','verify','align','crop','validate-child','assemble','validate'],required=True)
    parser.add_argument('--child',type=int,choices=[1,2]);parser.add_argument('--attempt',type=int,choices=[1,2,3])
    parser.add_argument('--kind',choices=['context','crop','parent'],default='context');parser.add_argument('--recognizer',choices=['qwen','turbo'])
    parser.add_argument('--left-attempt',type=int,choices=[1,2,3]);parser.add_argument('--right-attempt',type=int,choices=[1,2,3]);args=parser.parse_args()
    if args.stage!='plan' and args.attempt is None:parser.error('--attempt required')
    if args.stage in ('render','align','crop','validate-child') and args.child is None:parser.error('--child required')
    if args.stage=='verify' and (args.recognizer is None or (args.kind!='parent' and args.child is None) or (args.kind=='parent' and args.child is not None)):
        parser.error('Verifier and either child full/crop or composite parent required')
    if args.stage=='assemble' and (args.left_attempt is None or args.right_attempt is None):parser.error('Both selected child attempts required')
    folder=args.folder.resolve();assignment,_,_=isolated.frozen_context(folder);unit=next((u for u in assignment['units'] if u['id']==args.unit),None)
    if unit is None:parser.error('Unknown frozen source unit')
    if args.stage=='plan':plan(folder,unit)
    elif args.stage=='render':render_child(folder,unit,args.child,args.attempt)
    elif args.stage=='verify':verify(folder,unit,args.attempt,args.kind,args.recognizer,args.child)
    elif args.stage=='align':align_child(folder,unit,args.child,args.attempt)
    elif args.stage=='crop':crop_child(folder,unit,args.child,args.attempt)
    elif args.stage=='validate-child':
        immutable(child_home(folder,unit,args.child,args.attempt)/'validation.json',validate_child(folder,unit,args.child,args.attempt))
    elif args.stage=='assemble':assemble(folder,unit,args.attempt,args.left_attempt,args.right_attempt)
    else:immutable(attempt_home(folder,unit,args.attempt)/'validation.json',validate_ellipsis_assembly_receipt(folder,unit,read(receipt_path(folder,unit,args.attempt))))


if __name__=='__main__':main()


