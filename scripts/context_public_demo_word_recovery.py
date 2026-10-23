"""Bounded exact-source context around an already approved word stress repair.

No CLI proposal is added: adjacent source windows and decoding profiles are
closed deterministic choices. Full-context and target-crop recognition are
independent gates; alignment provides coordinates only. All stages are explicit.
"""
from __future__ import annotations
import argparse
import copy
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_audiobook_recovery as context
import pronounce_public_audiobook_word_recovery as words
import render_public_audiobook_demo as renderer
import recover_public_audiobook_demo as isolated
from rimsky.integrity import validate_cues
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.speech_quality import transcript_check,word_tokens
from rimsky.util import file_digest

SELF=Path(__file__).resolve()
POLICY='public-demo-approved-word-context-crop-v1'
PROFILES=isolated.PROFILES
BOUNDARY=context.BOUNDARY
ASR_ROOT=context.ASR_ROOT
ALIGN_ROOT=context.ALIGN_ROOT
LANGUAGES={'Russian':'ru'}
read=renderer.read
immutable=isolated.immutable
crop_bounds=context.crop_bounds


def dependencies():
    return {**words.dependencies(),**context.dependencies(),SELF.relative_to(ROOT).as_posix():file_digest(SELF)}


def language(bound):
    if bound.get('language')!='Russian':raise ValueError('Approved word-context recovery requires Russian')
    return 'Russian'


def home(folder,unit):return Path(folder)/'word-context-recovery-v1'/unit['id']


def attempt_home(folder,unit,attempt):
    if type(attempt) is not int or attempt not in (1,2,3):raise ValueError('Word-context recovery is bounded to three attempts')
    return home(folder,unit)/('attempt-%02d'%attempt)


def receipt_path(folder,unit,attempt):return attempt_home(folder,unit,attempt)/'crop-receipt.json'


def command_for(base,spec):
    return [str(Path(spec['runner']['path'])),'--task','tts','--family','higgs_audio_tts','--model',str(Path(spec['model']['path'])),
            '--backend','cuda','--device','0','--threads','4','--request-sequence',str(base/'native-request.json'),
            '--out-dir',str(base/'native'),'--out-format','pcm16','--metrics']


def execution_proof(folder,base,spec,native):
    sequence=read(base/'native-request.json');session=read(base/'native-session.json');command=command_for(base,spec)
    if (sequence!={'requests':[spec['request']]} or session!={'command':command,'spec':spec,
            'sequence_sha256':file_digest(base/'native-request.json')} or not (base/'native.log').is_file()
            or not (base/'native.log').stat().st_size):
        raise ValueError('Actual native execution/request provenance is missing or changed')
    output=base/'native'/(spec['request']['id']+'.wav')
    if native.get('audio'):
        audio=isolated.relative_checked(folder,native['audio'],native['audio_sha256'])
        renderer.checked(output,native['audio_sha256'])
        actual=None
        try:
            actual=renderer.quality(audio,spec['request']['max_tokens'])
            if actual['duration']>30:raise ValueError('Context exceeds 30 seconds; do not truncate')
        except Exception as error:
            if (native.get('quality_passed') is not False or native.get('quality_error')!=str(error)
                    or actual is not None and native.get('quality')!=actual):
                raise ValueError('Negative native quality cannot be reproduced') from error
            return {'failed_stage':'native-quality','quality_error':str(error),'audio_sha256':native['audio_sha256']}
        if native.get('quality_passed') is not True or actual!=native.get('quality') or native.get('generation_error'):
            raise ValueError('Native quality label or successful execution cannot be reproduced')
        return None
    error=native.get('generation_error')
    import re
    match=re.fullmatch(r"Command '.+' returned non-zero exit status (-?\d+)\.",error or '',re.DOTALL)
    if (match is None or int(match[1])==0 or output.exists()
            or error!=str(subprocess.CalledProcessError(int(match[1]),command)) or native.get('quality_passed') is not False):
        raise ValueError('Missing native output lacks exact failed-command evidence')
    return {'failed_stage':'native-generation','generation_error':error,'exit_code':int(match[1]),'audio_sha256':None}


def failed_word_attempts(folder,unit):
    rows=[]
    for attempt in (1,2,3):
        bound,spec,native=words.native_receipt(folder,unit,attempt)
        base=words.attempt_home(folder,unit,attempt)
        failure=execution_proof(folder,base,spec,native)
        if failure is None:
            decision=words.blind_decision(folder,unit,attempt)
            if decision['passed']:raise ValueError('An approved word take already passes; context repair is forbidden')
            failure={'failed_stage':'native-recognition','decision':decision}
        files=[{'path':p.relative_to(folder).as_posix(),'sha256':file_digest(p)} for p in sorted(base.rglob('*'))
               if p.is_file() and p.suffix in ('.json','.wav','.log')]
        rows.append({'attempt':attempt,'native_receipt_sha256':file_digest(base/'native-receipt.json'),
                     **failure,'files':files,'audio_approval':False})
    return rows


def candidates(assignment,blocks,unit,repair):
    if any(p['kind']!='stress-only' for p in repair['patches']):
        raise ValueError('Combined context currently permits only previously approved stress additions')
    native=unit.get('synthesis_text',unit['spoken'])
    if (words.plain(repair['synthesis_text'])!=words.plain(native)
            or not set(words.stress_anchors(native))<=set(words.stress_anchors(repair['synthesis_text']))):
        raise ValueError('Combined context cannot change original native letters or existing stress')
    by_id={u['id']:u for u in assignment['units']};index=next(i for i,u in enumerate(assignment['units']) if u['id']==unit['id'])
    windows=context.context_candidates(assignment,blocks,unit)
    wanted=[]
    for offsets in ((-1,0,1),(-1,0),(0,1)):
        indices=[index+offset for offset in offsets]
        if any(i<0 or i>=len(assignment['units']) for i in indices):continue
        ids=[assignment['units'][i]['id'] for i in indices]
        matches=[c for c in windows if [s['id'] for s in c['source_spans']]==ids]
        if not matches:continue
        value=copy.deepcopy(matches[0]);value.pop('id')
        if sum(span['id']==unit['id'] for span in value['source_spans'])!=1:raise ValueError('Context must contain target exactly once')
        parts=[]
        for span in value['source_spans']:
            frozen=by_id[span['id']]
            if span['text']!=frozen['text']:raise ValueError('Context lost its exact original source span')
            # Neighboring proposals are deliberately not consulted or applied.
            parts.append(repair['synthesis_text'] if span['id']==unit['id'] else frozen.get('synthesis_text',frozen['spoken']))
        value['synthesis_text']=' '.join(parts)
        value['source_stress_projection']=value['synthesis_text']
        value['id']='word-context-'+renderer.sha(value)[:16]
        wanted.append(value)
    if not wanted:raise ValueError('No compact complete source-adjacent context fits the verification window')
    return wanted


def plan(folder,unit):
    import resolve_public_demo_recognition as resolver
    source=resolver.Context(folder);language(source.assignment)
    bound=words.campaign(folder,unit);proposal=words.validated_proposal(folder,unit,bound)
    stress=words.validated_stress(folder,bound);failed=failed_word_attempts(folder,unit)
    windows=candidates(source.assignment,source.blocks,unit,bound['repair'])
    profiles=list(PROFILES)
    choices=[{'context_id':windows[i%len(windows)]['id'],'profile':profiles[i]} for i in range(3)]
    value={'schema':1,'policy':POLICY,'language':'Russian','generation':source.generation,'target':unit,
           'original_spec':bound['original_spec'],'dependencies':dependencies(),'bindings':source.bindings,
           'word_campaign_sha256':file_digest(words.home(folder,unit)/'campaign.json'),
           'word_proposal_sha256':file_digest(words.home(folder,unit)/'proposal.json'),
           'word_stress':stress,'word_repair':bound['repair'],'word_failures':failed,
           'candidates':windows,'attempts':choices,'max_takes':3,'profiles':PROFILES,'boundary':BOUNDARY,
           'qwen_files':words.checked_inventory(ASR_ROOT),'aligner_files':words.checked_inventory(ALIGN_ROOT),
           'neighbor_policy':'Exact frozen source and native stress; no neighboring recovery proposals applied',
           'source_paragraph':source.blocks[unit['source_id']],'audio_approval':False}
    immutable(home(folder,unit)/'campaign.json',value)
    print('WORD CONTEXT PLAN',unit['id'],choices,flush=True)
    return value


def campaign(folder,unit):
    value=read(home(folder,unit)/'campaign.json');assignment,manifest,generation=isolated.frozen_context(folder)
    word_bound=words.campaign(folder,unit);words.validated_proposal(folder,unit,word_bound)
    if (value.get('policy')!=POLICY or language(value)!=language(assignment) or value['target']!=unit
            or value['generation']!=generation or value['dependencies']!=dependencies() or value['max_takes']!=3
            or value['profiles']!=PROFILES or value['boundary']!=BOUNDARY or value['audio_approval'] is not False
            or value['original_spec']!=word_bound['original_spec'] or value['word_repair']!=word_bound['repair']
            or value['word_campaign_sha256']!=file_digest(words.home(folder,unit)/'campaign.json')
            or value['word_proposal_sha256']!=file_digest(words.home(folder,unit)/'proposal.json')
            or value['word_stress']!=words.validated_stress(folder,word_bound)
            or value['word_failures']!=failed_word_attempts(folder,unit)):
        raise ValueError('Combined context word/source/failed-take/proposal binding changed')
    for name,checksum in value['bindings'].items():isolated.relative_checked(folder,name,checksum)
    import freeze_public_audiobook_demo as preparation
    book=preparation.verify_assignment_source(folder,assignment)
    blocks={b['id']:b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')}
    expected=candidates(assignment,blocks,unit,word_bound['repair'])
    choices=[{'context_id':expected[i%len(expected)]['id'],'profile':list(PROFILES)[i]} for i in range(3)]
    if (value['candidates']!=expected or value['attempts']!=choices or value['source_paragraph']!=blocks[unit['source_id']]):
        raise ValueError('Combined context contains changed or nonadjacent source material')
    return value


def native_spec(folder,unit,bound,attempt):
    attempt_home(folder,unit,attempt);choice=bound['attempts'][attempt-1]
    chosen=next(c for c in bound['candidates'] if c['id']==choice['context_id']);profile=PROFILES[choice['profile']]
    spec=copy.deepcopy(bound['original_spec']);spec['request'].update({k:v for k,v in profile.items() if k!='seed_offset'})
    spec['request'].update(id=unit['id']+'-word-context-'+str(attempt),text=chosen['synthesis_text'],
       seed=(bound['original_spec']['request']['seed']+profile['seed_offset']+400000*attempt)%2**31,
       max_tokens=min(2048,math.ceil((6+.35*sum(c.isalpha() for c in chosen['synthesis_text']))*25)+8))
    spec['word_context_recovery']={'policy':POLICY,'attempt':attempt,'limit':3,'context':chosen,'profile':choice['profile'],
         'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'implementation_sha256':file_digest(SELF),
         'word_proposal_sha256':bound['word_proposal_sha256'],'word_repair_sha256':renderer.sha(bound['word_repair'])}
    return spec


def native_receipt(folder,unit,attempt):
    bound=campaign(folder,unit);spec=native_spec(folder,unit,bound,attempt)
    native=read(attempt_home(folder,unit,attempt)/'native-receipt.json')
    if native['spec']!=spec or native['spec_sha256']!=renderer.sha(spec) or native.get('audio_approval') is not False:
        raise ValueError('Combined context actual native input changed')
    execution_proof(folder,attempt_home(folder,unit,attempt),spec,native)
    return bound,spec,native


def verification_audio(folder,unit,attempt,kind):
    bound,spec,native=native_receipt(folder,unit,attempt)
    if native.get('quality_passed') is not True:raise ValueError('Full context native audio is held')
    if kind=='context':return bound,spec['word_context_recovery']['context']['spoken'],native['audio'],native['audio_sha256']
    if kind!='crop':raise ValueError('Unknown combined-context verification kind')
    crop=read(receipt_path(folder,unit,attempt))
    isolated.relative_checked(folder,crop['audio'],crop['audio_sha256'])
    return bound,unit['spoken'],crop['audio'],crop['audio_sha256']


def verifier_config(bound,recognizer,torch_version,transformers_version):
    # Explicit versioned configuration, with unchanged pinned source-blind decoder.
    return {**words.verifier_config(bound,recognizer,torch_version,transformers_version),
            'policy':POLICY,'implementation_sha256':file_digest(SELF)}


def check_record(check,expected,checksum,bound,recognizer):
    config=check['verifier']
    if config!=verifier_config(bound,recognizer,config.get('torch'),config.get('transformers')):
        raise ValueError('Combined context verifier source-blind implementation/config changed')
    if recognizer=='qwen':
        if words.checked_inventory(ASR_ROOT)!=bound['qwen_files']:raise ValueError('Qwen verifier inventory changed')
    else:
        import witness_public_demo_turbo as turbo
        if words.checked_inventory(turbo.MODEL_PATH)!=turbo.FILES:raise ValueError('Whisper verifier inventory changed')
    raw=transcript_check(expected,check['heard'],checksum,renderer.sha(config))
    if any(check.get(k)!=v for k,v in raw.items()):raise ValueError('Combined context raw transcript cannot be recomputed')
    return raw


def blind_decision(folder,unit,attempt,kind):
    bound,expected,_,checksum=verification_audio(folder,unit,attempt,kind);evidence=[]
    for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
        path=attempt_home(folder,unit,attempt)/(kind+'-'+recognizer+'.json');check=read(path)
        evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':file_digest(path),
                         'raw':check_record(check,expected,checksum,bound,recognizer),'verifier':check['verifier'],
                         'role':role,'family':family})
    repair=copy.deepcopy(bound['word_repair'])
    if kind=='crop':return words.strict_decision(unit,evidence,bound['source_paragraph'],repair)
    chosen=next(c for c in bound['candidates'] if c['id']==bound['attempts'][attempt-1]['context_id'])
    synthetic={'text':chosen['source_text'],'spoken':chosen['spoken'],'synthesis_text':chosen['synthesis_text']}
    repair['source_stress_projection']=chosen['source_stress_projection']
    for p in repair['patches']:p['spoken_index']+=chosen['target_word_start']
    return words.strict_decision(synthetic,evidence,chosen['source_text'],repair)


def render(folder, unit, attempt):
    bound = campaign(folder, unit)
    spec = native_spec(folder, unit, bound, attempt)
    base = attempt_home(folder, unit, attempt)
    if (base/'native-receipt.json').exists():
        native_receipt(folder, unit, attempt)
        return
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


def verify(folder, unit, attempt, kind, recognizer):
    bound, expected, relative, checksum = verification_audio(folder, unit, attempt, kind)
    path = isolated.relative_checked(folder, relative, checksum)
    base = attempt_home(folder, unit, attempt)
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


def align(folder, unit, attempt):
    bound, spec, native = native_receipt(folder, unit, attempt)
    if not blind_decision(folder, unit, attempt, 'context')['passed']:
        raise ValueError('Full context has unresolved blind recognition disagreements')
    target = attempt_home(folder, unit, attempt)/'alignment.json'
    if target.exists():
        validate_alignment(folder, unit, attempt)
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
        result = model.align(audio=str(folder/native['audio']), text=spec['word_context_recovery']['context']['spoken'], language=config['language'])[0]
        raw = [{'text': item.text, 'start': float(item.start_time), 'end': float(item.end_time)} for item in result.items]
    finally:
        del model
        torch.cuda.empty_cache()
    cues = validate_cues(raw, spec['word_context_recovery']['context']['spoken'], native['quality']['duration'])
    immutable(target, {'audio_sha256': native['audio_sha256'], 'native_receipt_sha256': file_digest(target.parent/'native-receipt.json'),
                       'expected_text_sha256': renderer.sha(spec['word_context_recovery']['context']['spoken']),
                       'raw_cues': raw, 'cues': cues, 'files': bound['aligner_files'], 'model': 'Qwen3-ForcedAligner-0.6B',
                       'expected_text_given_to_aligner': True, 'alignment_is_audio_approval': False,
                       'implementation_sha256': file_digest(Path(__file__)), 'configuration': config})


def validate_alignment(folder, unit, attempt):
    bound, spec, native = native_receipt(folder, unit, attempt)
    path = attempt_home(folder, unit, attempt)/'alignment.json'
    value = read(path)
    if words.checked_inventory(ALIGN_ROOT)!=bound['aligner_files']:raise ValueError('Actual aligner inventory changed')
    expected = spec['word_context_recovery']['context']['spoken']
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


def crop(folder, unit, attempt):
    import numpy as np
    import soundfile as sf
    _, spec, native = native_receipt(folder, unit, attempt)
    if not blind_decision(folder, unit, attempt, 'context')['passed']:
        raise ValueError('Blind full-context verification is unresolved')
    cues = validate_alignment(folder, unit, attempt)
    audio, rate = sf.read(folder/native['audio'], dtype='int16')
    bounds = crop_bounds(audio, rate, cues, spec['word_context_recovery']['context'])
    base = attempt_home(folder, unit, attempt)
    output = base/'target-crop.wav'
    if not output.exists():
        sf.write(output, audio[bounds['start_sample']:bounds['end_sample']], rate, subtype='PCM_16')
    existing, existing_rate = sf.read(output, dtype='int16')
    if existing_rate != rate or not np.array_equal(existing, audio[bounds['start_sample']:bounds['end_sample']]):
        raise ValueError('Existing target crop changed; never overwrite its evidence')
    checksum = file_digest(output)
    crop_spec = {**spec, 'word_context_recovery': {**spec['word_context_recovery'],
                 'native_receipt_sha256': file_digest(base/'native-receipt.json'),
                 'alignment_sha256': file_digest(base/'alignment.json'), 'crop': bounds}}
    receipt = {'schema': 1, 'unit': unit['id'], 'take': attempt+17, 'spec': crop_spec,
               'spec_sha256': renderer.sha(crop_spec), 'audio': output.relative_to(folder).as_posix(),
               'audio_sha256': checksum, 'quality_passed': True, 'quality': renderer.quality(output),
               'canonical_registration': False, 'human_approval': False}
    immutable(base/'crop-receipt.json', receipt)


def validate_word_context_receipt(folder, unit, receipt):
    """CPU-only future resolver adapter; no canonical publication/registration."""
    import numpy as np
    import soundfile as sf
    folder = Path(folder).resolve()
    attempt = receipt['spec']['word_context_recovery']['attempt']
    bound, spec, native = native_receipt(folder, unit, attempt)
    base = attempt_home(folder, unit, attempt)
    if read(base/'crop-receipt.json') != receipt or receipt.get('take') != attempt+17 or receipt.get('unit') != unit['id']:
        raise ValueError('Context crop receipt differs from explicit namespace')
    audio, rate = sf.read(folder/native['audio'], dtype='int16')
    bounds = crop_bounds(audio, rate, validate_alignment(folder, unit, attempt), spec['word_context_recovery']['context'])
    expected = {**spec, 'word_context_recovery': {**spec['word_context_recovery'],
                'native_receipt_sha256': file_digest(base/'native-receipt.json'),
                'alignment_sha256': file_digest(base/'alignment.json'), 'crop': bounds}}
    if (receipt['spec'] != expected or receipt['spec_sha256'] != renderer.sha(expected)
            or receipt.get('canonical_registration') is not False or receipt.get('human_approval') is not False
            or receipt.get('audio')!=(base/'target-crop.wav').relative_to(folder).as_posix()):
        raise ValueError('Context source/alignment/sample crop changed')
    path = isolated.relative_checked(folder, receipt['audio'], receipt['audio_sha256'])
    child, child_rate = sf.read(path, dtype='int16')
    if child_rate != rate or not np.array_equal(child, audio[bounds['start_sample']:bounds['end_sample']]):
        raise ValueError('Crop is not the exact unprocessed native PCM sample slice')
    if receipt.get('quality_passed') is not True or renderer.quality(path) != receipt['quality']:
        raise ValueError('Cropped target quality/boundaries cannot be reproduced')
    full_decision = blind_decision(folder, unit, attempt, 'context')
    target_decision = blind_decision(folder, unit, attempt, 'crop')
    if not full_decision['passed'] or not target_decision['passed']:
        raise ValueError('Full context or target crop retains blind recognition disagreements')
    word_bound=words.campaign(folder,unit);proposal=words.validated_proposal(folder,unit,word_bound)
    providers=[]
    for row in proposal['providers']:
        saved=read(isolated.relative_checked(folder,row['answer'],row['answer_sha256']))
        providers.append({'provider':saved['metadata']['provider'],'model':saved['metadata']['model_used'],
                          'effort':saved['metadata']['effort'],'request_sha256':row['request_sha256'],
                          'answer_sha256':row['answer_sha256'],'audio_approval':False})
    return {'policy': POLICY, 'receipt_sha256': file_digest(base/'crop-receipt.json'),
            'full_context_decision': full_decision, 'target_decision': target_decision,
            'native_audio_sha256': native['audio_sha256'], 'crop_audio_sha256': receipt['audio_sha256'],
            'source_spans': spec['word_context_recovery']['context']['source_spans'], 'crop': bounds,
            'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},
            'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),
            'word_provenance':{'campaign_sha256':bound['word_campaign_sha256'],'proposal_sha256':bound['word_proposal_sha256'],
                'repair':bound['word_repair'],'stress':bound['word_stress'],'dictionary':word_bound['dictionary'],
                'providers':providers,'original_text_unchanged':True,'original_spoken_unchanged':True,
                'acoustic_stress_approval':False,'audio_approval':False},
            'alignment_is_approval': False, 'canonical_registration': False, 'human_approval': False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--unit',required=True)
    parser.add_argument('--stage',choices=['plan','render','verify','align','crop','validate'],required=True)
    parser.add_argument('--attempt',type=int,choices=[1,2,3]);parser.add_argument('--recognizer',choices=['qwen','turbo'])
    parser.add_argument('--kind',choices=['context','crop'],default='context')
    args=parser.parse_args()
    if args.stage!='plan' and args.attempt is None:parser.error('--attempt is required')
    if args.stage=='verify' and args.recognizer is None:parser.error('--recognizer is required')
    folder=args.folder.resolve();assignment,_,_=isolated.frozen_context(folder)
    unit=next((u for u in assignment['units'] if u['id']==args.unit),None)
    if unit is None:parser.error('Unknown original source unit')
    if args.stage=='plan':plan(folder,unit)
    elif args.stage=='render':render(folder,unit,args.attempt)
    elif args.stage=='verify':verify(folder,unit,args.attempt,args.kind,args.recognizer)
    elif args.stage=='align':align(folder,unit,args.attempt)
    elif args.stage=='crop':crop(folder,unit,args.attempt)
    else:
        proof=validate_word_context_receipt(folder,unit,read(receipt_path(folder,unit,args.attempt)))
        immutable(attempt_home(folder,unit,args.attempt)/'validation.json',proof)
        print(json.dumps({'passed':True,'audio_sha256':proof['crop_audio_sha256']}))


if __name__=='__main__':main()
