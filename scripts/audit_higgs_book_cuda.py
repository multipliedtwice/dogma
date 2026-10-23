"""Audio-only GPU word check, used after Higgs releases the 6 GB device."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json

def read(p):return read_json(p)

def audit(folders,flags_only=False,witnesses=None):
    import torch
    from asr_batching import transcribe as batched,BATCH,last_position_logits
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(4)
    import time
    from rimsky.run_events import event
    started=time.time()
    prefix='training-work/models/Qwen3-ASR-1.7B'
    model_path=ROOT/prefix
    from higgs_file_checks import persistent_sha
    files={p.relative_to(ROOT).as_posix():persistent_sha(p) for p in model_path.rglob('*')
           if p.is_file() and '.cache' not in p.parts}
    config={'model_files':files,'device':'cuda:0','dtype':'bfloat16','attention':'sdpa','language':'Russian',
            'max_new_tokens':512,'implementation_sha256':file_digest(Path(__file__)),
            'batching_sha256':file_digest(Path(__file__).with_name('asr_batching.py'))}
    signature=digest(config)
    event('asr.hash_model',recognizer='primary',seconds=round(time.time()-started,3));started=time.time()
    model=last_position_logits(Qwen3ASRModel.from_pretrained(str(model_path),device_map='cuda:0',dtype=torch.bfloat16,
            attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=BATCH,max_new_tokens=512))
    event('asr.load',recognizer='primary',seconds=round(time.time()-started,3))
    later=[]
    for folder in folders:
        plan_path=folder/'render-plan.json';plan=read(plan_path);binding=file_digest(plan_path)
        receipts=read(folder/'receipts.json')
        if receipts['plan_sha256']!=binding:raise ValueError('Receipt/plan mismatch')
        units={u['id']:u for u in plan['units']}
        report_path=folder/'word-checks-1.7B.json'
        report=read(report_path) if report_path.exists() else {'plan_sha256':binding,'verifier':config,'checks':{}}
        if report['plan_sha256']!=binding or report['verifier']!=config:raise ValueError('Word audit binding changed')
        original=read(folder/'word-checks.json')['checks'] if flags_only else {}
        keys=[k for k in receipts['takes'] if not flags_only or original[k]['status']!='asr_exact_match']
        for key in keys:
            if units[key]['synthesis'].get('backend')!='nonverbal_asset' or key in report['checks']:continue
            take=receipts['takes'][key]
            if file_digest(folder/take['audio'])!=take['audio_sha256']:raise ValueError('Event audio changed before check')
            report['checks'][key]={'status':'nonverbal_not_spoken_text','expected_text_sha256':digest(b''),
                'audio_sha256':take['audio_sha256'],'verifier_sha256':signature,'audio':take['audio'],
                'synthesis_key':take['key'],'heard':'','word_verification_applicable':False}
        write_json(report_path,report)
        pending=[k for k in keys if k not in report['checks']];started=time.time()
        def audio_of(key):
            take=receipts['takes'][key];path=folder/take['audio']
            if file_digest(path)!=take['audio_sha256']:raise ValueError('Take changed before check')
            return path
        for n,(key,result) in enumerate(batched(model,pending,audio_of,language='Russian'),1):
            take=receipts['takes'][key]
            if file_digest(folder/take['audio'])!=take['audio_sha256']:raise ValueError('Take changed during check')
            report['checks'][key]={**transcript_check(units[key]['spoken'],result.text,take['audio_sha256'],signature),
                                   'audio':take['audio'],'synthesis_key':take['key']}
            if n%BATCH==0 or n==len(pending):
                report['peak_gpu_reserved_bytes']=torch.cuda.max_memory_reserved()
                write_json(report_path,report)
                exact=sum(v['status']=='asr_exact_match' for v in report['checks'].values())
                print('CUDA AUDIT',folder.name,len(report['checks']),'/',len(keys),'exact',exact,flush=True)
        print('CUDA AUDIT COMPLETE',folder.name,len(report['checks']),flush=True)
        event('asr.transcribe',recognizer='primary',files=len(pending),seconds=round(time.time()-started,3),folder=folder.name)
        if witnesses:later+=inline_witnesses(folder,units,receipts,report,model,witnesses)
    if later:
        # Whisper witnesses need the GPU memory Qwen3-ASR holds.
        del model;import gc;gc.collect();torch.cuda.empty_cache()
        for folder,section,rows in later:whisper_witnesses(folder,section,rows)


OK=('asr_exact_match','nonverbal_not_spoken_text')


def _unresolved(rows,paths):
    from higgs_secondary_verification import apply_secondary
    return {k:rows[k] for k,r in apply_secondary(rows,paths).items() if r['word_check']['status'] not in OK}


def _section(folder):
    """The section a take belongs to: the worker reads its witness reports there (report_paths)."""
    for parent in folder.parents:
        if (parent/'assignment.json').exists() and parent.name.startswith(('chunk-','presentation-','number-groups')):return parent
        if (parent/'manifest.json').exists():return None
    return None


def inline_witnesses(folder,units,receipts,report,model,witnesses):
    """Every independent witness the worker would launch next, heard here in its exact report.

    The worker applied the text-only rules (sound-alike, proper-noun slot, interjections) only
    after this audit, so the glossary/padded witnesses heard all raw flags: 58 per two sections,
    of which the rules alone accept 35 (iteration 9, 2026-10-17; identical rule results in this
    environment). Each later witness hears only what the earlier ones left, the worker's order.
    Reports keep their folders and formats, so the worker finds them complete and launches
    nothing; nothing here decides acceptance.
    """
    import time
    from rimsky.run_events import event
    from higgs_secondary_verification import report_paths
    from audit_higgs_orthography_witness import glossary_witness,primary_witness
    from audit_higgs_padded_asr import padded_witness
    rows={k:{'unit':units[k],'receipt':receipts['takes'][k],'path':str(folder/receipts['takes'][k]['audio']),
             'word_check':report['checks'][k]} for k in report['checks']
          if report['checks'][k]['status'] not in OK and units[k].get('spoken')}
    if not rows:return []
    started=time.time();generation=next(p for p in folder.parents if (p/'manifest.json').exists())
    snapshot=folder/'witness-snapshot.json';write_json(snapshot,rows)
    tag=file_digest(ROOT/'scripts/audit_higgs_orthography_witness.py')[:16]
    primary_witness([snapshot],generation/('secondary-asr-orthography-primary-'+tag))
    remaining=_unresolved(rows,report_paths(folder))
    if remaining:
        snapshot=folder/'witness-qwen-snapshot.json';write_json(snapshot,remaining)
        glossary_witness([snapshot],Path(witnesses['glossary_output']),Path(witnesses['glossary']),model=model)
        padded_witness([snapshot],Path(witnesses['padded_output']),model=model)
    after=_unresolved(remaining,report_paths(folder)) if remaining else {}
    event('asr.inline_witnesses',files=2*len(remaining),flagged=len(rows),rules_accepted=len(rows)-len(remaining),
          remaining=len(after),seconds=round(time.time()-started,3),folder=folder.name)
    section=_section(folder)
    return [(folder,section,after)] if after and section else []


def whisper_witnesses(folder,section,rows):
    """Whisper-small, large-v3-turbo, digit-suppressed turbo, then phone contrast: the worker's early-witness order."""
    import time
    import soundfile as sf
    from rimsky.run_events import event
    from rimsky.audio_join import boundary_check
    from higgs_secondary_verification import report_paths
    import audit_higgs_secondary_asr,audit_higgs_tertiary_asr
    started=time.time()
    snapshot=folder/'witness-whisper-snapshot.json';write_json(snapshot,rows)
    audit_higgs_secondary_asr.audit([snapshot],audit_higgs_secondary_asr.output_folder(section))
    remaining=_unresolved(rows,report_paths(section))
    # The worker's exhausted verification hears only edge-safe takes.
    remaining={k:r for k,r in remaining.items() if not any(boundary_check(*sf.read(r['path'],dtype='float32'))[e] for e in ('unsafe_start','unsafe_end'))}
    turbo=len(remaining)
    if remaining:
        snapshot=folder/'witness-turbo-snapshot.json';write_json(snapshot,remaining)
        audit_higgs_tertiary_asr.audit([snapshot],section/'secondary-asr-large-v3-turbo')
        remaining=_unresolved(remaining,report_paths(section))
    # Digit-suppressed turbo for numbers, then the phone-contrast witness: the worker launched one more
    # process and reloaded wav2vec2 in its own for these (27 s + 47 s, iteration 10, 2026-10-17).
    import re
    import audit_higgs_spelled_asr,audit_higgs_phone_contrast
    digits={(c['unit_id'],c['audio_sha256']) for path in report_paths(section) for c in read(path)['checks'].values()
            if any(ch.isdecimal() for ch in c.get('heard',''))}
    spelled={k:r for k,r in remaining.items() if re.search(r'[0-9]',r['unit']['text']) or (r['unit']['id'],r['receipt']['audio_sha256']) in digits}
    if spelled:
        snapshot=folder/'witness-spelled-snapshot.json';write_json(snapshot,spelled)
        audit_higgs_spelled_asr.audit([snapshot],section/('secondary-asr-spelled-'+file_digest(ROOT/'scripts/audit_higgs_spelled_asr.py')[:16]))
        remaining=_unresolved(remaining,report_paths(section))
    if remaining:
        generation=next(p for p in folder.parents if (p/'manifest.json').exists())
        snapshot=folder/'witness-phone-snapshot.json';write_json(snapshot,remaining)
        audit_higgs_phone_contrast.audit([snapshot],generation/('secondary-asr-phone-contrast-'+file_digest(ROOT/'scripts/audit_higgs_phone_contrast.py')[:16]))
    event('asr.whisper_witnesses',whisper=len(rows),turbo=turbo,spelled=len(spelled),phone=len(remaining),seconds=round(time.time()-started,3),folder=folder.name)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folders',type=Path,nargs='+',required=True)
    p.add_argument('--flags-only',action='store_true')
    p.add_argument('--witness-glossary',type=Path);p.add_argument('--glossary-output',type=Path);p.add_argument('--padded-output',type=Path)
    a=p.parse_args()
    witnesses={'glossary':a.witness_glossary,'glossary_output':a.glossary_output,'padded_output':a.padded_output} if a.witness_glossary else None
    audit([f.resolve() for f in a.folders],a.flags_only,witnesses)
