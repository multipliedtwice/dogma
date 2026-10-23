"""Source-bound listener pronunciation revisions for a playable partial chapter."""
from __future__ import annotations
import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import file_digest,digest,write_json
from rimsky.spoken_pronunciation import unmarked,validate_marks,phrase_stress
from rimsky.audio_join import boundary_check,prepare_clip

def read(p):return json.loads(Path(p).read_text('utf-8'))


def tolerate_name_check(check, source_text, respellings=None):
    """Apply the production proper-name ASR policy without hiding its evidence."""
    if check.get('status') != 'spoken_text_mismatch':return check
    from rimsky.name_tolerance import tolerance,RULE
    from rimsky.speech_quality import transcript_check
    expected=list(check.get('expected_words') or [])
    heard=list(check.get('heard_words') or [])
    corrected,deviations=tolerance(expected,heard,source_text,respellings)
    if not deviations or corrected != expected:return check
    verifier={
        'primary_verifier_sha256':check.get('verifier_sha256'),
        'tolerance_rule':RULE,
        'tolerance_sha256':file_digest(ROOT/'runtime/rimsky/name_tolerance.py'),
        'scope':'proper name spelling only; listener stress still requires the new audio',
    }
    resolved=transcript_check(source_text,' '.join(corrected),check['audio_sha256'],digest(verifier),
                              normalize_elongations=True)
    return {**resolved,'synthesis_key':check['synthesis_key'],
        'heard_raw':check.get('heard_raw',check.get('heard','')),
        'primary_disagreement':check,'tolerated_deviations':deviations,
        'audio':check.get('audio'),'verifier':verifier,'human_approval':False}


def retry_revision(folder,request_path,request):
    """Retry sound failures without changing the listener's requested wording."""
    base=request.get('seed_offset',0)
    if type(base) is not int or not 0<=base<=1000:raise ValueError('Invalid pronunciation retake offset')
    attempts=[]
    for attempt in range(3):
        path=request_path
        if attempt:
            candidate=copy.deepcopy(request)
            candidate['seed_offset']=(base+attempt)%1001
            candidate['automatic_retake']={'request_sha256':file_digest(request_path),'attempt':attempt,'limit':3}
            path=folder/'pronunciation-retry-inputs'/(digest(candidate)+'.json')
            if path.exists() and read(path)!=candidate:raise ValueError('Frozen pronunciation retry changed')
            if not path.exists():write_json(path,candidate)
        try:
            subprocess.run([sys.executable,'-X','utf8','-u',str(Path(__file__).resolve()),
                '--folder',str(folder),'--request',str(path),'--single-attempt'],cwd=ROOT,check=True,timeout=1200)
            attempts.append({'attempt':attempt,'request_sha256':file_digest(path),'status':'applied'})
            write_json(folder/'pronunciation-retry-reports'/(file_digest(request_path)+'.json'),{'attempts':attempts})
            return
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
            attempts.append({'attempt':attempt,'request_sha256':file_digest(path),'status':'held','error':str(exc)})
            write_json(folder/'pronunciation-retry-reports'/(file_digest(request_path)+'.json'),{'attempts':attempts})
    raise ValueError('Pronunciation revision remains held after three verified-take attempts')


def apply_batch(folder,request):
    revisions=request['revisions']
    if not isinstance(revisions,list) or not revisions:raise ValueError('Empty pronunciation revision batch')
    ids=[]
    for revision in revisions:
        if (revision.get('generation')!=request['generation'] or revision.get('source_sha256')!=request['source_sha256']
            or 'revisions' in revision):raise ValueError('Pronunciation batch escaped its generation')
        ids.append(revision['before']['unit']['id'])
    if len(set(ids))!=len(ids):raise ValueError('Duplicate pronunciation batch unit')
    records=[];failures=[]
    for revision in revisions:
        uid=revision['before']['unit']['id'];path=folder/'pronunciation-batch-inputs'/(digest(revision)+'.json')
        if path.exists() and read(path)!=revision:raise ValueError('Frozen pronunciation batch input changed')
        if not path.exists():write_json(path,revision)
        try:
            subprocess.run([sys.executable,'-X','utf8','-u',str(Path(__file__).resolve()),
                '--folder',str(folder),'--request',str(path)],cwd=ROOT,check=True,timeout=1200)
            records.append({'unit_id':uid,'request_sha256':file_digest(path),'status':'applied'})
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
            failures.append(uid);records.append({'unit_id':uid,'request_sha256':file_digest(path),'status':'held','error':str(exc)})
    write_json(folder/'pronunciation-batches'/(digest(request)+'.json'),{'revisions':records,'human_audio_approval':False})
    if failures:raise ValueError('Pronunciation batch retains held revisions: '+', '.join(failures))

def validate_revision(before,after,request,replacement_model_sha256=None):
    import soundfile as sf
    old,new=before['unit'],after['unit']
    for k in ('id','source_id','start','end','chapter_id','speaker','voice','render_voice','mood','text','palette_reference','palette_assignment'):
        if old.get(k)!=new.get(k):raise ValueError('Pronunciation revision changed identity/source: '+k)
    a,b=old['synthesis'],new['synthesis']
    native=request['native_text'];plain=unmarked(native)
    # A terminal period may complete a short answer before its attribution;
    # every original word remains. No phonetic respelling is permitted.
    comma_phrasing=(request.get('recovery_kind')=='listener_comma_phrasing'
        and ',' in old['spoken'] and plain==old['spoken'].replace(',',''))
    synthesis_bound=(request.get('recovery_kind')=='listener_pronunciation_from_synthesis'
        and plain==unmarked(a['text']))
    equivalent=plain==old['spoken'] or comma_phrasing or synthesis_bound or (plain==old['spoken'].rstrip()+'.'
        and not old['spoken'].rstrip().endswith(('.', '!', '?', '…')))
    if not equivalent:raise ValueError('Pronunciation revision changed source words')
    validate_marks(request['native_text'])
    if b['text']!=request['native_text'] or b['prompt']!=b['text'] or b.get('effects_policy')!='disabled':
        raise ValueError('Pronunciation instruction not faithfully rendered')
    if b.get('phrase_pronunciation')!=a.get('phrase_pronunciation'):
        applied,provenance=phrase_stress(request['native_text'])
        if applied!=b['text'] or b.get('phrase_pronunciation')!=provenance:
            raise ValueError('Pronunciation revision changed phrase-pronunciation policy or evidence')
    for k in set(a)|set(b):
        # The aggregate pack fingerprint can include unused base-reference
        # metadata. Every resolved sound input, including reference/model/runner
        # hashes and sampling, must still match independently.
        if k=='sampling' and request.get('seed_offset'):
            offset=request['seed_offset']
            if type(offset) is not int or not 1<=offset<=1000:raise ValueError('Invalid pronunciation retake offset')
            expected={**a['sampling'],'seed':(a['sampling']['seed']+offset)%(2**31)}
            if b['sampling']!=expected:raise ValueError('Pronunciation retake changed other sampling inputs')
            continue
        if k=='model_sha256' and replacement_model_sha256 is not None:
            if b.get(k)!=replacement_model_sha256:
                raise ValueError('Pronunciation revision did not use the pinned production model')
            continue
        # Receipts made before the checkpoint label existed omit it. A new take
        # may add the label only when it names the pinned production checkpoint
        # and its model hash is that checkpoint's exact deployed file.
        if k=='model_checkpoint' and a.get(k)!=b.get(k):
            pin=read(ROOT/'resources/production-higgs-model.json')
            if b.get(k)!=pin['checkpoint'] or b.get('model_sha256')!=pin['model']['sha256']:
                raise ValueError('Pronunciation revision changed another sound input: '+k)
            continue
        # The listener instruction replaces automatic stress provenance for the
        # corrected token.  Its exact marked text and immutable instruction are
        # already bound above; retaining the contradicted automatic mark would
        # claim the old decision still produced the sound.
        if k not in ('text','prompt','spoken_text_plan','phrase_pronunciation','automatic_pronunciation','listener_respelling','pack') and a.get(k)!=b.get(k):
            raise ValueError('Pronunciation revision changed another sound input: '+k)
    receipt=after['receipt'];check=tolerate_name_check(after['word_check'],new['spoken'],((b.get('listener_respelling') or a.get('listener_respelling') or {}).get('records')));after['word_check']=check;wav=Path(after['path'])
    if (file_digest(wav)!=receipt['audio_sha256'] or digest(b)!=receipt['key'] or check['audio_sha256']!=receipt['audio_sha256']
        or check['synthesis_key']!=receipt['key'] or check['status']!='asr_exact_match'
        or check['expected_text_sha256']!=digest(new['spoken'].encode('utf-8'))):raise ValueError('Unverified pronunciation take')
    samples,rate=sf.read(wav,dtype='float32');edge=boundary_check(samples,rate)
    if rate!=24000 or edge['unsafe_start'] or edge['unsafe_end']:raise ValueError('Unsafe pronunciation take')
    return edge

def apply_revision_overrides(folder,chapter,selected):
    path=folder/'spoken-take-revisions.json'
    if not path.exists():return selected
    registry=read(path);manifest=read(folder/'manifest.json')
    if registry['generation']!=folder.name or registry['source_sha256']!=manifest['source_sha256']:
        raise ValueError('Pronunciation revisions belong to another book/generation')
    result=[]
    for before in selected:
        uid=before['unit']['id'];entry=registry['units'].get(uid)
        applied=folder/'pronunciation-revisions'/uid/'applied.json'
        if not entry or not applied.is_file() or before['unit']['chapter_id']!=chapter:
            result.append(before);continue
        instruction=Path(entry['instruction'])
        if not instruction.resolve().is_relative_to(folder.resolve()) or file_digest(instruction)!=entry['instruction_sha256']:
            raise ValueError('Pronunciation instruction changed')
        replacement=copy.deepcopy(entry['replacement'])
        if before['receipt']['key']==replacement['receipt']['key']:
            replacement['edge']=validate_revision(entry['before'],replacement,read(instruction))
            result.append(replacement);continue
        if before['receipt']['key']!=entry['previous_synthesis_key'] or before['receipt']['audio_sha256']!=entry['previous_audio_sha256']:
            raise ValueError('Original pronunciation take changed')
        replacement['edge']=validate_revision(before,replacement,read(instruction))
        result.append(replacement)
    return result

def patch_published_prefix(folder,cid,before,after):
    import soundfile as sf
    import numpy as np
    from render_higgs_palette_preview import PAGE,MEDIA,page
    from higgs_preview_music import attach_music
    state=read(folder/'progress.json');ready=state['chapters'][cid]
    old_path=MEDIA/Path(ready['stream']).name
    if file_digest(old_path)!=ready['wav_sha256']:raise ValueError('Published prefix changed before revision')
    from higgs_published_timeline import read_timeline,commit_publication
    timeline=read_timeline(folder,cid,ready)
    if len(timeline)!=ready['units']:raise ValueError('Published timeline does not match the prefix')
    target=next(r for r in timeline if r['id']==before['unit']['id']);old_end=target['end']
    x,rate=sf.read(old_path,dtype='int16');start=round(target['start']*rate);end=round(target['end']*rate)
    gains=read(folder/'frozen-gains.json');actor=before['unit']['speaker']+':'+before['unit']['render_voice'];gain=gains[actor]
    applied=folder/'pronunciation-revisions'/before['unit']['id']/'applied.json'
    if applied.exists():
        existing,existing_rate=sf.read(after['path'],dtype='float32');existing,_=prepare_clip(existing,existing_rate);existing*=gain
        expected=np.clip(np.floor(existing*32768),-32768,32767).astype('int32')
        if existing_rate==rate and len(existing)==end-start and np.max(np.abs(expected-x[start:end].astype('int32')))<=1:
            print('PRONUNCIATION ALREADY APPLIED',before['unit']['id'],flush=True);return
        # Other repaired sentences may have changed the chapter hash since this
        # take was installed. Its exact current PCM region proves idempotency.
        # A second listener correction can target an already patched sentence.
        # Fall through to the exact old-region proof before changing any PCM.
    raw,sr=sf.read(before['path'],dtype='float32')
    # Diagnostic one-shot publications deliberately preserved take 0 verbatim,
    # including a disclosed active edge.  Reproduce the exact publication
    # transform when proving the old PCM; do not reject or silently fade it now.
    # Every replacement still has to pass the normal strict edge gate above.
    if not (before.get('one_shot') and
            (before.get('edge',{}).get('unsafe_start') or before.get('edge',{}).get('unsafe_end'))):
        raw,_=prepare_clip(raw,sr)
    original=raw*gain
    # Prove the exact old spoken region, even while other retakes are being repaired.
    if sr!=rate or abs(len(original)-(end-start))>1:raise ValueError('Original clip does not fit its published timing')
    count=min(len(original),end-start)
    expected=np.clip(np.floor(original[:count]*32768),-32768,32767).astype('int32')
    if np.max(np.abs(expected-x[start:start+count].astype('int32')))>1:
        raise ValueError('Original clip differs from the published region')
    replacement,rr=sf.read(after['path'],dtype='float32');replacement,_=prepare_clip(replacement,rr);replacement*=gain
    if rr!=rate or np.max(np.abs(replacement))>=.999:raise ValueError('Revision would clip or change audio format')
    scratch=folder/'pronunciation-revisions'/before['unit']['id'];scratch.mkdir(parents=True,exist_ok=True)
    patch=scratch/'patched-prefix.wav'
    # Write unaffected PCM directly, retaining every other sample bit for bit.
    with sf.SoundFile(patch,'w',samplerate=rate,channels=1,subtype='PCM_16') as destination:
        destination.write(x[:start]);destination.write(replacement);destination.write(x[end:])
    sha=file_digest(patch);name=cid+'-'+sha[:12]+'.wav';destination=MEDIA/name
    if not destination.exists():
        building=destination.with_suffix('.building.wav');building.write_bytes(patch.read_bytes());building.replace(destination)
    elif file_digest(destination)!=sha:raise ValueError('Immutable pronunciation asset changed')
    new_data,_=sf.read(destination,dtype='int16');delta=(len(replacement)-(end-start))/rate
    assert np.array_equal(new_data[:start],x[:start])
    assert np.array_equal(new_data[start+len(replacement):],x[end:])
    for row in timeline:
        if row['id']==target['id']:row['end']=row['start']+len(replacement)/rate;row['word_status']=after['word_check']['status']
        elif row['start']>=old_end:row['start']+=delta;row['end']+=delta
    mp3=PAGE/(cid+'-'+sha[:12]+'.mp3')
    subprocess.run(['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(destination),
                    '-codec:a','libmp3lame','-b:a','128k',str(mp3)],check=True)
    ready.update(seconds=len(new_data)/rate,stream='/character-rb2/full/clips/elirm-iv-mvp/'+name,
        audio=mp3.name,wav_sha256=sha)
    ready.setdefault('pronunciation_revisions',[]).append(before['unit']['id'])
    ready['word_flags']=sum(r.get('word_status') not in ('asr_exact_match','nonverbal_not_spoken_text') for r in timeline)
    commit_publication(folder,cid,ready,timeline,state)
    write_json(scratch/'applied.json',{'previous_sha256':file_digest(old_path),'new_sha256':sha,
        'unit_id':before['unit']['id'],'unaffected_pcm_preserved':True,'source_text_unchanged':True,
        'stress_listening_verified':False,'word_verified':True})
    print('ONE SENTENCE UPDATED; all other published PCM preserved',before['unit']['id'],flush=True)

def main():
    from render_higgs_book_trial import MODEL_PIN,render,spoken_text
    from normalize_higgs_word_audit import normalize
    from regenerate_higgs_book_trial import cached_audit,wait_for_audio_resources
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--install-existing',action='store_true')
    p.add_argument('--existing-take',type=Path,
                   help='Install one already-rendered immutable candidate after independent verification.')
    p.add_argument('--single-attempt',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args();folder=args.folder.resolve();request=read(args.request)
    if request['generation']!=folder.name or request['source_sha256']!=read(folder/'manifest.json')['source_sha256']:
        raise ValueError('Instruction belongs to another source/generation')
    if 'revisions' in request:
        apply_batch(folder,request);return
    if not args.install_existing and not args.existing_take and not args.single_attempt:
        retry_revision(folder,args.request,request);return
    before=request['before'];uid=before['unit']['id'];cid=before['unit']['chapter_id']
    take=folder/'pronunciation-revisions'/uid/'take-0';take.mkdir(parents=True,exist_ok=True)
    unit={k:copy.deepcopy(v) for k,v in before['unit'].items() if k not in ('key','audio','synthesis','render_voice','spoken')}
    offset=request.get('seed_offset',0)
    if type(offset) is not int or not 0<=offset<=1000:raise ValueError('Invalid pronunciation retake offset')
    unit['render_seed']=(before['unit']['synthesis']['sampling']['seed']+offset)%(2**31)
    instruction=folder/'pronunciation-instructions'/(file_digest(args.request)+'.json')
    instruction.parent.mkdir(parents=True,exist_ok=True)
    if not instruction.exists():instruction.write_bytes(args.request.read_bytes())
    if file_digest(instruction)!=file_digest(args.request):raise ValueError('Immutable pronunciation instruction changed')
    if args.existing_take:
        take=args.existing_take.resolve()
        allowed=(folder/'pronunciation-revisions'/uid).resolve()
        if not take.is_relative_to(allowed) or not take.name.startswith('take-'):
            raise ValueError('Existing pronunciation take escaped its unit folder')
    elif not args.install_existing:
        take=folder/'pronunciation-revisions'/uid/('take-'+file_digest(instruction)[:12]+'-'+MODEL_PIN.sha256[:12]
            +'-'+file_digest(Path(__file__))[:8])
    take.mkdir(parents=True,exist_ok=True)
    # A progressively recovered/presentation-routed unit can live below a
    # chunk's immutable subfolder rather than its initial assignment root.
    assignments=[]
    for assignment_path in sorted((folder/cid).glob('chunk-*/**/assignment.json')):
        candidate=read(assignment_path)
        if any(u['id']==uid for u in candidate.get('units',[])):
            assignments.append(candidate)
    if not assignments:raise ValueError('Original pronunciation assignment is unavailable')
    original=assignments[0]
    policy=unit.get('spoken_number_policy','grouped-digits-v2')
    expected=spoken_text(unit['text'],policy)
    if unit.get('finish_fragment') and not expected.rstrip().endswith(('.', '!', '?', '…', ',', ':', ';')):expected+=','
    unit['spoken_text_plan']={'expected_spoken':expected,'native_text':request['native_text'],
        'evidence':{'source':'explicit_listener_instruction','path':str(instruction),'sha256':file_digest(instruction),
                    'revision_implementation_sha256':file_digest(Path(__file__)),
                    'spoken_edits_sha256':file_digest(ROOT/'runtime/rimsky/spoken_edits.py')},
        'scope':'desired stress, not approval of newly generated audio'}
    if request.get('recovery_kind')=='listener_comma_phrasing':
        if (',' not in before['unit']['spoken'] or
            request['native_text']!=before['unit']['spoken'].replace(',','')):
            raise ValueError('Comma phrasing revision changed more than commas')
        unit['spoken_text_plan']['recovery_kind']='listener_comma_phrasing'
        unit['spoken_text_plan']['scope']='listener-reported pause repair; new audio requires listening'
    previous_native=request.get('previous_native_text')
    if request.get('recovery_kind')=='listener_pronunciation_from_synthesis':
        if previous_native != before['unit']['synthesis']['text']:
            raise ValueError('Listener pronunciation request changed its previous native input')
        unit['spoken_text_plan']['recovery_kind']='listener_pronunciation_from_synthesis'
        unit['spoken_text_plan']['previous_native_text']=previous_native
    elif unmarked(request['native_text'])==expected.rstrip()+'.':
        unit['spoken_text_plan']['recovery_kind']='listener_pronunciation'
    assignment={**original,'units':[unit]};path=take/'assignment.json'
    if not args.install_existing and not args.existing_take:
        write_json(path,assignment);render(path,take,[cid],execution_guard=wait_for_audio_resources)
    # Never load an expensive recognizer for a take already known to be cut off.
    import soundfile as sf
    rendered=read(take/'receipts.json')['takes'][uid]
    samples,rate=sf.read(take/rendered['audio'],dtype='float32')
    edges=boundary_check(samples,rate)
    if edges['unsafe_start'] or edges['unsafe_end']:raise ValueError('Unsafe pronunciation take')
    audit=cached_audit(take)
    if (args.install_existing or args.existing_take) and audit is None:
        raise ValueError('Existing pronunciation take has no bound word audit')
    if audit is None:
        wait_for_audio_resources()
        subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',str(ROOT/'scripts/audit_higgs_book_cuda.py'),
                        '--folders',str(take)],cwd=ROOT,check=True);audit=normalize(take)
    new=read(take/'render-plan.json')['units'][0];receipt=read(take/'receipts.json')['takes'][uid]
    after={'unit':new,'receipt':receipt,'path':str(take/receipt['audio']),'word_check':audit['checks'][uid]}
    if after['word_check']['status']!='asr_exact_match':
        from higgs_secondary_verification import apply_secondary,report_paths
        after=apply_secondary({uid:after},report_paths(take))[uid]
    after['edge']=validate_revision(before,after,request,MODEL_PIN.sha256)
    registry_path=folder/'spoken-take-revisions.json'
    registry=read(registry_path) if registry_path.exists() else {'schema':1,'generation':folder.name,
             'source_sha256':request['source_sha256'],'units':{}}
    registry['units'][uid]={'previous_synthesis_key':before['receipt']['key'],'previous_audio_sha256':before['receipt']['audio_sha256'],
        'instruction':str(instruction),'instruction_sha256':file_digest(instruction),'replacement':after,
        'before':before,'audio_listening_approved':False}
    published_before=request.get('published_before',before)
    if published_before is not before:
        previous=read(registry_path)['units'].get(uid) if registry_path.exists() else None
        same_take=previous and previous['replacement']['receipt']==after['receipt']
        if not same_take and (not previous or previous['replacement']!=published_before):
            raise ValueError('Repeated pronunciation correction is not bound to the prior verified take')
    write_json(registry_path,registry);patch_published_prefix(folder,cid,published_before,after)

if __name__=='__main__':main()
