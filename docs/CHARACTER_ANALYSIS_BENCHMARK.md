# Reality Benders 1 character analysis benchmark

## CLM-8B preflight (2026-09-26)

The released [Contrastive-LM/CLM](https://github.com/Contrastive-LM/CLM) is a closed-choice state/action scorer, so its `Choice` interface could be compared on the existing 38-item first-speaker fixture. That would measure only attribution among supplied chapter candidates, not character discovery, dialogue boundaries, or voice delivery. Its released projection head requires the matching Qwen3-8B last-token encoder. The official serving instructions use Linux, NVIDIA GPU, and vLLM pooling. This Windows laptop has 6 GB VRAM; neither the encoder, CLM package, vLLM, nor a local CLM server is present. The preflight in `training-work/character-rb1/benchmark/clm_preflight.json` therefore records **no score**. Substituting the installed Qwen3.5-4B model would not evaluate the released CLM checkpoint.

Measured on 2026-09-26 with locally installed Ollama models. Full prompts,
item-level responses, timings, and the reviewed 38-item fixture are in
`training-work/character-rb1/benchmark/` (local workspace data).

## First-turn speaker choice

For each of 38 selected early-book dialogue paragraphs, the model received the
target, up to two nearby paragraphs on either side, and a reviewed list of
chapter speakers plus `unknown`. The task was to identify only the first speech
turn. The cast was supplied, so this does not measure discovery or stable
identity across a book.

| Ollama model | Correct | Sum of per-item wall time | New voices marked unknown |
| --- | ---: | ---: | ---: |
| Qwen 3.5 4B Q4_K_M | 30/38 | 41.4 s | 0/3 |
| Ministral 3 8B Instruct 2512 Q4_K_M | 29/38 | 126.5 s | 2/3 |
| Bonsai 8B (`digitsflow/bonsai-8b`) | 25/38 | 15.7 s | 0/3 |

An attempted Bonsai 27B community Ollama pull did not finish, so the measured
Bonsai result applies only to the 8B build. DeepSeek results remain in the
local benchmark report; neither tested DeepSeek variant beat Qwen on the
diagnostic fixture.

## Staged analysis pilot

The all-in-one app prompt failed validation on 3/3 tested source fragments with
Qwen, even after response-aware retry. The new experimental
`runtime/rimsky/staged_analysis.py` uses two smaller decisions: code establishes
exact candidate ranges, a model labels speech versus narration, and another
model call resolves each speech speaker. The app's validator checks complete
source coverage. Each stage may retry one structurally invalid response with
the rejected JSON and validator error.

| Ollama model | Exact speech/narration boundaries | Valid analysis objects |
| --- | ---: | ---: |
| Qwen 3.5 4B | 3/3 | 3/3 |
| Ministral 3 8B | 3/3 | 3/3 |
| Bonsai 8B | 0/3 | 3/3 |

Qwen left the two speaking roles unresolved in this pilot. Ministral created
consistent protagonist and investigator roles, although those labels still
need linking to canonical characters and review. Bonsai's valid JSON contained
incorrect speech boundaries. Schema validity must be scored separately from
semantic correctness.

These passages cover only the first conversation of one chapter. The staged
path remains diagnostic; it is not the default app analyzer. Delivery is set
to neutral until it has its own audio-grounded benchmark. Before assigning
character voices for a full book, review a random sample spanning the book
for dialogue boundaries, speaker identity, aliases, and performance style.

Run `training-work/benchmark_character_attribution.py --model MODEL` for the
38-item test and `training-work/benchmark_staged_analysis.py --model MODEL` for
the staged pilot. The latter records both validator and boundary correctness.
The app's `llama_cpp` backend has not been tested with the staged path; these
experiments used Ollama.

## Book-wide holdout (2026-09-26)

Before querying either model, `training-work/sample_character_holdout.py`
selected 18 paragraphs from chapters c0009–c0040 with a fixed seed and a
manifest hash: six simple dialogue paragraphs, six with multiple em dashes,
and six narration paragraphs. The three categories come from distinct
chapters in each of six sequential chapter bins. The sample excludes the
early chapters used to develop the analyzer. EPUB context was reviewed and
saved in `holdout_v1_gold.json` before inference. The runner reads only the
unlabelled fixture. Each item starts with an empty character registry, so
speaker scores test isolated cold-start discovery rather than a mature
book-wide cast.

| Staged model | Validator and coverage | Exact speech boundaries | Correct identity on 12 dialogue items | Both boundaries and identity |
| --- | ---: | ---: | ---: | ---: |
| Qwen 3.5 4B | 18/18 | 15/18 | 1/12 | 1/12 |
| Ministral 3 8B | 18/18 | 10/18 | 7/12 | 5/12 |

Identity was manually adjudicated against the sealed text labels. Established
aliases and distinct recurring roles count; generic species, `unknown`, and
an unlinked “first-person narrator” do not. Qwen recognized the protagonist
role in one additional item without linking him to Кирилл. Ministral recognized
two additional roles without canonical links. Ministral also assigned speech
to four of six plain narration passages; Qwen did so for two. Both models
missed the narration tag in `c0013-p00028`. These are sample counts, not
whole-book accuracy estimates, and the sample was intentionally balanced
across three passage types rather than drawn in proportion to their frequency.

The review exposed two incorrect **high-confidence** entries in the existing
partial dialogue labels: `c0015-p00008` is Коста Дыхш, not Иван Лозовский;
`c0022-p00067` is Иван Лозовский, not Кирилл Комаров. Both partial label files
were corrected. Existing derived character-group views need regeneration
after the larger label review; this small audit does not certify the rest of
the labels.

Three audiobook excerpts were located using the existing ASR word timings and
saved in `training-work/character-rb1/benchmark/audio-review/`. Their short
speech-only clips had median measured pitch of 153 Hz (Кирилл's question),
167 Hz (Тюленев's exclamation), and 129 Hz (Радугин's demand). These figures
describe different excerpts; they do not identify an emotion or establish
that the generated voice can reproduce the performances. The local review
page `audio-review/index.html` lets a listener record perceived pace, pauses,
and delivery. Audio-grounded delivery labels remain pending listening review.

## Persistent staged path and fresh holdouts

The staged path can now be selected with `text_model.analysis_mode: "staged"`
in a model pack. Validated character IDs are passed to later analysis batches
and committed with the app's existing cast record. The speaker stage asks for a
source passage ID and a contiguous quote; code checks the quote and rejects a
proposed speaker when an opening address names that same character. These are
structural safeguards, not proof that the quoted words establish the identity.
The `llama_cpp` pack backend has a focused mock integration test, but no real
pack has been run end to end with this path.

The first fresh 18-item holdout (`v2`) was sealed before adapting the evidence
quote check or adding a dialogue-format rule. All 18 analyses validated. Scores
below use the same cold-start protocol as `v1`, with an empty cast for each item.

| v2 staged model | Exact boundaries | Correct identities / 12 | Both / 12 |
| --- | ---: | ---: | ---: |
| Qwen 3.5 4B | 14/18 | 0/12 | 0/12 |
| Ministral 3 8B | 12/18 | 8/12 | 7/12 |

In this book, all 692 extracted dialogue targets start with an em dash. An
opt-in `text_model.dialogue_style: "russian_dash"` now classifies a fresh
paragraph that does not start with an em dash as narration; fragments that
continue a split source still use context. This rule was developed after the
`v2` results and must not be evaluated as if `v2` were unseen.

A third disjoint 18-item holdout (`v3`) was drawn and manually labeled before
the revised analyzer ran. It covered the same three passage categories across
chapters c0009–c0040. All analyses validated and preserved exact source text.

| v3 model, Russian em-dash style | Exact boundaries | Correct identities / 12 | Both / 12 |
| --- | ---: | ---: | ---: |
| Qwen 3.5 4B | 15/18 | 0/12 | 0/12 |
| Ministral 3 8B | 17/18 | 4/12 | 4/12 |

For Ministral, the six plain narration passages and six simple speech
passages all had correct boundaries. One mixed paragraph lost its narration
tag. Identity remains the limiting factor: it returned generic roles such as
“дипломат” for Иван Лозовский, a first-person “narrator” for Кирилл, and in
one case named Устинов because he was mentioned in Радугин's spoken line.
Qwen left every v3 speech identity `unknown`. The cold-start setup does not
measure a mature persistent cast, but the app must first build and reconcile
that cast correctly before it can use it. Generic or unresolved identities
need human review before character voices are frozen.

The next experiment supplies a reviewed, evidence-backed starter character map
to the speaker stage. Expressive delivery remains neutral and awaits the
listening review of the saved audiobook excerpts.

## Reviewed starter cast and speaker-state test

`training-work/character-rb1/benchmark/curated_cast_v2.json` is a manually
reviewed **starter** map with 18 recurring characters and 32 distinct canonical
names or aliases. It is not the complete book cast. Every entry cites EPUB
passages, and `training-work/validate_curated_cast.py` checks that every alias
appears in the cited text, IDs do not collide, and no cited passage is a
holdout target. This caught an unsupported “Улина” alias copied from a partial
model label; the earlier `curated_cast_v1.json` and its preliminary results are
retained for audit. The corrected map keeps similarly named people separate,
including Баша/Ваша Тушихх and Радугин/Устинов. The staged speaker request now
receives each cast entry's short role description along with its name and aliases.

A paired 18-item `v4` comparison used the same passages with an empty cast and
with the corrected map. The map was compiled **after** inspecting the v4 gold,
so v4 measures the effect of supplied state on those passages, not blind
generalization. The map's citations exclude the v4 target paragraphs, but
that does not remove the selection bias. All four runs validated and covered
the source text exactly.

| v4 model | Empty cast: identities / 12 | Corrected cast: identities / 12 | Exact boundaries / 18 |
| --- | ---: | ---: | ---: |
| Qwen 3.5 4B | 0 | 6 | 15 |
| Ministral 3 8B | 7 | 11 | 14 |

The corrected map was then frozen at SHA-256
`d516fa08e21df911a41b4fb460738b5275e5c8a7c031c9e36919c773b1ebae37`
before drawing and labeling a disjoint `v5` sample. That sample intentionally
contains eight speakers in the starter map, three new speakers, and one
unidentified voice. It also has six plain narration passages. These are
balanced diagnostic counts, not the proportions in the full book.

| Blind v5 model with frozen map | Valid | Exact boundaries | Correct known / 8 | Correct new / 3 | Correct unknown / 1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.5 4B | 18/18 | 15/18 | 5/8 | 0/3 | 1/1 |
| Ministral 3 8B | 18/18 | 13/18 | 8/8 | 1/3 | 0/1 |

Ministral discovered Маша, but assigned an unnamed scout to Иван Лозовский,
Игорь Тарасов to Кирилл, and the unidentified exclamation to Кирилл. Qwen
marked all three new speakers `unknown`, which flags them for review but does
not create their identities; it also missed three known links and assigned one
first-person line to Тюленев. Both models kept all six plain narration
passages as narration. Most boundary misses were mixed speech and narration.

The map materially improves known-character attribution, while new entrants,
unresolved speech, and mixed paragraphs still require review. A quote copied
from the target speech does not by itself prove who spoke it; the speaker stage
needs stronger local narrative evidence and a review gate before voice casting.
The map is manually reviewed, so these scores do not measure automatic
character-map construction from an uploaded EPUB.

## Explicit lead-ins and book 1 review queue

The staged analyzer now recognizes only narrow colon-ended lead-ins: an
explicit first-person speech verb with the reviewed protagonist, a reviewed
name serving as subject of a speech verb, or an explicit role serving as
subject when that role is absent from the starter cast. The rule applies only
to the first speech turn of a paragraph. A newly identified role restricts
the model to `new` or `unknown`; an invalid existing-character answer gets
one repair attempt, then falls back to `unknown`. This is a conservative
identity check, not a general pronoun or character resolver.

On the already seen v5 diagnostic, the unnamed First Legion scout changed
from the false identity Ivan Lozovsky to `unknown`. This prevents that false
voice merge but still needs review to create the scout's identity. The v5
Ministral run remained 18/18 valid with 13/18 exact boundaries. This v5
result is a development check, not a fresh accuracy estimate.

The disjoint v6 fixture and gold were sealed before either model run. It has
six simple speech, six mixed speech/narration, and six plain narration
passages, sampled across the same chapter range. The cast remained frozen
at the hash above. All outputs passed the app's structural validator.

| Blind v6 model with lead-in rule | Valid | Exact boundaries | Correct identities / 12 | Identity and boundaries / 12 |
| --- | ---: | ---: | ---: | ---: |
| Qwen 3.5 4B | 18/18 | 15/18 | 7/12 | 5/12 |
| Ministral 3 8B | 18/18 | 14/18 | 11/12 | 7/12 |

The rule fired on just one v6 passage, the explicit "Basha added:" lead-in.
Ministral assigned Basha correctly both with and without the rule; the
focused baseline took 17.91 seconds, versus 2.08 seconds with the rule.
This sample demonstrates a speed gain on that case, not an identity accuracy
gain. The sole Ministral identity miss was an elderly mage outside the
starter cast, left `unknown`. Most boundary misses were mixed paragraphs.
Qwen still confused known speakers in five of the twelve speech passages.
These balanced diagnostics are too small to estimate full-book error rates.

`training-work/build_character_review_queue.py` compiles all 692 book 1
dialogue candidates, the 460 partial labels, the frozen starter cast, and
observed v5/v6 benchmark errors into `character-review-queue.html` and JSON.
The current queue has 17 observed-error passages, 166 labels outside the
starter cast, 226 unlabeled passages, and 283 exact cast-name matches. **None
of those labels is a reviewed final speaker assignment.** The HTML page
offers context, a decision control, notes, and a JSON export. It stores edits
in the browser until export; there is no automatic import to the app yet.

The current generation path can render `unknown` with the narrator voice and
a warning. A high-quality final audiobook therefore requires speaker and
boundary review before rendering or voice freezing. The acoustic
intonation/character performance map remains separate and has not been
validated by these text-only benchmarks.

## First reviewed error batch

The 17 v5/v6 observed-error passages have now been checked against wider EPUB
scenes. `training-work/character-rb1/reviewed-benchmark-passages.json` records
the speaker, evidence IDs, and rationale for each. The build script validates
those source IDs, preserves exact text and the previously sealed speech
boundaries, and creates `reviewed-character-analysis.json`. Its cast map
`reviewed_cast_v3.json` has 32 entries: the 18 frozen starter entries, four
added from benchmark-error review (Masha, Igor Tarasov, the unnamed roadside
First Legion scout, and Timor-Ankhu La-Fin), and ten added during the first
chronological speaker pass through chapters c0004-c0008. Anonymous IDs are
scoped to their encounters when the book does not supply a name. This v3 map
cites benchmark passages, so it
must **not** be used to claim an unbiased v5/v6 score.

Wider context changes one corpus decision without changing the frozen test:
`c0029-p00087` was genuinely unidentified within the three-passage v5
excerpt. In the full scene, Kirill answers the question and subsequent
narration identifies Uline as the other person studying the anomaly. The
reviewed corpus assigns that question to Uline; the sealed v5 gold remains
`unknown` for its narrower context.

The first chronological pass adds 49 speaker decisions grounded in EPUB
dialogue tags and nearby scene evidence. Every em-dash chunk in those 49
passages was then classified as speech or narration in
`reviewed-early-boundaries.json`; internal em dashes inside spoken sentences
remain speech. Three group or ambiguous utterances in those opening chapters
remain pending. The page now preselects 66 passages with speaker and
boundaries reviewed. The other 626 candidates comprise 123 labels outside the 32-person cast, 226
unlabeled entries, and 277 unverified cast-name matches. The export validator
`training-work/import_character_review_export.py` checks EPUB, cast, label,
and prior-review hashes before producing a corpus overlay. New-character
choices and boundary changes are marked for further review rather than
silently applied. This overlay is not yet imported into a saved app generation;
the app's passage correction screen cannot add a new cast member.

`training-work/check_character_render_readiness.py --strict` is the offline
gate for this book 1 workflow. After the opening pass it reported 66/692
candidates with speaker and boundaries reviewed, 626 unreviewed, and exited
with code 2.
Audio performance and intonation review is separately incomplete. The gate
does not control the desktop application's current render command, so a
desktop generation should not be treated as a final reviewed performance yet.

A second chronological pass reviews all 17 remaining dialogue targets in
source sections c0010-c0012, including Kosta's first conversation with
Kirill and Tamara's first reprimand. Speaker and every em-dash chunk mode are
recorded in `reviewed-next-passages.json`. At that point the totals were **83/692
fully reviewed** and **609 pending**. Source text, evidence IDs, and exact
coverage are checked when the reviewed overlay is rebuilt.

A third pass reviews all 29 dialogue targets in source section c0013. The
decisions in `reviewed-c0013-passages.json` identify the new unnamed teenage
courier and distinguish Kirill's replies from Tyulenev's, including several
incorrect partial labels. One passage uses an ASCII hyphen before a narrator
tag (`- добавил я`). It has a manually checked exact speech/narration split;
the staged analyzer now also offers that hyphen as a candidate boundary. This
candidate still needs mode classification and does not by itself prove a
correct automatic assignment. The working cast has **33** entries. The
rebuilt queue and offline gate report **112/692 fully reviewed** and **580
pending**. Acoustic intonation and character performance review is still
pending, and the desktop render path does not yet consume this offline
reviewed overlay or enforce the gate.

The c0014 pass adds 12 speaker and boundary decisions; its thirteenth
dialogue candidate, Masha's question, was already in the reviewed benchmark
error batch. The explicit dialogue tags identify Irishka and Kirill in the
lecture hall. Ivan Lozovsky gives the lecture, while the text links the
spectacled questioner to the earlier lecture encounter. At breakfast, the
First Legion woman numbered 343 gives Kirill the patrol assignment and
continues the explanation; the partial label incorrectly attributed her
second speech to Kirill. All internal em dashes in the long lecture and
patrol explanation remain speech. The queue now displays complete EPUB
paragraphs, including the end of Ivan's 1,129-character answer, and its
export hash includes the EPUB manifest. The rebuilt gate reports **124/692
fully reviewed** and **568 pending**. Audio performance review and app
integration remain pending.

The c0015 pass reviews all 27 dialogue candidates from the preparation for
patrol. The scene grounds Kosta and Ivan's exchange, Dmitry Zheltov's pilot
conversation, Vasiliadi's equipment advice, and Kisly's orders. In
particular, the opening Gekho greeting is Kosta's rather than Kirill's;
Vasiliadi asks about armor and ammunition; and Kisly's final reaction to the
radio is not attributed to the unsupported partial label San-Sanych. A
single unnamed gate sentry is scoped to the base entry encounter. Internal
em dashes remain speech where the character continues a thought. The cast
now has **34** entries, and the offline gate reports **151/692 fully
reviewed** and **541 pending**. These are text-side speaker and boundary
decisions; audio delivery and intonation still need listening review.

The c0016 pass checks all 16 dialogue candidates in the bus journey and
border patrol scene. Fifteen have supported speaker and exact boundary
decisions. The driver San-Sanych is added to the cast, separate from Kisly;
Kisly speaks the gun and pier explanations that partial labels attributed to
Imran. The call to stop the bus at `c0016-p00017` remains deferred: four
soldiers leave together, and the text does not identify which one speaks or
whether they speak in unison. The review queue displays this ambiguity and
the offline gate counts it as a blocker. The cast has **35** entries; the
gate reports **166/692 fully reviewed**, **one deferred ambiguous turn**, and
**525 other unreviewed candidates**. Audio delivery remains unreviewed.

The c0017 pass adds 22 decisions; Kirill's short reply was already reviewed
in the benchmark-error batch. Wider context changes two misleading partial
labels: Irishka asks Kirill to wait for the shells, and Imran calls Kirill
over after Irishka finds the mineral. A voice from the pier intercom is kept
as an unidentified, encounter-scoped role: the EPUB does not establish
whether it is a person or recording. Kirill later impersonates Kosta at the
gate, so that spoken line remains Kirill's. An ASCII hyphen before
`представился я` now becomes a candidate speech/narration boundary in the
staged analyzer; the reviewed corpus records the exact split. The cast has
**36** entries. The gate reports **188/692 fully reviewed**, **one deferred
ambiguous turn**, and **503 other unreviewed candidates**. Audio delivery and
intonation remain pending listening review.

The c0018 pass reviews all 26 dialogue candidates during the pier robbery
and centaur encounter. It corrects three consequential partial labels:
Kosta asks Kirill to demonstrate the gate trick, Ivan warns the humans after
leading them aside, and Kisly delivers the final reprimand about crossing
the border. Filira's spoken offers are separated from her narrated laugh
and reactions; Ness is mentioned but has no direct speech in this section.
The cast remains at **36** entries. The gate reports **214/692 fully
reviewed**, **one deferred ambiguous turn**, and **477 other unreviewed
candidates**. Audio delivery and intonation still require listening review.

The c0019-c0021 pass adds 72 reviewed dialogue candidates: 28 in c0019,
26 in c0020, and 18 in c0021. In c0019, Kisly tells Tamara's backstory,
Masha describes her laboratory work, Denis answers Svetlana, and Svetlana
offers to trade for Kirill's pearls; several partial labels attributed her
offer to Kirill. In c0020, the returning spectacled questioner addresses
Svetlana, while Zheltov and Kirill alternate during the ride. In c0021,
Minno speaks near the lake, Kirill calls in the Dark Faction group, Rupor
responds later, and Tamara's later radio authorization is explicitly
recognized by Kirill. Roman Pavlovich is named in his dialogue tag.

Three c0021 speaker identities remain deferred despite clear speech
boundaries: an unseen man among Minno's escorts, a radio order Kirill only
tentatively attributes to Tamara through interference, and one of several
soldiers who confront Kirill. Together with the c0016 soldier turn, the
overlay now carries **four deferred identities with reviewed boundaries**.
The working cast has **39** entries. The offline gate reports **286/692
fully reviewed** and **402 other unreviewed candidates**. Audio-grounded
delivery and intonation remain to be reviewed.

The c0022 pass reviews all 22 dash dialogue candidates plus two explicitly
spoken blocks missed by the original dash-only inventory: the enemy
psionic's quoted telepathic command and an all-caps announcement by the
Dome chief. The same inventory audit recovered an earlier Dome announcement
in c0008, which is now reviewed. It also queued four later non-dash blocks
for chronological review: another loudspeaker announcement, Irishka's
quoted thoughts, and two paragraphs of Tamara's written note. The original
692 dash targets and their partial model labels remain frozen; the seven
curated supplemental targets bring the active queue to **699**. The
supplement is an explicit audit list, not a claim that every embedded quote
has been found.

In c0022, Tamara offers the infrared viewer and confronts the visiting
psionic; Kirill answers her accusation, and Ivan speaks with Kirill after
the session. Several partial labels reverse those speakers. The working cast
remains at **39**. The gate now reports **311/699 fully reviewed**, **four
deferred identities with checked boundaries**, and **384 other pending
candidates**. Text-side character rendering also requires the desktop app
to consume this overlay; audio-grounded delivery is still pending.

The c0023 pass reviews the remaining 19 dash dialogue candidates in the
chapter; c0023-p00033 already had a benchmark-error review. The recurring
bald courier wakes Kirill, Vasiliadi handles the weapon exchange, Kisly
recounts Filira's story, and Ivan reports the attack on San-Sanych's bus.
The partial label for Kisly's account incorrectly named Filira, who does
not speak in that passage. The concealed First Legion scout
Strelok-Ubivashka speaks twice, and Neyli Svistunova reacts to the unit's
punishment. Those two named characters bring the working cast to **41**.
The gate reports **330/699 fully reviewed**, **four deferred identities
with checked boundaries**, and **365 other pending candidates**. The
non-dash quote in this section is an item name and description, not a
spoken turn. Text-side review still needs the later chapters, followed by
audio listening for delivery and intonation and desktop app integration.

The c0024 pass reviews the remaining 26 dash dialogue candidates in the
convoy chapter; c0024-p00015 already had a benchmark-error review. Vadim,
the named mechanic-driver, interrupts Ivan and later answers him. The
roadside First Legion scout reports to Ivan after handing Vadim the route;
this is the existing encounter-scoped scout, not Strelok-Ubivashka from
c0023. Several partial labels reverse Ivan and Kirill: Ivan explains the
faction's shortage and suspected informant, while Kirill protests the
rumors about his actions. The working cast has **42** entries. The gate
reports **356/699 fully reviewed**, **four deferred identities with
checked boundaries**, and **339 other pending candidates**. Audio delivery
and intonation remain pending listening review.

The c0030 pass reviews all 21 dash dialogue turns during the excavation
failure and the survivors' return to the shuttle. The unnamed drilling
operator speaks in two consecutive radio turns and receives an
encounter-scoped cast entry. Uline, Basha, Vasha, Uraz, and Kirill remain
separate. The EPUB explicitly names Vasha as the brother agreeing with
Basha, although its level detail conflicts with the earlier status lines;
the speaker decision follows the direct name tag and records the conflict.
Basha, not Kirill, answers the question about the former human crewmate.
The cast has **46** entries. The gate reports **468/702 fully reviewed**,
**five deferred identities with checked boundaries**, and **229 other
pending candidates**. Audio delivery and intonation remain pending
listening review.

The c0032 pass reviews the remaining 11 dash dialogue turns after the
relic-base discovery; Uline's opening observation was previously reviewed
as a benchmark error. Leng Vaide Shishish identifies himself in a radio
order and joins the cast. Uline, not Vaide, repeats the order to leave
while the channel is monitored. She later explains how Uraz sold the
rights, and Kirill proposes that Vaide's troops triggered the explosion.
Uraz accepts that explanation. The working cast has **47** entries. The
gate reports **495/702 fully reviewed**, **six deferred identities with
checked boundaries**, and **201 other pending candidates**. Audio delivery
and intonation remain pending listening review.

The c0031 pass reviews 16 of 17 dash dialogue turns in the relic-drone
operation. Kirill's calls while operating the target designator remain
his speech; the adjacent sentence about Basha and Vasha firing describes
their action and does not assign them the call. Vasha hands over the
designator, Basha joins by radio, and Uline delivers the ceremonial
recording at the entrance. The remaining turn, a question attributed
jointly to the two giant twins, has checked speech boundaries but a
deferred individual speaker. The cast remains at **46**. The gate reports
**484/702 fully reviewed**, **six deferred identities with checked
boundaries**, and **212 other pending candidates**. Audio delivery and
intonation remain pending listening review.

The c0029 pass reviews the remaining 21 dash dialogue turns on the
asteroid; Uline's first question about the anomalous scan was already in
the benchmark-error review. Uraz gives the crew orders, Basha asks about
Kirill's pneumatic rifle, and Uline guides Kirill during the survey.
Kirill, not Uline, reassures her in the crevasse. Basha's twin asks a
further question only in indirect narration, so no separate voice turn
is added there. The working cast remains at **45**. The gate reports
**447/702 fully reviewed**, **five deferred identities with checked
boundaries**, and **250 other pending candidates**. Audio delivery and
intonation remain pending listening review.

The c0028 pass reviews all 15 dash dialogue turns aboard Uraz Tukhsh's
shuttle plus Kirill's quoted greeting embedded in narration. The partial
language fragments remain assigned to the speaker identified by the
surrounding EPUB context: Uraz gives directions and complains about
analyzer prices, Vasha makes room for Kirill in the cabin, and Uline
complains about the captain. Basha and Uline are kept separate during
their exchange about the shuttle's gravity compensator, and the later
reveal that Uline is female is preserved in her cast assignment. The
working cast remains at **45**. The active queue has **702** candidates;
the gate reports **426 fully reviewed**, **five deferred identities with
checked boundaries**, and **271 other pending candidates**. Audio delivery
and intonation remain pending listening review.

The c0027 pass reviews 28 dash dialogue turns plus Ivan's quoted reason
for visiting the gekho officials inside a narration paragraph. That
quotation adds one curated supplemental target, bringing the active queue
to **701**. A First Legion soldier's objection to Kirill remains a
deferred identity because the EPUB does not identify the individual;
its speech boundary is checked. Tarasov later acknowledges uncertainty
about his shot. Artyomov identifies himself on the radio, and the new
driver is addressed as Kostoprav after Vadim's death. Several partial
labels incorrectly assign Kirill's protest to Tarasov or Ivan's offer
about Uraz to Uraz himself. The working cast has **45** entries. The gate
reports **410/701 fully reviewed**, **five deferred identities with checked
boundaries**, and **286 other pending candidates**. Audio delivery and
intonation remain pending listening review.

The c0025 pass reviews the remaining 17 dash dialogue candidates plus
Ivan's shouted warning embedded in a narration paragraph. That quotation
adds one curated supplemental target, bringing the active queue to **700**.
The chapter alternates Ivan's explanation of the Dark Faction, Kirill's
questions, and Vadim's observations about the route and harpies. Vadim's
calm response to the mine warning is assigned to him even though the text
later establishes that an enemy psionic controls his actions at that point.
The embedded warning is split into narration, Ivan's spoken command, and
narration. The cast remains at **42**. The gate reports **374/700 fully
reviewed**, **four deferred identities with checked boundaries**, and
**322 other pending candidates**. Audio delivery and intonation remain
pending listening review.

The c0026 pass reviews seven remaining dash dialogue targets; Kirill's
warning to the harpy was already in the benchmark-error review. Minno's
plea in her own language stays assigned to Minno, even though Kirill does
not understand the words at first. The same unnamed male harpy then
bargains with Kirill in two turns; it gets an encounter-scoped cast entry
instead of a generic harpy voice. Kirill's negotiated counteroffer is
split around its narration tag. The cast has **43** entries. The gate
reports **381/700 fully reviewed**, **four deferred identities with
checked boundaries**, and **315 other pending candidates**. Audio delivery
and intonation remain pending listening review.

## Book 1 speaker and boundary corpus gate

The chronological EPUB pass now accounts for **all 708 active dialogue
candidates**, including chapters c0033-c0040, the five remaining early
turns, and six direct quotations embedded in narration paragraphs. Every
candidate has a checked speech/narration split and a reviewed
speaker decision. The working cast has **48** entries. Eighteen decisions
are deliberately `unknown`: the source attributes them to a group, an
unnamed individual, or a voice whose identity remains uncertain. In
particular, the noisy radio voice at c0021-p00026 is not asserted to be
Tamara merely because Kirill thinks it may be her. These IDs are available
for later acoustic comparison without fabricating a character identity.

The presentation overlay separately marks **70 radio turns**, **8 public
address turns**, **2 intercom turns**, **2 telepathic turns**, **2 recordings**,
and **2 written-note spans**. It marks **449 game UI
blocks** as `mechanical_game_voice`. Chapter 40's radio conversation and
Tamara's signed note are distinguished from live speech. The delivery
overlay is selective, and an audio performance review of tempo, pauses,
and character intonation has not been completed. Thus the speaker and
boundary corpus gate passes while final character rendering readiness
remains false. The desktop generator does not yet consume this offline
presentation overlay.

The second corpus audit found three EPUB website watermark blocks and
records their exact source hashes in `source-exclusions.json`. The dataset
builder rejects accepted clips sourced from this watermark. The full-book
fast ASR text pass covers all 37 recordings (11.23 hours), but these text
windows are not word-aligned or human-approved training clips. The three
existing pairing review files contain no accepted clips. The audio
training gate therefore remains open even though the EPUB speaker and
boundary gate passes.
