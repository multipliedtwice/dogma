"""Build the listening rulebook for the mood labels (served on the Book 2 board).

Each entry has five fields: meaning, reviewed examples (your explicit choices,
including split parts), boundary against the nearest competing mood, measured
tendencies (named measurements, separate from listening interpretations) and
confidence. Examples are grouped by performer (book, character and voice family):
each section compares one performer's mood with that performer's own baseline
and rival-mood take, never across characters. Meanings and boundaries are
drafts written from your choices; your
rulebook feedback is saved separately and never changes clip labels.

Inputs are pinned: the explicit-choice evidence snapshot (scripts/analyze_reviewed_moods.py)
and the Book 2 sound profile report (scripts/describe_book2_mood_labels.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SERVED = ROOT / 'training-work/character-rb2-full/fine-tune-all/mood-rulebook'
EVIDENCE = ROOT / 'training-work/label-mood-analysis'
PROFILES = ROOT / 'training-work/character-rb2-full/mood-label-analysis'
BOOK1_AUDIO = ROOT / 'manual-labeling/reality-benders-1/audio'
BOOK2_CLIPS = ROOT / 'training-work/character-rb2/full/clips'
BOOK2_REVIEW = ROOT / 'manual-labeling/reality-benders-2-full/review.json'
EXAMPLES = 3

GROUPS = [
    ('Baseline, quiet and informative delivery', ['neutral', 'calm', 'soft', 'whisper', 'reflective', 'reminiscent', 'explaining', 'solemn']),
    ('Engagement, positive expression and relationships', ['energetic', 'excited', 'cheerful', 'playful', 'curious', 'confident', 'triumphant',
                                                          'awe', 'encouraging', 'warm', 'gentle', 'reassuring', 'relieved']),
    ('Uncertainty, concern and reactions', ['perplexed', 'hesitant', 'worried', 'anxious', 'surprised', 'shocked', 'stunned', 'afraid',
                                           'panicked', 'questioning']),
    ('Disappointment, suffering and reduced capacity', ['disappointed', 'sad', 'grieving', 'distressed', 'tired', 'injured', 'embarrassed']),
    ('Resistance, force, criticism and hostility', ['irritated', 'angry', 'indignant', 'firm', 'commanding', 'threatening', 'warning',
                                                   'urgent', 'fast_action', 'shouting', 'sarcastic', 'mocking', 'contemptuous', 'disgusted',
                                                   'suspicious']),
]

# Draft meanings and boundaries, written from the explicit choices. Each
# boundary names the nearest competing mood that the page plays alongside.
ENTRIES = {
    'neutral': ('Ordinary, direct delivery without a pronounced expressive treatment. It can describe danger or excitement without performing it; game announcements and plain dialogue often sit here.',
                'calm', 'Normal is ordinary speaking; Calm is the softer, more unhurried storytelling register. Material type is only a tendency: narrator storytelling can be either.'),
    'calm': ('Comfortable, unforced storytelling or composed conversation: softer and more unhurried than Normal. Serious events and instructions can still be told calmly.',
             'neutral', 'Calm relaxes the delivery; Normal keeps ordinary speaking energy. Within one voice the measured differences are small, so this is a listening distinction first.'),
    'soft': ('Keeping the voice down: a subdued or intimate delivery.',
             'calm', 'Soft lowers the voice on purpose; Calm is relaxed but at ordinary level.'),
    'whisper': ('Confidential delivery: speaking so that others will not hear, with discreet questions or private information.',
                'soft', 'Whisper hides the words from others; Soft is gentle but still voiced.'),
    'reflective': ('Working something out: weighing possibilities, connecting observations or considering consequences, with attention turned inward.',
                   'curious', 'Thinking reasons through what is already known; Curious reaches toward something not yet known.'),
    'reminiscent': ('Recounting or searching memory. Nostalgia or sadness is not required.',
                    'reflective', 'Remembering looks back at past events; Thinking works on the present problem.'),
    'explaining': ('Making something understandable to another person: mechanisms, procedures, rules and reasons.',
                   'reflective', 'Explaining is aimed at a listener; Thinking can stay an internal process.'),
    'solemn': ('Giving a matter weight: responsibility, consequences, important decisions and warnings. Broader than ceremonial solemnity.',
               'calm', 'Serious loads the words with consequence; Calm tells them comfortably.'),
    'energetic': ('Active, engaged delivery that moves the account forward: plans, decisions and actions. Energy does not imply happiness or urgency.',
                  'excited', 'Energetic is forward momentum; Excited is emotional activation, positive or distressed.'),
    'excited': ('Emotionally activated delivery: enthusiasm, anticipation and admiration, but also agitated concern or protest.',
                'energetic', 'Excited is heightened emotional involvement; Energetic is engaged but not emotionally raised.'),
    'cheerful': ('Clearly positive enjoyment or satisfaction with how things are going.',
                 'excited', 'Happy is pleasure; Excited is activation, which need not be pleasant.'),
    'playful': ('Amused, teasing, mischievous or lightly flirtatious; the listener is invited into the joke.',
                'sarcastic', 'Playful shares the fun; Sarcastic carries a critical edge.'),
    'curious': ('Interested attention toward something unresolved: investigating, noticing an unusual detail, wondering what it means.',
                'perplexed', 'Curious explores the unknown with interest; Confused fails to reconcile it.'),
    'confident': ('Speaking from certainty or competence: a conclusion, a workable plan, a definite answer.',
                  'firm', 'Confident expresses assurance; Firm closes a negotiation or sets a boundary.'),
    'triumphant': ('Savouring an achievement: "we did it" or "look what I achieved".',
                   'confident', 'Proud after winning celebrates a result; Confident states a certainty.'),
    'awe': ('Being overwhelmed by the scale of something, such as a favourable discovery.',
            'surprised', 'Amazed dwells on the scale of what is found; Surprised registers that it is unexpected.'),
    'encouraging': ('Helping someone continue or act: making success seem possible, restoring motivation.',
                    'reassuring', 'Encouraging gives momentum; Comforting removes worry.'),
    'warm': ('Welcoming, companionable delivery toward another person, even for greetings or instructions.',
             'gentle', 'Friendly is companionship; Gentle is considerate, softened treatment.'),
    'gentle': ('A considerate, softened approach to the listener.',
               'warm', 'Gentle softens how something lands; Friendly keeps the relationship warm.'),
    'reassuring': ('Reducing another person\'s concern: establishing trust and a manageable way forward.',
                   'warm', 'Comforting actively soothes a worry; Friendly needs no worry to address.'),
    'relieved': ('Pressure easing after difficulty, discomfort or uncertainty.',
                 'cheerful', 'Relieved moves from strain to release; Happy starts from pleasure.'),
    'perplexed': ('Struggling to reconcile what is happening with one\'s understanding: a disrupted mental picture.',
                  'curious', 'Confused cannot make sense of it; Curious is interested in finding out.'),
    'hesitant': ('Holding back, qualifying a thought, reconsidering an action or approaching an uncomfortable conclusion.',
                 'perplexed', 'Hesitating is reluctance to commit; Confused is not understanding.'),
    'worried': ('Concern about consequences, someone\'s welfare or what might have gone wrong; often through questions and cautious objections.',
                'anxious', 'Worried is concern about an outcome; Nervous / tense is pressure felt right now.'),
    'anxious': ('Pressure felt in the present: something matters urgently, a threat is close, there is little room for error.',
                'worried', 'Nervous / tense is immediate pressure; Worried looks ahead to consequences.'),
    'surprised': ('Registering an unexpected fact: a sudden change in expectation.',
                  'shocked', 'Surprised is a change of expectation; Shocked is a stronger, destabilising impact.'),
    'shocked': ('A strong, destabilising reaction to something alarming or hard to accept.',
                'stunned', 'Shocked reacts outward; Quiet shock is being stopped by the impact.'),
    'stunned': ('Being stopped by the impact of something: disbelief, stunned recognition.',
                'shocked', 'Quiet shock goes still; Shocked reacts. Punctuation alone does not separate them.'),
    'afraid': ('Directly felt fear of a threatening presence or possibility.',
               'anxious', 'Scared fears the threat itself; Nervous / tense is pressure to get something right.'),
    'panicked': ('Losing composure under threat or defeat.',
                 'afraid', 'Panicking has lost control; Scared still holds together.'),
    'questioning': ('Asking a question as the main act of the line.',
                    'curious', 'Asking a question is the form; Curious is the interest behind it.'),
    'disappointed': ('A hoped-for outcome failing: something does not work, help is unavailable, expectations are not met.',
                     'sad', 'Disappointed is a deflated expectation; Sad is sorrow or loss.'),
    'sad': ('Sorrow, loss and bleakness without becoming an overwhelming reaction.',
            'grieving', 'Sad stays contained; Deeply sad is anguish.'),
    'grieving': ('Anguish over a loss, such as helplessness after losing something hard-won.',
                 'sad', 'Deeply sad is overwhelming; Sad is contained.'),
    'distressed': ('Emotional discomfort that is harder to contain: painful circumstances, disturbing news, personal vulnerability.',
                   'worried', 'Upset is already hurting; Worried anticipates harm.'),
    'tired': ('Depletion: struggling to think, feeling worn down, finding another demand burdensome.',
              'calm', 'Tired is low energy from exhaustion; Calm is low energy by comfort.'),
    'injured': ('Physical suffering, such as pain or nausea; an injury is not required.',
                'tired', 'In pain is physical suffering; Tired is depletion.'),
    'embarrassed': ('Social discomfort around dignity, status or being diminished.',
                    'indignant', 'Embarrassed is discomfort at being diminished; Offended protests it.'),
    'irritated': ('Friction that has become audible: an unwelcome complication, a repeated problem, irritating behaviour.',
                  'angry', 'Annoyed is friction; Angry is direct confrontation. Too few Angry examples to compare by sound.'),
    'angry': ('Direct emotional confrontation: blame, resentment, a forceful complaint or hostile response.',
              'irritated', 'Angry confronts; Annoyed is friction that has not become confrontation.'),
    'indignant': ('Protest grounded in perceived unfairness: objecting to what is being done or alleged.',
                  'angry', 'Offended protests unfairness; Angry confronts with blame.'),
    'firm': ('Closing negotiation or establishing a boundary. Decisiveness matters more than emotional heat.',
             'commanding', 'Firm sets a limit; Giving orders directs what others do.'),
    'commanding': ('Directing what others should do, or speaking with institutional authority.',
                   'firm', 'Giving orders seeks compliance; Firm refuses to move.'),
    'threatening': ('Making danger felt: an explicit consequence, an intimidating presence or an ominous prediction. Also used for narrated menace.',
                    'angry', 'Threatening points at a consequence; Angry vents the feeling.'),
    'warning': ('Bringing danger to everyone\'s attention: urgent announcements and explicit alerts.',
                'shouting', 'Warning / alarm is the purpose; Shouting is the vocal technique.'),
    'urgent': ('Being in a hurry: pressing for speed.',
               'fast_action', 'In a hurry is a speaker pressing for time; Fast action is narration carrying rapid events.'),
    'fast_action': ('Narration carrying a rapid sequence of physical events: fighting, scrambling, impacts and immediate reactions.',
                    'energetic', 'Fast action is the unfolding event; Energetic is engaged momentum in ordinary activity.'),
    'shouting': ('Projected, raised-voice delivery.',
                 'warning', 'Shouting is a vocal technique; Warning / alarm is a purpose.'),
    'sarcastic': ('A critical or knowing edge: pointed questions, dry commentary, statements meaning more than they say.',
                  'playful', 'Sarcastic criticises; Playful shares the fun.'),
    'mocking': ('Making fun of someone.',
                'sarcastic', 'Making fun targets a person; Sarcastic is an edge on a statement.'),
    'contemptuous': ('Dismissive superiority: treating another person\'s words, abilities or concerns as beneath the speaker.',
                     'sarcastic', 'Looking down dismisses the person; Sarcastic comments with an edge.'),
    'disgusted': ('Visceral rejection of something repulsive.',
                  'contemptuous', 'Disgusted recoils physically; Looking down dismisses socially.'),
    'suspicious': ('Rejecting another person\'s credibility or truthfulness.',
                   'sarcastic', 'Does not trust someone questions the truth; Sarcastic comments with an edge.'),
}

MEASURES = {'level_db': 'active speech level (dBFS)', 'pitch_st': 'median pitch (semitones)',
            'pitch_range_st': 'pitch range (semitones)', 'level_range_db': 'level range (dB)',
            'articulation_syl_per_s': 'articulation rate (syllables/s, word timings)', 'pause_share': 'pause share within the clip',
            'final_word_lengthening': 'last-word lengthening', 'ending_change_st': 'pitch change at the phrase end (semitones)',
            'periodic_fraction': 'periodic-frame share (pitch detector)'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest(folder: Path, pattern: str) -> Path:
    found = sorted(folder.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not found:
        raise SystemExit(f'No {pattern} under {folder}; run its analysis script first.')
    return found[-1]


def display_names() -> dict:
    source = (HERE / 'book1_review_shared.js').read_text(encoding='utf-8')
    block = source[source.index('const MOOD_LABELS = {'):]
    block = block[:block.index('};')]
    return dict(re.findall(r"(\w+):'([^']+)'", block))


def confidence(count: int) -> str:
    return ('No examples yet' if count == 0 else 'Single examples' if count < 5 else 'Tentative' if count < 20
            else 'Moderate' if count < 80 else 'Well supported')


def split_ranges() -> dict:
    decisions = json.loads(BOOK2_REVIEW.read_text(encoding='utf-8'))['decisions']
    ranges = {}
    for parent, decision in decisions.items():
        for part_id, part in ((decision.get('split_review') or {}).get('parts') or {}).items():
            source = part.get('split_source') or {}
            if source.get('sample_rate'):
                ranges[part_id] = (parent, source['first_frame'] / source['sample_rate'], source['last_frame'] / source['sample_rate'])
    return ranges


def playable(unit: dict, ranges: dict) -> dict | None:
    if unit['book'] == 'reality-benders-1' and (BOOK1_AUDIO / f"{unit['audio_sha256']}.wav").is_file():
        return {'audio': f"/manual-labeling/reality-benders-1/audio/{unit['audio_sha256']}.wav"}
    if unit['book'] == 'reality-benders-2-full':
        if unit['unit'] == 'clip' and (BOOK2_CLIPS / f"{unit['id']}.wav").is_file():
            return {'audio': f"/character-rb2/full/clips/{unit['id']}.wav"}
        if unit['unit'] == 'split_part' and unit['id'] in ranges:
            parent, start, end = ranges[unit['id']]
            if (BOOK2_CLIPS / f'{parent}.wav').is_file():
                return {'audio': f'/character-rb2/full/clips/{parent}.wav', 'start': round(start, 3), 'end': round(end, 3)}
    return None


def book1_names() -> dict:
    path = ROOT / 'training-work/character-rb1/character-reference-coverage.json'
    if not path.is_file():
        return {}
    found = {}
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('id'), str) and isinstance(value.get('name'), str):
                found[value['id']] = value['name']
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(json.loads(path.read_text(encoding='utf-8')))
    return found


def speaker(unit: dict, names: dict) -> tuple[tuple, str]:
    """One performer: same book, same character, same voice family. Narration is the narrator."""
    voice = unit['voice'] or 'unresolved'
    who = 'narrator' if voice.startswith('narrator') else (unit.get('speaker_proposed') or voice)
    book = 'Book 1' if unit['book'] == 'reality-benders-1' else 'Book 2'
    name = 'Narrator' if who == 'narrator' else names.get(who, who)
    return (unit['book'], who, voice), f'{name} · {voice} · {book}'


def example(unit: dict, audio: dict) -> dict:
    return {'id': unit['id'], 'book': 'Book 1' if unit['book'] == 'reality-benders-1' else 'Book 2',
            'kind': 'split part' if unit['unit'] == 'split_part' else 'clip', 'voice': unit['voice'],
            'text': unit['text'].strip(), **audio}


def pick(units: list[dict], ranges: dict, limit: int, avoid: set) -> list[dict]:
    """Up to `limit` playable examples in a stable order, at most one split part."""
    chosen = []
    for unit in sorted(units, key=lambda item: hashlib.sha256(item['id'].encode()).hexdigest()):
        if len(chosen) >= limit:
            break
        if unit['id'] in avoid or unit['unit'] == 'split_part' and any(item['kind'] == 'split part' for item in chosen):
            continue
        if audio := playable(unit, ranges):
            chosen.append(example(unit, audio))
    return chosen


def sections_for(mood: str, rival: str, by_speaker: dict, labels: dict, ranges: dict, limit: int) -> list[dict]:
    """Per performer: their examples of this mood, their own baseline and their rival-mood take.

    Different characters and voices are never compared inside one section.
    """
    ranked = sorted(by_speaker, key=lambda key: (-len(by_speaker[key].get(mood, [])), labels[key]))
    sections = []
    for key in ranked:
        takes = by_speaker[key]
        if len(sections) >= limit or not takes.get(mood):
            continue
        own = pick(takes[mood], ranges, 2, set())
        if not own:
            continue
        # The performer's own baseline: Calm, else Normal (Normal for the Calm entry itself).
        base_mood = next((choice for choice in (('neutral',) if mood == 'calm' else ('calm', 'neutral'))
                          if choice != mood and pick(takes.get(choice, []), ranges, 1, set())), None)
        base = pick(takes.get(base_mood, []), ranges, 1, set()) if base_mood else []
        versus = pick(takes.get(rival, []), ranges, 1, set()) if rival != base_mood else []
        sections.append({'speaker': labels[key], 'examples': own,
                         'baseline': {'mood': base_mood, 'example': base[0]} if base else None,
                         'rival': None if rival == base_mood else {'mood': rival, 'example': versus[0] if versus else None}})
    return sections


def tendencies(profile: dict | None) -> list[dict]:
    if not profile:
        return []
    return [{'measure': MEASURES[key], 'sd': value, 'direction': 'higher' if value > 0 else 'lower'}
            for key, value in sorted(profile['sound_vs_own_voice_baseline_z'].items(), key=lambda item: -abs(item[1]))
            if abs(value) >= .3]


def build(evidence_path: Path, profile_path: Path, limit: int) -> dict:
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    profiles = json.loads(profile_path.read_text(encoding='utf-8'))
    names = display_names()
    missing = set(names) - set(ENTRIES)
    if missing:
        raise SystemExit(f'Rulebook drafts are missing for: {sorted(missing)}')
    by_mood = defaultdict(list)
    by_speaker, labels, people = defaultdict(lambda: defaultdict(list)), {}, book1_names()
    for unit in evidence['units']:
        by_mood[unit['mood']].append(unit)
        if unit['voice']:
            key, label = speaker(unit, people)
            by_speaker[key][unit['mood']].append(unit)
            labels[key] = label
    ranges = split_ranges()
    entries = []
    for group, moods in GROUPS:
        for mood in moods:
            meaning, rival, boundary = ENTRIES[mood]
            units = by_mood.get(mood, [])
            profile = profiles['moods'].get(mood)
            entries.append({'mood': mood, 'name': names[mood], 'group': group, 'meaning': meaning,
                            'rival': rival, 'rival_name': names[rival], 'boundary': boundary,
                            'count': len(units), 'books': dict((evidence['moods'].get(mood) or {}).get('books', {})),
                            'split_parts': sum(unit['unit'] == 'split_part' for unit in units),
                            'confidence': confidence(len(units)),
                            'sections': (sections := sections_for(mood, rival, by_speaker, labels, ranges, limit)),
                            'examples': [item for section in sections for item in section['examples']],
                            'speakers': len([key for key in by_speaker if by_speaker[key].get(mood)]),
                            'measured': {'clips': profile['clips'] if profile else 0, 'tendencies': tendencies(profile)}})
    grouped = [mood for _, moods in GROUPS for mood in moods]
    if sorted(grouped) != sorted(names):
        raise SystemExit('Rulebook groups do not cover every board mood exactly once.')
    version = hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {'schema': 1, 'version': version, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'inputs': {'evidence': {'path': str(evidence_path.relative_to(ROOT)), 'sha256': sha(evidence_path),
                                    'captured_at': evidence.get('captured_at')},
                       'sound_profiles': {'path': str(profile_path.relative_to(ROOT)), 'sha256': sha(profile_path)}},
            'measurement_note': ('Measured tendencies are exploratory averages from Book 2 whole clips, in standard deviations '
                                 'from the same voice\'s Calm/Normal clips (a pooled baseline for small voice families), '
                                 'without uncertainty intervals or chapter controls. They are not listening judgements: '
                                 'the periodic-frame share is not a breathiness measure, and vocal attack was not measured.'),
            'entries': entries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--profiles', type=Path)
    parser.add_argument('--examples', type=int, default=EXAMPLES)
    args = parser.parse_args()
    evidence = (args.evidence or latest(EVIDENCE, '*/evidence.json')).resolve()
    profiles = (args.profiles or latest(PROFILES, 'mood-profiles-*.json')).resolve()
    rulebook = build(evidence, profiles, args.examples)
    SERVED.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / 'mood_rulebook.html', SERVED / 'index.html')
    (SERVED / 'rulebook.json').write_text(json.dumps(rulebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    entries = rulebook['entries']
    print(json.dumps({'version': rulebook['version'], 'moods': len(entries),
                      'with_examples': sum(bool(entry['examples']) for entry in entries),
                      'without_examples': [entry['mood'] for entry in entries if not entry['examples']],
                      'page': '/character-rb2-full/fine-tune-all/mood-rulebook/'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
