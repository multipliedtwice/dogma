"""Shared gates for turning review answers into trainable labels."""

MOOD_ALIASES = {
    'exhausted': 'tired',
    'wistful': 'reminiscent',
    'uncertain': 'hesitant',
    'cheerful, excited': 'excited',
}
PENDING_MOODS = {'mood_pending'}


def canonical_review_mood(value):
    """Return a real mood, or None for missing/pending review state."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value in PENDING_MOODS:
        return None
    return MOOD_ALIASES.get(value, value)


def resolved_review_mood(proposed, verdict, correction):
    """Resolve only an explicit human mood verdict; passive playback is ignored."""
    if verdict == 'yes':
        return canonical_review_mood(proposed)
    # Older reviews stored a manually selected correction without a separate
    # `mood_verdict=no` field. Preserve that explicit choice. Passive playback
    # stores only `playback_acceptance.mood`, so it can never enter here.
    return canonical_review_mood(correction)


def resolved_review_speaker(proposed, verdict, correction):
    """Resolve an explicit speaker decision without inferring one from a voice family."""
    proposed = proposed.strip() if isinstance(proposed, str) else None
    correction = correction.strip() if isinstance(correction, str) else None
    if verdict == 'yes':
        return proposed or None
    # Accept the current `no` convention and the earlier descriptive spelling.
    # A stored correction is an explicit human action even if an older client
    # did not write a separate verdict field.
    if verdict in ('no', 'corrected') or correction:
        return correction or None
    return proposed or None
