# Core narration and progressive playback

Product decisions confirmed by the user on 2026-10-14.

## One-shot production and development feedback

User decision, 2026-10-23: production targets the best possible first render
through preparation. Automated post-render recognition, adjudication and retake
loops are optional and **disabled by default**. A normal request to generate or
regenerate narration does not enable them. Record an explicit opt-in and its
bounded recovery policy in the generation configuration; resuming a job preserves
that choice and must not silently enable additional processing.

With post-processing off, render each prepared unit once. Do not start ASR,
recognizer reconciliation or automatic retakes that revise pronunciation,
seeds or references. Pre-render pronunciation and stress preparation remain on.
Keep basic output integrity checks (model/provenance pin, successful synthesis,
decodable audio and valid format); a failed output is an error, not permission
to enter a recovery loop. Show recognition as **not checked**, never as passed
or zero confirmed errors. Preserve the source and expose generation failures
honestly. Music and progressive delivery remain independent of this option.

Production mode never changes application code, preprocessing rules, prompts,
model weights or shared defaults in response to an error. Opt-in post-processing
can check and recover the current generation using released, fixed behavior;
it cannot patch the application or teach itself new behavior. Ordinary
generation and error recovery do not authorize code changes in production mode.

The error-to-improvement feedback loop runs **only in development mode**. Retain
all post-processing attempts and outcomes locally: original/source and native
inputs, preparation version, model/reference hashes, sampling configuration,
audio hashes, raw recognizer results, quality failures, repairs, decisions and
their provenance. Production records may be imported into a development
investigation explicitly; no automatic upload, rule promotion or learning runs
in production. Keep training material and protected labels out of public exports.

In development, distinguish preparation defects, synthesis failures, recognizer
errors and technical failures before drawing conclusions. Use these cases to
improve source analysis, spoken-text conversion, pronunciation, stress and
synthesis-unit preparation before the first render. A recognizer's preferred
spelling is not a source correction, and a failed repair is not a proven rule.
Preserve unsuccessful strategies and actual listening evidence without inventing
approval. Turn supported conclusions into reusable regression cases, then
measure first-take correctness and rendering cost on both the retained cases and
unseen material with automatic retakes disabled. Ship validated improvements
through a normal versioned release; freeze their inputs and policy in synthesis
and cache keys. Never silently rewrite an existing generation.

This decision supersedes earlier unconditional automatic-verification and
recovery wording. The strict recognition and retake contracts below apply only
when post-processing is explicitly enabled, or to an explicitly selected
development/evaluation run. They are not the production default.

This section specifies required behavior. The documentation change does not
implement a production toggle or certify that the existing runtime already
enforces these defaults; implementation must be audited separately in development.

## Stress preparation authority

User decision, 2026-10-25: restore Silero homograph stress, cross-check with
RUAccent, and resolve disagreements with pinned Claude Opus 5.5 at high effort.
The blanket Luna dictionary-context stress overlay is disabled for new
preparation. Luna still decides ambiguous е/ё; its conditional second check
does not grant authority over homograph stress. In the public-demo path, the
existing finite-verb Opus stress pass must preserve Luna's fixed е/ё spelling.
Explicit source marks and durable listener corrections outrank automatic
analysis. Apply the current human correction snapshot last, before phonetics.

Preserve historical Luna decisions, raw requests and audio. A restoration uses
new immutable inputs and exact retained pre-context text, verifies that replay
reproduces the old contextual result, then applies current human corrections.
Do not infer a baseline from word-only matches or overwrite a historical plan.
Regenerate only units whose effective inputs change, plus explicitly reported
audio requiring a fresh take. This does not enable post-processing or establish
that the regenerated audio follows its stress marks.

## Pinned production speech checkpoint

User decision, 2026-10-21: every new production render, chapter preview,
evaluation, recovery and listener-requested retake must use the latest
fine-tuned Higgs checkpoint below, and no earlier voice-only, pilot-stress,
stock Higgs or other speech checkpoint:

- checkpoint: `custom-voice-plus-stress-scale-v1`;
- native merged model:
  `training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-native`;
- native merge's fine-tuning adapter SHA-256:
  `a5e454feecbb350e190e6b3c6b559b6a9cffaf606914d3589f7cc030da75aa90`;
- deployed Q8_0 model:
  `training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-q8_0.gguf`;
- deployed model SHA-256:
  `78f09d20145ab79b8a6d425bc68fe104e3c624a7aea687bb0ff31c3a186374d1`;
- immutable provenance:
  `training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-q8_0.provenance.json`.

The machine-readable source of this pin is
`resources/production-higgs-model.json`; entry points must resolve it through
`runtime/rimsky/production_higgs_model.py`, not duplicate a model filename.

Resolve the configured model to a real file and verify its SHA-256 before any
synthesis. Missing provenance, a missing artifact, a checksum mismatch or a
different checkpoint is a hard failure: do not silently fall back to an older
fine-tune or stock model. Every synthesis/cache key, generation manifest,
published asset provenance and listener-retake request must record the checkpoint
name and verified deployed-model SHA-256. A future training run remains an
unapproved candidate until the user explicitly promotes its exact immutable
checkpoint and checksum by updating this section. Selecting this checkpoint does
not waive its open audio-quality holds or any normal validation gate.

## Voice and mood

The human-reviewed Vlad Rimsky recordings define the available performance
palette. Resolve character identity, aliases, presentation route and scene
meaning first. Assign a compatible palette voice and a reviewed mood in the
context of that character and conversation. Select the recorded performance
that demonstrates the choice; fine-tuning must preserve and reproduce it.

Higgs emotion, expressive, pitch, speed, whisper/shout and other vocal-effect
tags are out of scope for the foreseeable work. They are disabled for production
and chapter previews. Do not use effects to compensate for an incorrect cast,
missing reference, weak mood assignment or poor synthesis. There is no planned
effects milestone; reconsider only at the user's explicit request.

Preserve a character's identity throughout a conversation. Delivery variants
come from a compatible reviewed reference set, not arbitrary unrelated voices.
Author remarks and scene intent outweigh punctuation. Narration, Opening,
historical prose, mechanical readouts and animal sounds retain separate routes.
Retain source formatting in the presentation analysis. Resolve the complete
boundary of a displayed system card before directing its individual sentences:
the identity line, headings, values and list rows share one mechanical palette
performance until ordinary prose or dialogue resumes. Digits, emphasis or an
internal dash alone do not establish that route. Independent boundary checks
must agree or the disputed range goes to adjudication; later speaker or mood
passes cannot split a checked readout back into ordinary narration.
The user's suggestion for gentle praise to the old dog is narrator Soft.

Casting must receive descriptions of the audible palette (weight, depth,
brightness, roughness and perceived age), with the reference hashes and the
basis of each description. A family ID, the source character's name or the
meaning of its sample transcript does not establish its sound. Keep physical
character facts separate from proposed casting interpretations: a combat role
or transformation alone does not prove a particular pitch. The listener's
direction for Герман is a stronger, weightier voice like Кислый (masculine_2);
his good nature can affect delivery without replacing that vocal identity.

Allocate compatible voices jointly for the main cast and its scene interactions,
then minor roles. Different source-character templates within one family are
not evidence that the synthesized characters sound different. Shared-family
co-scene voices need measured and listening qualification; describe unresolved
distinctness as unresolved. Author actions trigger contextual reassessment of
delivery, not a one-keyword-to-one-mood rule.

A character's stable signature may be a voice family plus a reviewed mood:
for example, Normal for one character and Calm for another in the same family.
Family sharing alone is not a casting failure and does not require reassignment.
Qualify the resulting voices by listening in conversation. Preserve each signature
as its baseline, while permitting scene-supported departures; never impose an
inappropriate mood merely for contrast. Report identical family/mood signatures
separately from shared families with different moods or references.

Listener casting and delivery directions are per-book data, separate from corpus
labels. Bind them to the source hash; scope delivery exceptions to exact speaker,
paragraph, offsets and text. Casting must obey fixed family decisions and jointly
reallocate the interacting group. A source character preference is a preference,
not proof that two templates sound distinct. Pin the direction file and reviewed
family-description registry in analysis requests and generation provenance;
changes require a new assignment revision and invalidate affected synthesis keys.
Never place a book's named character directions in generic prompts. The current
Элирм directions are in `training-work/elirm-iv-higgs-mvp/listener-directions.json`.
The runtime loader is `runtime/rimsky/listener_directions.py`; the palette preview
uses it for new generations. Existing frozen previews retain their original
request bytes rather than retroactively applying new directions.

Describe the five sound dimensions once per family. Use individual-template
descriptions only where shared-family scene partners need further qualification.
Missing descriptions hold new joint casting; transcripts cannot fill the gap.
These descriptors do not require relabeling the Book 1–2 clips.

The automatic sound catalog measures exact reviewed palette WAVs and keeps its
pitch and spectral measurements separate from perceived traits. Rank register and
spectral brightness within the performed voice category; do not infer weight,
age, sex or hoarseness from pitch, level or periodicity. Explicit listener family
descriptions override automatic proposals. Unknown traits remain unknown.
Casting receives family sound descriptions and performance availability, without
sample transcripts as timbre evidence, and cites known supporting dimensions for
its selected family. Unmet vocal requirements remain disclosed and held for
production; an evaluation candidate is not qualification. Freeze catalog and
audio hashes in the assignment and synthesis key. The shared loader is
`runtime/rimsky/voice_descriptors.py`; automatic reports stay outside the protected
human registry. New palette casts resolve the pinned catalog from
`training-work/higgs-production/voice-descriptors/latest.json`.

Source-audio labels remain protected human evidence. Model assignments and new
renders are separate proposals. The specific accepted qualification take is an
MVP listening decision, not universal approval of a native control tag.

Spoken punctuation may differ from printed punctuation when a reviewed or
validated reading identifies an unwanted phrase break. Keep the canonical text
unchanged and pin the exact spoken edit and its evidence in synthesis provenance
and the cache key. Do not remove commas globally: lists, subordinate clauses,
address and meaning-changing punctuation still require their own interpretation.
The listener preferred «Сил моих больше нет это терпеть.» over the comma version
on 2026-10-14, with the same voice, reference and seed. Both takes measured a
roughly half-second internal pause, so pause duration alone cannot decide whether
the phrasing sounds connected. This is an approved exact take and a benchmark
case, not a universal punctuation or mood rule.

Resolve Russian homographs from sentence/scene meaning before synthesis, and
pin the lexical sense and desired stress in the spoken-text plan. «Кровью, потом
и немытыми задницами» requires по́том (sweat), not пото́м (later). Do not assign
that stress to every occurrence of «потом». Pronunciation spelling/markers must
be tested against the actual backend before treating them as reliable. ASR word
agreement does not establish correct stress; unresolved pronunciation remains a
separate quality issue. Keep the printed source and human corpus unchanged.

The listener confirmed the explicit combining acute accent in по́том worked on
2026-10-14. Use this as the spoken-text stress method for context-resolved words:
apply U+0301 after the stressed vowel, retain unmarked expected words for ASR,
and include the marked synthesis input in the immutable key. Bind each edit to
the exact word/offsets, lexical sense and evidence; never globally replace a
homograph without resolving its meaning. Preserve written spelling, й and ё.
This confirms the method on the current example, not a universal pronunciation
pass. The approved take retains the separate ASR «немытыми»/«не мытыми» flag.

Pronunciation planning must also consider stress across a spoken group, including
stress carried by a preposition. The reusable, listener-approved
`ru-feet-preposition-v1` spoken-layer rule applies across books: nearby unambiguous
getting/putting onto feet (встал, вскочил, поднялся, поставил) becomes «на́ ноги».
Keep printed text intact, respect explicit stress, and do not cross clause or
sentence boundaries or mark looking/clothing uses. New synthesis keys carry
the exact native text and rule receipt; old frozen takes retain their policy.
Comma removal remains a same-word listening experiment, not a global production
rewrite. Compare original and comma-free narration with the same reference,
model, sampling settings and five matched seeds before drawing a conclusion.
Late pronunciation instructions are recorded separately from the
frozen cast and human corpus, with source, unit, original take and replacement
hashes. User decision, 2026-10-24: every stress/pronunciation note belongs to the
shared local user correction profile, not to one generation. Development and
production must both save/read it automatically and replay all applicable notes
before new synthesis, including notes from superseded generations. Preserve raw
notes and their history; the latest explicit instruction wins over automatic
analysis. Freeze the applied correction evidence into synthesis/cache identity.
The default profile is app-local user data, shared by the desktop runtime and
development renderers (Windows uses
`%LOCALAPPDATA%/app.rimskifikator.desktop/pronunciation/user-corrections.sqlite3`).
`RIMSKY_CORRECTION_PROFILE` may explicitly
select another profile, including an isolated profile for tests. Keep this file
and its SQLite sidecars local and protected from workspace cache cleanup.

A proper-noun stress correction updates the entire name family throughout the
source book, including inflected forms and names not listed in the character
cast. Derive the family from the source and morphology, with ambiguity guards
against unrelated words. Automatically regenerate every affected available
passage in the current book generation and apply the correction to later
passages. Ordinary ambiguous-word corrections remain bound to their exact source
context. Preserve historical audio, all unaffected PCM, music and progressive
playback. A report from a stale generation must still update the shared rule and
reach the current generation. Show actual queued/running/applied/held status;
do not present a saved report as a completed repair.

User clarification, 2026-10-25: a proper-name choice belongs to the character's
series, across every volume and regeneration in both development and production.
The latest explicit human choice replaces earlier choices, including accidental
ones. Original notes and audio remain historical evidence. Reopening an old
volume, replaying an old queue item or importing old preparation must never make
its superseded choice authoritative again; equal timestamps follow save order.
Use an established, uniquely matching series family to resolve an ambiguous name
form in a later volume, while retaining identity and ordinary-word guards.
Regenerate every available matching mention in the current book after an explicit
name correction, including inflected forms and passages already carrying the same
input mark. The latter may still have been spoken incorrectly. Keep the real
heard-note provenance distinct from derived family propagation; do not fabricate
listening evidence for the other mentions. Future rendering in any series volume
uses the latest choice. Context-only ordinary-word notes remain occurrence-bound.

If an external renderer publishes a replacement generation, return its identity
and affected source passages in the correction outcome. The player must adopt
that replacement while the listener remains on the requesting book/version,
preserving play/pause state and the source anchor. Reset a replaced passage's
audio offset; retain an unaffected passage's offset. A later explicit book,
version or reading navigation takes priority over automatic adoption. Keep the
replacement accessible without requiring the listener to report the issue again.

Explicit user corrections work with automatic post-processing disabled. They do
not opt the book into blanket recognition or quality-retake loops. Any optional
word/audio-edge checks follow the selected processing mode; production never
changes application code, models or shared automatic rules in response to a note.
An instruction specifying stress is not listener approval of a newly rendered
take. The preview consumes `spoken-take-revisions.json` through
`scripts/revise_higgs_spoken_take.py` so later chapter extensions retain the fix.

## Recognizer orthography

This section describes the opt-in post-processing/development audit path.
It does not run during default one-shot production rendering.

A word flag means a recognizer wrote something other than the source words. It
is not, by itself, evidence that the audio is wrong: recognizers must choose
one spelling for sounds that standard Russian merges, and they cannot spell the
book's invented names. Before rendering more takes for a flagged unit, the
preview exhausts bound evidence for the takes it already has
(`render_higgs_palette_preview.verify_with_prior_takes`):

1. `audit_higgs_orthography_witness.py --mode primary` re-reads the primary
   Qwen transcript under the rules below. Its report states that it is not an
   independent recognizer.
2. `--mode glossary` re-listens with Qwen3-ASR-1.7B given one frozen, book-level
   glossary of dictionary-unknown words (names and coinages, chosen from the
   text up to the last requested chapter with pymorphy3). No sentence text,
   flag or transcript reaches the recognizer. On 300 verified takes it inserted
   no unspoken glossary term; it made 27 spelling-only disagreements, so it can
   only confirm source words, never clear a gate by itself.
3. `higgs_prior_take_sweep.py` reconsiders every earlier take of the same
   unit, voice and mood and selects the first that reproduces an exact match
   with safe edges.

`rimsky/spoken_homophones.py` accepts only spellings that speech cannot tell
apart: word spacing, geminate letters, iotation, [j] between a vowel and и/е,
unstressed akanye/ikanye vowels, unstressed ни/не (not before forms of быть or
preposition + pronoun), hard е in foreign names (Готэн/Готен), the English
homophone eye/I, and letter counts of stylized sustained sounds (Не-е-ет,
М-м-м). Consonants are compared after espeak-ng conversion, so devoicing and
assimilation must agree. Where either spelling is stressed the vowel letters
must agree, and two dictionary words must share stress positions (было/была
stay flagged). Dictionary-word pairs that differ in a vowel letter need an
entry in `REVIEWED_LEXICAL_PAIRS` after a stress review, because espeak's
stress dictionary is occasionally wrong (могла/могло).

Calibration on verified takes, 2026-10-15: 985/988 injected word substitutions
and every injected deletion stayed flagged; the three accepted are true
homophones (великий/великей, готовит/готовет, хранилище/хранилища). When the
source is one dictionary word and the recognizer wrote a non-word, only the
source stress counts, and only if espeak and silero-stress agree on it
(ольхи́/альхи accepted; коро́ва/корава and доро́га/дорага stay flagged). On 2,327 single-vowel dictionary minimal pairs
the unreviewed rule accepted 153, almost all post-tonic endings, plus about
ten stress-ambiguous forms; the reviewed-pair requirement now blocks those. An
acoustic phone-likelihood comparison was evaluated and rejected: about 13% of
injected errors scored as well as the truth.

Word-final unstressed -ие/-ия and -ище/-ища both end in [ə] (здание,
армия) and recognizers choose the likelier case in either direction
(условие→условия, задания→задание). They are accepted only between
inflections of one lemma; -ье/-ья can be stressed (статья́/статье́) and stays
flagged. Every acceptance keeps the raw transcript, the witness report hash,
each applied rule and `human_approval: false`.

Additions, 2026-10-16 (measured on 2,262 first renders of finished runs, where
727 were flagged and 437 of those proved to be checking problems rather than
audio problems):

- **Order.** `early_witnesses` runs the independent recognizers, the
  number-spelling witness and the orthography witnesses on flagged first takes
  before any retake; only units they cannot verify are rendered again.
- **Post-tonic endings.** Two dictionary words may differ only in vowels after
  the stressed syllable, within one reduction class (а/о, or е/и/я), with
  identical consonants and the same stressed syllable by silero-stress
  (уходи́ла/уходи́ло accepted; было/была, городом/городам and
  пребывает/прибывает stay flagged). Of 31,358 single-vowel minimal pairs from
  the book's vocabulary, 2,403 qualify, all of this kind.
- **Explicit synthesis stress.** For a non-dictionary word marked with U+0301 in
  the synthesis input, that mark replaces espeak's guessed stress (Затоли́се
  heard as Заталисе).
- **Numbers written as digits.** A recognizer that writes 18 loses the case
  ending; the source form (восемнадцати) is accepted only when another
  recognizer of the same audio heard it with both neighbouring words.
- **Silence padding.** `audit_higgs_padded_asr.py` hears the exact take with
  0.4 s of digital silence on each side (recognizer input only), which recovers
  clipped first phonemes of short lines (тихо heard as ихо). It can only add a
  pass: on 123 flagged short takes it matched 12 more and missed 3 that matched raw.
- **Proper nouns** (user decision): not spelling-checked, with the guards and
  disclosure described in AGENTS.md and `rimsky/name_tolerance.py`.
- **Generation length cap.** A take that never emits end-of-speech now stops at
  6 s + 0.35 s per letter instead of 82 s; none of 3,279 verified takes reached
  80% of that cap.

## Progressive delivery

End users must not wait for a complete book or chapter to begin listening.
Analyze and render in chapter order, keep a buffer ahead of playback, and
reprioritize work after seeking. For the current listening preview, publish
the first 5–10 minutes, then extend it each additional 5–10 minutes.

Publish completed audio prefixes/chunks atomically, with their actual check
status under the generation's selected processing mode. Asset URLs include
their version/hash; an existing version never changes underneath a player.
Updates preserve the current chapter, absolute playback position and paused or
playing state. Reaching the current buffer end waits for more audio, rather
than falsely reporting that the chapter or book is finished.

The page distinguishes available audio, generation progress, pending checks and
completed chapters. A later chapter finishing must not reset the active player.
Do not renormalize a published prefix when later audio arrives: freeze gains
per persistent voice/reference before the first publication.

The selected mode's gates apply to each publishable chunk; final chapter/book
reports aggregate them. Disabled recognition checks remain visibly not checked.
Known missing text, unsafe audio edges, identity continuity and unresolved
assignment must not be hidden by streaming. Evaluation
previews explicitly show historical word flags and qualification limits; unavailable
measurements are not reported as passed. Protected labels are never changed.

When post-processing is enabled, every word-audit flag is a generation-pipeline
responsibility. Substitutions,
extra words and missing words all require automatic independent verification
or source-preserving repair. Newly accepted chunks and completed requested
chapters must have zero unresolved recognition flags; displaying a warning is
not a substitute for resolving it. The raw recognizer evidence remains stored.
Verified spoken equivalences must be narrow, explicit and source-bound, never
general fuzzy matching that masks absent words or different vowels.

An unresolved word-audit flag cannot be published as an ordinary preview
warning: run bounded word recovery and hold the affected section if it remains
unresolved. An acoustically safe waveform can still omit words. Preserve both
the source text and the failed takes; bind a recovered take to its synthesis key,
word check and reported disagreement without calling it a human listening approval.
The required opt-in implementation must perform this recovery only in the
enabled mode: failed audio is retained
as unpublished evidence, repeated failures go to isolated Claude Opus 5.5 for
same-word punctuation/stress proposals, and every resulting take repeats the
audio quality gate. The successful native text, provider provenance and actual
sampling take survive resume through their real synthesis/cache keys. The
printed book, character/voice/mood assignment and human labels stay unchanged.

In the enabled mode, an unsafe generated edge triggers bounded automatic
recovery before publication. With the option off, report the failed output
without automatically rendering another take.
Try independent, pinned seeds and alternate reviewed references only within the
same frozen source-character, presentation route, voice family and assigned
mood. Do not add effect tags, fade over speech, pad a clipped waveform to hide
the failure, or omit the blocked line. Exhausted recovery leaves that section
held while the completed prefix remains playable. A restart validates frozen
requests, takes and audits, preserves published duration and asset versions,
and continues from the saved checkpoints.

Music remains a separate optional overlay. Disabling Higgs vocal effects does
not imply disabling music. Music must not cover speech or shift reader timings.

Hosted network playback uses standard AAC/fMP4 HLS derived directly from accepted
saved PCM. One chapter/run has continuous encoder timestamps and six-second HLS
fragments. hls.js handles loading, retries and bounded media buffering on Chrome;
Apple browsers use native HLS. Crossing a sentence or HLS fragment keeps the same
media source and updates reader positions. Show actual buffered media separately
from prepared narration. Unsupported browsers and bearer-auth desktop sessions
retain bounded compressed MP3 playback. Chapter music is premixed at the selected
music volume; a phone does not load a second full-chapter PCM WAV or repeatedly
seek a separate music player. Music settings, source hashes, layout and encoder
recipe are immutable cache inputs. Source audio, source offsets, quality holds
and export audio remain unchanged. The Dogma opening retains its own immutable
asset internally, but is part of the first generated chapter's playback track.

New audiobook generations include one mandatory, matching-language Dogma branded
opening, using the approved Soft narrator and Mist music. Its music is part of
the branding recording and always plays, including when optional chapter music
is off. The opening is a separate immutable audio asset, never source-book text
or a fabricated narration segment. A missing matching-language recording blocks
new generation until an approved recording is available.

The first chapter has one timeline beginning with that opening: its duration
and elapsed time include the opening, and seeking to zero plays it automatically.
Seeking within the opening or across its boundary works like any other point
in the track. Do not expose branding as an optional listening mode, a separate
intro clock or a special skip control. Resuming a source passage preserves its
source-relative position; reader alignment and source audio offsets exclude the
opening. Later chapters do not repeat it.

Exports prepend that opening once when their selection includes the generation's
first chapter. Later-chapter selections do not repeat it; a chapter ZIP adds it
only to its first generated chapter. Subtitle timings shift by the measured
opening duration, and M4B files expose a separate Dogma chapter before the first
book chapter. Book music begins after the branding. Frozen opening checksums,
format and duration are verified before publication alongside saved narration;
historical generations without a pinned opening retain their original timeline.

Explicit structural scene separators (`* * *`, `⁂`) mark audible transitions,
even when adjacent scenes share a music bed. Use a four-second speech pause at
scene breaks and six seconds between chapters. These pauses belong to the
narration timeline, so captions, seeking and music all use the resulting times.
Begin a smooth music bridge six seconds before the closing speech ends; keep
its increase modest under words (up to 1.2 times the quiet bed), crest only in
the pause (up to twice the bed), then ease back before the next words. Reuse
verified instrumental takes, preserve the frozen speech gains, and apply the
same envelope in the player and export. Music-off keeps the structural pauses.
Never add a chapter transition at an incomplete streaming buffer edge, or
rewrite an existing audio URL when publishing these changes.
