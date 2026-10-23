"""Read-only source coverage and shared speech-turn checks for trial assignments."""
import argparse
import re
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.boundaries import starts_new_speech_turn
from rimsky.speaker_resolution import resumes_after_insertion
from rimsky.util import write_json
from higgs_trial_io import read_json

def validate(folder):
    book=read_json(folder/'source/book.json');issues=[];paragraphs=0
    for chapter in book['chapters'][:3]:
        location=folder/'analysis'/chapter['id'];fixed=read_json(location/'speech-structure.json')
        units=read_json(location/'units.json')
        for block in chapter['blocks']:
            text=block.get('text','');parts=[u for u in units if u['source_id']==block['id']]
            if re.findall(r'\w+',text)!=re.findall(r'\w+',''.join(u['text'] for u in parts)):
                issues.append({'source_id':block['id'],'issue':'source_word_coverage'})
            if not parts:continue
            paragraphs+=1;chunks=fixed[block['id']];turns=[]
            for i,chunk in enumerate(chunks):
                if chunk['mode']!='speech':continue
                previous=chunks[i-1] if i else None
                if not turns or previous is None:new=True
                elif previous['mode']=='speech':new=starts_new_speech_turn(previous,chunk,'speech','speech')
                else:new=not(i>=2 and resumes_after_insertion(chunks[i-2],previous,chunk,text))
                if new:turns.append([chunk['start'],chunk['end']])
                else:turns[-1][1]=chunk['end']
            for a,b in turns:
                speakers={u['speaker'] for u in parts if u['start']<b and u['end']>a and u['speaker']!='narrator'}
                if len(speakers)>1:issues.append({'source_id':block['id'],'issue':'multiple_performers_in_one_turn',
                                                'start':a,'end':b,'speakers':sorted(speakers)})
    report={'paragraphs_checked':paragraphs,'issues':issues,'rules':'shared turn endings and attribution continuation',
            'human_labels_changed':False}
    write_json(folder/'assignment-validation.json',report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    a=p.parse_args();r=validate(a.folder.resolve());print(r)
