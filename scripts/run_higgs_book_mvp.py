"""Chapter-ordered renderer with overlapping independent CPU word audit."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json,digest
from render_higgs_book_trial import render,assemble
from finalize_higgs_trial_cast import finalize
from rimsky.books import sentence_spans
from higgs_trial_io import read_json
from normalize_higgs_word_audit import normalize
from direct_higgs_scene_turns import direct_chapter
from higgs_character_direction import scene_delivery_profile
from rimsky.nonverbal import animal_vocalization
from regenerate_higgs_book_trial import wait_for_audio_resources

def read(p): return read_json(p)

def sentence_requests(units,blocks):
    by_id={b['id']:b['text'] for b in blocks if b.get('text')}
    spans={key:sentence_spans(text) for key,text in by_id.items()}
    result=[]
    for i,unit in enumerate(units):
        sentence=next((j for j,(a,b) in enumerate(spans[unit['source_id']]) if a<=unit['start']<b),-1)
        item={**unit,'render_seed':20261013+i,'sentence_index':sentence,'analysis_parts':[dict(unit)]}
        previous=result[-1] if result else None
        if (previous and all(previous.get(k)==item.get(k) for k in
               ('source_id','sentence_index','speaker','voice','mood','mode'))
                and previous['end']<=item['start']):
            previous['end']=item['end']
            previous['text']=by_id[item['source_id']][previous['start']:previous['end']]
            previous['analysis_parts'].append(dict(unit))
            if not previous['id'].endswith('-joined'):previous['id']+='-joined'
        else:result.append(item)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--audio-folder',default='audio-v1')
    p.add_argument('--reuse-folder')
    p.add_argument('--verification-mode',choices=['cpu','cuda-after-render'],default='cuda-after-render')
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003'])
    p.add_argument('--retake-limit',type=int,default=0)
    a=p.parse_args();folder=a.folder.resolve()
    source=read(folder/'source/book.json')
    from rimsky.cli_process import run_cli
    from rimsky.codex_analysis import codex_executable
    cli_version=run_cli([codex_executable(),'--version'],cwd=ROOT,timeout=30).stdout.strip()
    for chapter in a.chapters:
        analysis=folder/'analysis'/chapter
        units=analysis/'units.json'
        while not units.exists():
            print('waiting for analysis',chapter,flush=True);time.sleep(30)
        output=folder/a.audio_folder/chapter;output.mkdir(parents=True,exist_ok=True)
        snapshot=output/'assignment.json'
        chapter_source=next(c for c in source['chapters'] if c['id']==chapter)
        scene=0;scene_for={}
        for block in chapter_source['blocks']:
            if block['kind']=='separator':scene+=1
            scene_for[block['id']]=scene
        profile_path=folder/'revision-01/character-profiles.json'
        if not profile_path.exists() or read(profile_path).get('evidence_scope')!='complete_book':
            from direct_higgs_book_revision import profiles as prepare_profiles
            # Seed only identities, not frozen voice choices. The full-book
            # prepass supplies later nature/alias/voice facts before casting.
            identities={}
            for cast_file in sorted((folder/'analysis').glob('*/cast.json')):
                for identity in read(cast_file):identities[identity['id']]=identity
            if not identities:raise ValueError('Character identity analysis is not ready')
            revision='profile-first-'+digest({'source':file_digest(folder/'source/book.json'),'identities':identities})[:16]
            seed=folder/revision/'identity-seed.json';write_json(seed,identities)
            profile_path=folder/revision/'character-profiles.json'
            if not profile_path.exists():prepare_profiles(folder,True,revision_name=revision,cast_input=seed)
            if not profile_path.exists():raise ValueError('Whole-book character prepass did not finish')
        profile_package=read(profile_path)
        if profile_package.get('source_sha256')!=file_digest(folder/'source/book.json'):
            raise ValueError('Character profiles are stale for this book source')
        personas=profile_package['profiles']
        cast=finalize(folder,chapter,cli_version,profiles=personas)
        chapter_units=[{**u,'scene':scene_for[u['source_id']],
                       'voice':u.get('narration_voice') or cast[u['speaker']]['voice']} for u in read(units)]
        chapter_units=sentence_requests(chapter_units,chapter_source['blocks'])
        # Complete source-bound direction is mandatory before any audio. Sparse
        # correction files cannot silently make an omitted turn ordinary.
        direction=direct_chapter(folder,chapter,chapter_units,chapter_source['blocks'],personas,run=True)
        if direction is None:raise ValueError('Complete scene direction is not ready')
        for unit in chapter_units:
            if unit['mode']=='speech':
                unit['scene_direction']=direction['by_unit'][unit['id']]
                intent=animal_vocalization(unit['text'],personas[unit['speaker']])
                if intent:
                    unit['nonverbal_intent']=intent
                    if unit['scene_direction']['audio_kind']!='nonverbal':
                        raise ValueError('Scene direction misclassified a source-backed animal vocalization')
                else:
                    unit['delivery_profile']=scene_delivery_profile(unit['speaker'],unit['scene_direction'],
                                                                    personas[unit['speaker']],file_digest(profile_path))
                    unit['mood']='baseline'
        used={u['speaker'] for u in chapter_units}
        assigned={'source_sha256':source['source_hash'],'cast':[v for k,v in cast.items() if k in used],
                  'units':chapter_units,'analysis_units_sha256':file_digest(units),
                  'structure_sha256':file_digest(analysis/'speech-structure.json')}
        if snapshot.exists() and read(snapshot)!=assigned: raise ValueError('Chapter assignment changed')
        write_json(snapshot,assigned)
        audit_python=ROOT/'training-work/.venv-asr/Scripts/python.exe'
        if a.verification_mode=='cuda-after-render':
            reuse=folder/a.reuse_folder/chapter if a.reuse_folder else None
            render(snapshot,output,[chapter],reuse_folder=reuse,execution_guard=wait_for_audio_resources)
            print('CHAPTER AUDIO COMPLETE',chapter,'GPU word audit queued',flush=True)
            continue
        with (output/'audit.log').open('a',encoding='utf-8') as log:
            audit=subprocess.Popen([str(audit_python),'-X','utf8','-u',str(ROOT/'scripts/audit_higgs_book_trial.py'),
                                    '--folder',str(output),'--follow'],stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
            try:
                reuse=folder/a.reuse_folder/chapter if a.reuse_folder else None
                render(snapshot,output,[chapter],reuse_folder=reuse,execution_guard=wait_for_audio_resources)
                while audit.poll() is None:
                    print('finishing word audit',chapter,flush=True);time.sleep(30)
                if audit.returncode: raise ValueError('Word audit failed; see '+str(output/'audit.log'))
            finally:
                if audit.poll() is None: audit.terminate();audit.wait()
        receipt=read(output/'receipts.json');checks=read(output/'word-checks.json')['checks']
        for key,row in receipt['takes'].items():
            check=checks[key]
            if check['audio_sha256']!=row['audio_sha256'] or check['synthesis_key']!=row['key']:
                raise ValueError('Word audit does not bind to take')
            row['word_status']=check['status']
        write_json(output/'receipts.json',receipt)
        assemble(output,read(output/'render-plan.json'),receipt)
        exact=sum(c['status']=='asr_exact_match' for c in checks.values())
        write_json(output/'chapter-quality.json',{'word_checked':len(checks),'asr_exact':exact,
            'asr_flags':len(checks)-exact,'flags_are_not_proven_synthesis_errors':True,
            'production_approved':False,'candidate_mood_controls':True})
        print('CHAPTER COMPLETE',chapter,'words exact',exact,'/',len(checks),flush=True)
    if a.verification_mode=='cuda-after-render':
        outputs=[folder/a.audio_folder/c for c in a.chapters]
        wait_for_audio_resources()
        subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                        str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',*[str(x) for x in outputs]],check=True)
        for output in outputs:
            receipt=read(output/'receipts.json');report=normalize(output);checks=report['checks']
            if report['plan_sha256']!=receipt['plan_sha256']:raise ValueError('Final word audit/plan mismatch')
            for key,row in receipt['takes'].items():
                check=checks[key]
                if check['audio_sha256']!=row['audio_sha256'] or check['synthesis_key']!=row['key']:
                    raise ValueError('Final word audit does not bind to take')
                row['word_status']=check['status'];row['word_verifier_sha256']=check['verifier_sha256']
            write_json(output/'receipts.json',receipt)
            assemble(output,read(output/'render-plan.json'),receipt)
            exact=sum(c['status']=='asr_exact_match' for c in checks.values())
            write_json(output/'chapter-quality.json',{'word_checked':len(checks),'asr_exact':exact,
                'asr_flags':len(checks)-exact,'flags_are_not_proven_synthesis_errors':True,
                'production_approved':False,'candidate_mood_controls':True,'word_verifier':report['verifier']})
            print('CHAPTER COMPLETE',output.name,'words exact',exact,'/',len(checks),flush=True)
            if a.retake_limit:
                subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/retake_higgs_book_trial.py'),
                                '--folder',str(output),'--limit',str(a.retake_limit)],check=True)

if __name__=='__main__':main()
