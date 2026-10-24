"""V18 development-only same-family/mood reviewed reference override for exact source context.

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
import context_public_demo_word_recovery as predecessor
import context_public_demo_historical_word_recovery as historical
import supplement_public_demo_word_recovery as supplement
import context_public_demo_supplement_word_recovery as lexical_context
import public_demo_reviewed_reference as reference_guard
import pronounce_public_audiobook_word_recovery as words
import render_public_audiobook_demo as renderer
import recover_public_audiobook_demo as isolated
from rimsky.integrity import validate_cues
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.speech_quality import transcript_check,word_tokens
from rimsky.util import file_digest

SELF=Path(__file__).resolve()
POLICY='public-demo-approved-reviewed-reference-context-crop-v1'
PROFILES=isolated.PROFILES
BOUNDARY=context.BOUNDARY
ASR_ROOT=context.ASR_ROOT
ALIGN_ROOT=context.ALIGN_ROOT
LANGUAGES={'Russian':'ru'}
read=renderer.read
immutable=isolated.immutable
crop_bounds=context.crop_bounds


def dependencies():
    return {**lexical_context.dependencies(),reference_guard.SELF.relative_to(ROOT).as_posix():file_digest(reference_guard.SELF),SELF.relative_to(ROOT).as_posix():file_digest(SELF)}


def language(bound):
    if bound.get('language')!='Russian':raise ValueError('Approved word-context recovery requires Russian')
    return 'Russian'


def home(folder,unit):return Path(folder)/'reviewed-reference-context-recovery-v1'/unit['id']


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
        _,spec,native=lexical_context.native_receipt(folder,unit,attempt)
        base=lexical_context.attempt_home(folder,unit,attempt)
        failure=execution_proof(folder,base,spec,native)
        if failure is None:
            decision=lexical_context.blind_decision(folder,unit,attempt,'context')
            if decision['passed']:
                if not lexical_context.receipt_path(folder,unit,attempt).exists():raise ValueError('Prior source context still has unfinished target verification')
                decision=lexical_context.blind_decision(folder,unit,attempt,'crop')
                if decision['passed']:raise ValueError('Prior source-bound audio already passes; new reference is not authorized')
            failure={'failed_stage':'source-bound-recognition','decision':decision}
        files=[{'path':p.relative_to(folder).as_posix(),'sha256':file_digest(p)} for p in sorted(base.rglob('*'))
               if p.is_file() and p.suffix in ('.json','.wav','.log')]
        rows.append({'attempt':attempt,'native_receipt_sha256':file_digest(base/'native-receipt.json'),**failure,
                     'files':files,'audio_approval':False})
    return rows




def candidates(assignment,blocks,unit,repair):
    validate_repair(unit,repair)
    if unit['id']!='u0071':raise ValueError('Only the reviewed u0071 supplemental context is authorized')
    units=assignment['units'];index=next(i for i,u in enumerate(units) if u['id']==unit['id'])
    if index+1>=len(units):raise ValueError('Exact next frozen source unit is missing')
    neighbor=units[index+1]
    if (neighbor['id']!='u0072' or unit['source_id']!=neighbor['source_id'] or unit['end']!=neighbor['start']
            or any(unit.get(k)!=neighbor.get(k) for k in ('chapter_id','speaker','voice','mood','mode','palette_reference'))):
        raise ValueError('Supplement context must retain the exact same-source next voice/performance unit')
    windows=context.context_candidates(assignment,blocks,unit)
    matches=[c for c in windows if [x['id'] for x in c['source_spans']]==[unit['id'],neighbor['id']]]
    if len(matches)!=1:raise ValueError('Exact source-adjacent two-unit context is absent or ambiguous')
    value=copy.deepcopy(matches[0]);value.pop('id')
    for span,frozen in zip(value['source_spans'],(unit,neighbor)):
        if any(span.get(k)!=frozen[k] for k in ('id','source_id','start','end','text','spoken')):
            raise ValueError('Frozen source/native context span changed')
        if blocks[span['source_id']][span['start']:span['end']]!=span['text']:raise ValueError('Source slice changed')
    value['synthesis_text']=' '.join((repair['synthesis_text'],neighbor.get('synthesis_text',neighbor['spoken'])))
    value['source_stress_projection']=' '.join((repair['source_stress_projection'],neighbor.get('synthesis_text',neighbor['spoken'])))
    value['id']='reviewed-reference-context-'+renderer.sha(value)[:16]
    return [value]



def validate_repair(unit,repair):
    """Reproduce the approved pure patch operation, including its primary facts.

    This adds no pronunciation equivalence. The frozen word helper owns the
    exact post-ц letter/stress rule and all source/native offsets.
    """
    required={'source_start','source_end','source_surface','native_start','native_end',
              'before','after','kind','dictionary_facts'}
    if (not isinstance(repair,dict) or not isinstance(repair.get('patches'),list)
            or not any(p.get('kind')=='historical-ts-spelling' for p in repair['patches'])):
        raise ValueError('Historical context requires an explicitly approved historical spelling patch')
    row={k:unit[k] for k in ('id','source_id','start','end')}
    row.update(text_sha256=renderer.sha(unit['text']),native_before=unit.get('synthesis_text',unit['spoken']),
               native_after=repair['synthesis_text'],patches=[{k:p[k] for k in required} for p in repair['patches']])
    reproduced=words.repair(unit,row)
    if reproduced!=repair:raise ValueError('Approved historical patch/source/stress evidence cannot be reproduced')
    return reproduced


def approved_campaign(folder,unit):
    bound,old=supplement.prepared_bound(folder,unit)
    supplement.validated_proposal(folder,unit,bound,old)
    return old


def approved_proposal(folder,unit,word_bound):
    bound,old=supplement.prepared_bound(folder,unit)
    if word_bound!=old:raise ValueError('Original source-bound repair changed')
    return supplement.validated_proposal(folder,unit,bound,old)


def normative_evidence(folder,unit,word_bound,proposal,stress):
    bound,old=supplement.prepared_bound(folder,unit)
    if word_bound!=old or proposal!=supplement.validated_proposal(folder,unit,bound,old):
        raise ValueError('Only the accepted supplemental evidence/reviews authorize this context')
    repair=validate_repair(unit,old['repair'])
    if stress!=bound['stress']:raise ValueError('Accepted stress evidence changed')
    providers=[]
    for row in proposal['providers']:
        saved=read(isolated.relative_checked(folder,row['answer'],row['answer_sha256']))
        providers.append({'provider':saved['metadata']['provider'],'model':saved['metadata']['model_used'],
            'effort':saved['metadata']['effort'],'request_sha256':row['request_sha256'],
            'answer_sha256':row['answer_sha256'],'accepted':True,'audio_approval':False})
    facts=[*old['dictionary'],*bound['supplement']['facts']]
    return {'policy':'existing-approved-additive-primary-word-evidence-v1',
        'word_campaign_sha256':file_digest(supplement.home(folder,unit)/'campaign.json'),
        'word_proposal_sha256':file_digest(supplement.home(folder,unit)/'proposal.json'),
        'old_campaign_sha256':bound['old_campaign_sha256'],'supplement_sha256':bound['supplement_sha256'],
        'old_rejection_sha256':bound['supplement']['prior_rejection']['provider_answer']['sha256'],
        'old_rejection_retained':True,'prior_rejection_bindings_sha256':renderer.sha(bound['supplement']['prior_rejection']),
        'repair_sha256':renderer.sha(repair),'dictionary':facts,'dictionary_sha256':renderer.sha(facts),
        'stress':stress,'stress_sha256':renderer.sha(stress),'providers':providers,
        'source_text_sha256':renderer.sha(unit['text']),'original_spoken_sha256':renderer.sha(unit['spoken']),
        'audio_approval':False,'acoustic_stress_approval':False}



def no_conflicting_context_attempts(folder,unit):
    if unit['id']!='u0071':raise ValueError('Only u0071 is authorized for this reviewed context')
    for take in range(33,36):
        canonical=Path(folder)/'takes'/unit['id']/('take-%02d.json'%take)
        if canonical.exists() and 'reviewed_reference_context_recovery' not in read(canonical).get('spec',{}):
            raise ValueError('Supplement context canonical take slot is already occupied')



def plan(folder,unit,dossier):
    import resolve_public_demo_recognition as resolver
    source=resolver.Context(folder);language(source.assignment)
    no_conflicting_context_attempts(folder,unit)
    reference=reference_guard.reference_override(dossier,unit)
    bound=approved_campaign(folder,unit);proposal=approved_proposal(folder,unit,bound)
    stress=words.validated_stress(folder,bound);failed=failed_word_attempts(folder,unit)
    normative=normative_evidence(folder,unit,bound,proposal,stress)
    windows=candidates(source.assignment,source.blocks,unit,bound['repair'])
    profiles=list(PROFILES)
    choices=[{'context_id':windows[i%len(windows)]['id'],'profile':profiles[i]} for i in range(3)]
    value={'schema':1,'policy':POLICY,'language':'Russian','generation':source.generation,'target':unit,
           'original_spec':bound['original_spec'],'dependencies':dependencies(),'bindings':source.bindings,
           'word_campaign_sha256':file_digest(supplement.home(folder,unit)/'campaign.json'),
           'word_proposal_sha256':file_digest(supplement.home(folder,unit)/'proposal.json'),
           'word_stress':stress,'word_repair':bound['repair'],'word_failures':failed,
           'normative_evidence':normative,'reference_dossier_path':str(Path(dossier).resolve()),'reference_override':reference,
           'candidates':windows,'attempts':choices,'max_takes':3,'profiles':PROFILES,'boundary':BOUNDARY,
           'qwen_files':words.checked_inventory(ASR_ROOT),'aligner_files':words.checked_inventory(ALIGN_ROOT),
           'neighbor_policy':'Exact frozen source and native stress; no neighboring recovery proposals applied',
           'source_paragraph':source.blocks[unit['source_id']],'audio_approval':False}
    immutable(home(folder,unit)/'campaign.json',value)
    print('WORD CONTEXT PLAN',unit['id'],choices,flush=True)
    return value


def campaign(folder,unit):
    value=read(home(folder,unit)/'campaign.json');assignment,manifest,generation=isolated.frozen_context(folder)
    no_conflicting_context_attempts(folder,unit)
    word_bound=approved_campaign(folder,unit);proposal=approved_proposal(folder,unit,word_bound)
    stress=words.validated_stress(folder,word_bound)
    if (value.get('policy')!=POLICY or language(value)!=language(assignment) or value['target']!=unit
            or value['generation']!=generation or value['dependencies']!=dependencies() or value['max_takes']!=3
            or value['profiles']!=PROFILES or value['boundary']!=BOUNDARY or value['audio_approval'] is not False
            or value['original_spec']!=word_bound['original_spec'] or value['word_repair']!=word_bound['repair']
            or value['word_campaign_sha256']!=file_digest(supplement.home(folder,unit)/'campaign.json')
            or value['word_proposal_sha256']!=file_digest(supplement.home(folder,unit)/'proposal.json')
            or value['word_stress']!=stress
            or value['normative_evidence']!=normative_evidence(folder,unit,word_bound,proposal,stress)
            or value['word_failures']!=failed_word_attempts(folder,unit)
            or value.get('reference_override')!=reference_guard.reference_override(value['reference_dossier_path'],unit)):
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
    spec['request'].update(id=unit['id']+'-reviewed-reference-context-'+str(attempt),text=chosen['synthesis_text'],
       seed=(bound['original_spec']['request']['seed']+profile['seed_offset']+600000*attempt)%2**31,
       max_tokens=min(2048,math.ceil((6+.35*sum(c.isalpha() for c in chosen['synthesis_text']))*25)+8))
    actual=bound['reference_override']['actual_reference']
    spec['reference']=copy.deepcopy(actual)
    spec['request'].update(voice_ref=actual['path'],voice_ref_text=actual['text'])
    spec['reviewed_reference_context_recovery']={'policy':POLICY,'attempt':attempt,'limit':3,'context':chosen,'profile':choice['profile'],
         'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'implementation_sha256':file_digest(SELF),
         'word_proposal_sha256':bound['word_proposal_sha256'],'word_repair_sha256':renderer.sha(bound['word_repair']),
         'normative_evidence_sha256':renderer.sha(bound['normative_evidence']),
         'reference_override_sha256':renderer.sha(bound['reference_override']),
         'original_reference_sha256':renderer.sha(unit['palette_reference']),
         'actual_reference_audio_sha256':actual['audio_sha256'],'actual_reference_text_sha256':renderer.sha(actual['text'])}
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
    if kind=='context':return bound,spec['reviewed_reference_context_recovery']['context']['spoken'],native['audio'],native['audio_sha256']
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
    if config!=verifier_config(bound,recognizer,importlib.metadata.version('torch'),importlib.metadata.version('transformers')):
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
        result = model.align(audio=str(folder/native['audio']), text=spec['reviewed_reference_context_recovery']['context']['spoken'], language=config['language'])[0]
        raw = [{'text': item.text, 'start': float(item.start_time), 'end': float(item.end_time)} for item in result.items]
    finally:
        del model
        torch.cuda.empty_cache()
    cues = validate_cues(raw, spec['reviewed_reference_context_recovery']['context']['spoken'], native['quality']['duration'])
    immutable(target, {'audio_sha256': native['audio_sha256'], 'native_receipt_sha256': file_digest(target.parent/'native-receipt.json'),
                       'expected_text_sha256': renderer.sha(spec['reviewed_reference_context_recovery']['context']['spoken']),
                       'raw_cues': raw, 'cues': cues, 'files': bound['aligner_files'], 'model': 'Qwen3-ForcedAligner-0.6B',
                       'expected_text_given_to_aligner': True, 'alignment_is_audio_approval': False,
                       'implementation_sha256': file_digest(Path(__file__)), 'configuration': config})


def validate_alignment(folder, unit, attempt):
    bound, spec, native = native_receipt(folder, unit, attempt)
    path = attempt_home(folder, unit, attempt)/'alignment.json'
    value = read(path)
    if words.checked_inventory(ALIGN_ROOT)!=bound['aligner_files']:raise ValueError('Actual aligner inventory changed')
    expected = spec['reviewed_reference_context_recovery']['context']['spoken']
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
    bounds = crop_bounds(audio, rate, cues, spec['reviewed_reference_context_recovery']['context'])
    base = attempt_home(folder, unit, attempt)
    output = base/'target-crop.wav'
    if not output.exists():
        sf.write(output, audio[bounds['start_sample']:bounds['end_sample']], rate, subtype='PCM_16')
    existing, existing_rate = sf.read(output, dtype='int16')
    if existing_rate != rate or not np.array_equal(existing, audio[bounds['start_sample']:bounds['end_sample']]):
        raise ValueError('Existing target crop changed; never overwrite its evidence')
    checksum = file_digest(output)
    crop_spec = {**spec, 'reviewed_reference_context_recovery': {**spec['reviewed_reference_context_recovery'],
                 'native_receipt_sha256': file_digest(base/'native-receipt.json'),
                 'alignment_sha256': file_digest(base/'alignment.json'), 'crop': bounds}}
    receipt = {'schema': 1, 'unit': unit['id'], 'take': attempt+32, 'spec': crop_spec,
               'spec_sha256': renderer.sha(crop_spec), 'audio': output.relative_to(folder).as_posix(),
               'audio_sha256': checksum, 'quality_passed': True, 'quality': renderer.quality(output),
               'canonical_registration': False, 'human_approval': False}
    immutable(base/'crop-receipt.json', receipt)


def validate_reviewed_reference_context_receipt(folder, unit, receipt):
    """CPU-only future resolver adapter; no canonical publication/registration."""
    import numpy as np
    import soundfile as sf
    folder = Path(folder).resolve()
    attempt = receipt['spec']['reviewed_reference_context_recovery']['attempt']
    bound, spec, native = native_receipt(folder, unit, attempt)
    base = attempt_home(folder, unit, attempt)
    if read(base/'crop-receipt.json') != receipt or receipt.get('take') != attempt+32 or receipt.get('unit') != unit['id']:
        raise ValueError('Context crop receipt differs from explicit namespace')
    audio, rate = sf.read(folder/native['audio'], dtype='int16')
    bounds = crop_bounds(audio, rate, validate_alignment(folder, unit, attempt), spec['reviewed_reference_context_recovery']['context'])
    expected = {**spec, 'reviewed_reference_context_recovery': {**spec['reviewed_reference_context_recovery'],
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
    word_bound=approved_campaign(folder,unit);proposal=approved_proposal(folder,unit,word_bound)
    providers=[]
    for row in proposal['providers']:
        saved=read(isolated.relative_checked(folder,row['answer'],row['answer_sha256']))
        providers.append({'provider':saved['metadata']['provider'],'model':saved['metadata']['model_used'],
                          'effort':saved['metadata']['effort'],'request_sha256':row['request_sha256'],
                          'answer_sha256':row['answer_sha256'],'audio_approval':False})
    return {'policy': POLICY, 'receipt_sha256': file_digest(base/'crop-receipt.json'),
            'full_context_decision': full_decision, 'target_decision': target_decision,
            'native_audio_sha256': native['audio_sha256'], 'crop_audio_sha256': receipt['audio_sha256'],
            'source_spans': spec['reviewed_reference_context_recovery']['context']['source_spans'], 'crop': bounds,
            'target_source_span':{k:unit[k] for k in ('id','source_id','start','end','text')},
            'campaign_sha256':file_digest(home(folder,unit)/'campaign.json'),'reference_override_provenance':bound['reference_override'],
            'word_provenance':{'campaign_sha256':bound['word_campaign_sha256'],'proposal_sha256':bound['word_proposal_sha256'],
                'repair':bound['word_repair'],'stress':bound['word_stress'],'dictionary':bound['normative_evidence']['dictionary'],
                'normative_evidence':bound['normative_evidence'],
                'providers':providers,'original_text_unchanged':True,'original_spoken_unchanged':True,
                'acoustic_stress_approval':False,'audio_approval':False},
            'alignment_is_approval': False, 'canonical_registration': False, 'human_approval': False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--unit',required=True)
    parser.add_argument('--stage',choices=['plan','render','verify','align','crop','validate'],required=True)
    parser.add_argument('--attempt',type=int,choices=[1,2,3]);parser.add_argument('--recognizer',choices=['qwen','turbo'])
    parser.add_argument('--kind',choices=['context','crop'],default='context');parser.add_argument('--dossier',type=Path)
    args=parser.parse_args()
    if args.stage!='plan' and args.attempt is None:parser.error('--attempt is required')
    if args.stage=='verify' and args.recognizer is None:parser.error('--recognizer is required')
    folder=args.folder.resolve();assignment,_,_=isolated.frozen_context(folder)
    unit=next((u for u in assignment['units'] if u['id']==args.unit),None)
    if unit is None:parser.error('Unknown original source unit')
    if args.stage=='plan':
        if args.dossier is None:parser.error('--dossier required for reviewed-reference plan')
        plan(folder,unit,args.dossier)
    elif args.stage=='render':render(folder,unit,args.attempt)
    elif args.stage=='verify':verify(folder,unit,args.attempt,args.kind,args.recognizer)
    elif args.stage=='align':align(folder,unit,args.attempt)
    elif args.stage=='crop':crop(folder,unit,args.attempt)
    else:
        proof=validate_reviewed_reference_context_receipt(folder,unit,read(receipt_path(folder,unit,args.attempt)))
        immutable(attempt_home(folder,unit,args.attempt)/'validation.json',proof)
        print(json.dumps({'passed':True,'audio_sha256':proof['crop_audio_sha256']}))


if __name__=='__main__':main()
