"""Development demo preprocessing v4; immutable v3 generations remain untouched.

Read source + existing reviewed name/homograph evidence with --folder, write a
NEW --output folder, then --stage freeze consumes that actual synthesis input.
No synthesis, recognition or retake implementation lives in this entrypoint.
Every ambiguous YO occurrence is decided by isolated GPT-6 Luna at low effort;
finite-verb stress remains a separate Opus-high pass constrained to that spelling.
"""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import freeze_public_audiobook_demo as legacy
import build_yo_restoration as builder
import build_homograph_stress as stress_builder
from rimsky import yo_restoration as yo,homograph_stress as hs
from rimsky import russian_pronunciation as phonetics
from rimsky.auto_pronunciation import auto_pronunciation,policy_options
from rimsky.util import digest,file_digest

SELF=Path(__file__).resolve()
RULE='public-demo-contextual-yo-verb-stress-v1'
PIN=('claude_cli','claude-opus-5-5','high')
VOWELS='аеёиоуыэюя'
read=legacy.read
immutable=legacy.immutable
SYSTEM=yo.SYSTEM+(' For these ambiguous finite-verb items, jointly decide the contextual е/ё reading AND its stress in ONE answer. '
    'Echo the chosen word without acute in word. Supply stressed with the SAME chosen letters: exactly one U+0301 immediately '
    'after a vowel for a multi-vowel е form; ё itself carries stress, so add no acute to a ё form. '
    'Use sentence grammar/meaning, not tagger consensus: future perfective and present imperfective can differ in both vowel and stress. '
    'Keep every other word and all source text unchanged. This is text preparation, never audio approval.')


def dependencies():
    modules=(legacy,builder,stress_builder,yo,hs,phonetics)
    return {str(Path(m.__file__).resolve().relative_to(ROOT)).replace('\\','/'):file_digest(Path(m.__file__)) for m in modules}|{
        SELF.relative_to(ROOT).as_posix():file_digest(SELF),'runtime/rimsky/auto_pronunciation.py':file_digest(ROOT/'runtime/rimsky/auto_pronunciation.py')}


def finite_forms(word):
    """Dictionary-only ambiguity with at least one finite VERB, never a lemma list."""
    if yo.classify(word)[0]!='ambiguous':return []
    rows=[{'surface':p.word,'lemma':p.normal_form,'tag':str(p.tag),'pos':p.tag.POS} for p in yo._dictionary_parses(word)
          if p.word.replace('ё','е')==word.replace('ё','е')]
    if not any(p['pos']=='VERB' for p in rows) or not any('ё' in p['surface'] for p in rows) or not any('ё' not in p['surface'] for p in rows):return []
    return [dict(zip(('surface','lemma','tag','pos'),row)) for row in sorted({(p['surface'],p['lemma'],p['tag'],p['pos']) for p in rows},key=str)]


def source_binding(unit,key,index):
    matches=[u for u in yo.units(unit['text']) if u['key']==key and u['occ']==index]
    if len(matches)!=1:raise ValueError('Context decision has no exact original source occurrence')
    token=matches[0]
    return {'unit':unit['id'],'source_id':unit['source_id'],'unit_start':unit['start'],'unit_end':unit['end'],
        'unit_text_sha256':digest(unit['text']),'start':unit['start']+token['start'],'end':unit['start']+token['end'],
        'surface':unit['text'][token['start']:token['end']],'spoken_sha256':digest(unit['spoken'].encode('utf8'))}


def analyse(unit,names,common,ruaccent=None):
    text=unit['spoken'];kwargs={} if ruaccent is None else {'ruaccent':ruaccent}
    decisions,rows,stats=builder.analyse_texts({text:unit.get('chapter_id','source')},names,common,**kwargs)
    normalized,_=yo.normalize_marked_e(text);tokens={(u['key'],u['occ']):u for u in yo.units(normalized)}
    auto=[];context=[];regular=[]
    for row in [*decisions,*rows]:
        item=copy.deepcopy(row);item['unit']=unit['id'];item['source_binding']=source_binding(unit,row['word'],row['index'])
        forms=finite_forms(row['word'])
        if forms:
            token=tokens[(row['word'],row['index'])]
            item.update(kind='context-required-finite-verb',dictionary_forms=forms,
                sentence=yo.sentence_of(normalized,token['start']),prior_tagger_basis=row.get('basis'),context_required=True)
            item.pop('choice',None);item.pop('basis',None);context.append(item)
        elif 'sentence' in row:regular.append(item)
        else:auto.append(item)
    return {'unit':unit['id'],'auto':auto,'context':context,'regular':regular,'stats':stats}


def context_request(rows):
    value=yo.request(rows)
    value['task']=RULE
    for item,row in zip(value['items'],rows,strict=True):
        item.update(source_binding=row['source_binding'],dictionary_forms=row['dictionary_forms'],
            witnesses=row['witnesses'],stress_required=True,tagger_agreement_is_not_context_authority=True)
        if row.get('yo_decision'):
            item['frozen_yo_decision']=row['yo_decision']
            item['instruction']='The separate Luna e/yo choice is fixed; determine stress for exactly that word, never revise its letters or choice.'
    return value


def context_schema(request):
    schema=yo.schema([i['id'] for i in request['items']]);item=schema['properties']['decisions']['items']
    item['properties']['stressed']={'type':'string'};item['required'].append('stressed');return schema


def validate_context(answer,request):
    yo.validate(answer,request)
    for row in answer['decisions']:
        source=next(i for i in request['items'] if i['id']==row['id'])
        fixed=source.get('frozen_yo_decision')
        if fixed and any(row[k]!=fixed[k] for k in ('choice','word')):raise ValueError('Context stress changed the frozen Luna YO choice')
        if set(row)!={'id','choice','word','basis','stressed'} or not row['basis'].strip():raise ValueError('Incomplete contextual stress decision')
        marked=row['stressed'];word=row['word'];plain=marked.replace('\u0301','')
        if plain!=word:raise ValueError('Context stress changed chosen letters')
        if 'ё' in word.lower():
            if marked!=word:raise ValueError('ё carries its own stress')
        else:
            count=sum(c.lower() in VOWELS for c in word)
            if marked.count('\u0301')!=(1 if count>1 else 0):raise ValueError('Plain-е multi-vowel form needs one explicit stress')
            if '\u0301' in marked and (marked.index('\u0301')==0 or marked[marked.index('\u0301')-1].lower() not in VOWELS):
                raise ValueError('Acute is not after a vowel')


def context_adjudicate(out,rows):
    """One source-bound Opus-high context pass per occurrence, cached immutably."""
    answers=[]
    if stress_builder.ADJUDICATOR!=PIN or stress_builder.CLAUDE_ONLY:raise ValueError('Context stress requires pinned Opus high without fallback')
    for start in range(0,len(rows),builder.BATCH):
        subset=rows[start:start+builder.BATCH];request=context_request(subset);schema=context_schema(request)
        bound={'system':SYSTEM,'request':request,'schema':schema,'provider':PIN[0],'model':PIN[1],'effort':PIN[2]}
        tag=digest(bound);rp=out/('context-'+tag+'.request.json');ap=out/('context-'+tag+'.answer.json');immutable(rp,bound)
        if ap.exists():saved=read(ap)
        else:
            answer,metadata=stress_builder.call(SYSTEM,request,schema,out,tag[:12])
            saved={'request_sha256':file_digest(rp),'answer':answer,'metadata':metadata,'audio_approval':False};immutable(ap,saved)
        if saved['request_sha256']!=file_digest(rp):
            raise ValueError('Context answer is not pinned Opus-high provenance')
        validate_provider(saved['metadata'])
        validate_context(saved['answer'],request)
        answers.extend({'row':row,'decision':decision,'request_sha256':file_digest(rp),'answer_sha256':file_digest(ap)}
            for row,decision in zip(subset,sorted(saved['answer']['decisions'],key=lambda d:d['id']),strict=True))
    return answers


def validate_provider(metadata):
    if any(metadata.get(k)!=v for k,v in zip(('provider','model','effort'),PIN)):raise ValueError('Expected pinned Opus-high adjudication')
    actual=metadata.get('model_usage',{})
    if not actual or any(not re.fullmatch(re.escape(PIN[1])+r'(?:-\d{8})?',name) for name in actual):
        raise ValueError('Reported actual context model differs from pinned Opus')
    if metadata.get('requested_model')!=PIN[1] or not metadata.get('cli_version'):raise ValueError('Missing actual CLI/model provenance')
    return sorted(actual)


def overlay_context(unit,synthesis,rows,homograph):
    edits=[];conflicts=[]
    for result in rows:
        row=result['row'];decision=result['decision'];binding=source_binding(unit,row['word'],row['index'])
        if row['source_binding']!=binding or row['sha256']!=digest(yo.normalize_marked_e(unit['spoken'])[0].encode('utf8')):
            raise ValueError('Context stress no longer binds exact source/input occurrence')
        if 'ё' in binding['surface'].lower() or '\u0301' in binding['surface']:raise ValueError('Explicit source stress/ё remains authoritative')
        matching=[t for t in yo.units(synthesis) if t['key']==row['word'] and t['occ']==row['index']]
        if len(matching)!=1:raise ValueError('Context target moved or disappeared')
        token=matching[0];before=synthesis[token['start']:token['end']]
        if before.replace('\u0301','')!=decision['word']:raise ValueError('v4 ё layer and context choice disagree')
        prior=[d for d in homograph.get('overrides',[]) if d['sha256']==digest(unit['spoken'].encode('utf8')) and (d['word'],d['index'])==(row['word'],row['index'])]
        marked=decision['stressed'];stress_position=(marked.index('\u0301')-1 if '\u0301' in marked
            else marked.lower().index('ё') if 'ё' in marked.lower() else None)
        if any(d.get('stress') is not None and d['stress']!=stress_position for d in prior):
            conflicts.append({'source_binding':binding,'context_stressed':marked,'older_homograph':prior,'reason':'Stress authorities disagree; no silent replacement'});continue
        edits.append({'start':token['start'],'end':token['end'],'before':before,'after':marked,'source_binding':binding,
            'request_sha256':result['request_sha256'],'answer_sha256':result['answer_sha256'],'older_homograph':prior})
    for edit in sorted(edits,key=lambda e:e['start'],reverse=True):synthesis=synthesis[:edit['start']]+edit['after']+synthesis[edit['end']:]
    legacy.validate_pronunciation(unit['spoken'],synthesis);return synthesis,{'rule':RULE,'applied':edits,'conflicts':conflicts,'audio_approval':False}


def prepare_rows(units,names,common,homograph,out):
    analyses=[analyse(u,names,common) for u in units];immutable(out/'analysis.json',{'rule':RULE,'units':analyses,'dependencies':dependencies(),'versions':yo._versions()})
    context_rows=[r for a in analyses for r in a['context']];regular=[r for a in analyses for r in a['regular']]
    all_rows=regular+context_rows
    req=yo.request(all_rows);immutable(out/'regular.request.json',{'request':req,'rows':all_rows,'policy':builder.POLICY,'provider':builder.PIN})
    batches=builder.adjudicate(out/'regular',req) if all_rows else []
    for batch in batches:builder.validate_provider(batch['metadata'])
    merged={'decisions':[d for b in batches for d in b['answer']['decisions']]};yo.validate(merged,req)
    all_decisions=yo.decisions_from(all_rows,merged);regular_decisions=all_decisions[:len(regular)]
    choices={d['id']:d for d in merged['decisions']}
    context_rows=[{**r,'yo_decision':choices[f'y{n+len(regular):04d}']} for n,r in enumerate(context_rows)]
    contextual=context_adjudicate(out/'context',context_rows)
    context_decisions=[]
    for item in contextual:
        row=item['row'];decision=item['decision'];context_decisions.append({**{k:row[k] for k in ('sha256','word','index','surface','yo')},
            'choice':row['yo_decision']['choice'],'basis':'luna-yo-choice-with-separate-opus-stress'})
    answer={'rule':yo.RULE,'guard':RULE,'yo_policy':builder.POLICY,'yo_metadata':{'batches':[b['metadata'] for b in batches]},
        'decisions':[d for a in analyses for d in a['auto']]+regular_decisions+context_decisions,'context':contextual,'human_approval':False}
    immutable(out/'yo.answer.json',answer);answer_sha=file_digest(out/'yo.answer.json');result=[]
    for unit,analysis in zip(units,analyses,strict=True):
        local=[d for d in analysis['auto']]+[d for r,d in zip(regular,regular_decisions,strict=True) if r['unit']==unit['id']]
        local += [d for row,d in zip(contextual,context_decisions,strict=True) if row['row']['unit']==unit['id']]
        options=policy_options('stress-rare-v4',{'homograph_stress':homograph,'yo_restoration':{'rule':yo.RULE,'answer_sha256':answer_sha,'decisions':local}})
        synthesis,record=auto_pronunciation(unit['spoken'],names,common,**options)
        synthesis,context_record=overlay_context(unit,synthesis,[r for r in contextual if r['row']['unit']==unit['id']],homograph)
        before_phonetic=synthesis;synthesis,phonetic_records=phonetics.apply(before_phonetic)
        phonetic={'rule':phonetics.RULE,'prephonetic_source':before_phonetic,'records':phonetic_records,
                  'module_sha256':file_digest(Path(phonetics.__file__))}
        legacy.validate_synthesis_pronunciation(unit['spoken'],synthesis,phonetic)
        if record['yo_restoration']['unbound'] or record.get('homograph_overrides',{}).get('unbound'):raise ValueError('Unbound pronunciation occurrence')
        result.append({**unit,'synthesis_text':synthesis,'pronunciation':{**record,'context_stress':context_record,'russian_phonetics':phonetic}})
    return result,{'policy':'stress-rare-v4','context_policy':RULE,'phonetic_policy':phonetics.RULE,'yo_answer_sha256':answer_sha,'context_conflicts':sum(len(u['pronunciation']['context_stress']['conflicts']) for u in result),
        'scope':'Development/demo text preparation only; no audio regeneration or acoustic-stress claim'}


def copy_bytes(target,source):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=source.read_bytes():raise ValueError('Immutable preparation input changed')
    else:
        with target.open('xb') as stream:stream.write(source.read_bytes())


def prepare(folder,output):
    if output==folder or output.is_relative_to(folder/'pronunciation'):raise ValueError('Use a separate preparation output; historical files are immutable')
    assigned=read(folder/'analysis/assigned.json');book=legacy.verify_assignment_source(folder,assigned)
    if assigned['language']!='Russian':raise ValueError('This focused pronunciation entrypoint is Russian only')
    baseline=read(folder/'pronunciation/result.json');binding=read(folder/'pronunciation/binding.json')
    if binding['assigned_sha256']!=file_digest(folder/'analysis/assigned.json') or binding['book_sha256']!=file_digest(folder/'prepared/book.json'):
        raise ValueError('Existing name/homograph evidence belongs to different source')
    for name,checksum in baseline['evidence_sha256'].items():
        if file_digest(folder/name)!=checksum:raise ValueError('Historical preparation evidence changed')
    lexicon=read(folder/'pronunciation/name-lexicon.json');homograph=hs.block(folder/'pronunciation/homograph.answer.json')
    if any(lexicon.get(k)!=v for k,v in zip(('provider','model','effort'),PIN)):raise ValueError('Name stress evidence is not Opus high')
    for row in read(folder/'pronunciation/homograph.answer.json')['metadata']['batches']:
        if any(row.get(k)!=v for k,v in zip(('provider','model','effort'),PIN)):raise ValueError('Homograph evidence is not Opus high')
    for name in ('prepared/book.json','analysis/assigned.json','analysis/structure.request.json','analysis/structure.answer.json'):
        copy_bytes(output/name,folder/name)
    for name in baseline['evidence_sha256']:
        if name.startswith('pronunciation/'):
            copy_bytes(output/'pronunciation/historical-evidence'/Path(name).relative_to('pronunciation'),folder/name)
    copy_bytes(output/'pronunciation/historical-evidence/result.json',folder/'pronunciation/result.json')
    out=output/'pronunciation';immutable(out/'binding.json',{'schema':1,'assigned_sha256':binding['assigned_sha256'],'book_sha256':binding['book_sha256'],
        'implementation_sha256':file_digest(SELF),'dependencies':dependencies(),'historical_result_sha256':file_digest(folder/'pronunciation/result.json')})
    units=copy.deepcopy(baseline['assignment']['units'])
    if any(a['id']!=b['id'] or a['text']!=b['text'] for a,b in zip(assigned['units'],units,strict=True)):raise ValueError('Historical preparation changed original units')
    for unit in units:unit.pop('synthesis_text',None);unit.pop('pronunciation',None)
    prepared,details=prepare_rows(units,lexicon['names'],lexicon['common'],homograph,out/'yo-v4')
    details.update(name_lexicon_sha256=file_digest(folder/'pronunciation/name-lexicon.json'),homograph_answer_sha256=file_digest(folder/'pronunciation/homograph.answer.json'))
    assignment={**baseline['assignment'],'units':prepared,'pronunciation':details}
    if details['context_conflicts']:
        immutable(out/'context-conflicts.json',{'status':'preparation_conflict','candidate_assignment':assignment,'ready_for_freeze':False})
        raise ValueError('Conflicting stress authorities retained; preparation cannot freeze')
    evidence={p.relative_to(output).as_posix():file_digest(p) for p in sorted(out.rglob('*.json')) if p!=out/'result.json'}
    evidence.update({name:file_digest(output/name) for name in ('prepared/book.json','analysis/assigned.json','analysis/structure.request.json','analysis/structure.answer.json')})
    immutable(out/'result.json',{'schema':1,'assignment':assignment,'evidence_sha256':evidence});immutable(output/'assignment.json',assignment)
    return assignment


def freeze(output,music_path=None):
    result=read(output/'pronunciation/result.json');binding=read(output/'pronunciation/binding.json')
    if (binding.get('implementation_sha256')!=file_digest(SELF) or binding.get('dependencies')!=dependencies()
            or result['assignment']['pronunciation'].get('policy')!='stress-rare-v4' or result['assignment']['pronunciation'].get('context_policy')!=RULE
            or result['assignment']['pronunciation'].get('context_conflicts')!=0):raise ValueError('Context-v4 preparation is incomplete, changed, or conflicted')
    legacy.freeze(output,music_path)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=['prepare','freeze'],default='prepare');parser.add_argument('--music-path',type=Path);args=parser.parse_args()
    if args.stage=='prepare':
        if args.folder is None:parser.error('--folder required for source/name/homograph evidence')
        prepare(args.folder.resolve(),args.output.resolve())
    else:freeze(args.output.resolve(),args.music_path)
if __name__=='__main__':main()
