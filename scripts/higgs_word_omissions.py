"""Conservative deletion-only recognizer flags require recovery, not publication."""


def clear_omission(check):
    expected=check.get('expected_words');heard=check.get('heard_words')
    if (check.get('status')=='asr_exact_match' or not isinstance(expected,list)
        or not isinstance(heard,list) or not expected or len(heard)>=len(expected)):
        return False
    remaining=iter(expected)
    return all(any(word==candidate for candidate in remaining) for word in heard)
