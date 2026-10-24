# Project working instructions

## Production speech engine decision: fine-tuned Higgs

- Production checkpoint decision, 2026-10-21: the only checkpoint allowed for
  new production synthesis, previews, evaluations, recovery and retakes is
  `custom-voice-plus-stress-scale-v1`, deployed as
  `training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-q8_0.gguf`
  with SHA-256
  `78f09d20145ab79b8a6d425bc68fe104e3c624a7aea687bb0ff31c3a186374d1`.
  Its fine-tuning adapter SHA-256 is
  `a5e454feecbb350e190e6b3c6b559b6a9cffaf606914d3589f7cc030da75aa90`.
  Verify the deployed file before synthesis and fail closed on a missing file,
  missing provenance, checksum mismatch or attempted fallback. Earlier Higgs
  checkpoints may remain only as immutable historical evidence; never select
  them for new audio. See `docs/CORE_NARRATION_SPEC.md` for the full promotion
  and provenance contract.
- The production audiobook speech engine is a custom fine-tuned Higgs model.
  This is a settled product decision; do not reopen Qwen versus Higgs unless the
  user explicitly asks to reconsider it. Existing Qwen speech and training code
  is legacy evaluation/fallback infrastructure, not the target production path.
- The speech worker must use a backend interface with Higgs as the production
  backend. A Higgs voice pack carries the fine-tuned model, reviewed reference
  voices and the human-reviewed voice-family + mood performance palette.
  Every input that can change generated sound must be included in the immutable
  synthesis/cache key.
- The protected human labeling corpus supplies both Higgs fine-tuning examples
  and reviewed performances for each voice family and mood. Assignment selects
  these performances; it must not manufacture missing delivery with effects.
- Product decision, 2026-10-14: Higgs emotion, prosody, pitch, speed and vocal
  effect tags are out of scope for the foreseeable implementation. Keep them
  disabled, including evaluation chapter renders. Do not schedule them as a
  later milestone or reopen them without an explicit user request. Prioritize
  correct character/alias, voice + mood palette assignment and faithful audio.
- Playback is progressive. Publish the first 5–10 minutes as soon as available,
  then extend chapter assets every additional 5–10 minutes without requiring
  all chapters to finish. Preserve the listener's position and playback state,
  use immutable chunk/version URLs, and disclose pending evaluation checks.
  See docs/CORE_NARRATION_SPEC.md for the acceptance and buffering contract.
- Current implementation order is:
  1. integrate custom fine-tuned Higgs into production generation;
  2. let playback pass held passages, reset playback offsets after regenerated
     audio, and recompute performance plans after corrections;
  3. add the spoken-text layer for numbers, symbols, placeholders, bracketed
     material and pronunciation;
  4. reproduce and validate the reviewed voice + mood palette without Higgs effects;
  5. add chapter-ordered rendering with seek priority, then word-level reader
     synchronization;
  6. complete descriptor-based casting, provider-login preflight and clear
     disclosure when book text is sent to an online analysis provider.

## Production one-shot policy (user decision, 2026-10-23)

- Production automatic post-processing (recognition, adjudication and retake
  loops) is optional and disabled by default. Generating or regenerating a book
  does not opt in. Preserve an explicitly selected mode through resume and show
  disabled checks as not checked, never passed or zero confirmed errors.
- Production mode must never change application code, preprocessing rules,
  prompts, model weights or shared defaults in response to errors. Opt-in
  recovery uses released behavior for the current generation only; it does not
  authorize application patches or self-improvement in production mode.
- The feedback loop from post-processing errors to preprocessing improvements
  runs only in development mode. Preserve all attempts, raw evidence and
  conclusions locally; explicitly import production evidence for development
  review when needed. Do not automatically transfer data or promote rules.
  Validate reusable improvements against first-take results and unseen cases
  with retakes off, then deliver them in a normal versioned release.
- Prefer the best possible first render through preparation. Failed repairs
  are evidence, not approved preprocessing rules. Keep source text and human
  labels protected. See `docs/CORE_NARRATION_SPEC.md` for the full contract.
- This decision supersedes older unconditional automatic audit/recovery
  instructions below. Their strict recognition gates apply only to explicitly
  enabled post-processing or development/evaluation runs. Basic model pinning,
  output integrity and honest failure reporting remain required in every mode.

## Autonomous generation ownership

- Within the selected processing mode, a request to generate or regenerate
  narration authorizes the necessary
  diagnosis, repairs, validation, restart/resume and progressive publication
  within the requested scope. Own that work through completion; the user must
  not have to ask repeatedly whether the job stopped or tell you to resume it.
- Launching a background process is not completion. Monitor its liveness,
  progress and exit status. If it stalls or fails, investigate and repair the
  cause, then resume from verified checkpoints automatically. Before reporting
  "running", verify a live worker and recent progress; report a stopped job as
  stopped. Provide progress updates without requiring the user to poll.
- In an enabled post-processing or development/evaluation run, recognition
  disagreements and quality flags are work to resolve, not merely
  counts to hand back to the listener. Inspect the affected source, context,
  audio evidence and take history; distinguish recognizer errors from synthesis
  errors. Use bounded retakes, source-preserving spoken-text repairs and
  independent verification. Do not repeat the same failed strategy indefinitely.
- For enabled post-processing and development/evaluation runs, the user
  requirement of 2026-10-14 applies: requested chapter regeneration is not complete
  while any recognition disagreement remains. Independently verify or repair
  every flagged take, including substitutions and extra words, not just omissions.
  Keep unresolved sections held and preserve their evidence. Never hide the
  counter or classify an unresolved mismatch as a harmless preview warning to
  claim zero. A source-bound, independently demonstrated spoken equivalence
  may resolve an orthographic recognizer difference, with the raw transcript and
  the exact rule retained. The final requested chapters must have zero open
  recognition flags and include the requested music.
- User decision, 2026-10-16: proper nouns are not spelling-checked against
  recognizer output, because a recognizer never sees the text and pulls invented
  names toward real words. Small pronunciation slips in names are acceptable;
  wrong stress is not. A proper noun is a word capitalized mid-sentence, or a
  capitalized sentence-initial non-dictionary word (hyphenated names count as a
  whole). Guards: the heard slot must be non-empty and within one syllable, and
  every other word must still match. Each use is recorded as
  `tolerated_deviations` (`proper-noun-slot-v1`, `runtime/rimsky/name_tolerance.py`)
  with the instruction; never present it as an exact recognition The same
  disclosure covers a line made only of interjections heard as interjections of
  the same kind (угу as ммм, хо-хо as ха-ха; `interjection-kind-v1`).
  Extended 2026-10-16 (iteration evidence): invented lowercase non-dictionary
  words (игв, кроколиск) get the proper-noun slot guards; a leading hesitation
  of the same kind (Хм-м-м heard Мм) is tolerated when the rest matches exactly
  (not угу/ага); hisses (ФС-С-С heard С С С or FSSS) are an interjection kind.
- Escalate persistent or contextual failures to the available independent CLI
  provider without waiting for the user to request help. User decision,
  2026-10-16: this text-only proposal work uses a small fast model, GPT-6 Luna
  (`gpt-6-luna`, Codex CLI, low effort), falling back to Claude Haiku 5.5
  (`claude-haiku-5-5`); on the same recorded request they took 10.8 s and
  26.1 s against about 2 minutes for Opus 5.5 at high effort. Every proposal
  still has to pass the audio checks. Verify availability, pin the exact model
  and record the model actually used. Preserve CLI isolation and
  immutable request/answer provenance; disclose any provider fallback. A
  text-only model's opinion cannot establish what the audio actually says.
- Recover transient provider failures with bounded retries and the permitted
  provider fallback, recording each attempt. Preserve valid prior work and
  regenerate only affected units. Keep independent work progressing when one
  section is held; never omit a sentence to make a chapter appear complete.
- Do not weaken quality gates, hide unresolved flags, fabricate listening
  approval, alter protected labels or introduce disabled Higgs effects to get
  a render through. Hold the failing section with its evidence and continue
  other eligible work. Ask the user only for a genuinely missing decision or
  authorization, after exhausting the remedies already authorized.
- Verify the published audio and listening page as part of delivery. Report
  what is actually playable, what remains held and what is still running.
  Never call the generation complete because a process was started or a repair
  passed its unit tests. Build reliable supervision into the pipeline so this
  behavior persists beyond the current chat session.

## Preparation layers (user findings, 2026-10-17)

- User decision, 2026-10-24: YO ambiguity and dictionary-context stress preprocessing
  first request one `gpt-6-luna` low opinion with self-rated integer confidence 0–100.
  Keep an unchanged bound preparation proposal after that single valid opinion.
  EVERY proposed change requires a second independent opinion, even at confidence
  100; missing/unmarked/unknown proposals also require it. Confidence never decides
  routing. Stress uses the accurately labeled Silero+HS/YO/guard selected snapshot;
  YO uses the categorical Silero е/ё witness. Pass 2 sees only the changed subset,
  the same baseline/witnesses and source, never pass 1's choice/reason/score.
  Counterbalance item/candidate order and check the actual inflected form. The
  SECOND valid opinion is authoritative when requested; preserve/report both and
  any disagreement without an Opus/third vote. Malformed/missing required evidence
  holds preparation. Exact candidate enums, isolated audited single-execution CLI
  calls and bounded transport retries remain required. Old policy artifacts stay
  immutable; use new cache/output namespaces.

- User decision, 2026-10-24: dictionary homographs receive contextual stress
  decisions from GPT-6 Luna (`gpt-6-luna`, Codex CLI, low effort), including
  words left unmarked by taggers and cases where taggers agree. Bind the exact
  source occurrence and selected е/ё spelling. Contextually selected stress
  must survive the adjacent-stress guard; preserve prior automatic evidence.
  Detect candidates from the pinned homograph lexicon, never book-specific
  phrases. This contextual authority supersedes older automatic homograph
  choices while preserving explicit source stress and reviewed proper names.
  Emit a private ambiguity report before rendering: every detected stress
  alternative, exact source occurrence, Luna choice and reason. Keep rejected
  responses visible in their evidence; a text decision is not audio approval.
- User decision, 2026-10-24: every ambiguous е/ё occurrence is decided during
  preprocessing by GPT-6 Luna (`gpt-6-luna`, Codex CLI, low effort), including
  occurrences where Silero and RUAccent agree. Their agreement is evidence,
  not an automatic verdict. Keep dictionary-certain spellings deterministic,
  bind decisions to exact source occurrences, and preserve frozen generations.
  This changes ё selection only; homograph stress keeps its separate policy.
- User pronunciation correction, 2026-10-24: final synthesis input uses the
  deterministic whole-word spellings `кого` → `каво` and `никого` → `никаво`,
  including recognized pronoun-particle forms. Preserve stress, case and source
  text, and record the source-bound phonetic substitutions after ё/stress
  preparation. Never replace a substring inside `коготь`, `коготки` or other
  words, or generalize this to arbitrary `-ого` endings.
- Stress: silero homograph marks are cross-checked with RUAccent; disagreements are
  adjudicated once per book by Claude Opus 5.5 at high effort (measured best on 75
  hand-labelled items; Luna and Haiku misplace stress too often, so the Luna decision
  for recovery proposals does not apply to stress). Proper nouns always keep their
  base-form stress in every inflected form, including families first seen
  sentence-initially or only in oblique forms.
- Higgs sometimes ignores a correct stress mark. The acoustic stress witness
  (`rimsky.stress_witness`, generation flag `--stress-witness`) only triggers retakes;
  it never holds a section and never certifies stress.
- Typesetting slips that change the reading (a dash inside one name, a split word)
  are found by `rimsky.reading_anomalies`: two independent models must propose the
  identical letter-preserving repair. Never hard-code book-specific patterns.
- Scene/time-skip markers and chapter titles get structural pauses with a music swell
  (`narration_transitions` v2). Candidates come from markup; two models must agree
  a line is a transition, so in-world interface lines keep normal pacing.

## Human labeling: preserve and consult first

- User decision, 2026-10-23: the entire `manual-labeling/` directory is
  protected local-only data. Keep it on this machine and in private backups;
  never publish datasets, human answers, source text or derived corpora in
  public Git or source releases. Labeling UI and workflow scripts remain
  public. See `docs/LABELING.md`; ignoring or untracking data never authorizes
  deleting the local files or rewriting Git history.

- Cleanup candidates and the canonical retained working set are documented in
  `docs/WORKSPACE_CLEANUP.md`. Never delete `training-work/` by age or name;
  recheck active processes, listener-report reachability and protected archive
  hashes immediately before a scoped cleanup.

- The user's labeling work is preserved in `manual-labeling/`. Read
  `manual-labeling/README.md` before corpus labeling or label-model training work.
- Latest Book 1 voice-family/mood choices and free-text notes:
  `manual-labeling/reality-benders-1/review.json`. Its checksum and save metadata
  are in `latest.json`. Earlier exports under `history/` do not override it.
- The full support snapshot includes resolved labels, candidate/source metadata,
  exact reviewed WAV copies, voice-evidence notes and narration feedback. Compare
  `archive-manifest.json`'s review hash with `latest.json` before using derived
  `labels.jsonl`; refresh the full archive after any additional manual labeling.
- Refresh using `scripts/manual_labeling_archive.py` in the existing TTS Python
  environment. Never replace human choices with automatic predictions, infer
  per-clip listening flags, or clear boundary/quality holds during archiving.
- Before cleaning, rebuilding, moving or removing relevant `training-work` data,
  verify the protected archive and its referenced media. This folder is valuable
  user data, not a cache. Review history snapshots are intentionally disabled;
  preserve the canonical `review.json` and its matching checksum.
- The review server mirrors durable saves to this folder. The working review UI
  still reads `training-work/character-rb1/fine-tune-all/review-draft.json`.
  The current server process predates this hook; a backup watcher mirrors changes
  every two seconds until the next normal restart. Restarting that server during
  setup was rejected by automatic approval review; do not claim it was restarted.
- Future books use `manual-labeling/<book-id>/`; keep experiments, predictions and
  trained weights separate from the human labeling archive.
- Source-audio labels describe Vlad's actual performance. Text context suggests
  intended delivery but must not override an audio-based human decision.

## Book 2 bucket pilot

- Open Book 2 directly in the bucket player at
  `http://127.0.0.1:1438/character-rb2/fine-tune-all/`.
- Mixed voice/mood splits are nested under the original candidate's
  `split_review.parts` in the canonical manual review, with original labels
  preserved. See `docs/BOOK2_BUCKET_PILOT.md`. Never train the mixed parent
  while its split is enabled or approve new child cuts from bucket labels alone.
  Their word-aligned audio edges need independent checking. The immutable
  overlay is protected in `manual-labeling/reality-benders-2/metadata/split-plan.json`.
- Human answers are in `manual-labeling/reality-benders-2/review.json`; its
  checksum is in `latest.json`, and proposals are separate under `metadata/`.
  The working UI lives in `training-work/character-rb2/fine-tune-all/`.
- Explicit listener corrections to model-attributed speakers are saved in the
  canonical review as `speaker_verdict` plus `corrected_character_id`. The full
  board's reviewed seeds are also recorded in
  `manual-labeling/reality-benders-2-full/metadata/speaker-overrides.json`.
  Apply review corrections before voice-family prefilling and keep the frozen
  context output unchanged so attribution errors remain measurable.
- The initial pilot uses selected independently transcribed sentence clips from
  Chapters 1 and 2, not complete chapter coverage. `pilot_asr_pass` does not mean
  human or training approval. Keep all mismatch and boundary holds.
- Voice suggestions come from the trained Book 1 predictor. Mood suggestions
  are weak; `mood_pending` and `voice_pending` are review states, never trainable
  labels. Do not convert model proposals into saved human verdicts.
- `narrator_opening` (UI label **Opening**) is a separate narrator register for
  structurally named Reality Benders introductions/prologues. Use Opening as the
  full-section voice proposal, including dialogue. The listener reset already
  saved opening assignments to Normal; unreviewed clips retain their audio/context
  mood suggestions. Preserve audible per-clip exceptions and treat structural
  detection only as a proposal.
- The Book 2 EPUB was replaced with the full edition on 2026-09-29. Its source
  hash is `4ed8157413044ddc27e1c5a31d4f89b2cda617fde43755e5ec56e4811b133785`;
  it contains the introduction and Chapters 1 through 29. Full-book derived
  work lives in `training-work/character-rb2/full/`, and its separate bucket
  board/archive use `character-rb2-full` / `manual-labeling/reality-benders-2-full`.
  Do not replace or merge the original pilot manifest. Transfer a pilot choice
  only when recording hash, exact cut WAV hash, and exact source text all match.
- The full-edition board is ready at
  `http://127.0.0.1:1439/character-rb2-full/fine-tune-all/`. Its frozen manifest
  contains 2,392 review clips: 1,197 exact ASR matches and 1,195 approximate
  matches visibly held for audio-edge review; another 424 weak matches stay out
  of the board. Seven exact pilot decisions were migrated and three remain only
  in the original pilot. Verify board, media, archive, context coverage and
  checksums with `scripts/verify_book2_full_board.py` before derived training use.
- The full-edition board's **Mixed mode** is a temporary listening aid. From the
  current cursor it alternates up to four protected, precision-passed Book 1
  examples with Book 2 clips in the same voice-family + mood bucket. Book 1
  cards are read-only, never create Book 2 playback acceptance, and disappear
  when Mixed mode is turned off. Rebuild the representative set with
  `scripts/build_book1_mixed_comparisons.py`; install the UI through
  `scripts/install_book1_mixed_mode.py`.
- Full Book 2 voice prefills use `scripts/build_series_voice_prefill.py`. Run it
  with `--apply` after rebuilding the board; `run_book2_full_finalize.py` does this
  automatically. The policy uses structural narrator/Opening roles, stable Book 1
  character priors with at least 3 clips, 2 chapters and 80% purity, and the
  validated mechanical-stats audio family. It abstains on other audio-only family
  guesses. Book 1 chapter-separated measurements were 356/382 for the character
  prior and 68/78 for prior plus audio fallback versus 54/78 for audio alone.
  The current overlay assigns 54 Opening, 1,310 narrator, 580 recurring-character,
  64 context-confirmed game UI and 15 additional mechanical clips; 369 remain
  `voice_pending`. Game UI takes priority over the broad narrator role. Eighty-four
  strong character-prior/audio disagreements are warning-only and sort first;
  this includes 28 Комар checks and the telepathic/written-text register question.
  After at least 75% of the exact clip is actually played with the shown voice
  unchanged, its matching `playback_acceptance` hides that voice warning and
  removes its priority on later loads. This is passive listening evidence only;
  it is not a voice verdict or training approval. A voice or audio-hash change
  invalidates the match and makes the warning visible again. `mood_pending` is
  permitted in this passive record so unresolved mood work does not force the
  listener to repeat a completed voice check.
  It changes proposals only,
  never human decisions, mood labels, boundary holds or playback evidence. The
  report is `training-work/character-rb2-full/series-voice-prefill-v2/report.json`.
- Emotion2vec+ large was measured on the same frozen 1,271 Book 1 WAVs. Paired
  reserved-chapter mood results are in
  `training-work/character-rb1/emotion2vec-large-comparison/paired-comparison.json`.
  It did not improve mood classification: base 29/73, large 26/73. Model size
  alone does not justify turning on automatic mood labels.

## Character-analysis provider policy

- For new production generations, automatic character analysis resolves and
  pins one provider in this order: authenticated Codex CLI, Claude Code CLI,
  then the local model. Users may explicitly choose the local model when they
  want the offline, no-subscription path.
- CLI analysis calls must remain noninteractive, schema-bounded and isolated
  from tools, MCP servers, plugins, repository rules and session persistence.
- The local speaker fallback checks each model decision twice. Accept the
  identity as a suggestion only when both passes agree; disagreement is a review
  hold. All model-resolved local speakers remain review items because measured
  agreement precision is only 72.60%. Never let pass two blindly replace pass one.
- Progressive full-book analysis is chapter ordered. The minimum three passes are
  separate structure, primary-analysis and critic jobs. Chapters farther ahead of
  playback may receive continuity, alternate-provider and adjudication passes, up
  to six total. Do not count identical prompt retries as independent evidence, do
  not let the final pass silently replace earlier proposals, and keep local-model
  consensus in review until a frozen evaluation supports automatic acceptance.
- A rolling scheduler may render a chapter after its minimum gate and used voices
  are frozen while it analyses later chapters. A seek reprioritizes the requested
  chapter. Late alias or speaker changes must invalidate only affected audio; they
  must never rewrite protected human labels.
- The final frozen report is
  `training-work/speaker-resolution-eval-qwen35-v1/report.json`; the provider
  decision is beside it in `decision.json`. On the untouched test, Codex measured
  896/912 total spans and Claude 892/912, so production auto selection follows
  the untouched result even though Claude led on the smaller selection split.
  The local two-pass agreement gate accepted 781/908 modeled test spans at
  72.60% precision and held 127 for review. It is a cost-saving fallback, not a
  substitute for either CLI.

## Russian em-dash transitions

- In this proofread fiction corpus, every em dash is a deliberate structural
  signal. GPT/context analysis must inspect both sides and classify whether it
  starts direct speech, changes from speech to an author remark or narrator
  action, resumes speech after an insertion, or introduces an explanation.
  Never discard an internal em dash as ordinary punctuation, and never assume
  that every dash changes speaker.
- Run the focused speech-structure pass before the general voice/mood pass. Its
  exact chunks and speech/narrator roles are fixed inputs to the later pass;
  speaker identity and mood may be resolved later but cannot overwrite the
  structural decision. Preserve the focused result in analysis checkpoints.
- Internal em-dash transitions are high-priority training examples because they
  contain difficult changes of voice, tone, pace and narration. Preserve them in
  future books and place word-aligned split proposals in the bucket review.
- A mixed parent clip is never trainable. Its child parts become training
  candidates after voice and mood labeling, independent word/audio edge checks,
  and exact-text verification. Do not lose the example merely because the parent
  contains mixed delivery; retain it in the boundary-verification queue.
- Production validation keeps sentence-sized synthesis units, but it must retain
  every internal model part in `segment.performance.analysis_parts`; Codex desktop
  jobs also retain the dedicated first-pass result in
  `generation.analysis.speech_structure`. Do not rebuild corpus structure from the
  consolidated speaker alone.
- Book 2 uses a low-click review workflow. Playing at least 75% of the exact
  hashed WAV with unchanged labels records a separate `playback_acceptance`
  object, including playback mode/rate and tab visibility. It is passive review
  evidence, not `voice_verdict`, `mood_verdict`, `bucket_review`, or training
  approval. Promote it only through a later explicit corpus-gate step. Merely
  opening a page or saving a split is not label confirmation. Untouched split
  dropdowns remain `model_prefill`; only changed fields are listener overrides.
- Book 2 browser smoke tests must run against a temporary copied/hard-linked
  workspace and random-port server. `verification/check_book2_full_ui.mjs`
  provides this setup and also verifies the real review file remains byte-for-byte
  unchanged. It copies `review-draft.json` to a distinct file and hard-links only
  replaceable board assets and large audio. Never point a mutation-capable browser
  test at ports 1438 or 1439.
- `mood_pending` is review state, never a trainable mood. Passive playback may
  store it inside `playback_acceptance` as the label that was visible, but corpus
  export must resolve it to no mood and still require an explicit human mood
  choice. Use `scripts/review_label_resolution.py` in label exporters; do not read
  a training mood from `playback_acceptance`.

## Optional generated music (YuE2)

- Branded opening decision, 2026-10-22: every new automatically generated
  audiobook begins with the approved eight-second Dogma opening, using the
  reviewed Soft narrator performance and Mist music. The branded opening
  always includes its music, even when the book's background music is off.
  This is an explicit exception to the scene-music toggle rules below.
  Russian uses «Озвучено в Dogma. Приятного прослушивания.»; English uses
  “Narrated with Dogma.” Require a matching-language opening for other or
  unknown languages; do not fall back to English or silently omit it.
  Keep the opening separate from source passages and reader alignment,
  pin its immutable assets per generation, and include it once at the start
  of playback and exports that include the beginning of that generation.

- Music is an overlay: scene beds that follow each chapter's mood, crossfading
  between scenes, and ending music that plays out after the final words. It
  never changes speech audio, segment timing, captions or chapter markers.
- House sound (user decision, 2026-10-14): a quiet Gothic-1-remake-like
  underscore, ambient medieval folk with lute, wooden flute and soft hand
  percussion, plus procedural ambience (campfire, rain, wind, cave) as filler.
  Piano is not in the vocabulary. A CLAP zero-shot check moved the strongest
  label from "solo piano" (old calm style) to lute + flute folk (0.57-0.58).
- Each chapter has 1-3 scenes. A bed plays short verified phrases separated by
  quiet gaps that the scene's ambience fills, and scenes crossfade over 6 s.
  Do not over-render or change music every minute: a new scene needs a lasting
  turn at least 6 estimated minutes after the previous one (and 3 before the
  chapter end), and neighbours with the same bed family and ambience merge.
  Twenty moods map to six bed families; each family has 3 variant takes shared
  by the whole book (rendered first-variants-first), so a book needs at most
  ~18 YuE2 takes plus the ending, about 15-20 s each on CUDA.
- `runtime/rimsky/music.py` runs YuE2 through audio.cpp only while the `music`
  setting is on and no narration job is active (6 GB GPU). Before rendering, a
  context-direction pass asks the generation's pinned analysis provider for a
  book palette (0-2 genres, 1-3 colour instruments on top of the house sound)
  and, per chapter, scenes (start passage, mood, ambience) from evenly spaced
  passage samples with estimated minutes. The provider only picks from closed
  vocabularies (no voice-like instruments), so book text never reaches the YuE2
  prompt. Answers are cached by exact request in `music_direction`; failures
  fall back to one delivery-mood scene per chapter and retry after 6 h or on
  music retry. A manual `analysis.music_direction` mood (`{"chapter:c0001":
  {"mood": "tense"}}`) still wins for that chapter. Every YuE2 take must pass
  format, level, dead-air and an independent Qwen3-ASR no-vocals check (up to
  three takes); ambience is synthesized, seamless and peak-limited. Every sound
  input is in the render key. Players and exports use the same layout
  (`bed_pattern`/`scene_layers`; `src/lib/music.ts`), and exports pre-mix one
  music track.
- YuE2 is a song model: with empty lyrics it plays an instrumental intro, then
  a singer usually enters. Measured: 75 s takes 0/3 clean, 22 s takes 7/8
  clean. Keep takes at or below ~24 s; beds are 20 s phrases. The pack pins reduced audio.cpp memory
  arenas (CPU commit ~16 GB instead of ~27 GB, identical output), and the ASR
  verifier is unloaded before each generation because both do not fit in 16 GB.
- Players and exports respect the toggle: off means no playback, no generation
  and speech-only exports. The pack is built by `scripts/build_music_pack.py`
  into `resources/music/` (git-ignored, hard links). YuE2 is CC BY-NC 4.0;
  this project is non-commercial, and the attribution is shown in the player.

