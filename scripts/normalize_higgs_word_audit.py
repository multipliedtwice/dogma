"""Canonicalize ASR number formatting without altering its audio-only output."""
from __future__ import annotations
import importlib.metadata
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.speech_quality import transcript_check
from rimsky.util import digest,file_digest,write_json
from higgs_trial_io import read_json
from render_higgs_book_trial import spoken_text

def normalize(folder,raw_path=None):
    plan=read_json(folder/'render-plan.json')
    raw_path=raw_path or (folder/'word-checks-1.7B-cpu.json' if (folder/'word-checks-1.7B-cpu.json').exists() else folder/'word-checks-1.7B.json')
    raw=read_json(raw_path)
    if raw['plan_sha256']!=file_digest(folder/'render-plan.json'):raise ValueError('Raw ASR plan binding changed')
    units={u['id']:u for u in plan['units']}
    config={'asr':raw['verifier'],'numeric_transcript_normalization':{
            'implementation_sha256':file_digest(ROOT/'scripts/render_higgs_book_trial.py'),
            'normalizer_sha256':file_digest(Path(__file__)),
            'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py'),
            'spoken_equivalence_sha256':file_digest(ROOT/'runtime/rimsky/word_verification.py'),
            'orthographic_elongations':'collapse only repeated trailing final letters, never lexical word repetition',
            'num2words':importlib.metadata.version('num2words'),
            'rule':'Apply the same documented spoken number/symbol forms to the ASR transcript; preserve raw output.'}}
    signature=digest(config);checks={}
    for key,row in raw['checks'].items():
        if row.get('status')=='nonverbal_not_spoken_text':
            if units[key]['synthesis'].get('backend')!='nonverbal_asset':raise ValueError('Speech cannot bypass its word check')
            checks[key]={**row,'verifier_sha256':signature}
            continue
        normalization_error=None
        try:heard=spoken_text(row['heard'])
        except ValueError as exc:
            # Recognizer output is untrusted text, not synthesis input. Keep
            # unsupported number/symbol formatting as a flag, never abort the
            # whole chapter or silently turn it into an exact match.
            heard=row['heard'];normalization_error=str(exc)
        check=transcript_check(units[key]['spoken'],heard,row['audio_sha256'],signature,normalize_elongations=True)
        if not normalization_error and check['status']!='asr_exact_match':
            from rimsky.word_verification import reconcile
            raw_comparison=transcript_check(units[key]['spoken'],heard,row['audio_sha256'],signature)
            reproduced=reconcile(units[key]['spoken'],raw_comparison,'Russian')
            if reproduced and reproduced['status']=='asr_exact_match':check=reproduced
        if normalization_error:
            check.update(status='asr_spoken_text_unresolved',normalization_error=normalization_error)
        checks[key]={**check,'heard_raw':row['heard'],'audio':row['audio'],'synthesis_key':row['synthesis_key']}
    report={**raw,'verifier':config,'checks':checks,'raw_asr_sha256':file_digest(raw_path)}
    write_json(folder/'word-checks-1.7B-normalized.json',report)
    return report
