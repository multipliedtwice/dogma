"""Write a private, source-bound ambiguity inventory from completed context preparation.

No provider, speech, recognition or GPU jobs. This reports detected dictionary
alternatives, conditional checks and self-rated uncertainty, never audio approval.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import build_contextual_stress as builder
from rimsky import contextual_stress as rules
from rimsky.homograph_stress import occurrences,ACUTE
from rimsky.util import file_digest,digest,atomic_write

POLICY='private-pronunciation-ambiguity-report-v2'
SELF=Path(__file__).resolve()


def immutable(path,data):
    """Atomic artifacts, byte-exact on resume; never replace an earlier report."""
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Immutable ambiguity report changed; use a new output: '+str(path))
    else:atomic_write(path,data)


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')


def assignment_locations(path):
    if path is None:return None
    value=builder.read(path);assignment=value.get('assignment',value);units=assignment['units'];seen=set();result={}
    for unit in units:
        if unit['id'] in seen:raise ValueError('Assignment has duplicate unit IDs')
        seen.add(unit['id'])
        source=unit.get('spoken',unit['text'])
        result.setdefault(digest(source.encode('utf8')),[]).append(unit)
    return result


def location(unit,row,source):
    result={k:unit[k] for k in ('id','chapter_id','source_id','start','end') if k in unit}
    if unit.get('text')==source and type(unit.get('start')) is int:
        result['word_source_start']=unit['start']+row['start'];result['word_source_end']=unit['start']+row['end']
        result['word_source_mapping']='exact original text'
    else:result['word_source_mapping']='prepared spoken offsets only; original unit range retained separately'
    return result


def readable(report):
    lines=['PRIVATE PRONUNCIATION AMBIGUITY REPORT',
        'Text preparation only. No audio/recognition/listening approval; confidence is self-rated, not calibrated.',
        'Scope: detected pinned-dictionary stress alternatives, not every possible natural-language ambiguity.',
        f"Detected prepared occurrences: {report['counts']['prepared_occurrences']}",
        f"Recorded input observations: {report['counts']['input_observations']}",
        f"Context policy: {report['context_policy']}",'']
    for n,row in enumerate(report['occurrences'],1):
        lines.extend([f"{n}. {row['surface']} — occurrence {row['word_occurrence_index']}",
            'Possible readings: '+' | '.join(row['possible_readings']),
            f"Luna choice: {row['luna']['stressed']} (raw: {row['luna']['raw_model_stressed']})",
            'Reason: '+row['luna']['basis'],'Sentence: '+row['sentence'],
            f"Prepared source: {row['source_sha256']} [{row['prepared_start']}:{row['prepared_end']}]",
            'Locations: '+json.dumps(row['locations'],ensure_ascii=False)])
        pair=row['luna'].get('two_luna')
        if pair:
            lines.append(f"Route: {pair['route']}; first self-rated confidence={pair['first_confidence']}; "
                         f"agreement={pair['agreement']}; authority={pair['selected_pass']}")
            for vote in pair['passes']:lines.append(f"  {vote['pass_id']}: {vote['choice']} — {vote['basis']} ({vote['metadata']['model']}, {vote['metadata']['effort']})")
        for observed in row['input_observations']:
            lines.append(f"Selected input: {observed['before_context']} → {row['luna']['stressed']}; "
                         f"superseded marked input: {observed['superseded_automatic_mark']}")
        lines.append('')
    return '\n'.join(lines)+'\n'


def report(answer_path,output,assignment_path=None):
    answer_path=answer_path.resolve();output=output.resolve()
    if assignment_path is not None:assignment_path=assignment_path.resolve()
    block=builder.block(answer_path)  # Rechecks source/schema/CLI/code/raw evidence, never calls a provider.
    inputs_path=answer_path.parent/'inputs.json';saved=builder.read(inputs_path)
    observations={};inputs_by_hash={}
    for item in saved['inputs']:
        source_hash=digest(item['source'].encode('utf8'))
        required=rules.analyze(item['source'],item['selected'],item.get('names'),item.get('common',()))
        _spoken,proof=rules.apply(item['source'],item['selected'],block,item.get('names'),item.get('common',()))
        selected={(key,index):match[0] for match,key,index in occurrences(item['selected'])}
        applied={(r['word'].casefold().replace('ё','е'),r['index']):r for r in proof['applied']}
        inputs_by_hash.setdefault(source_hash,[]).append(item)
        for row in required:
            key=(source_hash,row['word'],row['index']);before=selected[(row['word'],row['index'])]
            details={k:item[k] for k in ('chapter','chapter_id','unit','unit_id','source_id') if k in item}
            observations.setdefault(key,[]).append({'location':details,'selected_sha256':row['selected_sha256'],
                'before_context':before,'unmarked_before_context':ACUTE not in before,
                'superseded_automatic_mark':applied[(row['word'],row['index'])]['superseded_automatic_mark'],
                'history_scope':'Observed selected-input snapshot only; earlier model/guard history is not inferred.'})
    indexed=assignment_locations(assignment_path);rows=[]
    for decision in block['decisions']:
        key=(decision['source_sha256'],decision['word'],decision['index'])
        if key not in observations:raise ValueError('Answer has an occurrence not detected in its bound inputs')
        sources=inputs_by_hash[decision['source_sha256']];source=sources[0]['source']
        if source[decision['start']:decision['end']]!=decision['surface']:raise ValueError('Report source span changed')
        if indexed is not None:
            units=indexed.get(decision['source_sha256'],[])
            if not units:raise ValueError('Assignment does not contain a reported prepared source')
            locations=[location(unit,decision,source) for unit in units]
        else:
            locations=[{k:item[k] for k in ('chapter','chapter_id','unit','unit_id','source_id') if k in item} for item in sources]
        rows.append({'source_sha256':decision['source_sha256'],'prepared_start':decision['start'],'prepared_end':decision['end'],
            'word':decision['word'],'word_occurrence_index':decision['index'],'surface':decision['surface'],
            'selected_word':decision['selected_word'],'possible_readings':decision['candidates'],
            'dictionary_forms':decision['dictionary_forms'],'sentence':decision['sentence'],'locations':locations,
            'luna':{k:decision[k] for k in ('stressed','raw_model_stressed','case_projection','basis','two_luna')},
            'input_observations':observations.pop(key)})
    if observations:raise ValueError('Detected ambiguity is absent from the completed answer')
    result={'schema':1,'policy':POLICY,'private':True,'context_policy':block['rule'],
        'scope':'Every dictionary contextual-stress candidate detected in these bound preparation inputs; not exhaustive natural-language ambiguity.',
        'model_confidence':'first opinion self-rated 0..100, not calibrated; never a routing threshold','recognition_status':'not_checked',
        'human_approval':False,'acoustic_approval':False,'production_approved':False,
        'provenance':{'answer':str(answer_path),'answer_sha256':file_digest(answer_path),
            'inputs_sha256':file_digest(inputs_path),'dictionary_sha256':block['dictionary_sha256'],
            'context_module_sha256':file_digest(Path(rules.__file__)),'builder_sha256':file_digest(Path(builder.__file__)),
            'reporter_sha256':file_digest(SELF),'assignment_sha256':file_digest(assignment_path) if assignment_path else None},
        'counts':{'prepared_occurrences':len(rows),'input_observations':sum(len(r['input_observations']) for r in rows),
            'located_occurrences':sum(len(r['locations']) for r in rows),
            'baseline_kept':sum(r['luna']['two_luna']['route']=='baseline-kept' for r in rows),
            'changed':sum(r['luna']['two_luna']['route']=='proposed-change-rechecked' for r in rows),
            'second_checked':sum(r['luna']['two_luna']['selected_pass']=='pass-2' for r in rows),
            'luna_agreements':sum(r['luna']['two_luna']['agreement'] is True for r in rows),
            'luna_disagreements_second_selected':sum(r['luna']['two_luna']['agreement'] is False for r in rows)},'occurrences':rows}
    json_path=output/'report.json';text_path=output/'report.txt'
    immutable(json_path,json_bytes(result));immutable(text_path,readable(result).encode('utf8'))
    manifest={'schema':1,'policy':POLICY,'private':True,'answer_sha256':result['provenance']['answer_sha256'],
        'artifacts':{p.name:file_digest(p) for p in (json_path,text_path)},'counts':result['counts'],
        'human_approval':False,'acoustic_approval':False}
    immutable(output/'manifest.json',json_bytes(manifest))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--answer',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--assignment',type=Path)
    args=parser.parse_args();result=report(args.answer,args.output,args.assignment)
    print(json.dumps({'output':str(args.output.resolve()),**result['counts'],'recognition_status':'not_checked'},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
