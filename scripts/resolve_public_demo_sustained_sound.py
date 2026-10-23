"""V5: disclosed whole-unit sustained-consonant spelling, with two word witnesses.

This narrow adapter reuses the existing elongation helper. It does not accept a
single consonant (a letter name is ambiguous), ordinary words or mixed phrases.
Whisper models are correlated; agreement here is never human listening approval.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_beam_witness as previous
from resolve_public_demo_pronunciation import public_safe
from rimsky import spoken_homophones as sounds

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-isolated-sustained-consonant-adapter-v5'
CONSONANTS=frozenset('бвгджзйклмнпрстфхцчшщ')
CORRELATION='Both supporting models belong to the Whisper family and are correlated. This is source-bound spelling evidence, not independent human listening approval.'


def dependencies():
    paths=[SELF,Path(previous.__file__),Path(sounds.__file__)]
    return {'files':{path.relative_to(ROOT).as_posix():base.file_digest(path) for path in paths},
            'sustained_sound_helper_packages':sounds.dependencies()}


def repeated_consonant(text):
    letters=''.join(ch for ch in text.casefold() if not ch.isspace() and not unicodedata.category(ch).startswith('P'))
    return letters if len(letters)>=2 and len(set(letters))==1 and letters[0] in CONSONANTS else None


def spelling_decision(unit,language,decision,audio_sha256):
    """Pure narrow guard over upstream-validated raw evidence; return None to hold."""
    if language!='Russian' or decision.get('passed'):
        return None
    source=repeated_consonant(unit['text'])
    expected=base.word_tokens(unit['spoken'])
    if (source is None or base.word_tokens(unit['text'])!=[source] or expected!=[source]
            or base.word_tokens(unit.get('synthesis_text',unit['spoken']))!=expected):
        return None
    qualified=[]
    for item in decision['evidence']:
        config=item['verifier'];raw=item['raw']
        if item.get('role')!='witness' or item.get('family')!='whisper':
            continue
        if (config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                or raw!=base.transcript_check(unit['spoken'],raw['heard'],audio_sha256,base.sha(config))):
            raise ValueError('Sustained-sound witness raw/audio/source-blind configuration differs')
        heard=repeated_consonant(raw['heard'])
        if heard is None or heard[0]!=source[0]:
            continue
        canonical,changes=sounds.elongation_equivalence(unit['spoken'],expected,raw['heard_words'])
        if canonical!=expected or not changes and raw['heard_words']!=expected:
            continue
        qualified.append((item,changes))
    distinct={}
    for item,changes in sorted(qualified,key=lambda pair:(pair[0]['verifier']['model'],pair[0]['path'])):
        distinct.setdefault(item['verifier']['model'],(item,changes))
    if len(distinct)<2:
        return None
    selected=list(distinct.values())[:2]
    source_span={key:unit[key] for key in ('id','source_id','start','end','text')}
    proof={'rule':sounds.ELONGATION_RULE,'adapter_rule':RULE,'adapter_sha256':base.file_digest(SELF),
           'helper_sha256':base.file_digest(Path(sounds.__file__)),'helper_packages':sounds.dependencies(),
           'source_span':source_span,'source_text_sha256':base.sha(unit['text']),'expected_spoken':unit['spoken'],
           'audio_sha256':audio_sha256,'minimum_raw_consonant_repetitions':2,'required_distinct_models':2,
           'source_text_unchanged':True,'native_input_unchanged':True,'correlation':CORRELATION,'human_approval':False,
           'witnesses':[{'model':item['verifier']['model'],'receipt_sha256':item['sha256'],
                         'verifier_sha256':base.sha(item['verifier']),'heard':item['raw']['heard'],
                         'raw_status':item['raw']['status'],'helper_changes':changes}
                        for item,changes in selected]}
    equivalents=[{'rule':sounds.ELONGATION_RULE,'kind':'whole_isolated_sustained_consonant',
                  'source_tokens':expected,'heard_tokens':item['raw']['heard_words'],
                  'evidence':item['path'],'evidence_sha256':item['sha256'],
                  'audio_sha256':audio_sha256,'helper_sha256':proof['helper_sha256'],
                  'adapter_sha256':proof['adapter_sha256'],'correlation':CORRELATION,'human_approval':False}
                 for item,_ in selected]
    resolved={**decision,'passed':True,'status':'source_bound_spoken_equivalence',
              'resolution':'correlated_whisper_sustained_sound_spelling_agreement',
              'selected_evidence':selected[-1][0]['path'],'prior_resolution':decision.get('resolution'),
              'equivalences':[*decision.get('equivalences',[]),*equivalents],
              'tolerated_deviations':decision.get('tolerated_deviations',[])}
    return resolved,proof


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.sustained_sound_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'sustained_sound_dependencies') and dependencies()!=self.sustained_sound_dependencies:
            raise ValueError('Sustained-sound adapter/helper dependencies changed')

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if value is None or not previous.previous.original_take(receipt) or value['decision'].get('passed'):
            return value
        if (next((assigned for assigned in self.assignment['units'] if assigned['id']==unit['id']),None)!=unit
                or receipt['spec']['source']!=unit or receipt['spec'].get('generation')!=self.generation
                or value['source_text']!=unit['text'] or value['expected_spoken']!=unit['spoken']
                or value['audio_sha256']!=receipt['audio_sha256']):
            raise ValueError('Sustained-sound equivalence changed the frozen source/audio binding')
        result=spelling_decision(unit,self.assignment['language'],value['decision'],receipt['audio_sha256'])
        if result is None:
            return value
        decision,proof=result
        return {**value,'decision':decision,'sustained_sound_provenance':proof,
                'sustained_sound_extension_dependencies':self.sustained_sound_dependencies,
                'sustained_sound_adapter_sha256':base.file_digest(SELF)}


def report(context):
    value=previous.report(context)
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            proof=context.assess(unit,receipts[summary['take']]).get('sustained_sound_provenance')
            if proof:
                summary['sustained_sound_provenance']=public_safe(proof)
        if row.get('selected') and row['selected'].get('sustained_sound_provenance'):
            row['sustained_sound_provenance']=row['selected']['sustained_sound_provenance']
    value.update(beam_witness_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),sustained_sound_rule=RULE,
                 sustained_sound_extension_dependencies=context.sustained_sound_dependencies,
                 sustained_sound_resolved_units=sum(bool(row.get('sustained_sound_provenance')) for row in value['units']))
    value['disclosure']+=' The existing sustained-sound letter-count rule is applied only to a whole isolated repeated consonant, supported by two source-blind distinct Whisper models whose raw outputs each retain at least two copies of that consonant. '+CORRELATION+' Raw primary and witness mismatches remain counted unchanged.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('sustained-sound-report-'+base.sha(value)+'.json')
    base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value)
    eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot)
    return snapshot


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['resolve','publish','complete'],required=True)
    args=parser.parse_args();value=resolve(args.folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
