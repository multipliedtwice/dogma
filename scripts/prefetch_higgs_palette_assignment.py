"""Prepare future palette decisions while the current prefix renders.

Answers are staged separately. An atomic directory rename installs a complete
checkpoint only if the main renderer has not started that chunk's analysis.
An in-flight/main checkpoint is never overwritten or counted twice.
"""
import argparse
import copy
import json
import os
import time
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from render_higgs_palette_preview import Calls,read,templates,chunk_rows,assignments,direction_context


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--output-name',default='audio-palette-v1')
    parser.add_argument('--voice-descriptors',type=Path);parser.add_argument('--spoken-corrections',type=Path)
    parser.add_argument('--wait-for-cast',action='store_true')
    args=parser.parse_args()
    folder=args.folder.resolve();out=folder/args.output_name
    while args.wait_for_cast and not (out/'manifest.json').exists():
        if (out/'progress.json').exists() and read(out/'progress.json')['status'] in ('failed','superseded'):return
        time.sleep(5)
    cast=read(out/'manifest.json')['cast'];groups=templates(read(args.bank));book=read(folder/'source/book.json')
    units=[unit for chapter in book['chapters'][:3]
           for unit in read(next((folder/'audio-v7'/chapter['id']).rglob('render-plan.json')))['units']]
    regeneration=out/'regeneration-inputs.json'
    context=direction_context(folder,out,book,units,groups,
                              registry_path=args.voice_descriptors,
                              regeneration_path=regeneration if regeneration.exists() else None,
                              spoken_edits_path=args.spoken_corrections)
    calls=Calls()
    for chapter in book['chapters'][:3]:
        cid=chapter['id'];old=read(next((folder/'audio-v7'/cid).rglob('render-plan.json')))
        rows=[]
        for unit in old['units']:
            row={k:copy.deepcopy(unit[k]) for k in ('id','source_id','start','end','text','mode','kind','speaker','chapter_id','scene','sentence_index','analysis_parts') if k in unit}
            template=cast[row['speaker']]['template_id']
            if row['speaker']=='narrator':
                family=unit['render_voice']
                options=[key for key,g in groups.items() if g['voice_family']==family and (g['character']=='narrator' or family=='mechanical_stats')]
                if options:template=options[0]
            if template!='sound_event':row['_template']=template
            rows.append(row)
        blocks={b['id']:b for b in chapter['blocks']}
        for number,batch in enumerate(chunk_rows(rows)):
            if cid=='c0001' and number==0:continue
            if read(out/'progress.json')['status'] in ('failed','complete'):return
            destination=out/cid/f'chunk-{number:03d}'/'direction'
            if destination.exists():continue
            staged=out/'assignment-prefetch'/cid/f'chunk-{number:03d}'
            # A directory may contain only a partial provider checkpoint after
            # an interruption. Always validate/resume it before installation.
            assignments(calls,staged,batch,blocks,cast,groups,context)
            # Check absolute paths before a Windows directory move. Both live
            # under this generation, never the human labeling archive.
            if not staged.resolve().is_relative_to(out.resolve()) or not destination.resolve().is_relative_to(out.resolve()):
                raise ValueError('Prefetch checkpoint escaped generation workspace')
            destination.parent.mkdir(parents=True,exist_ok=True)
            try:os.rename(staged,destination)
            except OSError:
                if not destination.exists():raise
                print('PREFETCH NOT INSTALLED; renderer owns',cid,number,flush=True)
            else:print('PREFETCH READY',cid,number,flush=True)


if __name__=='__main__':main()
