"""Bind independent exact-word witnesses to selected takes; retain primary flags."""
from __future__ import annotations
import copy
import difflib
import re
from pathlib import Path
from higgs_trial_io import read_json
from rimsky.speech_quality import transcript_check,word_tokens,spoken_word_tokens
from rimsky.util import digest,file_digest


def report_paths(out):
    # Witness reports may sit beside the take, in any enclosing chunk/recovery
    # folder or at the generation; each check stays bound to its exact audio.
    generation=next((p for p in [out,*out.parents] if (p/'manifest.json').exists()),None)
    folders=[out]+([p for p in out.parents if generation in (p,*p.parents)] if generation else [])
    paths=[path for folder in reversed(folders) for path in sorted(folder.glob('secondary-asr-*/report.json'))]
    return list(dict.fromkeys(paths))


_ORDINALS=None


def _ordinal_lemmas(morph):
    """{ordinal lemma: cardinal lemma} for the words num2words uses (восьмой: восемь, тысячный: тысяча)."""
    global _ORDINALS
    if _ORDINALS is None:
        from num2words import num2words
        table={}
        for n in [*range(1,20),*range(20,100,10),*range(100,1000,100),1000,1000000]:
            card=num2words(n,lang='ru').split()[-1];ordi=num2words(n,lang='ru',to='ordinal').split()[-1]
            table[morph.parse(ordi)[0].normal_form]=morph.parse(card)[0].normal_form
        _ORDINALS=table
    return _ORDINALS


def numeral_corroboration(expected,heard,raw,others):
    """A recognizer that writes a number as digits loses its case ending.

    Accept such a token only where the raw transcript had digits, both tokens
    are numerals of the same lemma, and another recognizer of the same audio
    heard the exact source numeral together with both neighbouring words.
    """
    if not re.search(r'\d',raw):return heard,[]
    # One analyzer per process: building it costs ~0.1 s and this runs on every rule evaluation.
    from rimsky.spoken_homophones import _morph
    morph=_morph()
    ordinal=_ordinal_lemmas(morph)
    def numeral(word):
        # Cardinal lemma; an ordinal (восьмом) maps to its cardinal (восемь): a recognizer
        # writes "в 2058" for "в две тысячи пятьдесят восьмом" (iteration 5, 2026-10-16).
        parses=morph.parse(word)
        return (next((p.normal_form for p in parses if p.tag.POS=='NUMR'),None)
                or next((ordinal[p.normal_form] for p in parses if p.normal_form in ordinal),None))
    source=spoken_word_tokens(expected);recognised=spoken_word_tokens(heard)
    corrected=[];accepted=[]
    for tag,i,j,k,l in difflib.SequenceMatcher(a=source,b=recognised,autojunk=False).get_opcodes():
        if tag=='equal':corrected.extend(recognised[k:l]);continue
        if tag!='replace' or j-i!=l-k:return heard,[]
        if any(numeral(a) is None or numeral(a)!=numeral(b) for a,b in zip(source[i:j],recognised[k:l])):return heard,[]
        window=source[max(i-1,0):j+1]
        witness=next((name for name,tokens in others
                      if any(tokens[n:n+len(window)]==window for n in range(len(tokens)-len(window)+1))),None)
        if witness is None:return heard,[]
        corrected.extend(source[i:j])
        accepted.append({'source_tokens':source[i:j],'heard_tokens':recognised[k:l],'raw_has_digits':True,
                         'corroborating_window':window,'corroborating_report':witness,'rule':'numeral-case-corroboration-v1'})
    return ' '.join(corrected),accepted


def raw_witness_coverage(selected,paths,implementation_sha256,signal=None):
    """A comparison-code change doesn't require hearing the same audio again.

    Reuse only this recognizer's pinned signal configuration and bound raw
    transcript. The caller still compares every word under the current rules.
    """
    from audit_higgs_secondary_asr import MODEL,REVISION,DEVICE,DTYPE
    from higgs_file_checks import cached_sha
    from huggingface_hub import try_to_load_from_cache
    signal=signal or {'model':MODEL,'revision':REVISION,'language':'ru','task':'transcribe',
        'do_sample':False,'device':DEVICE,'dtype':DTYPE}
    by_uid={}
    for key,row in selected.items():by_uid.setdefault(row['unit'].get('id',key),[]).append((key,row))
    covered=set()
    for path in paths:
        report=read_json(path);config=report['verifier']
        if config.get('implementation_sha256')!=implementation_sha256 or any(config.get(k)!=v for k,v in signal.items()):continue
        files=config.get('files',{})
        if not files or 'model.safetensors' not in files:continue
        verified=True
        for name,sha in files.items():
            local=try_to_load_from_cache(signal['model'],name,revision=signal['revision'])
            if not isinstance(local,str) or cached_sha(Path(local))!=sha:verified=False;break
        if not verified:continue
        for witness in report['checks'].values():
            for key,row in by_uid.get(witness['unit_id'],[]):
                primary=row['word_check']
                if (witness['audio_sha256']==row['receipt']['audio_sha256']==primary['audio_sha256']
                    and witness['synthesis_key']==row['receipt']['key']==primary['synthesis_key']
                    and witness['expected_text_sha256']==digest(row['unit']['spoken'].encode('utf-8'))
                    and cached_sha(Path(row['path']))==witness['audio_sha256']):covered.add(key)
    return covered

def half_compound_expansion(expected,heard):
    """Expand only a heard пол+word that exactly matches two source tokens.

    This is a spoken orthographic comparison, not a general space deletion.
    It never equates неторопливо/не торопливо or fixes a missing preposition.
    """
    source=word_tokens(expected);recognised=word_tokens(heard)
    matcher=difflib.SequenceMatcher(a=source,b=recognised,autojunk=False)
    expanded=[];changes=[]
    for tag,i,j,k,l in matcher.get_opcodes():
        if tag=='replace' and j-i==2 and l-k==1 and source[i]=='пол' and recognised[k]==''.join(source[i:j]):
            expanded.extend(source[i:j]);changes.append({'source_tokens':source[i:j],'heard_token':recognised[k]})
        else:expanded.extend(recognised[k:l])
    # Unchanged transcripts keep their own spelling (hyphenated sustained sounds such as У-у-у).
    return (' '.join(expanded) if changes else heard),changes

from rimsky.word_verification import final_devoicing_equivalence,proper_name_hyphen_expansion


def apply_secondary(selected,report_paths):
    if not report_paths:return copy.deepcopy(selected)
    witnesses={}
    for path in report_paths:
        report=read_json(path)
        for check in report['checks'].values():
            witnesses.setdefault((check['audio_sha256'],check['synthesis_key']),[]).append((path,report['verifier'],check))
    result=copy.deepcopy(selected)
    for uid,item in result.items():
        primary=item['word_check']
        if primary['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
        wav=Path(item['path'])
        if file_digest(wav)!=item['receipt']['audio_sha256'] or primary['audio_sha256']!=item['receipt']['audio_sha256']:
            raise ValueError('Selected audio changed before independent verification')
        expected=item['unit']['spoken']
        for path,config,witness in witnesses.get((primary['audio_sha256'],primary['synthesis_key']),[]):
            if witness['unit_id']!=uid or witness['expected_text_sha256']!=digest(expected.encode('utf-8')):continue
            from render_higgs_book_trial import spoken_text
            try:canonical=spoken_text(witness['heard'])
            except ValueError:canonical=witness['heard']
            heard,changes=half_compound_expansion(expected,canonical)
            heard,name_boundaries=proper_name_hyphen_expansion(expected,heard)
            phonological=[];homophones=[]
            if config.get('language')=='ru':heard,phonological=final_devoicing_equivalence(expected,heard)
            if (config.get('language')=='ru' and transcript_check(expected,heard,'','',normalize_elongations=True)['status']!='asr_exact_match'):
                # Spellings that standard speech merges (spacing, geminates,
                # unstressed reduction) are recognizer orthography. Fail closed
                # when the phonemizer or lexicon is unavailable.
                try:from rimsky.spoken_homophones import homophone_equivalence,elongation_equivalence,synthesis_stress
                except ImportError:pass
                else:
                    corrected,elongations=elongation_equivalence(expected,spoken_word_tokens(expected),spoken_word_tokens(heard))
                    stress=synthesis_stress((item['unit'].get('synthesis') or {}).get('text',''))
                    corrected,homophones=homophone_equivalence(spoken_word_tokens(expected),corrected,stress)
                    homophones=elongations+homophones
                    if homophones:heard=' '.join(corrected)
            verifier={'independent_verifier':config,'orthography':'exact source пол + noun may be joined in an ASR transcript',
                      'implementation_sha256':file_digest(Path(__file__)),
                      'spoken_equivalence_sha256':file_digest(Path(__file__).resolve().parents[1]/'runtime/rimsky/word_verification.py')}
            if homophones:
                from rimsky.spoken_homophones import dependencies
                verifier.update(homophone_rule_sha256=file_digest(Path(__file__).resolve().parents[1]/'runtime/rimsky/spoken_homophones.py'),
                                homophone_dependencies=dependencies())
            numerals=[]
            if (config.get('language')=='ru' and transcript_check(expected,heard,'','',normalize_elongations=True)['status']!='asr_exact_match'):
                others=[(str(Path(other).resolve()),spoken_word_tokens(row['heard']))
                        for other,other_config,row in witnesses.get((primary['audio_sha256'],primary['synthesis_key']),[])
                        if other!=path and digest(other_config)!=digest(config) and row['unit_id']==uid
                        and row['expected_text_sha256']==witness['expected_text_sha256']]
                heard,numerals=numeral_corroboration(expected,heard,witness['heard'],others)
                if numerals:verifier['numeral_rule']='numeral-case-corroboration-v1'
            tolerated=[]
            if (config.get('language')=='ru' and transcript_check(expected,heard,'','',normalize_elongations=True)['status']!='asr_exact_match'):
                # Disclosed listener decision: proper nouns are not spelling-checked (guards in name_tolerance).
                from rimsky.name_tolerance import tolerance,RULE as TOLERANCE_RULE
                respelled=((item['unit'].get('synthesis') or {}).get('listener_respelling') or {}).get('records')
                corrected,tolerated=tolerance(spoken_word_tokens(expected),spoken_word_tokens(heard),expected,respelled)
                if tolerated:
                    heard=' '.join(corrected)
                    verifier.update(tolerance_rule=TOLERANCE_RULE,tolerance_sha256=file_digest(
                        Path(__file__).resolve().parents[1]/'runtime/rimsky/name_tolerance.py'))
            check=transcript_check(expected,heard,primary['audio_sha256'],digest(verifier),normalize_elongations=True)
            if check['status']!='asr_exact_match':continue
            item['word_check']={**check,'synthesis_key':primary['synthesis_key'],'heard_raw':witness['heard'],
                'primary_disagreement':primary,'independent_report_sha256':file_digest(path),
                'independent_report':str(path.resolve()),'orthographic_expansions':changes,
                'phonological_equivalences':phonological,
                'proper_name_boundaries':name_boundaries,
                **({'homophone_equivalences':homophones} if homophones else {}),
                **({'numeral_corroborations':numerals} if numerals else {}),
                **({'tolerated_deviations':tolerated} if tolerated else {}),
                'audio':primary.get('audio'),'verifier':verifier,'human_approval':False}
            break
    return result

def cached_secondary(out,selected):
    return apply_secondary(selected,report_paths(out))
