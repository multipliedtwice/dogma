"""Reassemble a chapter with one explicitly preferred punctuation take."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json,now
from rimsky.audio_join import boundary_check
from render_higgs_palette_preview import publish_prefix,PAGE
from higgs_reviewed_take_overrides import validate_punctuation_take,apply_overrides


def read(path):return json.loads(path.read_text('utf8'))


def load_takes(chapter_folder):
    import soundfile as sf
    result=[]
    for chunk in sorted(chapter_folder.glob('chunk-*')):
        assignment=read(chunk/'assignment.json');chosen={}
        initial=sorted(chunk.glob('take-*'),key=lambda p:int(p.name.split('-')[-1]))
        recovered=sorted((chunk/'word-omission-recovery').glob('take-*'),key=lambda p:int(p.name.split('-')[-1]))
        recovered+=sorted((chunk/'word-phrasing-recovery').glob('take-*'),key=lambda p:int(p.name.split('-')[-1]))
        for take in initial+recovered:
            if not (take/'word-checks-1.7B-normalized.json').exists():continue
            plan=read(take/'render-plan.json');receipts=read(take/'receipts.json')
            audit=read(take/'word-checks-1.7B-normalized.json')
            if receipts['plan_sha256']!=file_digest(take/'render-plan.json') or audit['plan_sha256']!=receipts['plan_sha256']:
                raise ValueError('Saved chapter plan/audit changed')
            for unit in plan['units']:
                receipt=receipts['takes'][unit['id']];check=audit['checks'][unit['id']];wav=take/receipt['audio']
                if (receipt['key']!=digest(unit['synthesis']) or check['synthesis_key']!=receipt['key']
                    or check['audio_sha256']!=receipt['audio_sha256'] or file_digest(wav)!=receipt['audio_sha256']):
                    raise ValueError('Saved chapter take/audit changed')
                x,sr=sf.read(wav,dtype='float32');edge=boundary_check(x,sr)
                if edge['unsafe_start'] or edge['unsafe_end']:continue
                exact=check['status'] in ('asr_exact_match','nonverbal_not_spoken_text')
                previous=chosen.get(unit['id'])
                if not previous or exact or previous['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text'):
                    chosen[unit['id']]={'unit':unit,'receipt':receipt,'word_check':check,'edge':edge,'path':str(wav)}
        if any(u['id'] not in chosen for u in assignment['units']):raise ValueError('Incomplete checked chapter takes')
        result.extend(chosen[u['id']] for u in assignment['units'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--unit',required=True);parser.add_argument('--chapter',default='c0001')
    parser.add_argument('--word-recovery',action='store_true')
    parser.add_argument('--stress-approved',action='store_true')
    args=parser.parse_args();folder=args.folder.resolve();comparison=args.comparison.resolve();cid=args.chapter
    state=read(folder/'progress.json');before_state=copy.deepcopy(state)
    if not state['chapters'][cid]['complete']:raise ValueError('This correction requires a complete published chapter')
    rows=load_takes(folder/cid);before=next(r for r in rows if r['unit']['id']==args.unit)
    plan=read(comparison/'render-plan.json');unit=next(u for u in plan['units'] if u['id']==args.unit)
    receipt=read(comparison/'receipts.json')['takes'][args.unit]
    check=read(comparison/'word-checks-1.7B-normalized.json')['checks'][args.unit]
    after={'unit':unit,'receipt':receipt,'word_check':check,'path':str(comparison/receipt['audio'])}
    after['edge']=validate_punctuation_take(before,after,listener_stress_approved=args.stress_approved)
    gains=read(folder/'frozen-gains.json')
    registry_path=folder/'reviewed-take-overrides.json'
    if registry_path.exists() and args.unit in read(registry_path)['units']:
        apply_overrides(folder,cid,rows)
        print('ALREADY RECORDED',args.unit,flush=True);return
    # Prove reconstruction reuses exactly the published audio before changing
    # a sentence. A partially rewritten selected.json after a restart is not
    # trusted; selection is rebuilt from the bound take/audit checkpoints.
    baseline=publish_prefix(folder/cid,cid,rows,gains,state,True,verify_only=True)
    if baseline['wav_sha256']!=before_state['chapters'][cid]['wav_sha256']:
        raise ValueError('Reconstructed original differs from published chapter; correction not applied')
    revision=folder/'sentence-revisions'/args.unit;revision.mkdir(parents=True,exist_ok=True)
    write_json(revision/'previous-progress.json',before_state)
    shutil.copyfile(folder/cid/(cid+'.timeline.json'),revision/'previous-timeline.json')
    answer=comparison/('answers-word-recovery-20261014.json' if args.word_recovery else 'answers-listener-20261014.json')
    feedback={'source':'automatic_word_recovery' if args.word_recovery else 'explicit_listener',
        'verdict':'word_verified' if args.word_recovery else 'approved',
        'approval_scope':'stress_of_exact_take' if args.stress_approved else 'exact_take',
        'unit_id':args.unit,'audio_sha256':receipt['audio_sha256'],'created':now(),
        'verbatim':'по́том fix worked correctly, we can use this method to fix stress issues' if args.stress_approved else
            'In this piece "все" is missing from output, why it disappeared?' if args.word_recovery else
            "second version sounds better, at least not as 2 separate sentences. I suggest we remove commas for this weird cases",
        'limit':'One reviewed punctuation take, not a global comma-deletion rule or corpus approval.'}
    if args.stress_approved:
        feedback.update(pronunciation_status='listener_verified',word_audit_resolved=False,
                        limit='Listener approved stress in this exact take. The ASR word flag is retained; this is not corpus approval.')
    if not answer.exists():write_json(answer,feedback)
    registry=read(registry_path) if registry_path.exists() else {'schema':1,'generation':folder.name,
        'source_sha256':read(folder/'manifest.json')['source_sha256'],'units':{}}
    registry['units'][args.unit]={'chapter':cid,'previous_audio_sha256':before['receipt']['audio_sha256'],
        'previous_synthesis_key':before['receipt']['key'],'listener_answer':str(answer),
        'listener_answer_sha256':file_digest(answer),'replacement':after}
    write_json(registry_path,registry)
    publish_prefix(folder/cid,cid,rows,gains,state,True,force_revision=True)
    state['chapters'][cid]['reviewed_corrections']=sorted(k for k,v in registry['units'].items() if v['chapter']==cid)
    state['status']=before_state['status'];state['active_chapter']=before_state.get('active_chapter',cid)
    write_json(folder/'progress.json',state);write_json(PAGE/'progress.json',state)
    write_json(revision/'applied.json',{'created':now(),'old_chapter':before_state['chapters'][cid],
        'new_chapter':state['chapters'][cid],'old_take_sha256':before['receipt']['audio_sha256'],
        'new_take_sha256':receipt['audio_sha256'],'reused_other_takes':len(rows)-1,
        'source_text_unchanged':True,'effects':0,'registry_sha256':file_digest(registry_path)})
    report=read(comparison/'comparison.json')
    report.update(listener_approved=not args.word_recovery,listener_answer_sha256=file_digest(answer),
                  word_recovery_verified=args.word_recovery)
    if args.stress_approved:report.update(pronunciation_verified=True,pronunciation_method='combining_acute',word_audit_resolved=False)
    write_json(comparison/'comparison.json',report)
    print('UPDATED ONE SENTENCE; REUSED',len(rows)-1,'OTHER TAKES',flush=True)


if __name__=='__main__':main()
