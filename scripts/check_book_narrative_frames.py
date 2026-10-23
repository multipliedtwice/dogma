"""Immutable source-bound narrative-frame pre-pass using the isolated provider."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.narrative_frames import SYSTEM,request_for,schema_for,checked_ranges,reconcile,adjudicate
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json
import prepare_higgs_book_trial as preparation

def detect(source,output,chapters):
    book=read_json(source/'book.json');output.mkdir(parents=True,exist_ok=True)
    source_hash=file_digest(source/'book.json');manifest_path=output/'manifest.json'
    manifest={'source_sha256':source_hash,'chapters':chapters,'schema':1}
    if manifest_path.exists() and read_json(manifest_path)!=manifest:raise ValueError('Narrative frame source/scope changed; use a new folder')
    write_json(manifest_path,manifest);by_source={};reports={}
    for chapter in chapters:
        folder=output/chapter;folder.mkdir(exist_ok=True)
        request=request_for(book,chapter);schema=schema_for(request)
        validate=lambda value:checked_ranges(value,request)
        primary=preparation.call(folder,'primary',SYSTEM,request,schema,validate)
        critic=preparation.call(folder,'critic',SYSTEM+' Independently inspect every range and challenge the proposed boundaries and registers.',
                                 {**request,'proposed_ranges':primary},schema,validate)
        report=reconcile(primary,critic,request)
        if any(f['status']=='needs_adjudication' for f in report['by_source'].values()):
            resolution=preparation.call(folder,'adjudication',SYSTEM+
                ' Resolve primary/critic disagreements using source evidence. Preserve already checked ranges; this is a distinct tie-break, not a vote.',
                {**request,'primary':primary,'critic':critic,'checked':report['by_source']},schema,
                lambda value:adjudicate(report,value,request))
            report=adjudicate(report,resolution,request)
        report['source_sha256']=source_hash
        write_json(folder/'report.json',report);by_source.update(report['by_source']);reports[chapter]=file_digest(folder/'report.json')
    result={'schema':1,'source_sha256':source_hash,'chapters':reports,'by_source':by_source,'production_approved':False}
    write_json(output/'frames.json',result);return result

if __name__=='__main__':
    from rimsky.codex_analysis import codex_executable
    from rimsky.cli_process import run_cli
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--chapters',nargs='+',default=['c0001'])
    p.add_argument('--run',action='store_true',help='Send the source-bound requests through the isolated online provider.')
    a=p.parse_args()
    if a.run:
        preparation.CLI_VERSION=run_cli([codex_executable(),'--version'],cwd=ROOT,timeout=30).stdout.strip()
        detect(a.source.resolve(),a.output.resolve(),a.chapters)
    else:
        book=read_json(a.source/'book.json');a.output.mkdir(parents=True,exist_ok=True)
        requests=[{'system':SYSTEM,'request':request_for(book,c),'schema':schema_for(request_for(book,c))} for c in a.chapters]
        write_json(a.output/'plan.json',{'source_sha256':file_digest(a.source/'book.json'),'sent':False,
            'primary_requests':requests,'critic':'Built from each validated primary answer; adjudication only on disagreement.'})
        print('OFFLINE PLAN',a.output/'plan.json')
