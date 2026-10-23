"""V9: one source-anchored separator in uncued Russian recognition text only.

An actual printed one-letter preposition followed by a mid-sentence capitalized
name can anchor a fused ASR token. Only one space may be inserted; every raw
UTF-8 byte otherwise remains unchanged. Both Qwen and Whisper must then pass
the unchanged Russian comparison. Source, native audio and captions never change.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_ru_acoustic_adjudication as previous
from resolve_public_demo_full_witness import original_take
from resolve_public_demo_pronunciation import public_safe

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-source-preposition-name-boundary-adapter-v9'
BOUNDARY_RULE='source-preposition-proper-name-boundary-v1'
PREPOSITIONS=frozenset('вксуо')
WHISPER_MODELS=frozenset({'openai/whisper-small','openai/whisper-large-v3-turbo','openai/whisper-large-v3'})
NAME=r'[А-Яа-яЁё]+(?:[-‐‑][А-Яа-яЁё]+)*'
WORDS=re.compile(NAME)
SOURCE_TOKENS=re.compile(NAME+r'|[.!?…]')


def dependencies():
    paths=[SELF,Path(previous.__file__),Path(base.__file__),ROOT/'runtime/rimsky/name_tolerance.py',
           ROOT/'runtime/rimsky/spoken_homophones.py',ROOT/'runtime/rimsky/speech_quality.py']
    return {path.relative_to(ROOT).as_posix():base.file_digest(path) for path in paths}


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def source_anchors(unit,paragraph):
    start,end=unit['start'],unit['end']
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(paragraph) or paragraph[start:end]!=unit['text']:
        raise ValueError('Boundary anchor differs from exact frozen paragraph/unit coordinates')
    conversion=unit.get('spoken_conversion',{})
    if (conversion.get('source')!=unit['text'] or conversion.get('changes')!=[]
            or re.sub(r'\s+',' ',conversion.get('expanded','')).strip()!=unit['spoken']
            or base.word_tokens(unit['text'])!=base.word_tokens(unit['spoken'])):
        return []
    anchors=[];initial=True;prior=None
    for match in SOURCE_TOKENS.finditer(paragraph):
        token=match[0]
        if token in '.!?…':initial=True;prior=None;continue
        if (not initial and token[0].isupper() and len(token)>=2 and prior is not None
                and prior[0].casefold() in PREPOSITIONS and len(prior[0])==1
                and paragraph[prior.end():match.start()].isspace() and start<=prior.start() and match.end()<=end):
            prep=prior[0]
            anchors.append({'source_id':unit['source_id'],'unit':unit['id'],'unit_start':start,'unit_end':end,
                            'preposition':prep,'preposition_start':prior.start(),'preposition_end':prior.end(),
                            'name':token,'name_start':match.start(),'name_end':match.end(),
                            'source_word_index':len(base.word_tokens(unit['text'][:prior.start()-start])),
                            'fused_casefold':(prep+token).casefold(),
                            'source_case_rule':'Actual capitalized mid-sentence name; no lowercase invention or initial-name exception'})
        initial=False;prior=match
    return anchors


def checked_raw(unit,item,audio_sha256):
    config=item['verifier'];raw=item['raw']
    if (config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
            or raw!=base.transcript_check(unit['spoken'],raw['heard'],audio_sha256,base.sha(config))):
        raise ValueError('Boundary evidence is not original source-bound uncued raw recognition')
    return raw


def comparison_option(unit,paragraph,item,audio_sha256):
    raw=checked_raw(unit,item,audio_sha256)
    original=base.russian_equivalence(unit,raw['heard'],paragraph,audio_sha256,raw['verifier_sha256'])
    wanted=base.word_tokens(unit['spoken'])
    if original.get('passed') is True and original.get('canonical_words')==wanted:
        return {'comparison':original,'raw_comparison':original,'comparison_input':raw['heard'],'split':None}
    anchors=source_anchors(unit,paragraph);candidates=[]
    for token in WORDS.finditer(raw['heard']):
        matching=[anchor for anchor in anchors if token[0].casefold()==anchor['fused_casefold']]
        if len(matching)>1:return None
        if not matching:continue
        anchor=matching[0];heard_index=len(base.word_tokens(raw['heard'][:token.start()]))
        if heard_index!=anchor['source_word_index']:return None
        offset=token.start()+1;derived=raw['heard'][:offset]+' '+raw['heard'][offset:]
        byte_offset=len(raw['heard'][:offset].encode('utf-8'))
        if derived.encode('utf-8')!=raw['heard'].encode('utf-8')[:byte_offset]+b' '+raw['heard'].encode('utf-8')[byte_offset:]:
            raise ValueError('Boundary repair changed raw bytes beyond one inserted space')
        split={'rule':BOUNDARY_RULE,'anchor':anchor,'raw_token':token[0],
               'heard_word_index':heard_index,'character_offset':offset,'utf8_byte_offset':byte_offset,
               'inserted':' ','raw_heard':raw['heard'],'derived_heard':derived,
               'raw_text_utf8_sha256':text_hash(raw['heard']),'derived_text_utf8_sha256':text_hash(derived),
               'source_paragraph_sha256':base.sha(paragraph),'audio_sha256':audio_sha256,
               'raw_receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
               'all_original_bytes_retained':True,'source_text_unchanged':True,'native_audio_unchanged':True,
               'interpretation':'Derived comparison input only, not a new recognizer transcript or listening approval',
               'human_approval':False}
        candidates.append((derived,split))
    if len(candidates)!=1:return None
    derived,split=candidates[0]
    comparison=base.russian_equivalence(unit,derived,paragraph,audio_sha256,raw['verifier_sha256'])
    if comparison.get('passed') is not True or comparison.get('canonical_words')!=wanted:return None
    return {'comparison':comparison,'raw_comparison':original,'comparison_input':derived,'split':split}


def validate_boundary_proof(unit,paragraph,item,proof,audio_sha256):
    computed=comparison_option(unit,paragraph,item,audio_sha256)
    if computed is None or computed['split'] is None or computed['split']!=proof:
        raise ValueError('Boundary proof cannot reproduce exact source anchor/raw insertion')
    return computed


def adjudicate(unit,decision,paragraph,audio_sha256):
    if decision.get('passed'):return None
    primary=[];witnesses=[]
    for item in decision['evidence']:
        is_primary=(item.get('role')=='primary' and item.get('family')=='qwen'
                    and item['verifier'].get('model')=='Qwen3-ASR-1.7B')
        is_witness=(item.get('role')=='witness' and item.get('family')=='whisper'
                    and item['verifier'].get('model') in WHISPER_MODELS)
        if not (is_primary or is_witness):continue
        option=comparison_option(unit,paragraph,item,audio_sha256)
        if option is not None:(primary if is_primary else witnesses).append((item,option))
    pairs=[(p,w) for p in primary for w in witnesses if p[1]['split'] is not None or w[1]['split'] is not None]
    if not pairs:return None
    pair=min(pairs,key=lambda pair:(sum(option['split'] is not None for _,option in pair),
                                   sum(len(option['comparison'].get('tolerated_deviations',[])) for _,option in pair),
                                   tuple(item['path'] for item,_ in pair)))
    proof={'rule':BOUNDARY_RULE,'adapter_rule':RULE,'adapter_sha256':base.file_digest(SELF),
           'russian_comparison_module_sha256':base.file_digest(Path(base.__file__)),
           'source_span':{key:unit[key] for key in ('id','source_id','start','end','text')},
           'source_paragraph_sha256':base.sha(paragraph),'expected_spoken':unit['spoken'],'audio_sha256':audio_sha256,
           'source_text_unchanged':True,'native_audio_unchanged':True,'max_insertions_per_recognizer':1,'human_approval':False,
           'recognizers':[{'model':item['verifier']['model'],'role':item['role'],'family':item['family'],
                           'receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
                           'raw_heard':item['raw']['heard'],**option} for item,option in pair]}
    equivalents=[];deviations=[]
    for item,option in pair:
        if option['split'] is not None:
            equivalents.append({'rule':BOUNDARY_RULE,'kind':'source_anchored_preposition_name_separator',
                                'boundary_proof':option['split'],'evidence':item['path'],'evidence_sha256':item['sha256']})
        equivalents.extend({**change,'evidence':item['path'],'evidence_sha256':item['sha256']}
                           for change in option['comparison'].get('equivalences',[]))
        deviations.extend({**change,'evidence':item['path'],'evidence_sha256':item['sha256']}
                          for change in option['comparison'].get('tolerated_deviations',[]))
    resolved={**decision,'passed':True,'status':'permitted_tolerance' if deviations else 'source_bound_spoken_equivalence',
              'resolution':'source_bound_separator_with_existing_russian_comparison','prior_resolution':decision.get('resolution'),
              'selected_evidence':pair[0][0]['path'],'equivalences':equivalents,'tolerated_deviations':deviations}
    return resolved,proof


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder);self.word_boundary_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'word_boundary_dependencies') and dependencies()!=self.word_boundary_dependencies:
            raise ValueError('Word-boundary adapter/helper dependencies changed')

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if (value is None or self.assignment['language']!='Russian' or not original_take(receipt)
                or value['decision'].get('passed')):return value
        if (next((assigned for assigned in self.assignment['units'] if assigned['id']==unit['id']),None)!=unit
                or receipt['spec']['source']!=unit or receipt['spec'].get('generation')!=self.generation
                or value['source_text']!=unit['text'] or value['expected_spoken']!=unit['spoken']
                or value['audio_sha256']!=receipt['audio_sha256']):
            raise ValueError('Word-boundary adjudication changed frozen source/audio binding')
        result=adjudicate(unit,value['decision'],self.blocks[unit['source_id']],receipt['audio_sha256'])
        if result is None:return value
        decision,proof=result
        return {**value,'decision':decision,'word_boundary_provenance':proof,
                'word_boundary_extension_dependencies':self.word_boundary_dependencies,'word_boundary_adapter_sha256':base.file_digest(SELF)}


def report(context):
    value=previous.report(context)
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            proof=context.assess(unit,receipts[summary['take']]).get('word_boundary_provenance')
            if proof:summary['word_boundary_provenance']=public_safe(proof)
        if row.get('selected') and row['selected'].get('word_boundary_provenance'):
            row['word_boundary_provenance']=row['selected']['word_boundary_provenance']
    value.update(ru_acoustic_adjudication_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),word_boundary_rule=BOUNDARY_RULE,
                 word_boundary_extension_dependencies=context.word_boundary_dependencies,
                 word_boundary_resolved_units=sum(bool(row.get('word_boundary_provenance')) for row in value['units']))
    value['disclosure']+=' A fused recognizer token may receive one disclosed separator only at a unique frozen-source one-letter-preposition plus capitalized mid-sentence name anchor, with every original UTF-8 byte retained. Original Qwen and Whisper must both canonically pass unchanged Russian rules. Derived comparison inputs, name/phonology disclosures and original failing raw checks remain separate; no source/audio/stress or human approval is changed.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('word-boundary-report-'+base.sha(value)+'.json');base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['resolve','publish','complete'],required=True)
    args=parser.parse_args();value=resolve(args.folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
