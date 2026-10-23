"""Batch retakes for every flagged unit across the requested chapters.

Per-chunk recovery renders one to three units per native launch and reloads
both Higgs and the recognizer each time: measured 2026-10-15 at about seven
seconds of wall time per second of audio, against about one for a first pass.
A round here renders each variant of every flagged unit in one native session
(25 units per launch) and audits all variants with one recognizer load. Each
chunk then selects a winner through higgs_prior_take_sweep, so the exact-word,
edge, voice and mood gates are unchanged. Rounds are numbered and capped:
the same strategy is never repeated indefinitely.
"""
from __future__ import annotations
import copy
import subprocess
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import digest,write_json

OK=('asr_exact_match','nonverbal_not_spoken_text')
FOLDER='batched-recovery'
MAX_ROUNDS=2
VARIANTS=3
FIRST_ATTEMPT=30  # per-chunk stages use 0-20; keep seeds and references distinct


def flagged_units(generation,chapters):
    """(assignment meta, assignment unit) for each unit whose chunk selection is unverified."""
    from higgs_secondary_verification import cached_secondary
    rows={};published=published_verified(generation)
    for chapter in chapters:
        for selected_path in sorted((generation/chapter).glob('chunk-*/**/selected.json')):
            rel=selected_path.relative_to(generation)
            if any(part.startswith(('word-','prior-take','take-')) or 'diagnosis' in part for part in rel.parts[:-1]):continue
            assignment_path=selected_path.parent/'assignment.json'
            if not assignment_path.exists():continue
            assignment=read(assignment_path);units={u['id']:u for u in assignment['units']}
            for uid,row in cached_secondary(selected_path.parent,read(selected_path)).items():
                if row['word_check']['status'] in OK or uid in published or uid not in units or uid in rows:continue
                if row['unit'].get('synthesis',{}).get('backend')=='nonverbal_asset':continue
                rows[uid]=({k:v for k,v in assignment.items() if k!='units'},units[uid])
    return rows


def published_verified(generation):
    """Units the listener already hears as verified audio."""
    from higgs_published_timeline import read_timeline
    state=read(generation/'progress.json');result=set()
    for cid,ready in state.get('chapters',{}).items():
        result.update(r['id'] for r in read_timeline(generation,cid,ready) if r.get('word_status') in OK)
    return result


def run_round(generation,chapters,groups,render,wait_for_audio_resources,root):
    """Render and audit one capped round; return the round folder or None."""
    from render_higgs_palette_preview import retry_units
    from higgs_exhausted_word_recovery import render_candidate
    from regenerate_higgs_book_trial import primary_audit_command,cached_audit
    base=generation/FOLDER
    done=sorted(base.glob('round-*/complete.json')) if base.exists() else []
    if len(done)>=MAX_ROUNDS:return None
    rows=flagged_units(generation,chapters)
    if not rows:return None
    number=len(done);folder=base/f'round-{number}'
    metas={digest(meta) for meta,_ in rows.values()}
    if len(metas)!=1:raise ValueError('Flagged units belong to different frozen assignments')
    meta=next(iter(rows.values()))[0];units=[unit for _,unit in rows.values()]
    print('BATCHED RECOVERY round',number,len(units),'flagged units x',VARIANTS,'variants',flush=True)
    takes=[]
    for variant in range(VARIANTS):
        attempt=FIRST_ATTEMPT+number*VARIANTS+variant
        take=folder/f'variant-{variant}';take.mkdir(parents=True,exist_ok=True)
        candidate={**copy.deepcopy(meta),'units':retry_units(units,attempt,groups)}
        for unit in candidate['units']:
            unit['batched_recovery']={'round':number,'variant':variant,'attempt':attempt}
        path=take/'assignment.json'
        if path.exists() and read(path)!=candidate:raise ValueError('Frozen batched recovery changed')
        write_json(path,candidate)
        render_candidate(path,take,list(chapters),render,wait_for_audio_resources)
        if (take/'receipts.json').exists() and read(take/'receipts.json')['takes']:takes.append(take)
    pending=[t for t in takes if cached_audit_safe(cached_audit,t) is None]
    if pending:
        wait_for_audio_resources()
        # One recognizer load for every variant folder.
        command=primary_audit_command(pending[0])
        subprocess.run([*command[:command.index('--folders')+1],*[str(t) for t in pending]],cwd=root,check=True)
    write_json(folder/'complete.json',{'units':sorted(rows),'variants':VARIANTS,'audited':[t.name for t in takes],
        'gates_unchanged':True,'human_approval':False})
    print('BATCHED RECOVERY round',number,'complete;',len(takes),'variant folders audited',flush=True)
    return folder


def cached_audit_safe(cached_audit,take):
    try:return cached_audit(take)
    except (ValueError,KeyError,OSError):return None
