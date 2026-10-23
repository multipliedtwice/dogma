"""Record explicit listener word verdicts for exact takes as a bound witness report.

A human listener confirmed that specific takes say their source words. Each
verdict is bound to the take's audio hash, synthesis key and expected-text
hash, and keeps the listener's verbatim answer. The report uses the
secondary-witness format, so held sections and publication reproduce it
exactly; it applies to no other audio. `heard` is the source text the listener
confirmed; it is a listening verdict, not a recognizer transcript.
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read


def take_of(audio):
    """Plan unit, receipt and primary audit check of the exact audio file."""
    from regenerate_higgs_book_trial import cached_audit
    folder=audio.parent;receipts=read(folder/'receipts.json')['takes']
    uid=next(k for k,v in receipts.items() if v['audio']==audio.name)
    receipt=receipts[uid];unit=next(u for u in read(folder/'render-plan.json')['units'] if u['id']==uid)
    if file_digest(audio)!=receipt['audio_sha256']:raise ValueError('Approved audio changed: '+str(audio))
    check=cached_audit(folder)['checks'][uid]
    if check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
        raise ValueError('Approved take audit binding changed')
    return uid,unit,receipt,check


def record(generation,listen,verbatim):
    items=read(listen);now=datetime.now(timezone.utc).isoformat()
    answers={'source':'explicit_listener','verdict':'approved','scope':'spoken words of exact takes',
             'verbatim':verbatim,'received':now,'questions':{},'human_approval':True}
    rows=[]
    for uid,item in items.items():
        audio=Path(item['audio']);found,unit,receipt,check=take_of(audio)
        if found!=uid:raise ValueError('Listening page take belongs to another unit')
        answers['questions'][uid]={'question':item['question'],'audio_sha256':receipt['audio_sha256'],
                                    'synthesis_key':receipt['key'],'answer':'approved'}
        rows.append((uid,unit,receipt,check,audio))
    answer_path=generation/'listener-verdicts'/('answers-'+digest(answers)[:16]+'.json')
    write_json(answer_path,answers)
    config={'source':'explicit_listener','language':'ru','independent_recognizer':False,
            'heard_semantics':'source words confirmed by a human listener for this exact take',
            'answers':str(answer_path.resolve()),'answers_sha256':file_digest(answer_path),
            'implementation_sha256':file_digest(Path(__file__))}
    signature=digest(config);output=generation/('secondary-asr-listener-'+file_digest(answer_path)[:16])
    report={'verifier':config,'checks':{}}
    for uid,unit,receipt,check,audio in rows:
        spoken=unit['spoken'];key=digest({'audio':receipt['audio_sha256'],'expected':spoken,'verifier':signature})
        report['checks'][key]={**transcript_check(spoken,spoken,receipt['audio_sha256'],signature,normalize_elongations=True),
            'unit_id':uid,'synthesis_key':receipt['key'],'primary_check':check,'listener_answer':answers['questions'][uid],
            'take':str(audio.parent.resolve())}
    write_json(output/'report.json',report)
    print('LISTENER VERDICTS recorded',len(rows),'->',output)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generation',type=Path,required=True);p.add_argument('--listen',type=Path,required=True)
    p.add_argument('--verbatim',required=True);a=p.parse_args()
    record(a.generation.resolve(),a.listen,a.verbatim)
