# Voice labels across books

The speaker and the sound of the speaker are different data. Keep these fields separate in every series:

| Field | Meaning | Scope |
| --- | --- | --- |
| `narrator_id` | Human performer | Whole licensed corpus |
| `character_id` | Fictional speaker and aliases | Story or series |
| `voice_family_id` | Repeatable sound Vlad uses | Shared taxonomy across books |
| Character reference clips | Stable examples of this role's particular sound | Character profile, linked to the series |
| `mood` and delivery cues | Emotion, pace, endings, pauses | Passage |
| `presentation_channel` | Direct, radio, game UI, etc. | Passage |

The reviewed Book 1 exporter writes `voice_family_id` and keeps `character_id` beside it. Its `performance` field currently equals the family ID because the official trainer selects one performance at a time. The shared choices live in [`training/voice_families.json`](../training/voice_families.json). `protagonist_kirill` is an alias for the reusable `masculine_4` family in existing immutable candidate manifests. `voice_of_<character>` labels are unresolved and cannot enter exported training data.

Book 1 also offers provisional `masculine_5` (higher pitched, runner 343 example) and `masculine_6` (mature, San Sanych example). These are acoustic choices for Vlad's performed sound, not claims about the character's sex or age. Runner 343 is a woman in the reviewed cast map. A listener must confirm each passage before it enters training, and further source examples should establish whether each sound repeats consistently.

`narrator_opening` (**Opening**) is the distinct narrator register used in the
Reality Benders introductory section. Opening or prologue structure may prefill
this family with **Normal** as the default for the full section, following the
listener's Reality Benders review. Every clip still requires listening
confirmation, and audible exceptions can override either label.

`feminine_4_mechanical` (**Feminine voice 4 (mechanical)**) is a performed
female mechanical register. Keep it separate from `feminine_3` (the ordinary
Uline-like family) and from `mechanical_stats`, which represents game and system
readouts rather than a character's robotic female voice.

## Adding Reality Benders and Disgardium books

1. Give each story character a stable ID and link aliases across its series. Do not use the character ID as a training voice class.
2. Compare clean source excerpts with the existing family references. Assign a family only where the sound is supported; otherwise leave it pending. Keep several verified character reference clips for continuity within that family. One supported family choice can prefill later passages for that character, with an override when Vlad audibly changes register.
3. Keep mood, pacing, final-word contour, and channel independent of the family. A quiet or angry line should not create a new family.
4. Add a new reusable family only when multiple clean excerpts show a repeatable sound that the current families cannot represent. Record its reference clips and contrasting examples before using it across books.
5. Hold out whole chapters and books for evaluation, including cross-series checks. Evaluate character continuity and dialogue contrast as well as word accuracy and natural prosody.

## Later preference or reinforcement training

For the same passage and character map, compare generated alternatives against the original narrator performance and listener preferences. Store the selected family, character reference, mood, pace, pause, and ending cues with each comparison. A reward or preference model can then learn which rendition is better **conditional on those controls**. Character IDs remain casting metadata, so adding Disgardium characters does not expand the model's output classes. The reference clips preserve distinctions between characters that share a broad family. The current single-performance supervised trainer does not yet provide that reference conditioning, expressive control, or a reinforcement-learning stage; those need a separate model and evaluation gate. Reinforcement learning alone will not create those controls.
