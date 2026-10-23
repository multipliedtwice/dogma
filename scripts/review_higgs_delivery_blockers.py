"""Freeze delivery failures and request an isolated, actionable Opus code review."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import file_digest,write_json
from rimsky.claude_analysis import structured_call
from higgs_trial_io import read_json as read

def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
TEXT={'type':'string'}
SYSTEM=('You are Claude Opus, the independent reviewer of a repeatedly failing audiobook delivery pipeline. '
    'The user asked for Elirm IV chapters 1-3 with music and zero unresolved word flags. Only chapter 1 is '
    'published with 52 flags; repeated bounded seed/punctuation retries have consumed hours. '
    'Review the supplied source code and actual evidence. Find concrete defects and propose the smallest '
    'implementable recovery plan that makes measurable progress, not another unbounded retry loop. '
    'You cannot hear audio or execute code. Separate real synthesis errors, ASR orthography/language errors, '
    'and selection/publication/scheduling errors. Do not approve audio from text, weaken gates, hide counters, '
    'skip words, modify protected labels/source, or enable any Higgs vocal effects. '
    'No relabeling exercise for the user. Existing reviewed voice/mood palette is fixed. '
    'Address whether current exact-ASR acceptance is a defensible operational goal and how to validate '
    'any narrow spoken equivalence or additional verifier against missing/substituted-word negatives. '
    'Prioritize a verified first playable prefix and updating the historical chapter-1 flags. '
    'Cite supplied file/functions and reproduce each confirmed bug. Give a concrete first action.')

def run(generation,output):
    output.mkdir(parents=True,exist_ok=True);path=output/'request.json'
    schema=obj({'diagnosis':TEXT,'confirmed_defects':{'type':'array','items':obj({
        'id':TEXT,'severity':TEXT,'file_and_function':TEXT,'evidence':TEXT,'reproduction':TEXT,
        'fix':TEXT,'regression_test':TEXT})},'recovery_plan':{'type':'array','items':obj({
        'step':TEXT,'implementation':TEXT,'why_new_evidence_or_progress':TEXT,'acceptance_check':TEXT})},
        'limits_and_unproven_claims':{'type':'array','items':TEXT},'first_action':TEXT})
    if not path.exists():
        files=['scripts/render_higgs_palette_preview.py','scripts/higgs_exhausted_word_recovery.py',
            'scripts/higgs_previous_verified_takes.py','scripts/higgs_secondary_verification.py',
            'scripts/higgs_verified_word_publication.py','scripts/higgs_edge_recovery.py',
            'scripts/audit_higgs_english_asr.py','scripts/supervise_higgs_preview.py',
            'scripts/normalize_higgs_word_audit.py','runtime/rimsky/word_verification.py']
        selections=[]
        for p in sorted(generation.glob('c*/chunk-*/**/selected.json')):
            if any(v.startswith('word-') or v.startswith('take-') for v in p.parts):continue
            data=read(p);bad=[r for r in data.values() if r['word_check']['status'] not in ('asr_exact_match','nonverbal_not_spoken_text')]
            chosen=bad[:4] if 'chunk-000' in p.parts else bad[:1]
            selections.append({'path':str(p.relative_to(generation)),'sha256':file_digest(p),
                'selected_count':len(data),'unresolved_count':len(bad),'examples':[
                    {'unit_id':r['unit']['id'],'source_text':r['unit']['text'],'native_text':r['unit']['synthesis']['text'],
                     'spoken':r['unit']['spoken'],'voice':r['unit']['voice'],'mood':r['unit']['mood'],
                     'path':r['path'],'audio_sha256':r['receipt']['audio_sha256'],'check':r['word_check'],'edge':r.get('edge')}
                    for r in chosen]})
        state=read(generation/'progress.json')
        payload={'objective':'Deliver all requested chapters with real quality evidence, no manual clip-by-clip decisions.',
            'state':state,'supervision':read(generation/'supervision.json'),'selections':selections,
            'source_code':{f:{'sha256':file_digest(ROOT/f),'text':(ROOT/f).read_text('utf8')} for f in files},
            'recent_changes':'English passages now receive pinned audio-only English ASR. Unsafe unterminated parts '
                'receive bounded comma/period recovery before section hold. Both just began on chapter 3 prefix. '
                'All earlier recovery failures and published flags remain. Worker ordering is now chapter3,2,1; '
                'whole chapter traversal means chapter1 repairs wait behind held later sections.'}
        write_json(path,{'system':SYSTEM,'request':payload,'schema':schema,'model':'claude-opus-5-5','effort':'high'})
    frozen=read(path);answer_path=output/'answer.json'
    if answer_path.exists():
        answer=read(answer_path)
        if answer['request_sha256']!=file_digest(path):raise ValueError('Review binding changed')
        print('REUSED OPUS REVIEW',answer['metadata'].get('model_usage'),flush=True);return
    metadata={};print('OPUS DELIVERY BLOCKER REVIEW',file_digest(path),'bytes',path.stat().st_size,flush=True)
    answer=structured_call(frozen['system'],frozen['request'],frozen['schema'],model='claude-opus-5-5',
        effort='high',timeout=1200,metadata=metadata)
    write_json(answer_path,{'request_sha256':file_digest(path),'answer':answer,'metadata':metadata,
        'scope':'code and evidence review only; no audio listening or audio approval'})
    print('OPUS REVIEW COMPLETE',metadata.get('model_usage'),len(answer['confirmed_defects']),'defects',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--generation',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.generation.resolve(),a.output.resolve())
