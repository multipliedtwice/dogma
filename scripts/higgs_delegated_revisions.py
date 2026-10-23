"""Render delegated editorial revisions for held units, then let the sweep select.

The user delegated editorial choices ("Use your own judgement", 2026-10-15):
synthesis-only respellings, a source typo and a number-reading bug fix. Each
decision lives in `delegated-revisions.json` with its rationale and is recorded
in every take as `delegated_editorial_decision` evidence, never as listening
approval. Variants and seeds render in one native session per folder and are
audited with one recognizer load; higgs_prior_take_sweep selects winners under
the unchanged exact-word, edge, voice and mood gates.
"""
from __future__ import annotations
import copy
import subprocess
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import digest,file_digest,write_json

FOLDER='delegated-revisions'
SEEDS=2
FIRST_ATTEMPT=60  # distinct from per-chunk (0-20) and batched (30-35) attempts


def assignment_units(generation,uid):
    """(assignment meta, unit) for the unit from its chunk's own frozen assignment."""
    chapter=uid.split('-')[0]
    for path in sorted((generation/chapter).glob('chunk-*/**/assignment.json')):
        rel=path.relative_to(generation)
        if any(p.startswith(('word-','take-','prior-take')) for p in rel.parts[:-1]):continue
        assignment=read(path)
        for unit in assignment['units']:
            if unit['id']==uid:return {k:v for k,v in assignment.items() if k!='units'},unit
    raise ValueError('No frozen assignment for '+uid)


def run_round(generation,chapters,groups,render,wait_for_audio_resources,root):
    from render_higgs_palette_preview import retry_units,spoken_text
    from higgs_exhausted_word_recovery import render_candidates
    from render_higgs_book_trial import render_many
    from regenerate_higgs_book_trial import primary_audit_command,cached_audit
    from rimsky.spoken_edits import apply_spoken_edit
    request_path=generation/'delegated-revisions.json'
    if not request_path.exists():return None
    request=read(request_path)
    # Completion belongs to the exact units rendered, not to the request file:
    # a later run for another chapter, or a section assigned since, still gets
    # its revisions. A unit whose section is not assigned yet waits for a later round.
    # A chapter already complete is not re-rendered; a source correction whose target our
    # (since fixed) reader already speaks is obsolete.
    progress=read(generation/'progress.json') if (generation/'progress.json').exists() else {}
    entries={}
    for uid,row in request['units'].items():
        chapter=uid.split('-')[0]
        if chapter not in chapters:continue
        if progress.get('chapters',{}).get(chapter,{}).get('complete'):
            print('DELEGATED REVISION SKIPPED',uid,'chapter complete',flush=True);continue
        try:_,unit=assignment_units(generation,uid)
        except ValueError:continue
        if row['kind']=='delegated_source_correction' and spoken_text(unit['text'],unit.get('spoken_number_policy','grouped-digits-v2'))==row['target_spoken']:
            print('DELEGATED REVISION SKIPPED',uid,'source already reads as target',flush=True);continue
        entries[uid]=row
    if not entries:return None
    tag=file_digest(request_path)[:12]
    if (generation/FOLDER/tag/'complete.json').exists() and sorted(entries)!=read(generation/FOLDER/tag/'complete.json')['units']:
        tag=digest({'request_sha256':file_digest(request_path),'units':sorted(entries)})[:12]
    base=generation/FOLDER/tag
    if (base/'complete.json').exists():return base
    variants=max(len(row['native_variants']) for row in entries.values())
    meta=None;jobs=[]
    for v in range(variants):
        for seed in range(SEEDS):
            units=[]
            for uid,row in entries.items():
                if v>=len(row['native_variants']):continue
                unit_meta,unit=assignment_units(generation,uid)
                if meta is None:meta=unit_meta
                elif digest(meta)!=digest(unit_meta):raise ValueError('Delegated units span different frozen assignments')
                attempt=FIRST_ATTEMPT+v*SEEDS+seed
                expected=spoken_text(unit['text'],unit.get('spoken_number_policy','grouped-digits-v2'))
                plan={'expected_spoken':expected,'native_text':row['native_variants'][v],
                      'recovery_kind':row['kind'],'evidence':{**request['evidence'],'rationale':row['rationale']},
                      'scope':'delegated editorial decision; not listening approval'}
                if row['kind']=='delegated_source_correction':plan['target_spoken']=row['target_spoken']
                try:apply_spoken_edit(expected,plan)
                except ValueError as error:
                    print('DELEGATED REVISION SKIPPED',uid,v,error,flush=True);continue
                candidate=retry_units([unit],attempt,groups)[0]
                candidate['spoken_text_plan']=plan
                candidate['delegated_revision']={'request_sha256':file_digest(request_path),'variant':v,'seed':seed}
                units.append(candidate)
            if not units:continue
            take=base/f'v{v}-s{seed}';take.mkdir(parents=True,exist_ok=True)
            path=take/'assignment.json';candidate_assignment={**copy.deepcopy(meta),'units':units}
            if path.exists() and read(path)!=candidate_assignment:raise ValueError('Frozen delegated revision changed')
            write_json(path,candidate_assignment)
            print('DELEGATED REVISIONS rendering',take.name,len(units),'units',flush=True)
            jobs.append((path,take,list(chapters)))
    # All v/seed folders render in one native session (one model load instead of one per folder, 2026-10-17).
    render_candidates(jobs,render_many,wait_for_audio_resources)
    takes=[t for _,t,_ in jobs if (t/'receipts.json').exists() and read(t/'receipts.json')['takes']]
    pending=[]
    for take in takes:
        try:
            if cached_audit(take) is None:pending.append(take)
        except (ValueError,KeyError,OSError):pending.append(take)
    if pending:
        wait_for_audio_resources()
        command=primary_audit_command(pending[0]);at=command.index('--folders')+1
        subprocess.run([*command[:at],*[str(t) for t in pending],*command[at+1:]],cwd=root,check=True)
    write_json(base/'complete.json',{'request_sha256':file_digest(request_path),'units':sorted(entries),
        'folders':[t.name for t in takes],'gates_unchanged':True,'human_approval':False})
    print('DELEGATED REVISIONS complete',len(takes),'folders',flush=True)
    return base
