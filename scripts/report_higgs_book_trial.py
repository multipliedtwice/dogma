"""Verify final published trial media and write a reproducible score summary."""
import argparse
import json
import sys
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json

def verify(folder):
    source=read_json(folder/'source/book.json')
    page=ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp'
    chapters=read_json(page/'chapters.json')
    if {c['id'] for c in chapters}!={'c0001','c0002','c0003'}:raise ValueError('Requested chapters are incomplete')
    rows=[]
    for chapter in chapters:
        master=folder/'audio-v3'/chapter['id']/'master-01'
        plan=read_json(master/'render-plan.json');receipts=read_json(master/'receipts.json')
        checks=read_json(master/'word-checks-1.7B.json');quality=read_json(master/'chapter-quality.json')
        binding=file_digest(master/'render-plan.json')
        if receipts['plan_sha256']!=binding or checks['plan_sha256']!=binding:raise ValueError('Master bindings changed')
        for unit in plan['units']:
            take=receipts['takes'][unit['id']];check=checks['checks'][unit['id']]
            if (file_digest(master/take['audio'])!=take['audio_sha256'] or check['audio_sha256']!=take['audio_sha256']
                    or check['synthesis_key']!=take['key'] or unit['key']!=take['key']):
                raise ValueError('Selected clip provenance is inconsistent')
        wav=ROOT/'training-work'/chapter['stream'].lstrip('/')
        mp3=page/chapter['audio']
        if file_digest(wav)!=chapter['wav_sha256'] or file_digest(mp3)!=chapter['sha256']:
            raise ValueError('Published chapter hash changed')
        request=urllib.request.Request('http://127.0.0.1:1437'+chapter['stream'],headers={'Range':'bytes=48000-48063'})
        with urllib.request.urlopen(request,timeout=30) as response:
            if response.status!=206 or len(response.read())!=64:raise ValueError('Chapter seeking failed')
        with urllib.request.urlopen('http://127.0.0.1:1437/character-rb2-full/fine-tune-all/elirm-iv-mvp/'+chapter['audio'],timeout=30) as response:
            if response.status!=200:raise ValueError('MP3 download unavailable')
        rows.append({**chapter,'word_checked':quality['word_checked'],'asr_exact':quality['asr_exact'],
                     'asr_flags':quality['asr_flags'],'seed_retakes':quality['seed_retakes'],
                     'word_verified_improvements':quality['word_verified_improvements'],
                     'master_plan_sha256':binding})
    with urllib.request.urlopen('http://127.0.0.1:1437/character-rb2-full/fine-tune-all/elirm-iv-mvp/',timeout=30) as response:
        if response.status!=200 or response.read().count(b'<option ')!=3:raise ValueError('Player lacks three chapters')
    report={'status':'listening_candidate','production_approved':False,'book':source['title'],
            'source_sha256':source['source_hash'],'model_sha256':plan['model_sha256'],
            'cast_identities':len(read_json(folder/'final-cast.json')),'chapters':rows,
            'total_minutes':sum(c['seconds'] for c in rows)/60,
            'original_render_requests':sum(c['units'] for c in rows),'seed_retakes':sum(c['seed_retakes'] for c in rows),
            'word_verified_improvements':sum(c['word_verified_improvements'] for c in rows),
            'asr_exact':sum(c['asr_exact'] for c in rows),'asr_flags':sum(c['asr_flags'] for c in rows),
            'limits':'ASR flags are not proven synthesis errors. Voice identity, pronunciation detail and artistic quality require listening.',
            'source_assignment_validation':read_json(folder/'assignment-validation.json'),
            'human_labels_changed':False,
            'url':'https://overplay-effort-humming.ngrok-free.dev/character-rb2-full/fine-tune-all/elirm-iv-mvp/'}
    write_json(folder/'MVP-REPORT.json',report)
    return {k:v for k,v in report.items() if k in ('status','total_minutes','asr_exact','asr_flags','word_verified_improvements')}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    a=p.parse_args();print(json.dumps(verify(a.folder.resolve())))
