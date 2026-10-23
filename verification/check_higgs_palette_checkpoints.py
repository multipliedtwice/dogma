"""Read-only replay of frozen cast/delivery checkpoints without provider calls."""
import argparse
import copy
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import render_higgs_palette_preview as preview


class FrozenCalls:
    count=0

    def call(self,dest,name,system,payload,schema,validate):
        path=dest/(name+'.request.json')
        request=preview.read(path)
        for field,current in [('system',system),('request',payload),('schema',schema)]:
            if request[field]!=current:raise ValueError(f'Checkpoint {name}: {field} changed')
        saved=preview.read(dest/(name+'.answer.json'))
        if saved['request_sha256']!=preview.file_digest(path):raise ValueError('Unbound checkpoint')
        validate(saved['answer']);self.count+=1
        return saved['answer']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--output-name',default='audio-palette-v1')
    parser.add_argument('--voice-descriptors',type=Path)
    parser.add_argument('--spoken-corrections',type=Path)
    args=parser.parse_args();folder=args.folder.resolve();out=folder/args.output_name
    book=preview.read(folder/'source/book.json');groups=preview.templates(preview.read(args.bank))
    fields=('id','source_id','start','end','text','mode','kind','speaker','chapter_id','scene','sentence_index','analysis_parts')
    chapters={cid:preview.read(next((folder/'audio-v7'/cid).rglob('render-plan.json')))['units']
              for cid in ['c0001','c0002','c0003']}
    all_rows=[{k:copy.deepcopy(u[k]) for k in fields if k in u} for units in chapters.values() for u in units]
    frozen=FrozenCalls();batches=0
    with patch.object(preview,'write_json'):
        context=preview.direction_context(folder,out,book,all_rows,groups,
            registry_path=args.voice_descriptors,spoken_edits_path=args.spoken_corrections)
        cast=preview.cast_book(frozen,folder,book,all_rows,groups,context)
        if cast!=preview.read(out/'manifest.json')['cast']:raise ValueError('Frozen cast changed')
        for cid,originals in chapters.items():
            rows=[]
            for unit in originals:
                row={k:copy.deepcopy(unit[k]) for k in fields if k in unit}
                template=cast[row['speaker']]['template_id']
                if row['speaker']=='narrator':
                    options=[k for k,g in groups.items() if g['voice_family']==unit['render_voice']
                             and (g['character']=='narrator' or unit['render_voice']=='mechanical_stats')]
                    if options:template=options[0]
                if template!='sound_event':row['_template']=template
                rows.append(row)
            blocks={b['id']:b for c in book['chapters'] if c['id']==cid for b in c['blocks']}
            for number,batch in enumerate(preview.chunk_rows(rows)):
                dest=out/cid/f'chunk-{number:03d}'
                if not (dest/'direction/decisions.json').exists():continue
                assigned=preview.assignments(frozen,dest/'direction',batch,blocks,cast,groups,context)
                if (dest/'assignment.json').exists() and assigned!=preview.read(dest/'assignment.json')['units']:
                    raise ValueError(f'Assignment changed: {cid}/{number}')
                batches+=1
    print(f'Frozen replay passed: {frozen.count} requests; {batches} completed direction batches. No writes or provider calls.')


if __name__=='__main__':main()
