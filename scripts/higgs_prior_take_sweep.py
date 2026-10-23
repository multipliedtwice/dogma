"""Reconsider every earlier take of a still-flagged unit before rendering more.

A chunk keeps only one selected take per unit. When a later attempt replaced
it, or when verification rules gained new evidence, an earlier take of the
same source unit, voice and mood may already pass the unchanged exact-word and
edge gates. Selecting it preserves valid prior work instead of spending more
retakes. Nothing here relaxes a gate: each candidate must reproduce an exact
match from bound primary audio checks and immutable witness reports.
"""
from __future__ import annotations
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import digest,file_digest,write_json

OK=('asr_exact_match','nonverbal_not_spoken_text')


def candidates(out,unit):
    """Bound earlier takes of exactly this unit, voice and mood, in render order."""
    import soundfile as sf
    from rimsky.audio_join import boundary_check
    from regenerate_higgs_book_trial import cached_audit
    rows=[]
    generation=next((p for p in [out,*out.parents] if (p/'manifest.json').exists()),None)
    batched=[r for name in ('batched-recovery','delegated-revisions') if generation and (generation/name).exists()
             for r in (generation/name).rglob('receipts.json')]
    for receipts_path in sorted([*out.rglob('receipts.json'),*batched],key=lambda p:p.stat().st_mtime_ns):
        take=receipts_path.parent
        if 'diagnosis' in take.parts or not (take/'render-plan.json').exists():continue
        try:
            receipts=read(receipts_path);plan={u['id']:u for u in read(take/'render-plan.json')['units']}
            if unit['id'] not in receipts['takes'] or unit['id'] not in plan:continue
            audit=cached_audit(take)
        except (ValueError,KeyError,OSError):continue
        if not audit or unit['id'] not in audit['checks']:continue
        rendered=plan[unit['id']];receipt=receipts['takes'][unit['id']];check=audit['checks'][unit['id']]
        synthesis=rendered.get('synthesis',{})
        plan=rendered.get('spoken_text_plan') or {}
        corrected=(plan.get('recovery_kind')=='delegated_source_correction' and plan.get('target_spoken')==rendered.get('spoken'))
        if (rendered.get('text','').strip()!=unit.get('text','').strip() or (rendered.get('spoken')!=unit.get('spoken') and not corrected)
            or any(rendered.get(k)!=unit.get(k) for k in ('speaker','render_voice','mood'))
            or synthesis.get('effects_policy')!='disabled' or synthesis.get('backend')!=_engine_backend()
            or synthesis.get('prompt')!=synthesis.get('text')):continue
        wav=take/receipt['audio']
        if (file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256']
            or check['synthesis_key']!=receipt['key'] or digest(synthesis)!=receipt['key']):continue
        x,rate=sf.read(wav,dtype='float32');edge=boundary_check(x,rate)
        if edge['unsafe_start'] or edge['unsafe_end']:continue
        rows.append({'unit':rendered,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check})
    return rows


def sweep(out,original,selected,witness=None):
    """Return selected with each flagged unit replaced by its first verified prior take."""
    from higgs_secondary_verification import apply_secondary,report_paths
    flagged=[u for u in original if u['id'] in selected and selected[u['id']]['word_check']['status'] not in OK]
    if not flagged:return selected
    pool={u['id']:candidates(out,selected[u['id']]['unit']) for u in flagged}
    snapshots=[]
    for uid,rows in pool.items():
        for row in rows:
            if row['word_check']['status'] in OK:continue
            snapshot=out/'prior-take-sweep'/(uid+'-'+digest(row)[:16]+'.json')
            if not snapshot.exists():write_json(snapshot,{uid:row})
            snapshots.append(snapshot)
    if snapshots and witness:witness(snapshots)
    result=dict(selected);changed=[]
    for uid,rows in pool.items():
        for row in rows:
            checked=row if row['word_check']['status'] in OK else apply_secondary({uid:row},report_paths(Path(row['path']).parent))[uid]
            if checked['word_check']['status'] in OK:
                result[uid]=checked;changed.append(uid);break
    if changed:
        write_json(out/'prior-take-sweep'/'last.json',{'units':changed,'selected_sha256_before':digest(selected),
            'gates_unchanged':True,'human_approval':False})
        print('PRIOR TAKE SWEEP verified',len(changed),'earlier takes:',', '.join(changed),flush=True)
    return result


def _engine_backend():
    # Production/evaluation sweeps only reuse takes from the pinned Higgs engine.
    return 'higgs_audio_cpp'
