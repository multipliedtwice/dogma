"""Use production GPT context analysis for Book 2, independently of audio labels."""
import argparse, json, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.analysis import batches, rejected_answer, review_analysis, synthesis_units
from rimsky.codex_analysis import analyze_with_details
from rimsky.util import write_json


def analyze_with_retry(payload):
    for attempt in range(20):
        try:
            return analyze_with_details(payload)
        except Exception as error:
            detail=str(error).lower()
            if not any(term in detail for term in ('at capacity','rate limit','too many requests','429')) or attempt==19:
                raise
            delay=min(30*2**attempt,300)
            print(f'GPT service temporarily busy; retrying this batch in {delay}s',flush=True)
            time.sleep(delay)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'training-work/character-rb2/pilot')
    parser.add_argument('--all-chapters',action='store_true')
    parser.add_argument('--maximum',type=int,default=9000)
    parser.add_argument('--chapter-ids',nargs='+')
    parser.add_argument('--context-subdir',default='context')
    parser.add_argument('--result-name',default='context.json')
    parser.add_argument('--seed-checkpoint',type=Path)
    parser.add_argument('--seed-context',type=Path,
                        help='Seed series character continuity from an earlier merged context file.')
    args=parser.parse_args()
    folder=args.out.resolve()
    sources=[json.loads(line) for line in (folder/'source/speech-inputs.jsonl').read_text(encoding='utf-8').splitlines()]
    if not args.all_chapters:
        sources=[s for s in sources if s['chapter_id'] in ('c0004','c0005')]
    if args.chapter_ids:
        allowed=set(args.chapter_ids)
        sources=[s for s in sources if s['chapter_id'] in allowed]
    out=folder/args.context_subdir;out.mkdir(exist_ok=True)
    cast=[];rows=[];processed=set();next_checkpoint=0
    checkpoints=sorted(out.glob('*.json'))
    for checkpoint in checkpoints:
        saved=json.loads(checkpoint.read_text(encoding='utf-8'))
        cast=saved['cast'];rows.extend(saved['rows'])
        processed.update(saved.get('target_ids') or
                         [passage['id'] for passage in saved.get('raw',{}).get('passages',[])])
        next_checkpoint=max(next_checkpoint,int(checkpoint.stem)+1)
    if not checkpoints and args.seed_checkpoint:
        seed=json.loads(args.seed_checkpoint.resolve().read_text(encoding='utf-8'))
        cast=seed['cast']
    if not checkpoints and args.seed_context:
        seed=json.loads(args.seed_context.resolve().read_text(encoding='utf-8'))
        cast=seed['characters']
    remaining=[source for source in sources if source['id'] not in processed]
    for offset,targets in enumerate(batches(remaining,maximum=args.maximum)):
        i=next_checkpoint+offset
        first=sources.index(targets[0]);last=sources.index(targets[-1])
        before=sources[max(0,first-3):first];after=sources[last+1:last+4]
        checkpoint=out/f'{i:03d}.json'
        payload={'cast':cast,'before':before,'segments':targets,'after':after}
        for repair in range(5):
            answer,speech_structure=analyze_with_retry(payload)
            parts,updated,invalid=review_analysis(answer,targets,cast,before+after,fixed=speech_structure)
            if not invalid:
                checked=synthesis_units(parts,targets,before+after)
                break
            error=next(iter(invalid.values()))
            if repair==4:
                raise RuntimeError(error)
            print(f'Context batch {i+1} failed validation ({error}); requesting repair {repair+1}/4',flush=True)
            payload.update(retry_error=error,retry_response=rejected_answer(answer,set(invalid)))
        cast=updated;rows.extend(checked)
        write_json(checkpoint,{'rows':checked,'cast':cast,'raw':answer,
                               'speech_structure':speech_structure,
                               'target_ids':[target['id'] for target in targets]})
        print(f'Context batch {i+1}: {len(targets)} targets, {len(cast)} characters',flush=True)
    write_json(folder/args.result_name,{'schema':1,'basis':'production Codex analyzer and validator; text intentions are not recorded audio labels','characters':cast,'passages':rows})

if __name__=='__main__':main()
