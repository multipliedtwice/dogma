"""Collect verified spoken-text plans into a book-level carried-forward fix list.

A plan that produced a word-verified take is keyed by the exact source wording it
applies to (plan['expected_spoken'], i.e. the unit's spoken text before the edit),
so later generations apply it at preparation instead of re-discovering it by retakes.
Plans are copied verbatim; apply_spoken_edit still validates them at use.
"""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
SKIP=('word-','take-','prior','batched','delegated')
DEFAULT=ROOT/'training-work/elirm-iv-higgs-mvp'

def collect(root):
    """Return {spoken: (rank, entry)} for the earliest verified take per wording."""
    best={}
    for generation in sorted(root.glob('audio-palette-*')):
        if not generation.is_dir():continue
        for path in sorted(generation.rglob('selected.json')):
            if any(part.startswith(SKIP) for part in path.relative_to(generation).parts[:-1]):continue
            try:data=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            if not isinstance(data,dict):continue
            for unit_id,row in data.items():
                if not isinstance(row,dict):continue
                unit=row.get('unit') or {};check=row.get('word_check') or {}
                plan=unit.get('spoken_text_plan')
                if not plan or check.get('status')!='asr_exact_match':continue
                spoken=plan.get('expected_spoken')
                if not isinstance(spoken,str) or not spoken:continue
                receipt=row.get('receipt') or {}
                rank=(unit.get('retake_number') if isinstance(unit.get('retake_number'),int) else 10**9,
                      generation.name,path.relative_to(root).as_posix(),unit_id)
                entry={'spoken_text_plan':plan,'source':{'generation':generation.name,
                    'selected_file':path.relative_to(root).as_posix(),'unit_id':unit.get('id',unit_id),
                    'audio_sha256':check.get('audio_sha256') or receipt.get('audio_sha256'),
                    'verified_via':check.get('independent_report') or 'primary'}}
                if spoken not in best or rank<best[spoken][0]:best[spoken]=(rank,entry)
    return best

def build(root,output=None):
    best=collect(root)
    result={'schema':1,'book_source_sha256':file_digest(root/'source/book.json'),
            'entries':{k:best[k][1] for k in sorted(best)}}
    write_json(output or root/'book-fixes.json',result)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,default=DEFAULT)
    ap.add_argument('--output',type=Path)
    a=ap.parse_args();r=build(a.root.resolve(),a.output)
    print('entries',len(r['entries']))
