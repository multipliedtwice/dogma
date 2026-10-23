"""Bounded native punctuation recovery for unfinished, clipped synthesis parts."""
import copy
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import file_digest,write_json


def recover(out,assignment,chapter,selected,groups):
    import soundfile as sf
    from render_higgs_palette_preview import retry_units,render,wait_for_audio_resources,boundary_check
    from render_higgs_book_trial import spoken_text
    from higgs_exhausted_word_recovery import render_candidate,audit_candidate
    originals={u['id']:u for u in assignment['units']}
    missing={uid for uid in originals if uid not in selected}
    result=copy.deepcopy(selected)
    for attempt in range(6):
        rows=[]
        for uid in sorted(missing-set(result)):
            unit=originals[uid]
            if unit.get('spoken_text_plan'):continue
            expected=spoken_text(unit['text'],unit.get('spoken_number_policy','grouped-digits-v2'))
            if not expected or expected[-1] in '.!?…,:;':continue
            failed=[]
            for receipt_path in sorted(out.glob('take-*/receipts.json')):
                receipt=read(receipt_path)['takes'].get(uid)
                if receipt is None:continue
                wav=receipt_path.parent/receipt['audio']
                if file_digest(wav)!=receipt['audio_sha256']:raise ValueError('Clipped take evidence changed')
                x,sr=sf.read(wav,dtype='float32');edge=boundary_check(x,sr)
                if edge['unsafe_start'] or edge['unsafe_end']:
                    failed.append({'audio_sha256':receipt['audio_sha256'],'synthesis_key':receipt['key'],
                        'path':str(wav.resolve()),'edge':edge})
            if not failed:continue
            candidate=retry_units([unit],51+attempt,groups)[0]
            candidate['spoken_text_plan']={'expected_spoken':expected,
                'native_text':expected+(',' if attempt<3 else '.'),'recovery_kind':'edge_fragment',
                'evidence':{'source':'unsafe_generated_audio_edge','takes':failed},
                'scope':'punctuation candidate; no word, voice or mood changes; fresh quality checks required'}
            rows.append(candidate)
        if not rows:break
        take=out/'edge-fragment-recovery'/f'take-{attempt}';path=take/'assignment.json'
        candidate={**assignment,'units':rows}
        if path.exists() and read(path)!=candidate:raise ValueError('Frozen edge recovery changed')
        write_json(path,candidate)
        print('FRAGMENT EDGE RECOVERY',chapter,attempt,[u['id'] for u in rows],flush=True)
        render_candidate(path,take,chapter,render,wait_for_audio_resources)
        report=audit_candidate(take)
        if report is None:continue
        receipts=read(take/'receipts.json')['takes']
        for unit in read(take/'render-plan.json')['units']:
            uid=unit['id'];receipt=receipts.get(uid);check=report['checks'].get(uid)
            if receipt is None or check is None:continue
            wav=take/receipt['audio']
            if (file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256']
                or check['synthesis_key']!=receipt['key']):raise ValueError('Unbound edge-recovery word audit')
            x,sr=sf.read(wav,dtype='float32');edge=boundary_check(x,sr)
            if not(edge['unsafe_start'] or edge['unsafe_end']):
                result[uid]={'unit':unit,'receipt':receipt,'path':str(wav),'edge':edge,'word_check':check}
        write_json(out/'edge-fragment-selected.json',result)
    return result
