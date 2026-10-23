"""Independent exact-word checks of bounded recovery takes before a section hold."""
from __future__ import annotations
from regenerate_higgs_book_trial import primary_audit_command
import copy
import subprocess
from pathlib import Path
from higgs_trial_io import read_json as read
from higgs_word_omissions import clear_omission
from higgs_secondary_verification import apply_secondary
from higgs_opus_word_recovery import frozen_recovery_matches
from rimsky.util import file_digest,write_json,digest,AppError
from rimsky.native_progress import CandidateRenderTimeout,CandidateRenderFailure

def render_candidate(path,take,chapter,render,guard):
    failed=next((p for p in (take/'candidate-render-timeout.json',take/'candidate-render-failure.json') if p.exists()),None)
    if failed:
        if read(failed)['plan_sha256']!=file_digest(take/'render-plan.json'):
            raise ValueError('Candidate timeout belongs to a different native plan')
        print('PRESERVED TIMED-OUT CANDIDATE; advancing to a different remedy',take,flush=True)
        return
    chapters=[chapter] if isinstance(chapter,str) else chapter
    try:render(path,take,chapters,execution_guard=guard)
    except (CandidateRenderTimeout,CandidateRenderFailure):
        if (take/'render-plan.json').exists():
            marker=take/'candidate-render-failure.json'
            if not marker.exists() and not (take/'candidate-render-timeout.json').exists():
                write_json(marker,{'plan_sha256':file_digest(take/'render-plan.json'),
                    'completed_takes_preserved':True,'retry_identical_plan':False})
        print('BOUNDED CANDIDATE TIMEOUT; completed takes preserved',take,flush=True)

def audit_candidate(take):
    from render_higgs_palette_preview import cached_audit,normalize,wait_for_audio_resources,ROOT
    receipt_path=take/'receipts.json'
    if not receipt_path.exists() or not read(receipt_path)['takes']:return None
    report=cached_audit(take)
    if report is not None:return report
    try:
        wait_for_audio_resources()
        subprocess.run(primary_audit_command(take),cwd=ROOT,check=True)
    except (OSError,subprocess.CalledProcessError) as exc:
        write_json(take/'candidate-audit-failure.json',{'plan_sha256':file_digest(take/'render-plan.json'),
            'receipts_sha256':file_digest(receipt_path),'error':str(exc),'audio_gate_unchanged':True})
        return None
    return normalize(take)

def render_candidates(items,render_many,guard):
    """items: [(assignment_path,take,chapter)]. Like render_candidate per item, one native session for all.

    Each take folder cost a 15-20 s model load for ~4.5 s of generation (2026-10-17).
    """
    jobs=[]
    for path,take,chapter in items:
        failed=next((p for p in (take/'candidate-render-timeout.json',take/'candidate-render-failure.json') if p.exists()),None)
        if failed:
            if read(failed)['plan_sha256']!=file_digest(take/'render-plan.json'):
                raise ValueError('Candidate timeout belongs to a different native plan')
            print('PRESERVED TIMED-OUT CANDIDATE; advancing to a different remedy',take,flush=True)
            continue
        jobs.append((path,take,[chapter] if isinstance(chapter,str) else chapter))
    if not jobs:return
    # render_many writes the timeout/failure marker into the folder each failing request belongs to.
    try:render_many([(p,t,c) for p,t,c in jobs],execution_guard=guard)
    except (CandidateRenderTimeout,CandidateRenderFailure):
        print('BOUNDED CANDIDATE TIMEOUT; completed takes preserved',[str(t) for _,t,_ in jobs],flush=True)

def audit_candidates(takes):
    """One primary-audit process (one model load, ~33 s) for every take lacking a cached audit."""
    from render_higgs_palette_preview import cached_audit,normalize,wait_for_audio_resources,ROOT
    reports={};pending=[]
    for take in takes:
        receipt_path=take/'receipts.json'
        if not receipt_path.exists() or not read(receipt_path)['takes']:reports[take]=None;continue
        report=cached_audit(take)
        if report is not None:reports[take]=report
        else:pending.append(take)
    if pending:
        try:
            wait_for_audio_resources()
            command=primary_audit_command(pending[0]);at=command.index('--folders')+1
            subprocess.run([*command[:at],*[str(t) for t in pending],*command[at+1:]],cwd=ROOT,check=True)
        except (OSError,subprocess.CalledProcessError) as exc:
            for take in pending:
                write_json(take/'candidate-audit-failure.json',{'plan_sha256':file_digest(take/'render-plan.json'),
                    'receipts_sha256':file_digest(take/'receipts.json'),'error':str(exc),'audio_gate_unchanged':True})
                reports[take]=None
            return {take:reports[take] for take in takes}
        for take in pending:reports[take]=normalize(take)
    return {take:reports[take] for take in takes}

def futile_units(out,uids):
    """Units whose >=3 earlier flagged primary transcripts (any folder of their section) are all identical.

    Across all generations decoding recovery verified 0 of 60 such sentences; its 4 historical
    rescues were names now covered by the proper-noun slot rule (2026-10-17). Not acceptance.
    """
    import re
    seen={}
    # Every take of the unit: the section's takes and recovery folders, plus the generation-level batched
    # and delegated rounds (u0291 had 2 of its 8 identical takes in take-* folders, full run 2026-10-17).
    generation=next((p for p in out.parents if (p/'manifest.json').exists()),None)
    roots=[out]+([generation/'batched-recovery',generation/'delegated-revisions'] if generation else [])
    for path in sorted(p for root in roots if root.exists() for p in root.rglob('word-checks-1.7B.json')):
        for uid,check in read(path)['checks'].items():
            if uid in uids and check['status'] not in ('asr_exact_match','nonverbal_not_spoken_text'):
                seen.setdefault(uid,[]).append(' '.join(re.findall(r'\w+',check.get('heard','').lower().replace('ё','е'))))
    return {uid:heard for uid,heard in seen.items() if len(heard)>=3 and len(set(heard))==1}


def unresolved(row):
    return row['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text')


def exhausted_recovery_take(out,attempt,candidate,assignment):
    """Reuse an identical frozen take, or preserve it under a content revision."""
    take=out/'word-exhausted-stress-recovery'/f'take-{attempt}';path=take/'assignment.json'
    if not path.exists():return take,path,candidate
    saved=read(path);allowed={r['id'] for r in assignment['units']};proposed={u['id']:u for u in candidate['units']}
    if any(u['id'] not in allowed for u in saved['units']):
        raise ValueError('Frozen exhausted stress retry escaped batch')
    if all(u['id'] not in proposed or frozen_recovery_matches(out,u,proposed[u['id']]) for u in saved['units']):
        return take,path,saved
    # A refreshed diagnosis is new immutable evidence. Never overwrite the old
    # assignment/audio; give the new proposal its own content-addressed take.
    take=out/'word-exhausted-stress-recovery'/f'take-{attempt}-{digest(candidate)[:12]}';path=take/'assignment.json'
    if path.exists():
        saved=read(path)
        if saved!=candidate:raise ValueError('Versioned exhausted stress recovery changed')
        candidate=saved
    return take,path,candidate


def candidates(out,assignment,selected):
    import soundfile as sf
    from render_higgs_palette_preview import boundary_check,cached_audit
    originals={u['id']:u for u in assignment['units']}
    missing={uid for uid,row in selected.items() if unresolved(row)}
    rows={}
    def add(uid,row):
        original=originals[uid];unit=row['unit'];receipt=row['receipt'];check=row['word_check']
        if any(unit.get(k)!=original.get(k) for k in ('text','source_id','start','end','speaker','voice','mood')):
            raise ValueError('Exhausted recovery changed source or palette assignment')
        if unit.get('palette_reference',{}).get('audio_sha256') not in original.get('palette_assignment',{}).get('allowed_reference_sha256',[]):
            raise ValueError('Exhausted recovery escaped its reference palette')
        wav=Path(row['path'])
        if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
            raise ValueError('Unbound exhausted recovery take')
        if digest(unit['synthesis'])!=receipt['key'] or unit['synthesis']['prompt']!=unit['synthesis']['text']:
            raise ValueError('Changed synthesis inputs or disabled vocal effects in recovery take')
        data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
        if edge['unsafe_start'] or edge['unsafe_end']:return
        rows[(uid,receipt['key'])]={**copy.deepcopy(row),'edge':edge}
    for uid in missing:add(uid,selected[uid])
    for path in sorted(out.glob('**/render-plan.json')):
        take=path.parent
        if not (take/'receipts.json').exists():continue
        units=[u for u in read(path)['units'] if u['id'] in missing]
        if not units:continue
        saved=read(take/'assignment.json')
        if any(saved.get(k)!=assignment.get(k) for k in ('source_sha256','bank_sha256','effects_policy')):
            raise ValueError('Exhausted recovery belongs to different generation inputs')
        report=cached_audit(take)
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in units:
            uid=unit['id']
            if uid not in receipts:continue
            receipt=receipts[uid]
            add(uid,{'unit':unit,'receipt':receipt,'path':str(take/receipt['audio']),
                     'word_check':report['checks'][uid]})
    return rows


def native_recovery(out,assignment,selected,groups):
    """A different bounded remedy: source-bound stress proposals after punctuation failed."""
    import soundfile as sf
    from adjudicate_higgs_word_flags import packet
    from rimsky.word_recovery import diagnose_fresh,RefreshCapReached
    from rimsky.spoken_edits import apply_spoken_edit
    from render_higgs_palette_preview import retry_units,render,cached_audit,normalize,ROOT,wait_for_audio_resources,boundary_check
    missing={uid:row for uid,row in selected.items() if unresolved(row)}
    generation=next(p for p in out.parents if (p/'manifest.json').exists())
    source=out/'exhausted-opus-selected.json';write_json(source,missing)
    request=packet(generation.parent,[source]);request['citation_contract']='literal-source-v2'
    request['recovery_constraints']=('Bounded seed/reference and earlier remedies did not verify these words. '
        'Propose different source-preserving acute-stress or connected-phrasing candidates for unclear words or names. '
        'Avoid variants already tried in the supplied history. Unknown canonical name stress remains explicitly unapproved. '
        'Text cannot certify the audio; each candidate must pass independent word and edge checks.')
    try:answer,used=diagnose_fresh(out/'opus-exhausted-stress-v1',request)
    except (ValueError,AppError) as exc:
        write_json(out/'exhausted-opus-failed.json',{'error':str(exc),'audio_gate_unchanged':True,
            **({'refresh_cap_reached':True} if isinstance(exc,RefreshCapReached) else {})});return selected
    diagnoses={d['unit_id']:d for d in answer['answer']['diagnoses']}
    originals={u['id']:u for u in assignment['units']};result=copy.deepcopy(selected)
    prepared=[]
    for attempt in range(6):
        units=[]
        for uid,row in missing.items():
            if result[uid]['word_check']['status']=='asr_exact_match':continue
            variants=diagnoses[uid]['native_candidates'];index=attempt//2
            if index>=len(variants):continue
            unit=retry_units([originals[uid]],31+attempt,groups)[0]
            if 'spoken_number_policy' in row['unit']:unit['spoken_number_policy']=row['unit']['spoken_number_policy']
            plan={'expected_spoken':row['unit']['spoken'],'native_text':variants[index],
                'recovery_kind':'opus_word_recovery','evidence':{'diagnosis_sha256':file_digest(used/'answer.json'),
                    'source_evidence':diagnoses[uid]['source_evidence']},
                'scope':'Unapproved stress candidate; independent words and edges must pass'}
            apply_spoken_edit(plan['expected_spoken'],plan);unit['spoken_text_plan']=plan;units.append(unit)
        if not units:continue
        proposed={**assignment,'units':units}
        take,path,proposed=exhausted_recovery_take(out,attempt,proposed,assignment)
        if not path.exists():write_json(path,proposed)
        print('EXHAUSTED STRESS RECOVERY',attempt,[u['id'] for u in units],flush=True)
        prepared.append((path,take,sorted({u['chapter_id'] for u in proposed['units']})))
    # Attempts are independent: one native session and one audit process for the whole stage
    # (6 launches + 6 audits cost ~5.5 min for one sentence, 2026-10-17). Acceptance keeps attempt order.
    from render_higgs_book_trial import render_many
    render_candidates(prepared,render_many,wait_for_audio_resources)
    reports=audit_candidates([take for _,take,_ in prepared])
    for _,take,_ in prepared:
        report=reports[take]
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            if unit['id'] not in receipts or unit['id'] not in report['checks']:continue
            uid=unit['id'];receipt=receipts[uid];check=report['checks'][uid];wav=take/receipt['audio']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound exhausted stress recovery audio')
            if not unresolved(result[uid]):continue
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if check['status']=='asr_exact_match' and not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
    return result


SWEEP_SEEDS=12
SWEEP_FIRST_ATTEMPT=70  # distinct from per-chunk (0-20), batched (30-35), decoding (41-45) and delegated (60+)


def seed_sweep(out,assignment,selected,groups,uids):
    """Fresh palette-bound seeds for units every earlier take was heard identically, in one session.

    u0291 "по наши души" was heard "по нашей душе" by every recognizer on 17 takes and passed on
    the 18th, a plain new seed (iteration 8, 2026-10-17); decoding profiles never helped such units
    (0 of 60). Seeds now share one native session and one audit, so 12 cost ~2 min instead of holding
    every later section. Same inputs and gates per take; the first exact, edge-safe take wins.
    """
    import soundfile as sf
    from render_higgs_palette_preview import retry_units,wait_for_audio_resources,boundary_check
    from render_higgs_book_trial import render_many
    result=copy.deepcopy(selected);originals={u['id']:u for u in assignment['units']};prepared=[]
    for n in range(SWEEP_SEEDS):
        units=[]
        for uid in sorted(uids):
            row=result[uid];unit=retry_units([originals[uid]],SWEEP_FIRST_ATTEMPT+n,groups)[0]
            if 'spoken_number_policy' in row['unit']:unit['spoken_number_policy']=row['unit']['spoken_number_policy']
            if row['unit'].get('spoken_text_plan'):unit['spoken_text_plan']=copy.deepcopy(row['unit']['spoken_text_plan'])
            if unit.get('palette_recovery'):unit['palette_recovery']['reason']='identical-transcript seed sweep'
            units.append(unit)
        take=out/'word-seed-sweep'/f'seed-{n:02d}';take.mkdir(parents=True,exist_ok=True);path=take/'assignment.json'
        proposed={**assignment,'units':units}
        if path.exists():
            saved=read(path)
            if any(u['id'] not in originals for u in saved['units']) or {u['id'] for u in units}-{u['id'] for u in saved['units']}:
                raise ValueError('Frozen seed sweep changed')
            proposed=saved
        else:write_json(path,proposed)
        prepared.append((path,take,sorted({u['chapter_id'] for u in proposed['units']})))
    print('SEED SWEEP',sorted(uids),SWEEP_SEEDS,'seeds in one session',flush=True)
    render_candidates(prepared,render_many,wait_for_audio_resources)
    reports=audit_candidates([take for _,take,_ in prepared])
    for _,take,_ in prepared:
        report=reports[take]
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            uid=unit['id']
            if uid not in uids or uid not in receipts or uid not in report['checks'] or not unresolved(result[uid]):continue
            receipt=receipts[uid];check=report['checks'][uid];wav=take/receipt['audio']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound seed sweep audio')
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if check['status']=='asr_exact_match' and not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
    write_json(out/'selected.json',result)
    return result


UNMARKED_SEEDS=3
UNMARKED_FIRST_ATTEMPT=90


def unmarked_recovery(out,assignment,selected,groups):
    """Last stage: the same words without automatic ordinary-word stress marks (names keep theirs).

    Higgs reads U+0301 as its own token; on c0001-u0312 "тебе́ позави́дую" every seed inserted a
    syllable between the two marked words (2026-10-17). Marks are synthesis hints only, so
    dropping them changes no source word; the gates are unchanged and the first exact take wins.
    """
    import soundfile as sf
    from render_higgs_palette_preview import retry_units,wait_for_audio_resources,boundary_check
    from render_higgs_book_trial import render_many
    result=copy.deepcopy(selected);originals={u['id']:u for u in assignment['units']}
    uids=sorted(uid for uid,row in result.items() if unresolved(row) and '́' in ((row['unit'].get('synthesis') or {}).get('text') or ''))
    if not uids:return result
    prepared=[]
    for n in range(UNMARKED_SEEDS):
        units=[]
        for uid in uids:
            row=result[uid];unit=retry_units([originals[uid]],UNMARKED_FIRST_ATTEMPT+n,groups)[0]
            if 'spoken_number_policy' in row['unit']:unit['spoken_number_policy']=row['unit']['spoken_number_policy']
            if row['unit'].get('spoken_text_plan'):unit['spoken_text_plan']=copy.deepcopy(row['unit']['spoken_text_plan'])
            if unit.get('palette_recovery'):unit['palette_recovery']['reason']='unmarked synthesis variant'
            unit['stress_marks']='suppress-automatic-v1';units.append(unit)
        take=out/'word-unmarked-recovery'/f'seed-{n:02d}';take.mkdir(parents=True,exist_ok=True);path=take/'assignment.json'
        proposed={**assignment,'units':units}
        if path.exists():
            saved=read(path)
            if any(u['id'] not in originals for u in saved['units']) or set(uids)-{u['id'] for u in saved['units']}:
                raise ValueError('Frozen unmarked recovery changed')
            proposed=saved
        else:write_json(path,proposed)
        prepared.append((path,take,sorted({u['chapter_id'] for u in proposed['units']})))
    print('UNMARKED RECOVERY',uids,UNMARKED_SEEDS,'seeds in one session',flush=True)
    render_candidates(prepared,render_many,wait_for_audio_resources)
    reports=audit_candidates([take for _,take,_ in prepared])
    for _,take,_ in prepared:
        report=reports[take]
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            uid=unit['id']
            if uid not in uids or uid not in receipts or uid not in report['checks'] or not unresolved(result[uid]):continue
            receipt=receipts[uid];check=report['checks'][uid];wav=take/receipt['audio']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound unmarked recovery audio')
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if check['status']=='asr_exact_match' and not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
    write_json(out/'selected.json',result)
    return result


def decoding_recovery(out,assignment,selected,groups):
    import soundfile as sf
    from render_higgs_palette_preview import retry_units,wait_for_audio_resources,boundary_check
    result=copy.deepcopy(selected);originals={u['id']:u for u in assignment['units']}
    # Futility (2026-10-17): 0 of 60 sentences whose >=3 earlier flagged takes shared one primary
    # transcript were verified by decoding recovery. They stay unresolved/held; this is not acceptance.
    futile=futile_units(out,{uid for uid,row in result.items() if unresolved(row)})
    if futile:
        write_json(out/'decoding-recovery-futility.json',{'skipped':futile,'reason':'>=3 earlier flagged takes with identical normalized primary transcript',
            'rule':'futile-identical-transcripts-v1','acceptance':False,'still_unresolved':True})
        print('DECODING RECOVERY SKIPS',sorted(futile),'(identical earlier transcripts)',flush=True)
        if groups is not None:result=seed_sweep(out,assignment,result,groups,set(futile))
    prepared=[]
    for attempt,profile in enumerate(('careful','conservative','careful_full','conservative_full','balanced_full')):
        # Legacy short-budget profiles remain readable for existing immutable
        # candidates; newly created attempts always retain the pack's budget.
        if profile in ('careful','conservative') and not (out/'word-decoding-recovery'/profile/'assignment.json').exists():
            continue
        units=[]
        for uid,row in result.items():
            if not unresolved(row) or uid in futile:continue
            unit=retry_units([originals[uid]],41+attempt,groups)[0]
            if 'spoken_number_policy' in row['unit']:unit['spoken_number_policy']=row['unit']['spoken_number_policy']
            if row['unit'].get('spoken_text_plan'):unit['spoken_text_plan']=copy.deepcopy(row['unit']['spoken_text_plan'])
            unit['decoding_profile']=profile;units.append(unit)
        if not units:break
        take=out/'word-decoding-recovery'/profile;path=take/'assignment.json'
        proposed={**assignment,'units':units}
        if path.exists():
            saved=read(path);by_id={u['id']:u for u in units}
            if any(u['id'] not in originals or (u['id'] in by_id and u!=by_id[u['id']]) for u in saved['units']):
                raise ValueError('Frozen decoding recovery changed')
            proposed=saved
        else:write_json(path,proposed)
        print('SOURCE-WORD DECODING RECOVERY',profile,[u['id'] for u in units],flush=True)
        prepared.append((path,take,sorted({u['chapter_id'] for u in proposed['units']})))
    # Profiles are independent: one native session and one audit process for the stage (2026-10-17).
    from render_higgs_book_trial import render_many
    render_candidates(prepared,render_many,wait_for_audio_resources)
    reports=audit_candidates([take for _,take,_ in prepared])
    for _,take,_ in prepared:
        report=reports[take]
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            if unit['id'] not in receipts or unit['id'] not in report['checks']:continue
            uid=unit['id'];receipt=receipts[uid];check=report['checks'][uid];wav=take/receipt['audio']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound decoding recovery audio')
            if not unresolved(result[uid]):continue
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if check['status']=='asr_exact_match' and not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
        write_json(out/'selected.json',result)
    return result


def number_surface_recovery(out,assignment,selected,groups):
    """Let Higgs read original digits, while auditing the same expanded values."""
    import soundfile as sf
    import re
    from rimsky.spoken_edits import apply_spoken_edit
    from render_higgs_book_trial import spoken_text
    from render_higgs_palette_preview import retry_units,render,wait_for_audio_resources,boundary_check
    result=copy.deepcopy(selected);originals={u['id']:u for u in assignment['units']}
    for attempt in range(2):
        units=[]
        for uid,row in result.items():
            if not unresolved(row):continue
            if not re.search(r'[0-9]',originals[uid]['text']):continue
            native=spoken_text(originals[uid]['text'],'raw-numbers-v1')
            plan={'expected_spoken':row['unit']['spoken'],'native_text':native,
                'recovery_kind':'source_number_surface',
                'evidence':{'native_surface_sha256':digest(native.encode('utf8'))}}
            try:apply_spoken_edit(plan['expected_spoken'],plan)
            except ValueError:continue
            unit=retry_units([originals[uid]],51+attempt,groups)[0]
            if 'spoken_number_policy' in row['unit']:unit['spoken_number_policy']=row['unit']['spoken_number_policy']
            unit['spoken_text_plan']=plan;units.append(unit)
        take=out/'word-number-surface-recovery'/f'take-{attempt}';path=take/'assignment.json'
        proposed={**assignment,'units':units}
        if not units:continue
        if path.exists():
            saved=read(path);by_id={u['id']:u for u in units}
            if any(u['id'] not in originals or (u['id'] in by_id and u!=by_id[u['id']]) for u in saved['units']):
                raise ValueError('Frozen numeric surface recovery changed')
            proposed=saved
        elif units:write_json(path,proposed)
        else:continue
        print('SOURCE-NUMBER SURFACE RECOVERY',attempt,[u['id'] for u in units],flush=True)
        render_candidate(path,take,sorted({u['chapter_id'] for u in proposed['units']}),render,wait_for_audio_resources)
        report=audit_candidate(take)
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            uid=unit['id']
            if uid not in receipts or uid not in report['checks']:continue
            receipt=receipts[uid];check=report['checks'][uid];wav=take/receipt['audio']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                raise ValueError('Unbound numeric surface recovery audio')
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if check['status']=='asr_exact_match' and not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
        write_json(out/'selected.json',result)
    return result


def verify_candidates(out,assignment,selected,spell_numbers=False):
    from render_higgs_palette_preview import ROOT,wait_for_audio_resources
    rows=candidates(out,assignment,selected)
    if spell_numbers:
        import re
        from higgs_secondary_verification import report_paths
        digit_witnesses={(w['unit_id'],w['audio_sha256'],w['synthesis_key'])
            for path in report_paths(out) for w in read(path)['checks'].values()
            if any(c.isdecimal() for c in w.get('heard',''))}
        rows={key:row for key,row in rows.items() if re.search(r'[0-9]',row['unit']['text'])
            or (row['unit']['id'],row['receipt']['audio_sha256'],row['receipt']['key']) in digit_witnesses}
    if not rows:return selected
    paths=[];by_path={}
    for (uid,key),row in rows.items():
        path=out/'tertiary-audio-snapshots'/(key+'-'+digest(row)[:16]+'.json')
        if path.exists() and read(path)!={uid:row}:raise ValueError('Frozen tertiary audio snapshot changed')
        if not path.exists():write_json(path,{uid:row})
        paths.append(path);by_path[path]=row
    script=ROOT/'scripts'/('audit_higgs_spelled_asr.py' if spell_numbers else 'audit_higgs_tertiary_asr.py')
    output=out/('secondary-asr-spelled-'+file_digest(script)[:16] if spell_numbers else 'secondary-asr-large-v3-turbo')
    print('EXHAUSTED WORD VERIFICATION',len(paths),'bound takes; unchanged exact-word gate',flush=True)
    from higgs_secondary_verification import raw_witness_coverage
    from audit_higgs_tertiary_asr import MODEL,REVISION
    bound_rows={uid+':'+key:row for (uid,key),row in rows.items()}
    signal={'model':MODEL,'revision':REVISION,'language':'ru','task':'transcribe','do_sample':False,
        'num_beams':3,'device':'cuda','dtype':'float16'}
    if spell_numbers:signal['numeric_output']='suppress-every-digit-bearing-non-special-token-v1'
    covered=raw_witness_coverage(bound_rows,[output/'report.json'],file_digest(script),signal) if (output/'report.json').exists() else set()
    try:
        if covered!=set(bound_rows):
            wait_for_audio_resources()
            subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                str(script),'--selected',*[str(p) for p in paths],
                '--output',str(output)],cwd=ROOT,check=True)
        else:print('REUSED COMPLETE RAW ASR CHECKPOINT',len(covered),'takes; no model reload',flush=True)
    except (OSError,subprocess.CalledProcessError) as exc:
        write_json(out/'tertiary-asr-failed.json',{'error':str(exc),'audio_gate_unchanged':True})
        return selected
    result=copy.deepcopy(selected)
    for path,row in by_path.items():
        uid=row['unit']['id']
        if result[uid]['word_check']['status']=='asr_exact_match':continue
        verified=apply_secondary({uid:row},[output/'report.json'])[uid]
        if verified['word_check']['status']=='asr_exact_match':result[uid]=verified
    write_json(out/'selected.json',result)
    write_json(out/'exhausted-word-verification.json',{'candidate_takes':len(rows),
        'resolved':[uid for uid in result if unresolved(selected[uid]) and not unresolved(result[uid])],
        'remaining':[uid for uid,row in result.items() if unresolved(row)],
        'human_approval':False,'report_sha256':file_digest(output/'report.json')})
    return result


def recover(out,assignment,selected,groups=None,allow_native=True,allow_decoding=True):
    result=verify_candidates(out,assignment,selected)
    if any(unresolved(r) for r in result.values()):
        result=verify_candidates(out,assignment,result,spell_numbers=True)
    if allow_native and groups is not None and any(unresolved(r) for r in result.values()):
        result=number_surface_recovery(out,assignment,result,groups)
    if allow_native and groups is not None and any(unresolved(r) for r in result.values()):
        result=native_recovery(out,assignment,result,groups)
        if any(unresolved(r) for r in result.values()):
            result=recover(out,assignment,result,groups,allow_native=False,allow_decoding=False)
    if allow_decoding and groups is not None and any(unresolved(r) for r in result.values()):
        result=decoding_recovery(out,assignment,result,groups)
        if any(unresolved(r) for r in result.values()):
            result=recover(out,assignment,result,groups,allow_native=False,allow_decoding=False)
        if any(unresolved(r) for r in result.values()):
            result=unmarked_recovery(out,assignment,result,groups)
            if any(unresolved(r) for r in result.values()):
                return recover(out,assignment,result,groups,allow_native=False,allow_decoding=False)
    return result
