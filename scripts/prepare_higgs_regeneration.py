"""Freeze an evaluation cast and accepted spoken edits for a fresh preview."""
import argparse
import copy
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from rimsky.listener_directions import read_directions,validate_cast,scene_allocation
from rimsky.spoken_edits import apply_spoken_edit
from render_higgs_palette_preview import read,templates
from render_higgs_book_trial import spoken_text


def prepare(folder,bank_path,output_name):
    book=read(folder/'source/book.json');groups=templates(read(bank_path))
    prior=read(folder/'audio-palette-v1/manifest.json')
    if prior['source_sha256']!=file_digest(folder/'source/book.json') or prior['bank_sha256']!=file_digest(bank_path):
        raise ValueError('Previous cast uses different source or palette')
    binding=read_directions(folder/'listener-directions.json',book)
    cast=copy.deepcopy(prior['cast'])
    for actor,direction in binding['data']['casting'].items():
        options=[key for key,g in groups.items() if g['voice_family']==direction['voice_family']
                 and g['character']==direction['preferred_source_character'] and g['route']=='dialogue']
        if len(options)!=1:raise ValueError('Fixed listener voice preference has no unique palette')
        template=options[0];baseline=cast[actor]['baseline_mood']
        if baseline not in groups[template]['moods']:
            raise ValueError('Fixed character baseline is unavailable in the requested family')
        cast[actor].update(template_id=template,reason=direction['instruction'],needs_review=True)
    validate_cast(binding,cast,groups)
    registry=read(folder/'audio-palette-v1/reviewed-take-overrides.json')
    edits={}
    for key,override in registry['units'].items():
        answer_path=Path(override['listener_answer'])
        if file_digest(answer_path)!=override['listener_answer_sha256']:
            raise ValueError('Spoken correction evidence changed')
        answer=read(answer_path);unit=override['replacement']['unit']
        if answer['unit_id']!=key or answer['source'] not in ('explicit_listener','automatic_word_recovery'):
            raise ValueError('Spoken correction has no matching evidence')
        row={k:unit[k] for k in ('source_id','start','end','speaker','text')}
        row.update(expected_spoken=spoken_text(unit['text']),native_text=unit['synthesis']['text'],
                   evidence={'path':str(answer_path),'sha256':file_digest(answer_path),
                             'source':answer['source'],'verdict':answer['verdict']},
                   scope='reading_recipe_for_new_take; exact previous audio approval is not transferred')
        apply_spoken_edit(row['expected_spoken'],row)
        edits[key]=row
    units=[u for cid in ('c0001','c0002','c0003')
           for u in read(next((folder/'audio-v7'/cid).rglob('render-plan.json')))['units']]
    payload={'schema':1,'source_sha256':file_digest(folder/'source/book.json'),
        'bank_sha256':file_digest(bank_path),'prior_manifest_sha256':file_digest(folder/'audio-palette-v1/manifest.json'),
        'listener_directions_sha256':binding['sha256'],'cast':cast,'spoken_edits':edits,
        'allocation':scene_allocation(units,cast,groups,binding),
        'qualification':'evaluation_only; sound descriptors and shared-family listening qualification pending'}
    path=folder/output_name/'regeneration-inputs.json'
    if path.exists() and read(path)!=payload:raise ValueError('Regeneration inputs already frozen differently')
    write_json(path,payload)
    print('FROZEN REGENERATION INPUTS',path,'cast',len(cast),'spoken edits',len(edits))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--output-name',default='audio-palette-v2')
    args=parser.parse_args();prepare(args.folder.resolve(),args.bank.resolve(),args.output_name)
