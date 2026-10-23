# Character and performance analysis

New generations can use **Local model** or **GPT · Codex CLI** in Settings. The
choice is saved in the generation config. GPT mode sends the book passages and
current character map through the signed-in Codex CLI; audio generation stays
on the selected speech backend. The existing Book 1 draft keeps its original
analysis and audio. Changing this setting does not retroactively relabel it.

The production analyzer validates exact text coverage, speaker IDs, delivery
labels, and evidence IDs. Each accepted segment gets a structured performance
plan: heading pauses, rhetorical enumeration pace, ellipsis cadence, explicit
excited speech, context-supported stunned reactions, and mechanical voice cues. The Higgs Book 1 renderer consumes
the shared planner for newly prepared prompts. It still has a few Book 1
pronunciation and performance overrides from listening feedback.

Audio chunks now start and end at sentence boundaries. The analyzer may mark
different speakers inside a sentence, but synthesis consolidates that sentence
into one voice request; mixed-character cases are flagged for review. A long
sentence stays whole even when it exceeds the preferred chunk length. The
Higgs sentence-safe draft also sets its backend character chunk limit above
every prepared prompt, and a pre-render gate checks exact EPUB coverage,
sentence boundaries, and unmodified prompt words.

These cues express **intended synthesis from the EPUB**, not measured features
of Vlad Rimsky's recording. A reviewer must check the original audio before
using a cue as a training label. The training dataset preserves the suggested
plan in `provenance.jsonl`. Audio-verified `review.performance_cues` require
`review.performance_checked: true`; `dataset.json` counts each verified cue
in train and held-out splits. `train.conditional.jsonl` and
`validation.conditional.jsonl` contain only audio-reviewed delivery labels for
a future conditional trainer. The official Qwen single-speaker SFT consumes
only audio, text, and reference audio, so these labels do not currently teach
instruction-level emotion or pause control. They are ready for a compatible
conditional training recipe and for stratified listening evaluation.

Text analysis can miss implicit tone and can overstate emotion from punctuation.
User reports should be compared against the source recording, then corrected
in generation revisions and in accepted corpus reviews where appropriate.

## Contextual moods and channels (2026-09-28)

New production analysis requests require a separate `context` decision on each
part: `mood`, `channel`, `confidence`, and exact quoted passage evidence. The
GPT/Codex and local structured analyzers share this vocabulary with the corpus
review mood list. The staged local analyzer runs the context stage even on
plain narrator prose, with the full target, adjacent passages, established
speaker and character registry. Speaker identity is independent of mood and
channel. A reference to game requirements, skills, notifications or a radio
is not sufficient to change the voice.

Validation checks quote identity, vocabulary and complete coverage. Invalid
staged responses get one repair request containing the original input,
rejected JSON and validation error; repeated failure stops analysis. Old
saved proposals without context remain compatible but have low confidence.
Different sentence moods/channels are kept in separate synthesis units; equal
labels can still be grouped. Context decisions persist in `segments.performance`.
The existing TTS delivery styles receive only directly compatible moods.
Other moods stay explicit metadata rather than being mapped to an unrelated
emotion. A configured game voice can be selected only for an evidenced,
confident `game_ui` decision; radio retains the character identity. Missing
or ambiguous game profiles produce a warning.

The Book 1 mistake at `c0028-p00084` came from the older corpus builder's
`?????????? ?` prefix shortcut. That builder now uses explicit contextual
review, and future routing/candidate tools refuse to promote legacy prefix
matches. Current immutable candidates are not rebuilt during labeling. A
hash-bound proposal overlay corrects this passage to narration in both review
steps and export, while saved human voice/mood labels take priority. Changing
a default never changes what an older Yes answer meant.

Checks: eight real GPT context cases passed (four narrator passages, two game
readouts, radio speech, and relief). A separate real request through the full
production schema and validator preserved the reported paragraph exactly,
with narrator/direct decisions and no repair. Reports are in
`training-work/character-rb1/context-analysis-regression.json` and
`context-production-regression.json`. This is a small regression set, not a
claim that all existing Book 1 classifications have been re-analyzed. In
particular GPT proposed neutral for the reported prose; the listener's saved
soft delivery remains the corpus label. Source audio review is still needed
for training mood labels.
