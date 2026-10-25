"""Local sentence/part synthesis with a sealed custom Higgs GGUF voice pack.

No text chunking, stripped-control retry, voice fallback or automatic gain.
Every input to this runner is represented in the immutable synthesis plan.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from .cli_process import run_cli
from .speech_backend import implementation_fingerprint
from .audio_join import JOIN_POLICY
from .util import AppError, GenerationCapError, SpeechHoldError, atomic_write, digest, inside

TAGS = frozenset(('affection amusement anger arousal awe bitterness confusion contemplation contentment '
                 'determination disgust elation enthusiasm fear helplessness longing pride relief sadness shame surprise '
                 'speed_very_slow speed_slow speed_fast speed_very_fast pitch_low pitch_high expressive_high expressive_low '
                 'pause long_pause singing shouting whispering cough laughter crying screaming burping humming sigh sniff sneeze').split())
INLINE_TAGS = frozenset('pause long_pause cough laughter crying screaming burping humming sigh sniff sneeze'.split())
SENTENCE_TAGS = TAGS - INLINE_TAGS


def tag_token(tag):
    if tag not in SENTENCE_TAGS:
        raise AppError('Inline effects need their own reviewed placement; they cannot prefix a sentence.')
    category = 'style' if tag in {'singing', 'shouting', 'whispering'} else 'prosody' if tag.startswith(('speed_', 'pitch_', 'expressive_')) else 'emotion'
    return f'<|{category}:{tag}|>'


def require_effect_policy(pack,tags):
    """Assignment uses recorded performances first; effects are an opt-in phase."""
    policy=pack['speech'].get('effects_policy','disabled')
    if policy not in ('disabled','qualification','qualified'):
        raise AppError('Unknown Higgs effects policy.')
    if not tags:return policy
    if policy=='disabled':
        raise SpeechHoldError('Higgs effects are disabled while voice and mood assignment is validated; select a reviewed performance reference.')
    if policy=='qualification' and pack['speech'].get('purpose')!='evaluation_only':
        raise SpeechHoldError('Experimental Higgs effects are allowed only in a disclosed qualification audition.')
    return policy
PRECISIONS = {0: 'f32', 1: 'f16', 2: 'q4_0', 3: 'q4_1', 7: 'q8_0', 8: 'q5_0', 9: 'q5_1',
              10: 'q2_k', 11: 'q3_k_s', 12: 'q3_k_m', 13: 'q3_k_l', 14: 'q4_k_s', 15: 'q4_k_m',
              16: 'q5_k_s', 17: 'q5_k_m', 18: 'q6_k', 30: 'bf16'}


def gguf_precision(path):
    """Read general.file_type from GGUF metadata, without loading tensor weights."""
    with Path(path).open('rb') as f:
        def unpack(fmt):
            size = struct.calcsize(fmt)
            data = f.read(size)
            if len(data) != size:
                raise AppError('Truncated Higgs GGUF metadata.')
            return struct.unpack(fmt, data)[0]

        def value(kind):
            formats = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}
            if kind in formats:
                return unpack(formats[kind])
            if kind == 8:
                size = unpack('<Q')
                if size > 16 * 1024 * 1024:
                    raise AppError('Invalid GGUF metadata string.')
                data = f.read(size)
                if len(data) != size:
                    raise AppError('Truncated GGUF string.')
                return data.decode('utf-8')
            if kind == 9:
                element, size = unpack('<I'), unpack('<Q')
                if size > 2_000_000:
                    raise AppError('Invalid GGUF metadata array.')
                for _ in range(size):
                    value(element)
                return None
            raise AppError('Unknown GGUF metadata type.')

        if f.read(4) != b'GGUF' or unpack('<I') not in (2, 3):
            raise AppError('Higgs model must be a supported GGUF file.')
        unpack('<Q')  # tensors
        count = unpack('<Q')
        if count > 100_000:
            raise AppError('Invalid GGUF metadata count.')
        for _ in range(count):
            key, item = value(8), value(unpack('<I'))
            if key == 'audiocpp.weight_type':
                if item not in {'f16', 'bf16', 'q8_0', 'q2_k', 'q3_k', 'q4_k', 'q5_k', 'q6_k'}:
                    raise AppError('Unsupported audio.cpp Higgs precision.')
                return item
            if key == 'general.file_type':
                if item not in PRECISIONS:
                    raise AppError('Unsupported Higgs model precision.')
                return PRECISIONS[item]
    raise AppError('Higgs model precision is missing from GGUF metadata.')


def validate_higgs_pack(pack, root):
    speech = pack['speech']
    if speech['variant'] != 'clone':
        raise AppError('Higgs production uses reviewed reference voices.')
    for field in ('model', 'runner', 'training_provenance', 'listening_review'):
        if not isinstance(speech.get(field), str) or not inside(root, speech[field]).is_file():
            raise AppError(f'Higgs pack is missing {field}.')
        if inside(root, speech[field]).relative_to(root.resolve()).as_posix() != speech[field]:
            raise AppError('Higgs model, runner and provenance paths must be relative POSIX paths inside the pack.')
    model = inside(root, speech['model'])
    if gguf_precision(model) != speech.get('precision'):
        raise AppError('Higgs precision does not match the actual GGUF.')
    try:
        path = inside(root, speech['training_provenance'])
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized training provenance')
        proof = json.loads(path.read_text('utf-8'))
        if not isinstance(proof, dict):
            raise ValueError('expected training provenance object')
    except (ValueError, OSError) as error:
        raise AppError('Invalid Higgs training provenance.') from error
    # Conversion binds the custom model to the dataset, adapter and completed run.
    if (proof.get('kind') not in ('lora', 'sft') or type(proof.get('optimizer_steps')) is not int
            or proof['optimizer_steps'] < 1 or proof.get('model_sha256') != pack['files'].get(speech['model'])
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(proof.get(k, ''))) for k in ('dataset_sha256', 'checkpoint_sha256'))):
        raise AppError('Higgs production requires a completed, hash-bound custom fine-tune; stock weights are evaluation only.')
    sampling = speech.get('sampling', {})
    expected = {'seed', 'temperature', 'top_k', 'top_p', 'repetition_penalty', 'do_sample'}
    if set(sampling) != expected or type(sampling.get('seed')) is not int or not 0 <= sampling['seed'] < 2**31:
        raise AppError('Higgs sampling settings and seed must be explicit.')
    if (type(sampling['top_k']) is not int or not 0 <= sampling['top_k'] <= 10000 or type(sampling['do_sample']) is not bool
            or any(type(sampling[k]) not in (int, float) or not math.isfinite(sampling[k]) for k in ('temperature', 'top_p', 'repetition_penalty'))
            or not 0 < sampling['temperature'] <= 5 or not 0 < sampling['top_p'] <= 1 or not .1 <= sampling['repetition_penalty'] <= 5):
        raise AppError('Invalid Higgs sampling settings.')
    if type(speech.get('threads')) is not int or not 1 <= speech['threads'] <= 128 or not speech.get('runner_version'):
        raise AppError('Higgs runner version and thread count must be pinned.')
    verifier = speech.get('verifier')
    if (not isinstance(verifier, dict) or verifier.get('backend') != 'qwen3_asr'
            or not isinstance(verifier.get('path'), str) or not inside(root, verifier['path']).is_dir()):
        raise AppError('Higgs production pack needs an independent local spoken-word verifier.')
    if inside(root, verifier['path']).relative_to(root.resolve()).as_posix() != verifier['path']:
        raise AppError('Higgs word-verifier path must be a relative POSIX path inside the pack.')
    try:
        listening_path = inside(root, speech['listening_review'])
        if listening_path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized listening report')
        listening = json.loads(listening_path.read_text('utf-8'))
        if (not isinstance(listening, dict) or listening.get('model_sha256') != pack['files'].get(speech['model'])
                or not isinstance(listening.get('reviews'), list)):
            raise ValueError('listening report must bind to this custom model')
    except (ValueError, OSError) as error:
        raise AppError('Invalid or stale Higgs listening approval.') from error
    for voice in pack['voices']:
        for reference in voice.get('references', {}).values():
            if (reference.get('reviewed') is not True or reference.get('voice_family') != voice.get('family', voice['id'])
                    or not re.fullmatch(r'[a-f0-9]{64}', str(reference.get('review_sha256', '')))):
                raise AppError('Higgs references must come from human-reviewed voice-family evidence.')
            if inside(root, reference['audio']).relative_to(root.resolve()).as_posix() != reference['audio']:
                raise AppError('Higgs reference paths must be relative POSIX paths inside the pack.')
            from .higgs_reference import check_reference
            check_reference(inside(root,reference['audio']),pack['files'][reference['audio']])
        controls = voice.get('higgs_controls')
        if (not isinstance(controls, dict) or not isinstance(controls.get('moods'), dict) or not controls['moods']
                or not isinstance(controls.get('paces'), dict) or not controls['paces']):
            raise AppError('Higgs voice needs explicit reviewed mood and pace mappings.')
        for control_type, table in (('mood', controls['moods']), ('pace', controls['paces'])):
            for control_id, mapping in table.items():
                if (not isinstance(mapping, dict) or mapping.get('reviewed') is not True
                        or not re.fullmatch(r'[a-f0-9]{64}', str(mapping.get('listening_sha256', '')))
                        or not isinstance(mapping.get('tags'), list) or not all(isinstance(t, str) and t in SENTENCE_TAGS for t in mapping['tags'])):
                    raise AppError('Higgs controls need a pinned listening check, including baseline controls.')
                if 'reference' in mapping and mapping['reference'] not in voice['references']:
                    raise AppError('Higgs control references an absent voice reference.')
                reference = voice['references'][mapping.get('reference', 'neutral')]
                approved = {'voice_family': voice.get('family', voice['id']), 'control_type': control_type,
                            'control_id': control_id, 'tags': mapping['tags'], 'reference_sha256': pack['files'][reference['audio']],
                            'verdict': 'approved'}
                if (mapping['listening_sha256'] != pack['files'].get(speech['listening_review'])
                        or not any(isinstance(item, dict) and (all(item.get(k) == v for k, v in approved.items()) or
                            item.get('control_type')=='combination' and all(item.get(k)==v for k,v in approved.items()
                                if k not in ('control_type','control_id'))) for item in listening['reviews'])):
                    raise AppError('Higgs control has no matching listening approval for this model, voice and reference.')


def synthesis_plan(pack, voice, text, delivery, controls, resolved, take=0):
    if not isinstance(resolved, dict) or resolved.get('backend') != 'higgs_audio_cpp':
        raise AppError('Resolve and pin the Higgs backend before synthesizing.')
    if resolved.get('precision') != pack['speech']['precision'] or resolved.get('runner_version') != pack['speech']['runner_version']:
        raise AppError('Resolved Higgs configuration does not match the pinned pack.')
    if not re.fullmatch(r'[a-f0-9]{64}', str(resolved.get('implementation_sha256', ''))):
        raise AppError('Pin the actual inference worker implementation before synthesis.')
    controls = controls or {}
    if set(controls) - {'mood', 'pace', 'ending_pace', 'direction', 'character_profile', 'voice_signature'}:
        raise AppError('Unsupported Higgs control; no control was discarded.')
    if controls.get('direction') is not None:
        if controls['direction'].get('audio_kind')=='nonverbal':
            from .sound_events import plan as event_plan
            return event_plan(pack,controls['direction'],controls.get('character_profile') or {},
                              controls.get('voice_signature') or {},pack['speech']['sampling']['seed']+take)
        return directed_plan(pack,voice,text,controls,resolved,take)
    mood, pace = controls.get('mood', delivery), controls.get('pace', 'normal')
    mappings = voice['higgs_controls']
    mood_rule, pace_rule = mappings['moods'].get(mood), mappings['paces'].get(pace)
    if mood_rule is None or pace_rule is None or controls.get('ending_pace', 'normal') != 'normal':
        raise SpeechHoldError('Requested Higgs delivery has no listen-checked mapping; render is held.')
    selected = mood_rule.get('reference', 'neutral')
    if selected not in voice.get('references', {}):
        raise SpeechHoldError('Assigned character has no reviewed Higgs reference; render is held.')
    reference = voice['references'][selected]
    if pack['speech'].get('purpose')!='evaluation_only':
        baseline_rule=mappings['moods'].get('neutral') or mappings['moods'].get('baseline') or {}
        anchor=baseline_rule.get('reference','neutral')
        if (anchor not in voice['references'] or
                pack['files'][voice['references'][anchor]['audio']]!=pack['files'][reference['audio']]):
            raise SpeechHoldError('Temporary delivery would change the frozen character reference; configure a stable voice signature before rendering.')
    tags = [*mood_rule['tags'], *pace_rule['tags']]
    effects_policy=require_effect_policy(pack,tags)
    if pack['speech'].get('purpose')!='evaluation_only':
        # Separate approvals for emotion and tempo do not prove that their
        # combined prefix preserves this voice. Require the actual tag tuple.
        from .util import file_digest
        root=Path(pack['_root'])
        review_path=inside(root,pack['speech']['listening_review'])
        if file_digest(review_path)!=pack['files'][pack['speech']['listening_review']]:
            raise SpeechHoldError('Higgs listening qualification changed; rendering is held.')
        review=json.loads(review_path.read_text(encoding='utf-8'))
        reference_hash=pack['files'][reference['audio']]
        actual={'voice_family':voice.get('family',voice['id']),'reference_sha256':reference_hash,
                'tags':tags,'verdict':'approved'}
        if (review.get('model_sha256')!=pack['files'][pack['speech']['model']]
                or not any(isinstance(row,dict) and all(row.get(k)==v for k,v in actual.items())
                           for row in review.get('reviews',[]))):
            raise SpeechHoldError('This combined Higgs voice/reference/control prefix has no listening qualification; rendering is held.')
    sampling = {**pack['speech']['sampling']}
    if type(take) is not int or take < 0:
        raise AppError('Invalid take number.')
    sampling['seed'] = (sampling['seed'] + take) % (2**31)
    prompt = ''.join(tag_token(tag) for tag in tags) + text
    return {'schema': 1, 'backend': 'higgs_audio_cpp', 'pack': pack['_fingerprint'],
            'implementation_sha256': resolved['implementation_sha256'],
            'resolved': resolved, 'model_sha256': pack['files'][pack['speech']['model']],
            'runner_sha256': pack['files'][pack['speech']['runner']], 'voice_family': voice.get('family', voice['id']),
            'reference': {'audio': reference['audio'], 'sha256': pack['files'][reference['audio']], 'text': reference['text']},
            'text': text, 'prompt': prompt, 'mood': mood, 'pace': pace, 'mappings': [mood_rule, pace_rule],
            'effects_policy':effects_policy,
            'sampling': sampling, 'max_tokens': pack['speech'].get('max_new_tokens', 2048),
            'codec_frame_rate': 25,
            'chunk_mode': 'tag_aware', 'chunk_size': 65536, 'threads': pack['speech']['threads'], 'take': take,
            'qualification_bypassed':pack['speech'].get('purpose')=='evaluation_only',
            'qualification_sha256':None if pack['speech'].get('purpose')=='evaluation_only' else pack['files'].get(pack['speech'].get('listening_review')),
            'boundary_quality': {'implementation_sha256': implementation_fingerprint('rimsky.audio_join'),
                                 'policy': dict(JOIN_POLICY)}}


def directed_plan(pack,voice,text,controls,resolved,take):
    """Keyed scene direction with a persistent signature and small palette.

    Evaluation permits unqualified palette entries, but never drops the scene
    intent or fabricates approval. Production uses exact listening-bound rows.
    """
    from .control_qualification import select
    direction=controls['direction'];persona=controls.get('character_profile') or {}
    signature=controls.get('voice_signature') or {}
    if signature.get('voice_family') not in (None,voice.get('family',voice['id'])):
        raise SpeechHoldError('Presentation route has no matching frozen voice signature.')
    if direction.get('needs_adjudication') or persona.get('needs_adjudication'):
        raise SpeechHoldError('Character or scene direction is unresolved; only this turn is held.')
    if direction.get('audio_kind')!='speech' or direction.get('technique')=='unmapped':
        raise SpeechHoldError('Required sound or technique has no speech realization.')
    pace=controls.get('pace','normal')
    if controls.get('ending_pace','normal')!='normal':raise SpeechHoldError('Ending pace has no qualified mapping.')
    evaluation=pack['speech'].get('purpose')=='evaluation_only'
    if evaluation:
        wanted={k:direction[k] for k in ('register','technique')};wanted['pace']=pace
        if direction.get('warmth') not in (None,'none'):wanted['warmth']=direction['warmth']
        entries=[e for e in voice.get('higgs_controls',{}).get('combinations',[]) if all(e.get(k)==v for k,v in wanted.items())
                 and e.get('reference','neutral')==signature.get('reference','neutral')
                 and (not e.get('tags') or pack['speech'].get('effects_policy','disabled')!='disabled')
                 and e.get('permanent_tags',[])==signature.get('permanent_tags',[])]
        if not entries:raise SpeechHoldError('Evaluation palette has no candidate for this performance; turn held.')
        entry=min(entries,key=lambda e:(len(e.get('tags',[])),e['id']));selection={'combination':entry['id'],'requested':wanted,'applied':wanted,
                                  'substituted':False,'qualification_bypassed':True}
    else:
        selection=select(pack,voice,direction,pace,signature)
        entry=next(e for e in voice['higgs_controls']['combinations'] if e['id']==selection['combination'])
    selected=entry.get('reference','neutral')
    if selected!=signature.get('reference','neutral'):
        raise SpeechHoldError('Temporary delivery would change the frozen character reference.')
    if selected not in voice.get('references',{}):raise SpeechHoldError('Character signature has no reference.')
    tags=entry['tags']
    if not set(signature.get('permanent_tags',[])).issubset(tags):
        raise SpeechHoldError('Performance combination drops permanent voice controls.')
    if any(t not in SENTENCE_TAGS for t in tags):raise SpeechHoldError('Sound events require reviewed inline placement.')
    # Reuse the native plan builder after choosing the exact combination. The
    # production tuple was checked above; the temporary view avoids unrelated
    # legacy per-mood approval and pins the original pack fingerprint below.
    import copy
    view=copy.deepcopy(pack);view['speech']['purpose']='evaluation_only'
    active=copy.deepcopy(voice)
    active['higgs_controls']={'moods':{'directed':{'tags':tags,'reference':selected}},'paces':{'normal':{'tags':[]}}}
    plan=synthesis_plan(view,active,text,'directed',{'mood':'directed','pace':'normal'},resolved,take)
    return {**plan,'direction':direction,'character_profile':persona,'voice_signature':signature,
            'performance_selection':selection,'qualification_bypassed':evaluation,
            'direction_implementation_sha256':implementation_fingerprint('rimsky.control_qualification')}


class HiggsBackend:
    def probe(self, payload):
        pack, requested = payload['pack'], payload['device']
        if requested not in ('auto', 'cpu', 'cuda'):
            raise AppError('Selected device is unavailable for Higgs synthesis.')
        runner = inside(Path(pack['_root']), pack['speech']['runner'])
        result = run_cli([str(runner), '--version'], cwd=runner.parent, timeout=30)
        version = result.stdout.strip()
        if result.returncode or version != pack['speech']['runner_version']:
            raise AppError('Installed Higgs runner differs from the sealed version.')
        # Resolve with the actual runner and its environment, rather than Torch's
        # device view (which can differ under CUDA_VISIBLE_DEVICES).
        devices = run_cli([str(runner), '--list-devices'], cwd=runner.parent, timeout=30)
        available = {kind.lower(): name for kind, name in re.findall(r'^([A-Z]+):0 "([^"]+)" \[(?:GPU|CPU)\]$', devices.stdout.replace('\r\n', '\n'), re.M)}
        device = ('cuda' if 'cuda' in available else 'cpu') if requested == 'auto' else requested
        if devices.returncode or device not in available:
            raise AppError('Selected device is unavailable in the sealed Higgs runner.')
        precision = gguf_precision(inside(Path(pack['_root']), pack['speech']['model']))
        if precision != pack['speech']['precision']:
            raise AppError('Actual Higgs precision differs from the sealed model.')
        return {'backend': 'higgs_audio_cpp', 'device': device, 'device_index': 0,
                'device_name': available[device],
                'precision': precision, 'runner_version': version,
                'implementation_sha256': implementation_fingerprint(__name__)}

    def render(self, payload):
        import numpy as np
        import soundfile as sf
        pack = payload['pack']
        voice = next((v for v in pack['voices'] if v['id'] == payload['voice_id']), None)
        if voice is None:
            raise SpeechHoldError('Assigned Higgs voice is absent; render is held.')
        actual = self.probe(payload)
        if actual != payload.get('resolved_speech'):
            raise AppError('Higgs device or precision changed; recompute the synthesis key before rendering.')
        plan = synthesis_plan(pack, voice, payload['text'], payload['delivery'], payload.get('controls'), actual, payload.get('take', 0))
        if plan.get('backend')=='nonverbal_asset':
            from .sound_events import render as render_event
            return render_event(pack,plan,Path(payload['output']))
        if len(plan['prompt']) >= plan['chunk_size']:
            raise GenerationCapError('Higgs request exceeds the sentence size limit; text was not split or clipped.')
        root = Path(pack['_root'])
        runner, model = inside(root, pack['speech']['runner']), inside(root, pack['speech']['model'])
        output = Path(payload['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.higgs-', dir=output.parent) as folder:
            wav = Path(folder) / 'output.wav'
            s = plan['sampling']
            command = [str(runner), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(model),
                       '--backend', actual['device'], '--device', '0', '--threads', str(plan['threads']),
                       '--max-tokens', str(plan['max_tokens']), '--seed', str(s['seed']),
                       '--temperature', str(s['temperature']), '--top-k', str(s['top_k']), '--top-p', str(s['top_p']),
                       '--repetition-penalty', str(s['repetition_penalty']), '--do-sample', str(s['do_sample']).lower(),
                       '--text-chunk-mode', plan['chunk_mode'], '--text-chunk-size', str(plan['chunk_size']),
                       '--text', plan['prompt'], '--voice-ref', str(inside(root, plan['reference']['audio'])),
                       '--reference-text', plan['reference']['text'], '--out', str(wav), '--out-format', 'pcm16', '--metrics']
            try:
                result = run_cli(command, cwd=runner.parent, timeout=900)
            except subprocess.TimeoutExpired as error:
                raise GenerationCapError('Higgs exceeded its time limit; no partial audio was published.') from error
            if result.returncode or not wav.is_file():
                raise AppError('Higgs synthesis failed: ' + result.stderr[-1500:])
            audio, rate = sf.read(wav, dtype='float32', always_2d=True)
            gain=plan.get('voice_signature',{}).get('gain',1.)
            if type(gain) not in (int,float) or not math.isfinite(gain) or not 10**(-9/20)<=gain<=10**(9/20):
                raise SpeechHoldError('Frozen voice mastering gain is invalid.')
            if gain!=1.:
                mastered=audio*gain
                if float(np.max(np.abs(mastered)))>=.999:
                    raise SpeechHoldError('Frozen mastering gain would clip this take; candidate is held.')
                sf.write(wav,mastered,rate,subtype='PCM_16')
                audio,rate=sf.read(wav,dtype='float32',always_2d=True)
            if audio.shape[1] != 1 or not np.isfinite(audio).all() or not .35 <= len(audio) / rate < 300:
                raise SpeechHoldError('Higgs output failed format, finite-sample or duration checks.')
            peak, rms = float(np.max(np.abs(audio))), float(np.sqrt(np.mean(audio ** 2)))
            active = float(np.mean(np.abs(audio) > .001))
            clipped = float(np.mean(np.abs(audio) >= .999))
            if rms < .0001 or active < .01 or clipped > .01:
                raise SpeechHoldError('Higgs output failed silence, level or clipping checks; render is held.')
            # Higgs v3's codec produces 25 frames/s (Qwen legacy uses 12.5).
            if len(audio) * plan['codec_frame_rate'] >= (plan['max_tokens'] - 8) * rate * .98:
                raise GenerationCapError('Higgs reached its token cap; text and controls were kept unchanged.')
            from .audio_join import boundary_check
            edges = boundary_check(audio[:, 0], rate, plan['boundary_quality']['policy'])
            if edges['unsafe_start'] or edges['unsafe_end']:
                raise SpeechHoldError('Higgs has an active abrupt audio edge; the candidate is held.')
            atomic_write(output, wav.read_bytes())
        return {'duration': len(audio) / rate, 'sample_rate': rate, 'samples': len(audio), 'warning': '',
                'synthesis': {**plan, 'plan_sha256': digest(plan)},
                'audio_checks': {'peak': peak, 'rms': rms, 'active_sample_fraction': active, 'clipped_sample_fraction': clipped,
                                 'spoken_words': 'not_verified', 'speaker_identity': 'not_verified', 'boundaries': edges}}
