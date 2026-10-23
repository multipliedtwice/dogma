"""Reuse an earlier verified seed only inside the current frozen performance palette."""
import copy
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import file_digest,digest,write_json
from rimsky.spoken_pronunciation import phrase_stress
_INDEX={};_AUDITS={}


def with_policy(native,assignment):
    """The exact synthesis text this run would render, including its pronunciation policy."""
    spec=assignment.get('pronunciation')
    if not spec:return native
    from rimsky.auto_pronunciation import auto_pronunciation,policy_options
    names={cid:row['stressed'] for cid,row in spec['names'].items()}
    names.update(spec.get('proper_nouns',{}))
    options=policy_options(spec['policy'],spec)
    from rimsky.respelling import respell_text
    native,_=respell_text(native,names,spec.get('common',[]),spec.get('terms'),options.get('implicit_single',False))
    return auto_pronunciation(native,names,spec.get('common',[]),**options)[0]


def reuse(out,assignment,groups):
    # Benchmark runs measure a new book: no takes copied from earlier generations.
    import os
    if not groups or os.environ.get('RIMSKY_NO_TAKE_REUSE')=='1':return {}
    generation=next((p for p in out.parents if (p/'manifest.json').exists()),None)
    if generation is None:return {}
    from render_higgs_book_trial import spoken_text,MODEL,RUNNER
    from regenerate_higgs_book_trial import cached_audit
    from higgs_secondary_verification import cached_secondary
    from higgs_file_checks import cached_sha
    from rimsky.audio_join import boundary_check
    import soundfile as sf
    identity=(generation.resolve(),assignment.get('source_sha256'),out.resolve())
    if identity not in _INDEX:
        index={};own=read(generation/'manifest.json')
        for older in sorted(generation.parent.glob('audio-palette-*')):
            if not (older/'manifest.json').exists():continue
            if read(older/'manifest.json').get('source_sha256')!=own['source_sha256']:continue
            selections=[*older.glob('c*/chunk-*/**/selected.json'),*older.glob('c*/readout-repair-*/selected.json')]
            for selected_path in selections:
                if selected_path.parent.resolve().is_relative_to(out.resolve()):continue
                for uid,row in read(selected_path).items():
                    if row['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text'):continue
                    index.setdefault(uid,[]).append((older,selected_path,row))
        _INDEX[identity]=index
    # Reuse only takes of the pinned production Higgs engine.
    from render_higgs_book_trial import ENGINE_MODEL
    backend='higgs_audio_cpp'
    stress=read(generation/'manifest.json').get('stress_witness')
    from rimsky.stress_witness import RULE as STRESS_RULE
    stress=stress==STRESS_RULE;alternatives={}
    found={};model_sha=cached_sha(ENGINE_MODEL);runner_sha=cached_sha(RUNNER)
    for original in assignment['units']:
        uid=original['id'];palette=original.get('palette_assignment',{});group=groups.get(palette.get('template_id'))
        if not group or original.get('spoken_text_plan'):continue
        baseline=spoken_text(original['text'])
        native=baseline
        if original.get('finish_fragment') and native and native[-1] not in '.!?…,:;':native+=','
        # Mirror render(): without the frozen terminal period no unpunctuated unit (titles, author
        # remarks) ever matched its earlier take, so 27 per section re-rendered each run (2026-10-17).
        if assignment.get('terminal_punctuation')=='period-if-missing-v1' and native and native[-1] not in '.!?…,:;':native+='.'
        native,_=phrase_stress(native)
        native=with_policy(native,assignment)
        permitted={r['audio_sha256']:r for r in group['moods'].get(original['mood'],[])}
        for older,selection_path,previous in _INDEX[identity].get(uid,[]):
            old=previous['unit'];plan=old['synthesis'];reference=plan.get('reference',{})
            if any(old.get(k)!=original.get(k) for k in ('text','source_id','start','end','speaker','voice','mood','mode')):continue
            ref=permitted.get(reference.get('sha256'))
            if not ref or reference.get('text')!=ref['text']:continue
            expected_native=native
            recipe=old.get('spoken_text_plan')
            if recipe:
                if recipe.get('recovery_kind')!='source_number_surface':continue
                from rimsky.spoken_edits import apply_spoken_edit
                try:expected_native,_=apply_spoken_edit(baseline,recipe)
                except ValueError:continue
                expected_native,_=phrase_stress(expected_native)
                expected_native=with_policy(expected_native,assignment)
            if plan.get('backend')!=backend or plan.get('effects_policy')!='disabled' or plan.get('prompt')!=expected_native or plan.get('text')!=expected_native:continue
            if plan['model_sha256']!=model_sha or plan['runner_sha256']!=runner_sha:continue
            wav=Path(previous['path']);take=wav.parent
            if not (take/'render-plan.json').exists() or digest(plan)!=old['key'] or previous['receipt']['key']!=old['key']:continue
            if take not in _AUDITS:
                try:_AUDITS[take]=cached_audit(take)
                except (ValueError,OSError):_AUDITS[take]=None
            audit=_AUDITS[take]
            if audit is None or uid not in audit['checks']:continue
            checked={uid:{**previous,'word_check':audit['checks'][uid]}}
            checked=cached_secondary(older,checked)
            if checked[uid]['word_check']['status']!='asr_exact_match':continue
            if file_digest(wav)!=previous['receipt']['audio_sha256']:raise ValueError('Earlier verified audio changed')
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if edge['unsafe_start'] or edge['unsafe_end']:continue
            row={**copy.deepcopy(original),'spoken':old['spoken'],'render_voice':old['render_voice'],
                'synthesis':copy.deepcopy(plan),'key':old['key'],'audio':old['audio'],
                'palette_reference':copy.deepcopy(ref),'render_seed':plan['sampling']['seed']}
            if recipe:row['spoken_text_plan']=copy.deepcopy(recipe)
            candidate={**checked[uid],'unit':row,'edge':edge,'earlier_generation_reuse':{
                'selection_sha256':file_digest(selection_path),'plan_sha256':file_digest(take/'render-plan.json'),
                'generation':older.name,'seed':plan['sampling']['seed'],
                'basis':'Earlier word/edge-verified seed; same source, character, family, mood and eligible reference; no effects.'}}
            if not stress:found[uid]=candidate;break
            alternatives.setdefault(uid,[]).append((wav,plan.get('text',''),candidate))
    if stress and alternatives:
        # Acoustic stress witness (2026-10-17): one model load for every candidate with marked words, released
        # before take-0's native launch. A suspect candidate is not reused; the unit renders fresh instead.
        from rimsky.stress_witness import judge_batch,marked_words
        items=[((uid,n),wav,text) for uid,rows in alternatives.items() for n,(wav,text,_) in enumerate(rows) if marked_words(text)]
        results=judge_batch(items) if items else {}
        for uid,rows in alternatives.items():
            for n,(wav,text,candidate) in enumerate(rows):
                result=results.get((uid,n))
                if result and result['suspect']:continue
                found[uid]={**candidate,**({'stress_witness':result} if result else {})};break
    if found:
        write_json(out/'earlier-verified-takes.json',found)
        print('REUSED EARLIER VERIFIED PERFORMANCE',len(found),'/',len(assignment['units']),flush=True)
    return found
