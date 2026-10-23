"""Revalidate a preserved provider response after a local validator correction.

No answer values are edited and no provider call is made. Exact request bytes,
schema, source citations and current casting constraints are checked first.
"""
import argparse
import copy
from pathlib import Path
from render_higgs_palette_preview import read,templates,direction_context,cast_book,ROOT
from rimsky.analysis import mismatch
from rimsky.util import file_digest,write_json


class PrimaryDone(Exception):pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--output-name',required=True)
    parser.add_argument('--bank',type=Path,required=True);parser.add_argument('--voice-descriptors',type=Path,required=True)
    parser.add_argument('--spoken-corrections',type=Path,required=True);parser.add_argument('--invalid-answer',type=Path,required=True)
    args=parser.parse_args();folder=args.folder.resolve();out=folder/args.output_name
    groups=templates(read(args.bank));book=read(folder/'source/book.json')
    fields=('id','source_id','start','end','text','mode','kind','speaker','chapter_id','scene','sentence_index','analysis_parts')
    units=[{k:copy.deepcopy(u[k]) for k in fields if k in u} for cid in ['c0001','c0002','c0003']
           for u in read(next((folder/'audio-v7'/cid).rglob('render-plan.json')))['units']]
    context=direction_context(folder,out,book,units,groups,registry_path=args.voice_descriptors,spoken_edits_path=args.spoken_corrections)
    source=args.invalid_answer.resolve()
    if source.parent!=context['cast_out'].resolve():raise ValueError('Invalid response is not in this frozen cast checkpoint')
    class Revalidate:
        def call(self,dest,name,system,payload,schema,validate):
            if name!='primary':raise PrimaryDone()
            request=dest/'primary.request.json';bound=read(request)
            if bound['system']!=system or bound['request']!=payload or bound['schema']!=schema:
                raise ValueError('Frozen request changed; old response cannot be revalidated')
            answer=read(source)['answer']
            issue=mismatch(answer,schema,'cast')
            if issue:raise ValueError(issue)
            validate(answer)
            checkpoint={'request_sha256':file_digest(request),'answer':answer,'revalidation':{
                'source_response_sha256':file_digest(source),'source_response':source.name,
                'validator_sha256':file_digest(ROOT/'scripts/render_higgs_palette_preview.py'),
                'reason':'Russian disclosure of missing hoarseness is valid; no provider answer fields changed'}}
            write_json(dest/'primary.answer.json',checkpoint)
            print('REVALIDATED UNCHANGED PROVIDER RESPONSE; all schema, source and sound constraints pass')
            return answer
    try:cast_book(Revalidate(),folder,book,units,groups,context)
    except PrimaryDone:pass


if __name__=='__main__':main()
