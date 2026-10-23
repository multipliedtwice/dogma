# Book 2 bucket pilot

The review starts directly at
http://127.0.0.1:1438/character-rb2/fine-tune-all/ .

Listen to each clip. If its proposed voice or mood is wrong, change the choice
and press **Move clip**; otherwise leave it in place. Moves save automatically.
Play all, pause, seeking, page follow and saved playback positions reuse the
Book 1 bucket player. The heading chips, Fits confirmation, comment entry and
verified filter are hidden in this pilot. Prior comments stay in the saved
review. **Details** shows surrounding text, comparisons and audio-edge flags.
Audio-edge flags hold a clip from training and keep it available in its group.

## Scope

The first pilot selects complete sentences from audiobook tracks 01 and 02:
Chapters 1 and 2. It is a sample for checking transfer of the Book 1 predictors,
not a completed corpus for either chapter. Initial ASR/alignment found 55 usable
cuts; independent recognition of the finished WAVs agreed exactly for 45.
All other sentences and failed cuts remain explicit holds. Independent ASR
agreement is useful evidence but does not guarantee perfect boundaries.

The EPUB was replaced with the full edition on 2026-09-29. The original pilot
remains frozen because the edition changed punctuation and paragraph boundaries.
Full-book work is built as a separate manifest and archive; compatible pilot
choices migrate only when the recording, exact WAV, and source text all match.

## Separate evidence

- The fitted voice predictor uses waveform measurements and the frozen Qwen
  speaker encoder, trained against the user's protected Book 1 labels.
- Voice scores above 0.68 propose a familiar family; the threshold is exploratory
  and has not been validated on Book 2. Lower scores enter Needs voice review.
  Previously unseen voices can still receive high incorrect scores.
- Tentative mood groups combine the classifier with the exact comparison
  functions used by Book 1 warnings: pitch, grouped contour cues, pace, pauses and
  volume. All proposed groups need manual confirmation. Short clips and voices
  without enough comparable audio start in Needs mood review. Model scores are
  not measured correctness probabilities.
- The production GPT context analyzer identifies speakers, channels and intended
  delivery from the surrounding text. Context stays separate from audio labels.
- Book 1 comparison WAVs come from its protected manual archive.

## Base versus large

Both frozen emotion2vec+ encoders were measured on the same 1,271 labeled WAVs.
A paired comparison used the same waveform inputs, linear classifier settings,
20 supported mood labels and two reserved chapters (73 scored clips).

| Encoder | Top 1 agreement | Top 3 agreement | Macro F1 |
|---|---:|---:|---:|
| Base | 39.7% (29/73) | 61.6% | 0.083 |
| Large | 35.6% (26/73) | 63.0% | 0.078 |

Only five clips differed in correctness: four favored base and one favored large.
This small, previously explored Book 1 split cannot establish a general ranking.
Neither result supports automatic fine-grained mood assignment. Large is pinned
to revision `6c303ba987b86b93193de93e34bb2b077a6bedc4`; its 1,024-dimensional
embeddings cannot be substituted into a classifier trained on base's 768 values.
The official model has nine broad emotion outputs, which are different from our
voice-family and delivery vocabulary:
[emotion2vec+ large model card](https://huggingface.co/emotion2vec/emotion2vec_plus_large).

The combined model/measurement shortlist uses a fixed mix selected only on three
development chapter folds. On the same 73 reserved clips, top 1 remained 39.7%,
while top 3 increased from 61.6% to 67.1% (49/73). Macro F1 increased from 0.083
to 0.087. These mood comparisons use the known human voice family in Book 1;
Book 2 uses predicted families, so this result can overstate transfer quality.
No confidence threshold met the required automatic-label agreement gate.
This supports useful comparisons, not reliable automatic mood decisions.

Artifacts: `training-work/character-rb1/emotion2vec-large-comparison/` and
`label-predictor-3a743ea3d029-large-v2/`. A prior `-large` folder contains an
incomplete feature-preparation attempt and no fitted models.

## Storage and restart

- Human answers: `manual-labeling/reality-benders-2/review.json`.
- Every distinct acknowledged version: `snapshots/`, with hash in `latest.json`.
- Frozen proposals and support metadata: `metadata/`.
- Independent exact WAV copies: `audio/`, linked by `audio-index.json`.
- Derived experiments: `training-work/character-rb2/pilot/`.
- Working bucket board: `training-work/character-rb2/fine-tune-all/`.

Start the separate server if needed:

```powershell
training-work/.venv-tts/Scripts/python.exe -X utf8 -u scripts/serve_label_pilot.py --port 1438
```

Do not overwrite a frozen candidate manifest after human review begins. Expand
the pilot through a separately versioned manifest and explicit migration of
unchanged sentence IDs/hashes. Never overwrite human choices with new predictions.

The complete-edition board is isolated at
`http://127.0.0.1:1439/character-rb2-full/fine-tune-all/`. Its working files are
under `training-work/character-rb2-full/fine-tune-all/`, while durable human
answers use `manual-labeling/reality-benders-2-full/`. The board is published
only after all audio tracks are aligned and audited, all source passages have
validated context analysis, feature extraction and Book 1 predictor transfer
finish, and the candidate manifest is frozen.

The frozen complete-edition manifest contains 2,392 clips. Independent ASR
matched 1,197 exactly; 1,195 close matches are present with an alignment warning
and remain unapproved for training. Another 424 weak matches are held outside
the board. The context pass covers all 2,835 source segments in three validated
lanes and produced 3,160 passages. Seven pilot decisions migrated because their
recording, WAV and text hashes matched; three pilot decisions did not migrate.
Run `training-work/.venv-tts/Scripts/python.exe -X utf8
scripts/verify_book2_full_board.py` to recheck hashes, audio copies, review
snapshots, candidate support files, context coverage and migration state.

### Mixed mode

Mixed mode temporarily inserts a small number of protected Book 1 references
into the selected Book 2 voice-family + mood bucket. It starts immediately after
the current playback cursor and alternates Book 1, Book 2, Book 1, Book 2. Each
bucket uses at most four representative Book 1 clips selected only from human
labels with precision-passed audio. Reference cards are playback only: they do
not expose label controls, write decisions, or count as Book 2 listening
acceptance. Turning Mixed mode off removes them from the queue.

## Mixed voices or moods

In a card, open **Different voices or moods? Split this clip**. Listen to each
part, choose its voice and mood, and press **Save split and labels**. Suggested
splits come from word alignment and the existing contextual part boundaries.
You can add or remove a split at any offered word boundary. Dialogue and narrator
words can have different labels inside one sentence. A context suggestion is
not proof of an audible change; only split when listening confirms the change.

Balanced parenthetical spans are also proposed as separate delivery parts,
including nested parentheses kept inside their outer aside. General asides do
not automatically change speaker or voice family; choose the audible register
and mood by ear. The spoken placeholders `(непонятно)` and `(нецензурно)` receive
a narrator-family proposal. Parentheses stay with their part, and boundaries
without reliable local word timings go to the forced-alignment queue. These
remain listening proposals, with independent audio-edge checks still required.

After saving, each part appears in its own voice + mood bucket, with its own
player, move and edge controls. The whole clip is removed from the
active bucket list. Mood displays **Split mix** on split cards; click it to open
the editor and see or change each part's saved voice and mood. Split mix is a
display label, never a training mood. Cards omit split badges and summaries.
**Change split, voices, or moods** also lets you return to
the whole clip. Its original labels and comments are preserved. Draft split
choices survive refresh in the same tab until explicitly saved or discarded.

Persistence uses the immutable parent ID: `decision.split_review` contains
`enabled`, source hashes, cut offsets, and `parts[part_id]` with independent
voice/mood decisions and PCM frame ranges. Child IDs are `parent~start-end`.
This uses the existing conflict-aware save and manual archive; never flatten
children into the frozen candidate manifest or overwrite the parent answer.
`metadata/split-plan.json` and the original protected WAV allow reconstruction.

New child audio is a sample-exact slice of the original PCM, played at 1x.
Word-alignment cuts are **not checked training cuts**. All split audio stays
held for independent text/edge checks, including after bucket consistency
confirmation. Existing beginning/end flags carry into the first/last part.
Whole-clip acoustic scores are not reused as measurements of child parts.
This annotation does not change sentence-sized production synthesis chunks.

Maintained code: `prepare_bucket_split_plan.py`, `book_bucket_splits.js`,
`book_bucket_split_controls.js`, and `install_bucket_split_ui.py`. The pilot
builder installs these without changing candidate IDs or saved human answers.

Bucket playback can stream a source-bound AAC/fMP4 HLS audition copy through
the local review server. Native Apple HLS and the pinned local hls.js client
share the same immutable fragments. These copies preserve the source sample
rate and channels; source/child WAV hashes, encoder recipe and AAC timing
offsets are retained in the derivative manifest. Original WAVs remain intact.
Streamed playback records separate `listening_evidence`, including its lossy
transport and derivative provenance. It does not create exact-WAV
`playback_acceptance`, `bucket_review` or training approval. Use Original WAV
for source-edge checks and the existing exact-audio listening workflow.
Install only the UI adapter with `scripts/install_bucket_hls_ui.py --board
<board-path>`; never rebuild frozen candidates to update playback.

### Dash-boundary recovery

The full-edition split planner does not require an exact match for the entire
clip at a dialogue or attribution dash. It first requires the words adjacent to
the cut and up to three lexical anchors on both sides, allowing only controlled
Russian vowel spelling folds. Short dialogue spans extend anchors past a
neighbouring dash and require a clear pause when only one anchor is available.
Grammatical dashes are recorded and do not create automatic delivery splits.
Their aligned cut points remain available in the split editor as manual options
when listening reveals a mood change.

Clips that still lack safe local anchors are aligned directly from the frozen
EPUB text with Qwen3 ForcedAligner. Every proposed cut records
`proposal_method`; approximate parent clips keep their audio-alignment hold.
Run `scripts/evaluate_bucket_split_boundaries.py` to report proposal coverage
and unchanged-cut acceptance separately for local anchors and forced alignment.
