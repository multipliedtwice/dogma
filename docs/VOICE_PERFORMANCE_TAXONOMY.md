# Vlad Rimsky performance map for audiobook training

This is an **open, compositional checklist**, not a claim that every listed
performance occurs in the available recordings or an exhaustive list of all
possible acting choices. New observed traits can be added with a definition
and source example. The EPUB supplies identity, physical description, and
scene clues. The audiobook supplies evidence of how Vlad actually performed
a passage. Record those two kinds of evidence separately. Do not turn a
character's described age, size, sex, species, or morality into an assumed
acoustic trait.

## Five independent layers

| Layer | Question | Keep stable? |
| --- | --- | --- |
| Performer | Which human made the recording? | Vlad Rimsky throughout this corpus, unless source review finds otherwise. |
| Role | Narrator, a named character, an unknown speaker, or a game/system voice? | Stable within a character's continuity; unknown stays unknown. |
| Narrator register | Baseline narration or a repeatable register such as `narrator_historical_prose`? | Keep the register separate from momentary mood; verify it against source examples. |
| Character sound | What repeatable vocal impression does Vlad use for this role? | Usually stable, with documented changes across scenes or books. |
| Momentary performance | What does this line do emotionally and rhythmically? | May change from sentence to sentence. |
| Presentation channel | Is the voice heard directly, by radio, as a recording, or represented by on-screen text? | Changes with the scene, even for the same role and performance. |

`radio`, `intercom`, `public_address`, and `recorded` describe how a voice is
presented. They do not create another character. A voice can be angry *and*
quiet, old *and* quick, or large *and* gentle. Preserve combinations rather
than forcing one exclusive label.

A **model voice reference** is a clip supplied to a cloning model. An
**evidence reference** is a time-coded excerpt of the original recording
supporting an annotation. They can be the same audio file, but they serve
different purposes. The evidence reference is required before calling an
acoustic training label verified; a cloning reference alone does not prove
what a character usually sounds like.

## Character sound profiles to look for

The following are **candidate acoustic descriptions**. A text description
such as “giant” or “fairy” is evidence about the character, not proof of how
Vlad voiced them. Confirm pitch, resonance, texture, articulation, and pace in
the source audio before applying an acoustic label.

| Family | Contrasts worth capturing in Vlad's performance |
| --- | --- |
| Protagonist and viewpoint | Quiet inner narration versus his spoken dialogue; ordinary conversation, private thoughts, resolve, confusion, triumph, shame, fear, exhaustion, and changing confidence across the story. |
| Large gentle character | Weight and chest resonance with soft attacks, warmth, patience, or hesitancy; distinct from a large forceful fighter. |
| Small or fairy-like character | Light or bright resonance, fine articulation, quickness, playfulness, or delicacy; distinct from a tiny but formidable or ancient character. |
| Young characters | Child, adolescent, and young adult where the recording supports the distinction; curiosity, impulsiveness, uncertainty, bravado, and maturity within the same role. |
| Older characters | Frailty, dry texture, measured authority, humor, or energy; age should not automatically mean slow or raspy. |
| Feminine-presenting characters | Distinct individual vocal profiles across age, size, status, and temperament. Do not assign one generic high-pitched “female voice.” |
| Masculine-presenting characters | Distinct individual profiles, including light, soft, deep, rough, youthful, and elderly performances. |
| Nonhuman characters | Species or magical quality when Vlad performs one: unusual resonance, cadence, articulation, accent, or vocal texture. Keep the words intelligible. |
| Antagonists | Separate quiet calculating menace, charming manipulation, contempt, cold command, fanatic conviction, panic, and physical aggression. A villain need not be loud or low-pitched. |
| Authority figures | Formal official speech, commander, teacher, diplomat, investigator, ruler; distinguish institutional manner from the individual's character sound. |
| Minor and anonymous roles | Preserve a recognizable local contrast without inventing a stable identity or falsely reusing a major character's voice. |
| Group, crowd, or ambiguous speech | Keep collective, overlapping, offstage, and uncertain identities distinct from a named individual. Do not manufacture a solo voice when the recording contains several voices or an unclear source. |
| Changed identity or body | Disguise, impersonation, possession, magical transformation, injury, aging, or a revealed true identity may change a role's sound. Keep the narrative identity and audible phase separately. |

For recurring characters, store a base profile plus verified scene variants,
not one reference clip that must cover every emotion. Link aliases and
continuity across books; do not merge two roles merely because their acoustic
features are similar.

Describe a verified character sound through **relative pitch range, resonance
and perceived weight, brightness, roughness or breathiness, articulation,
accent if actually performed, habitual rhythm, and stability across scenes**.
Record “no distinct character sound heard” when Vlad reads a role near his
normal narration voice. That is useful evidence, not a failed annotation.

## Momentary delivery labels

| Dimension | Examples to distinguish |
| --- | --- |
| Energy and intensity | Restful, conversational, animated, urgent, projected, or shouted. Track energy, acoustic loudness, and speaking rate independently. |
| Emotional intent | Warmth, reassurance, affection, delight, pride, triumph, awe, curiosity, uncertainty, suspicion, grief, restrained anger, explosive anger, contempt, fear, panic, relief, embarrassment, shame, bitterness, sarcasm, deception, and stunned suspension. |
| Physical or mental state | Sleepy, intoxicated, injured, in pain, out of breath, crying, laughing, disoriented, or trying to suppress emotion. The text may suggest one; the audio must confirm its audible effect. |
| Vocal mechanics and texture | Soft or hard onset, breath-released ending, whisper, breathy speech, tremor, sob, smile or laugh in the voice, grit, creak, nasality, fatigue, strain, and audible breath. Mark only what is audible, not a diagnosis of the performer. |
| Social intent | Confiding, persuading, teasing, flirting, commanding, pleading, threatening, warning others of danger, comforting, reporting, bargaining, lying, questioning, answering, thinking aloud, or talking to oneself. |
| Pace and emphasis | Local acceleration, deliberate slowing, held word, stress on a name or contrast, clipped speech, hesitation, self-correction, rhetorical buildup, and a change in pace inside a sentence. |
| Ending contour | Firm stop, gentle release, final-word taper, breath-released ellipsis, suspended ellipsis, shocked cutoff, unfinished thought, question rise, and sentence-final fall. Store the **word delivery** separately from the silence after it. |
| Pause and breath placement | Comma, clause, sentence, paragraph, scene, or heading boundary; a mid-thought pause; inhalation before a line; release after a word. Record placement and approximate duration separately from emotion. |

The Book 1 ellipsis comparison already shows why a single `trailing_ellipsis`
tag is insufficient: Vlad sometimes tapers the final word and sometimes keeps
it strong. Pause duration also varies. A fixed appended pause cannot teach
the model the release of the word itself.

The Book 1 listener identified a slightly softer historical-prose narrator
register in the nearby narration comparison for atlas case
`clip_48f3df62ddcad7af7655` (comparison
`sentence_6322621933caffba8adb`, recording `rb1-26`, 908.96–917.52 s).
Represent it as `narrator_historical_prose`, distinct from
`narrator_baseline`. This first example is a provisional anchor; apply the
register to additional passages only when listening evidence supports it.
`soft` remains an independent momentary delivery cue.

Reality Benders also uses a recognizably different narrator register in the
opening section titled **Вступление**. Represent that repeatable sound as
`narrator_opening` with the UI label **Opening**. Apply it to narrator prose in
the opening when listening confirms the register. For the reviewed Reality
Benders opening, the listener chose **Opening + Normal** as the section-wide
default, including its dialogue; a later audible exception can still be moved
to another family or mood. A structural opening title creates a review
proposal, not automatic training approval.

## Text and scene situations

- **Narration and viewpoint:** calm baseline, action, suspense, exposition,
  reflection, description, recollection, flashback, dream, inner monologue,
  unreliable or ironic narration, and a shift of point of view. Mark the
  protagonist's spoken line separately from surrounding first-person prose.
- **Dialogue:** speaker changes, attribution after a quote, direct address,
  interruptions, overlap, muttering, shouting, whispering, dialogue nested
  inside a quotation, imitation of another voice, and a character changing
  emotion within one scene. Keep the speech attribution's narrator words
  distinct even when audio must be generated sentence by sentence. A single
  grammatical sentence can contain both voices; preserve the subspan labels
  and flag it when the renderer cannot switch performance without an audible
  seam. Do not erase the distinction merely to fit the chunker.
- **Russian em-dash transitions:** treat every em dash in proofread source prose
  as structural. Check for a speech start, speech-to-author transition, inserted
  author remark followed by resumed speech, or an explanatory transition that
  remains narrator prose. Each side can require a different voice or mood even
  inside one grammatical sentence. Keep these as high-priority training examples
  after word-aligned splitting and independent audio-edge verification. Run this
  speech/narrator structure check as a separate pass before speaker identity,
  reusable voice family and mood assignment; later passes must preserve it.
- **Structure:** cover, credits, dedication, epigraph, prologue, chapter or
  part title, scene break, short fragment, paragraph turn, letter or document,
  and the extra space around headings. Identify what the audiobook actually
  reads versus what appears only on the EPUB page.
- **Punctuation and rhetoric:** comma phrasing, colons, dashes, questions,
  exclamations, enumerations that accelerate as a block, hesitation, trailing
  ellipses, interrupted words, and ellipses inside a continuing sentence.
  Include rhetorical repetition, contrast, gradual escalation, counting,
  technical terms, names, and numbers. Punctuation proposes a cue; it does
  not prove the emotion or the stress.
- **Presented speech:** radio, intercom, public address, phone, prerecorded
  announcements, telepathy, game UI/stats, letters, notes, and on-screen text;
  also distant or muffled speech, a helmet or mask, and a voice heard from
  another room when the scene and recording support that presentation.
  First decide whether the words were actually spoken in the audiobook. Keep
  channel processing and background effects distinct from the underlying
  vocal performance.
- **Nonverbal events:** breath, chuckle, laughter, gasp, sob, sigh, and other
  audible sounds. Do not insert these merely because the prose mentions them.
- **Stylized language:** chants, songs, poems, incantations, formal addresses,
  foreign-language phrases, invented words, and deliberately unusual accents
  or pronunciation. Preserve actual wording; do not fabricate an accent from
  a character's origin.
- **Long-form continuity:** a recurring character's recognizable sound across
  chapters and books, deliberate changes over an arc, return after a long
  absence, and smooth transitions between narration and dialogue.

## Evidence rule: a proposed case is not a confirmed performance

| Claim | Evidence needed before training use |
| --- | --- |
| Who speaks | Exact EPUB speech span plus surrounding attribution and stable cast ID, or `unknown` when the source does not identify the speaker. Acoustic similarity alone cannot identify fictional characters performed by one narrator. |
| What was spoken | Independent ASR or transcription compared with the EPUB; confirm additions, omissions, pronunciation, and exact clip boundaries by listening. The model must not train on mismatched text. |
| Momentary delivery | A time-coded original recording clip in which the claimed feature is audible. One clear example may establish that this performance happened *there*, but not that it is habitual for the role. |
| Stable character sound | Prefer several clean examples from at least two scenes, including a calmer line and an expressive line when available. Compare with Vlad's narration baseline and with other roles. A lone line remains provisional. |
| Voice changing over time | Examples on both sides of the change, with story evidence for whether the role, body, disguise, injury, or emotional state changed. |
| Channel effect | EPUB scene evidence plus original audio review for whether Vlad or the production actually used radio filtering, distance, reverberation, etc. A mention of a radio is not evidence that the line was heard through one. |
| Acoustic absence | Explicitly record `no_distinct_change_heard` if Vlad does not audibly differentiate a role or cue. Do not train an invented performance. |

Every accepted acoustic example needs book and recording IDs, exact EPUB
passage and character offsets, spoken transcript, recording start/end times,
source and clip hashes, reviewer and review date, feature-specific observations,
and a status. A reviewer should compare the clip with adjacent context to
avoid mistaking microphone gain, music, compression, or a cut edge for an
acting choice. Quantitative pitch, energy, tempo, and breath measures can
prioritize review, but they do not by themselves establish emotion or intent.

Use these statuses: `text_candidate` (EPUB hypothesis), `audio_located`
(approximate excerpt), `audio_confirmed` (the specific feature was heard),
`no_distinct_change_heard`, `uncertain`, and `rejected`. Only confirmed and
clean clips may enter a labelled training set; rejected or uncertain examples
remain available for audit. Keep a positive exemplar and a contrasting or
negative exemplar for each important label. For example, compare a soft
ellipsis with a strong ellipsis, and a gentle giant with another low voice.

One concrete **review candidate** already exists: Book 1 `rb1-00`, passage
`c0004-p00010-0-292`, ending in “устройство…”. The aligned final word is
approximately 312.32–312.88 seconds in the original recording. The user
heard a calmer, breath-released ending than the Higgs draft, and the measured
final-word level falls about 6.6 dB across the source word. This supports a
specific listening hypothesis, not a universal rule for every ellipsis or a
training acceptance. The [local A/B review](../training-work/higgs-tts-3-pilot/ellipsis-pilot/review/index.html)
also includes a contrasting “живого…” ending. Its original final word does
not show the same loudness fall. Both need a formal feature-specific review
decision before becoming training exemplars.

## Proposed training record

A future accepted record needs separate fields for `narrator_id`, `role_id`,
`character_profile_id`, momentary `delivery` and `vocal_texture`,
`ending_contour`, `pace`, `intensity`, `channel`, exact spoken text, EPUB
evidence IDs, recording time span and hashes, and the feature-specific audio
review decision. A single `audio_reviewed: true` flag is insufficient to
prove every label attached to a clip. These are metadata concepts, not an
assertion that the current Higgs or Qwen trainer consumes them. A usable
training run needs an explicit, evaluated way to condition generation on the
labels. Store `unknown` whenever identity or performance evidence is weak.

For a new EPUB, no original Vlad recording exists to confirm its particular
scene. The analyzer must infer a delivery from text and context, express
uncertainty, and select a performance learned from **previously confirmed**
source examples. Text inference is evaluated against held-out books with
audio, rather than relabelled as audio truth.

## Corpus and evaluation priorities

1. Build clean, sentence-complete aligned clips with no chopped words, music,
   credits, or mistaken speaker. Retain original pronunciation and pauses.
2. Review a balanced sample of character families and delivery combinations.
   Ordinary calm narration must remain well represented; rare shouting should
   not dominate because it is easy to detect. Include examples where an
   expected theatrical distinction is *not* audible.
3. Collect **same-character contrasts** (one role, several emotions) and
   **same-delivery contrasts** (different roles, e.g. gentle giant and gentle
   child). These test whether identity and performance are independently
   controllable.
4. Reserve complete books for held-out evaluation. Test voice likeness,
   character consistency, exact words, stress, local tempo, ending contours,
   breathing, and transition seams with blind listening. Measure both correct
   use of a cue and the model's ability to leave an unsupported cue out.
5. Expand to more books only when those held-out tests improve. Preserve
   recording and mastering provenance so the model does not learn noise or
   compression as a character trait.

Current Book 1 text-side speaker/boundary decisions are a starting point.
They are not audio-confirmed acting labels or accepted training clips. The
remaining available books need the same book-by-book pass before their
annotations can be treated as ground truth.

The [Book 1 voice evidence atlas](../training-work/character-rb1/voice-evidence-atlas/index.html)
collects 107 original-audio candidates for 47 roles and selected delivery or
channel cases, each tied to recording offsets, hashes, exact source text, and
a nearby narration baseline when available. It opens on a 17-case first pass;
the full set remains available. Eighteen roles have only one
candidate reference, and only thirteen have candidate references in two or
more chapters. Ustinov still lacks a candidate role reference. The atlas is
an auditable review queue, not 107 confirmed
voice distinctions. Its exported decisions are validated by
`scripts/import_book1_voice_evidence_review.py`; even a reviewed distinction
does not automatically pass the separate transcript, speaker, boundary, and
clean-speech training gates.

## Analysis and review provenance

Production narration remains sentence sized to avoid audible mid-sentence
seams. Exact internal speech, narrator-attribution, voice, and delivery ranges
are retained as `performance.analysis_parts`, and the dedicated Codex
speech-structure pass is retained in the generation analysis record. Corpus
builders must use those ranges instead of inferring structure again from the
single voice selected for synthesis.

Preselected bucket and split labels are model proposals. On the low-click
Book 2 board, a complete playback with unchanged labels records passive
`playback_acceptance` evidence tied to the exact WAV hash, playback mode/rate,
and tab visibility. It does not write label verdicts or satisfy the bucket
training gate. Saving a split alone does not turn prefilled labels into human
corrections; only dropdowns the listener changed receive listener-override
provenance. Split audio still requires its independent boundary and transcript
gates before training.
