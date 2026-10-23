"""Bounded same-palette repairs from independently checked Opus proposals."""
from __future__ import annotations
from regenerate_higgs_book_trial import primary_audit_command
import copy
import subprocess
from pathlib import Path
from rimsky.util import write_json,file_digest,digest,AppError
from rimsky.spoken_edits import apply_spoken_edit
from higgs_trial_io import read_json as read

class SectionHeld(ValueError):pass

def _saved_diagnosis_is_bound(out,unit):
    """Return whether a frozen retry still cites its original, bound diagnosis."""
    plan=unit.get('spoken_text_plan',{});evidence=plan.get('evidence',{})
    digest=evidence.get('diagnosis_sha256')
    if not isinstance(digest,str) or not digest:return False
    generation=next((p for p in (out,*out.parents) if (p/'manifest.json').exists()),None)
    candidates=list(out.glob('opus-diagnosis-v2*/answer.json'))
    if generation is not None:candidates.append(generation/'word-adjudication-01'/'answer.json')
    for answer_path in candidates:
        request_path=answer_path.parent/'request.json'
        if not answer_path.is_file() or not request_path.is_file():continue
        try:
            if file_digest(answer_path)!=digest:continue
            answer=read(answer_path)
            if answer.get('request_sha256')!=file_digest(request_path):continue
            diagnoses=answer.get('answer',{}).get('diagnoses',[])
        except (OSError,ValueError,TypeError):
            continue
        for diagnosis in diagnoses:
            if (diagnosis.get('unit_id')==unit.get('id')
                and plan.get('native_text') in diagnosis.get('native_candidates',[])
                and evidence.get('source_evidence')==diagnosis.get('source_evidence')):
                return True
    return False

def frozen_recovery_matches(out,saved,proposed):
    """Allow a refreshed diagnosis to reuse identical, immutably bound retry input."""
    immutable=('id','source_id','start','end','text','speaker','voice','mood',
               'analysis_parts','palette_reference','palette_assignment')
    if any(saved.get(key)!=proposed.get(key) for key in immutable):return False
    saved_plan=saved.get('spoken_text_plan',{});proposed_plan=proposed.get('spoken_text_plan',{})
    if any(saved_plan.get(key)!=proposed_plan.get(key) for key in ('expected_spoken','native_text')):return False
    if not _saved_diagnosis_is_bound(out,saved):return False
    # The old evidence remains authoritative. Everything else, including the deterministic
    # render seed and recovery recipe, must still be byte-for-byte equivalent as JSON data.
    comparable=copy.deepcopy(proposed)
    comparable.setdefault('spoken_text_plan',{})['evidence']=copy.deepcopy(saved_plan.get('evidence'))
    return comparable==saved

def recovery_take(out,attempt,candidate,assignment):
    """Reuse an identical frozen take, or preserve it and open an immutable revision."""
    take=out/'word-opus-recovery'/f'take-{attempt}';path=take/'assignment.json'
    if not path.exists():return take,path,candidate
    saved=read(path);allowed={r['id'] for r in assignment['units']};proposed={u['id']:u for u in candidate['units']}
    if any(u['id'] not in allowed for u in saved['units']):raise ValueError('Frozen Opus retry escaped batch')
    if all(u['id'] not in proposed or frozen_recovery_matches(out,u,proposed[u['id']]) for u in saved['units']):
        return take,path,saved
    # A refreshed provider answer is new evidence, not permission to overwrite an old render.
    # Give it a content-addressed directory so both requests, answers and audio remain auditable.
    take=out/'word-opus-recovery'/f'take-{attempt}-{digest(candidate)[:12]}';path=take/'assignment.json'
    if path.exists():
        saved=read(path)
        if saved!=candidate:raise ValueError('Versioned Opus recovery changed')
        candidate=saved
    return take,path,candidate

def independent_check(out,selected):
    from render_higgs_palette_preview import ROOT,wait_for_audio_resources
    from higgs_secondary_verification import cached_secondary,report_paths,raw_witness_coverage
    before=cached_secondary(out,selected)
    if all(r['word_check']['status'] in ('asr_exact_match','nonverbal_not_spoken_text') for r in before.values()):return before
    unresolved={uid:row for uid,row in before.items() if row['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text')}
    if set(unresolved)<=raw_witness_coverage(unresolved,report_paths(out),file_digest(ROOT/'scripts/audit_higgs_secondary_asr.py')):
        print('REUSED BOUND INDEPENDENT TRANSCRIPTS',len(unresolved),'disagreements retained',flush=True)
        return before
    # A separate recognizer receives audio only. All words still have to match.
    write_json(out/'secondary-selected.json',before)
    from audit_higgs_secondary_asr import output_folder
    wait_for_audio_resources()
    try:
        subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
            str(ROOT/'scripts/audit_higgs_secondary_asr.py'),'--selected',str(out/'secondary-selected.json'),
            '--output',str(output_folder(out))],cwd=ROOT,check=True)
    except (OSError,subprocess.CalledProcessError) as exc:
        write_json(out/'secondary-asr-failed.json',{'error':str(exc),'primary_checks_retained':True})
        return before
    return cached_secondary(out,before)

def proposals(out,selected):
    from adjudicate_higgs_word_flags import packet
    from rimsky.word_recovery import diagnose_fresh,RefreshCapReached
    generation=next((p for p in out.parents if (p/'manifest.json').exists()),None)
    if generation is None:return {},None
    folder=generation.parent;book=read(folder/'source/book.json')
    blocks={b['id']:b['text'] for c in book['chapters'] for b in c['blocks']}
    known=generation/'word-adjudication-01'
    available={};evidence={}
    if (known/'answer.json').exists():
        saved=read(known/'answer.json');bound=read(known/'request.json')
        if saved['request_sha256']!=file_digest(known/'request.json'):raise ValueError('Unbound saved Opus diagnosis')
        if bound['request']['source_sha256']!=file_digest(folder/'source/book.json'):raise ValueError('Opus source changed')
        request_units={r['unit_id']:r for r in bound['request']['units']}
        for d in saved['answer']['diagnoses']:
            uid=d['unit_id']
            if uid not in selected:continue
            item=selected[uid];source=request_units[uid]
            if (source['spoken']==item['unit']['spoken'] and source['paragraph']==blocks[item['unit']['source_id']]
                and source['audio_sha256']==item['word_check']['audio_sha256']
                and source['synthesis_key']==item['word_check']['synthesis_key']):
                available[uid]=d;evidence[uid]=file_digest(known/'answer.json')
    missing={uid:r for uid,r in selected.items() if r['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text')
             and uid not in available and not r['unit'].get('spoken_text_plan')}
    if missing:
        path=out/'opus-selected.json';write_json(path,missing)
        request=packet(folder,[path]);request['citation_contract']='literal-source-v2';
        try:
            result,destination=diagnose_fresh(out/'opus-diagnosis-v2',request)
        except (ValueError,AppError) as exc:
            write_json(out/'opus-diagnosis-failed.json',{'error':str(exc),'unit_ids':list(missing),
                'existing_valid_diagnoses_retained':list(available),'audio_gate_unchanged':True,
                **({'refresh_cap_reached':True} if isinstance(exc,RefreshCapReached) else {})})
        else:
            for d in result['answer']['diagnoses']:
                available[d['unit_id']]=d;evidence[d['unit_id']]=file_digest(destination/'answer.json')
    return available,evidence

def prior_verified_takes(out,assignment,selected):
    """Recover already verified solutions before diagnosing the same failure again."""
    from render_higgs_palette_preview import cached_audit,boundary_check,spoken_text
    import soundfile as sf
    originals={u['id']:u for u in assignment['units']}
    for stage in ('word-omission-recovery','word-phrasing-recovery','word-opus-recovery'):
        for path in sorted((out/stage).glob('take-*/render-plan.json')):
            plan=read(path);take=path.parent
            pending=[u for u in plan['units'] if u['id'] in selected and selected[u['id']]['word_check']['status']!='asr_exact_match']
            if not pending:continue
            candidate=read(take/'assignment.json')
            if any(candidate.get(k)!=assignment.get(k) for k in ('source_sha256','bank_sha256','effects_policy')):
                raise ValueError('Earlier repair belongs to different generation inputs')
            report=cached_audit(take)
            if report is None:continue
            receipts=read(take/'receipts.json')
            for unit in pending:
                uid=unit['id'];original=originals[uid]
                if uid not in receipts['takes'] or uid not in report['checks']:continue
                if any(unit.get(k)!=original.get(k) for k in ('text','source_id','start','end','speaker','voice','mood')):
                    raise ValueError('Earlier repair changed source or voice assignment')
                check=report['checks'][uid];receipt=receipts['takes'][uid];wav=take/receipt['audio']
                if check['audio_sha256']!=file_digest(wav) or check['synthesis_key']!=receipt['key']:
                    raise ValueError('Earlier verified repair audio changed')
                if check['status']!='asr_exact_match':
                    # A repair verified through the bound witness reports and rules (glossary, homophone,
                    # numeral) counts as before; otherwise a resumed section lost it when the stage could
                    # not re-run its diagnosis (c0002-u0056, chapters 1-3 run, 2026-10-17).
                    from higgs_secondary_verification import cached_secondary
                    check=cached_secondary(out,{uid:{'unit':unit,'receipt':receipt,'path':str(wav),'word_check':check}})[uid]['word_check']
                    if check['status']!='asr_exact_match':continue
                if unit.get('palette_reference',{}).get('audio_sha256') not in original.get('palette_assignment',{}).get('allowed_reference_sha256',[]):
                    raise ValueError('Earlier repair escaped the allowed reference palette')
                samples,rate=sf.read(wav,dtype='float32');edge=boundary_check(samples,rate)
                if edge['unsafe_start'] or edge['unsafe_end']:continue
                selected[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
                print('REUSED VERIFIED WORD REPAIR',uid,take,flush=True)
    return selected

def recover(out,assignment,chapter,selected,groups):
    from render_higgs_palette_preview import retry_units,render,cached_audit,normalize,ROOT,wait_for_audio_resources,boundary_check
    from higgs_secondary_verification import cached_secondary
    import soundfile as sf
    selected=prior_verified_takes(out,assignment,selected)
    selected=independent_check(out,selected)
    diagnoses,evidence=proposals(out,selected)
    originals={r['id']:r for r in assignment['units']}
    prepared=[]
    for attempt in range(6):
        rows=[]
        for uid,item in selected.items():
            if item['word_check']['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            if uid not in diagnoses or originals[uid].get('spoken_text_plan'):continue
            variants=diagnoses[uid]['native_candidates'];index=attempt//2
            if index>=len(variants):continue
            candidate=retry_units([originals[uid]],21+attempt,groups)[0]
            plan={'expected_spoken':item['unit']['spoken'],'native_text':variants[index],
                  'recovery_kind':'opus_word_recovery','evidence':{'diagnosis_sha256':evidence[uid],
                   'source_evidence':diagnoses[uid]['source_evidence']},
                  'scope':'automatic candidate; words and audio edges must verify; no human approval'}
            try:apply_spoken_edit(plan['expected_spoken'],plan)
            except ValueError as exc:
                write_json(out/f'opus-rejected-{uid}-{attempt}.json',{'proposal':plan,'error':str(exc)})
                continue
            candidate['spoken_text_plan']=plan;rows.append(candidate)
        if not rows:continue
        candidate={**assignment,'units':rows}
        take,path,candidate=recovery_take(out,attempt,candidate,assignment);take.mkdir(parents=True,exist_ok=True)
        write_json(path,candidate)
        print('OPUS WORD RECOVERY',chapter,attempt,[u['id'] for u in rows],flush=True)
        prepared.append((path,take,[chapter]))
    # Attempts are independent (an attempt only skips units already verified): one native session and one
    # audit process for all of them; acceptance still runs in attempt order (2026-10-17).
    from higgs_exhausted_word_recovery import render_candidates,audit_candidates
    from render_higgs_book_trial import render_many
    render_candidates(prepared,render_many,wait_for_audio_resources)
    reports=audit_candidates([take for _,take,_ in prepared])
    for path,take,_ in prepared:
        report=reports[take]
        if report is None:continue
        plan=read(take/'render-plan.json');receipts=read(take/'receipts.json');unresolved={}
        for unit in plan['units']:
            if unit['id'] not in receipts['takes'] or unit['id'] not in report['checks']:continue
            uid=unit['id'];receipt=receipts['takes'][uid];check=report['checks'][uid];wav=take/receipt['audio']
            if check['audio_sha256']!=file_digest(wav) or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound Opus repair audio')
            samples,rate=sf.read(wav,dtype='float32');edges=boundary_check(samples,rate)
            if edges['unsafe_start'] or edges['unsafe_end']:continue
            row={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edges,'word_check':check}
            if selected[uid]['word_check']['status']=='asr_exact_match':continue
            if check['status']=='asr_exact_match':selected[uid]=row
            else:unresolved[uid]=row
        if unresolved:
            checked=independent_check(take,unresolved)
            for uid,row in checked.items():
                if row['word_check']['status']=='asr_exact_match':selected[uid]=row
        selected=cached_secondary(out,selected)
        write_json(out/'selected.json',selected)
    write_json(out/'autoheal-report.json',{'resolved_words':sum(r['word_check']['status']=='asr_exact_match' for r in selected.values()),
               'unresolved':[uid for uid,r in selected.items() if r['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text')],
               'diagnosis_ids':list(diagnoses),'human_approval':False})
    return selected
