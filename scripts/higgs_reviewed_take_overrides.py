"""Exact listener-approved punctuation takes; never rewrite source/corpus text."""
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest
from rimsky.speech_quality import word_tokens
from rimsky.spoken_pronunciation import unmarked,validate_marks


def validate_punctuation_take(before,after,listener_stress_approved=False):
    import soundfile as sf
    from rimsky.audio_join import boundary_check
    old,new=before['unit'],after['unit'];a,b=old['synthesis'],new['synthesis']
    for field in ('id','chapter_id','source_id','speaker','render_voice','voice','mood','text','palette_reference','palette_assignment'):
        if old.get(field)!=new.get(field):raise ValueError('Punctuation take changes identity/source: '+field)
    changed={k for k in set(a)|set(b) if a.get(k)!=b.get(k)}
    if changed!={'text','prompt'} or a['prompt']!=a['text'] or b['prompt']!=b['text']:
        raise ValueError('Punctuation take changes sound inputs other than text')
    comma_removed=a['text'].count(',') and b['text']==a['text'].replace(',','')
    completed_fragment=b['text']==a['text'].rstrip()+'.' and not a['text'].rstrip().endswith(('.', '!', '?', '…'))
    stressed=b['text']!=a['text'] and unmarked(b['text'])==a['text']
    if stressed:validate_marks(b['text'])
    if not (comma_removed or completed_fragment or stressed) or word_tokens(a['text'])!=word_tokens(unmarked(b['text'])):
        raise ValueError('Only explicit comma removal or a fragment completion period is permitted')
    if new['spoken']!=unmarked(b['text']) or b.get('effects_policy')!='disabled':
        raise ValueError('Punctuation take has inconsistent spoken text or effects')
    receipt=after['receipt'];check=after['word_check'];path=Path(after['path'])
    from higgs_word_omissions import clear_omission
    permitted_audit=(check['status']=='asr_exact_match' or (stressed and listener_stress_approved
        and check['status']=='spoken_text_mismatch' and not clear_omission(check)))
    if (digest(b)!=receipt['key'] or receipt['synthesis_sha256']!=receipt['key']
        or file_digest(path)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256']
        or check['synthesis_key']!=receipt['key'] or not permitted_audit
        or check['expected_text_sha256']!=digest(new['spoken'].encode('utf8'))):
        raise ValueError('Replacement take/word audit binding changed')
    x,sr=sf.read(path,dtype='float32');edge=boundary_check(x,sr)
    if sr!=24000 or edge['unsafe_start'] or edge['unsafe_end']:raise ValueError('Replacement has unsafe audio edges')
    return edge


def apply_overrides(folder,chapter,selected):
    path=folder/'reviewed-take-overrides.json'
    if not path.exists():return selected
    registry=json.loads(path.read_text('utf8'))
    manifest=json.loads((folder/'manifest.json').read_text('utf8'))
    if registry['generation']!=folder.name or registry['source_sha256']!=manifest['source_sha256']:
        raise ValueError('Reviewed take registry belongs to different source/generation')
    result=[]
    for row in selected:
        override=registry['units'].get(row['unit']['id'])
        if not override or row['unit']['chapter_id']!=chapter:result.append(row);continue
        answer_path=Path(override['listener_answer'])
        if not answer_path.resolve().is_relative_to(folder.parent.resolve()):raise ValueError('Listener answer escaped generation workspace')
        if file_digest(answer_path)!=override['listener_answer_sha256']:raise ValueError('Listener answer changed')
        answer=json.loads(answer_path.read_text('utf8'))
        replacement=copy.deepcopy(override['replacement'])
        listener=answer.get('verdict')=='approved' and answer.get('source')=='explicit_listener'
        recovered=answer.get('verdict')=='word_verified' and answer.get('source')=='automatic_word_recovery'
        if (not (listener or recovered) or answer.get('audio_sha256')!=replacement['receipt']['audio_sha256']
            or answer.get('unit_id')!=row['unit']['id']):raise ValueError('Replacement has no matching listener approval')
        if recovered:
            from higgs_word_omissions import clear_omission
            if not clear_omission(row['word_check']):raise ValueError('Automatic recovery requires a bound original omission')
        if not Path(replacement['path']).resolve().is_relative_to(folder.parent.resolve()):raise ValueError('Replacement escaped generation workspace')
        if (row['receipt']['audio_sha256']!=override['previous_audio_sha256']
            or row['receipt']['key']!=override['previous_synthesis_key']):raise ValueError('Original take changed after listener comparison')
        stress_approved=listener and answer.get('approval_scope')=='stress_of_exact_take' and answer.get('pronunciation_status')=='listener_verified'
        replacement['edge']=validate_punctuation_take(row,replacement,listener_stress_approved=stress_approved)
        result.append(replacement)
    return result
