"""Reuse the descriptor sheet with up to three verified palette clips per family.

Only derived board assets change. No corpus labels or descriptor decisions are
created, and no online provider or acoustic measurement is run.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json

BOARD=ROOT/'training-work/voice-family-registry-evidence/vlad-rimsky-v1'
DATASET=ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1'


def install(bank_path):
    data=json.loads((BOARD/'review-data.json').read_text('utf-8'))
    bank=json.loads(bank_path.read_text('utf-8'))
    previous={row['id']:row for row in data['families']}
    families={}
    for entry in bank['entries']:
        families.setdefault(entry['voice_family'],[]).extend(entry['candidates'])
    rows=[]
    for family,candidates in sorted(families.items()):
        unique={r['audio_sha256']:r for r in candidates}
        ordered=sorted(unique.values(),key=lambda r:(r['mood'] not in ('calm','neutral'),r['character'],r['id']))
        selected=[];characters=set()
        for row in ordered:
            if row['character'] not in characters:
                selected.append(row);characters.add(row['character'])
            if len(selected)==3:break
        for row in ordered:
            if len(selected)==3:break
            if row not in selected:selected.append(row)
        examples=[]
        for row in selected:
            audio=(DATASET/row['audio']).resolve()
            if not audio.is_relative_to((DATASET/'audio').resolve()) or file_digest(audio)!=row['audio_sha256']:
                raise ValueError('Descriptor example audio changed or escaped dataset')
            if row['voice_basis']!='human' or row['mood_basis']!='human':
                raise ValueError('Descriptor example lacks human voice/mood evidence')
            examples.append({'id':row['id'],'character_id':row['character'],
                'character_name':row['character']+' · '+row['mood'],'text':row['text'],'seconds':row['seconds'],
                'audio_url':'/'+audio.relative_to(ROOT/'training-work').as_posix(),
                'audio_sha256':row['audio_sha256']})
        original=previous.get(family,{})
        rows.append({**original,'id':family,'label':original.get('label',family),'examples':examples,
            'usage':original.get('usage',{'labeled_clips':len(unique),'measured_clips':0,
                'measured_source_paragraphs':0,'characters':len({r['character'] for r in candidates}),
                'books':0,'seconds':sum(r['seconds'] for r in unique.values())}),
            'book1_metrics':original.get('book1_metrics',{}),'comparisons':original.get('comparisons',[]),
            'sample_size_note':original.get('sample_size_note','No acoustic measurements in this sheet.'),
            'proposed_descriptors':{},'palette_examples_sha256':[r['audio_sha256'] for r in selected]})
    data.update(families=rows,palette_bank_sha256=file_digest(bank_path))
    write_json(BOARD/'review-data.json',data)
    shutil.copyfile(ROOT/'scripts/voice_family_descriptor_review.js',BOARD/'review.js')
    print('Descriptor sheet:',len(rows),'families;',sum(len(r['examples']) for r in rows),'verified audio examples. No decisions changed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',type=Path,required=True)
    install(parser.parse_args().bank)
