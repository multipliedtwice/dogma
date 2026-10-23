"""Recognizer-orthography witnesses for persistent Russian word flags.

Two report kinds share the secondary-witness format consumed by
higgs_secondary_verification.apply_secondary, which applies the documented
spelling rules (rimsky.spoken_homophones) before an exact comparison:

* ``primary``: no new recognition. The primary Qwen transcript is re-read under
  the rules; the report says it is not an independent recognizer.
* ``glossary``: Qwen3-ASR-1.7B with a fixed book glossary as context. The
  glossary lists dictionary-unknown words (names, coinages) of the book up to
  the audited chapter, chosen from the text alone and never from a sentence,
  a flag or a primary transcript. No sentence text reaches the recognizer.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read

OPEN=('asr_exact_match','nonverbal_not_spoken_text')
GLOSSARY_MODEL='training-work/models/Qwen3-ASR-1.7B'


def book_glossary(book,last_chapter):
    """Dictionary-unknown words of every chapter up to last_chapter, in order."""
    import pymorphy3
    morph=pymorphy3.MorphAnalyzer();terms={};ids=[c['id'] for c in book['chapters']]
    for chapter in book['chapters'][:ids.index(last_chapter)+1]:
        for block in chapter['blocks']:
            for match in re.finditer(r'[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*',block['text']):
                word=match[0]
                if len(word)>=3 and any(not morph.word_is_known(p.lower()) for p in word.split('-') if len(p)>=2):
                    terms.setdefault(word.casefold(),word)
    return 'Имена и термины книги: '+', '.join(sorted(set(terms.values()),key=str.casefold))+'.'


def write_glossary(book_path,last_chapter,output):
    """Freeze the glossary in the pipeline environment (pymorphy3 lexicon)."""
    import importlib.metadata as metadata
    glossary=book_glossary(read(book_path),last_chapter)
    record={'glossary':glossary,'book_sha256':file_digest(book_path),'last_chapter':last_chapter,
            'rule':'words of >=3 letters with a dictionary-unknown component (pymorphy3), chapters up to last_chapter',
            'lexicon':{n:metadata.version(n) for n in ('pymorphy3','pymorphy3-dicts-ru')},
            'implementation_sha256':file_digest(Path(__file__))}
    path=output/('glossary-'+digest(record)[:16]+'.json')
    if not path.exists():write_json(path,record)
    return path


def flagged(selected_paths):
    for path in selected_paths:
        for uid,row in read(path).items():
            check=row['word_check']
            if check['status'] in OPEN or not row['unit'].get('spoken'):continue
            audio=Path(row['path']);sha=file_digest(audio)
            if sha!=row['receipt']['audio_sha256'] or sha!=check['audio_sha256']:raise ValueError('Witness audio changed')
            yield path,uid,row,check,audio,sha


def entry(result,uid,check,path):
    return {**result,'unit_id':uid,'synthesis_key':check['synthesis_key'],'primary_check':check,
            'selected_file':str(Path(path).resolve()),'selected_sha256':file_digest(path)}


def primary_witness(selected_paths,output):
    config={'model':'primary Qwen3-ASR-1.7B raw transcript; no new recognition','independent_recognizer':False,
            'language':'ru','implementation_sha256':file_digest(Path(__file__)),
            'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('Primary orthography witness changed; use a new output folder')
    for path,uid,row,check,audio,sha in flagged(selected_paths):
        heard=check.get('heard_raw',check.get('raw_verification',{}).get('heard',check.get('heard')))
        if not isinstance(heard,str):continue
        expected=row['unit']['spoken'];key=digest({'audio':sha,'expected':expected,'verifier':signature})
        if key in report['checks']:continue
        report['checks'][key]=entry(transcript_check(expected,heard,sha,signature,normalize_elongations=True),uid,check,path)
    write_json(report_path,report)
    return report


def glossary_witness(selected_paths,output,glossary_path,model=None):
    import torch
    import asr_batching  # stubs the unused aligner tokenizer before qwen_asr loads
    from qwen_asr import Qwen3ASRModel
    frozen=read(glossary_path);glossary=frozen['glossary'];model_path=ROOT/GLOSSARY_MODEL
    from higgs_file_checks import persistent_sha
    files={p.relative_to(ROOT).as_posix():persistent_sha(p) for p in model_path.rglob('*') if p.is_file() and '.cache' not in p.parts}
    config={'model_files':files,'device':'cuda:0','dtype':'bfloat16','attention':'sdpa','language':'ru',
            'max_new_tokens':512,'context':'book glossary of dictionary-unknown words; no sentence text',
            'glossary_sha256':digest(glossary.encode('utf-8')),'glossary_record_sha256':file_digest(glossary_path),
            'glossary_book_sha256':frozen['book_sha256'],'glossary_last_chapter':frozen['last_chapter'],'independent_recognizer':'same weights as primary, different context',
            'implementation_sha256':file_digest(Path(__file__)),'batching_sha256':file_digest(Path(__file__).with_name('asr_batching.py')),
            'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);output.mkdir(parents=True,exist_ok=True);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'glossary':glossary,'checks':{}}
    if report['verifier']!=config:raise ValueError('Glossary witness changed; use a new output folder')
    pending=[]
    for path,uid,row,check,audio,sha in flagged(selected_paths):
        expected=row['unit']['spoken'];key=digest({'audio':sha,'expected':expected,'verifier':signature})
        if key not in report['checks'] and all(p[0]!=key for p in pending):pending.append((key,path,uid,check,audio,sha,expected))
    if not pending:write_json(report_path,report);return report
    # The primary audit passes its already-loaded Qwen3-ASR (same weights) to avoid a reload.
    from asr_batching import last_position_logits
    model=model or last_position_logits(Qwen3ASRModel.from_pretrained(str(model_path),device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa',
                                        local_files_only=True,max_inference_batch_size=32,max_new_tokens=512))
    from asr_batching import transcribe as batched,CONTEXT_BATCH
    for n,((key,path,uid,check,audio,sha,expected),result) in enumerate(batched(model,pending,lambda p:p[4],size=CONTEXT_BATCH,context=[glossary]*len(pending),language='Russian'),1):
        if file_digest(audio)!=sha:raise ValueError('Audio changed during glossary recognition')
        report['checks'][key]=entry(transcript_check(expected,result.text,sha,signature,normalize_elongations=True),uid,check,path)
        print('GLOSSARY ASR',uid,report['checks'][key]['status'],result.text,flush=True)
        if n%CONTEXT_BATCH==0 or n==len(pending):write_json(report_path,report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['primary','glossary'],required=True)
    p.add_argument('--selected',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--glossary',type=Path,help='Frozen glossary record from write_glossary')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    if a.mode=='primary':primary_witness(a.selected,a.output)
    else:glossary_witness(a.selected,a.output,a.glossary)
