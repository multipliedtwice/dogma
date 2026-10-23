"""Apply exact, provenance-bound pronunciation/punctuation recipes to new takes."""
import re
from .spoken_pronunciation import unmarked, validate_marks
from .speech_quality import word_tokens


def _edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def _word_distance_ok(a, b, words=2, letters=3):
    """At most `words` changed words, each within `letters` edits (or a split/joined form)."""
    import difflib
    x, y = word_tokens(unmarked(a)), word_tokens(unmarked(b))
    changed = 0
    for tag, i, j, k, l in difflib.SequenceMatcher(a=x, b=y, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        left, right = ''.join(x[i:j]), ''.join(y[k:l])
        if not left or not right:
            return False
        if _edit_distance(left, right) > letters:
            return False
        changed += max(j - i, l - k)
    # Stress- or punctuation-only variants keep every token but change the text.
    return changed <= words and (changed > 0 or a != b)


def _delegated(plan):
    evidence = plan.get('evidence') or {}
    return (evidence.get('source') == 'delegated_editorial_decision' and evidence.get('decided_by')
            and evidence.get('user_instruction') and evidence.get('rationale'))


def apply_spoken_edit(spoken, plan):
    if plan.get('expected_spoken') != spoken:
        raise ValueError('Spoken edit belongs to different source wording')
    native = plan['native_text']
    if plan.get('recovery_kind') == 'listener_pronunciation_from_synthesis':
        previous = plan.get('previous_native_text', '')
        source_words = [word.replace('ё', 'е') for word in word_tokens(unmarked(spoken))]
        native_words = [word.replace('ё', 'е') for word in word_tokens(unmarked(native))]
        if (plan.get('evidence', {}).get('source') != 'explicit_listener_instruction'
                or not previous or unmarked(native) != unmarked(previous)
                or source_words != native_words):
            raise ValueError('Listener pronunciation changed prior native wording')
        validate_marks(native)
        return native, spoken
    # Editorial decisions the user delegated (2026-10-15, "Use your own judgement").
    # A respelling only changes what Higgs reads: the recognizers must still hear
    # the source words. A source correction (a typo, or our own number-reading
    # bug) names its corrected target and is verified against that target.
    if plan.get('recovery_kind') == 'delegated_respelling':
        if not _delegated(plan) or not _word_distance_ok(spoken, native):
            raise ValueError('Unsupported or unbound spoken edit')
        validate_marks(native)
        return native, spoken
    if plan.get('recovery_kind') == 'delegated_source_correction':
        target = plan.get('target_spoken', '')
        if (not _delegated(plan) or not _word_distance_ok(spoken, target, words=3, letters=12)
                or word_tokens(unmarked(native)) != word_tokens(unmarked(target))):
            raise ValueError('Unsupported or unbound spoken edit')
        validate_marks(native)
        return native, target
    if plan.get('recovery_kind') == 'editorial_reading_repair':
        # A typesetting slip (Атом — 5 for Атом-5) confirmed by two independent text models
        # (rimsky.reading_anomalies, 2026-10-17): only dashes, hyphens, spaces and punctuation
        # may differ, so the recognizers are still held to the source words.
        from .reading_anomalies import EVIDENCE_SOURCE, check_spoken_edit
        evidence = plan.get('evidence', {})
        if (evidence.get('source') != EVIDENCE_SOURCE or not evidence.get('request_sha256')
                or not evidence.get('answer_sha256') or not evidence.get('confirmed_by')):
            raise ValueError('Unconfirmed editorial reading repair')
        check_spoken_edit(spoken, native)
        return native, spoken
    if plan.get('recovery_kind')=='source_number_surface':
        from .spoken_numbers import same_surface
        from .util import digest
        if (not same_surface(spoken,native) or
            plan.get('evidence',{}).get('native_surface_sha256')!=digest(native.encode('utf8'))):
            raise ValueError('Numeric pronunciation surface changes the source value or wording')
        return native,spoken
    stressed = native != spoken and unmarked(native) == spoken
    comma = ',' in spoken and native == spoken.replace(',', '')
    # A bounded omission-recovery candidate may add a comma before a weak
    # phrase. It cannot drop/change words or replace existing punctuation.
    comma_cue = (plan.get('recovery_kind') == 'missing_word_phrasing'
                 and native.replace(',', '') == spoken.replace(',', '')
                 and native.count(',') > spoken.count(',')
                 and word_tokens(native) == word_tokens(spoken))
    period = native == spoken.rstrip() + '.' and not spoken.rstrip().endswith(('.', '!', '?', '…'))
    edge_fragment=(plan.get('recovery_kind')=='edge_fragment'
        and spoken and spoken[-1] not in '.!?…,:;'
        and native in (spoken+',',spoken+'.')
        and plan.get('evidence',{}).get('source')=='unsafe_generated_audio_edge'
        and bool(plan.get('evidence',{}).get('takes')))
    marked_period=(plan.get('recovery_kind')=='listener_pronunciation'
        and unmarked(native)==spoken.rstrip()+'.'
        and not spoken.rstrip().endswith(('.', '!', '?', '…'))
        and plan.get('evidence',{}).get('source')=='explicit_listener_instruction')
    opus = (plan.get('recovery_kind') == 'opus_word_recovery'
            and re.findall(r'[^\W_]+',unmarked(native).casefold()) == re.findall(r'[^\W_]+',unmarked(spoken).casefold())
            and plan.get('evidence',{}).get('diagnosis_sha256'))
    if not (stressed or comma or comma_cue or period or marked_period or opus or edge_fragment) or not plan.get('evidence'):
        raise ValueError('Unsupported or unbound spoken edit')
    if stressed or marked_period or opus:
        validate_marks(native)
    # Keep canonical punctuation in the word-audit target for this explicit
    # phrasing recipe; only the native synthesis surface loses its commas.
    if plan.get('recovery_kind')=='listener_comma_phrasing':
        if not comma or plan.get('evidence',{}).get('source')!='explicit_listener_instruction':
            raise ValueError('Comma phrasing revision is not listener-bound')
        return native,spoken
    return native, unmarked(native)
