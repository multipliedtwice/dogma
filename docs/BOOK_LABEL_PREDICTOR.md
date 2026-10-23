# Learned Book 1 labeling assistant

The user declared the grouped Book 1 labels complete. The training run freezes
the latest saved review and retains all boundary and audio exclusions. It records
this overall signoff separately; it does not fabricate per-clip listening flags.
The protected source is `manual-labeling/reality-benders-1/review.json`. Training
checks it matches the active working copy before proceeding. See
[`manual-labeling/README.md`](../manual-labeling/README.md) for archive refresh and
the preserved source clips and metadata.

`scripts/train_book1_label_predictor.py` trains two classifier heads on frozen
audio features: reusable voice family and mood. Voice candidates include waveform
measurements and the cached Qwen speaker encoder. Mood candidates use waveform
measurements and emotion2vec+ features. The audio encoders are not updated.

Three chapter-separated development folds choose the model configuration. The two
previously designated evaluation chapters are excluded from model selection and
threshold fitting. These chapters appeared in an earlier exploratory benchmark,
so this is not a new external test. Features are fitted and scaled only on the
training portion of each fold. After evaluation, inference models are fitted on
all eligible examples of supported classes. No Book 2 transfer has been tested.

Each output folder contains fitted `voice_family.joblib` and `mood.joblib`,
the exact review snapshot and taxonomy, training feature matrices and manifest,
evaluation predictions, and a report. Classes with fewer than ten development
examples or examples in fewer than three development chapters are excluded from
learned output classes. Rare classes and unfamiliar voices need reference-based
review; classifier scores cannot establish that a new voice is familiar.

The exploratory voice score threshold requires 90% agreement, at least 30
predictions, and a Wilson lower bound of at least 80% in development predictions.
It is evaluated separately on reserved chapters. Scores are not calibrated
probabilities. A threshold never approves labels or training data. The current
mood model does not meet the threshold; its predictions are comparisons only.

## Train

```powershell
training-work/.venv-tts/Scripts/python.exe -X utf8 -u scripts/train_book1_label_predictor.py
```

The output directory is named with the saved review hash and existing runs are
not overwritten. Feature caches must already exist for every exact WAV. The live
review, source audio, existing emotion benchmark, and player settings remain intact.

## Predict

```powershell
training-work/.venv-tts/Scripts/python.exe -X utf8 scripts/predict_book_labels.py --model-directory MODEL_FOLDER --features FEATURES.npz --manifest CLIPS.jsonl --output SUGGESTIONS.jsonl
```

The NPZ contains `ids`, `audio_sha256`, and `wave`, `timbre`, `emotion` matrices.
Waveform columns are the trained artifact's `acoustic_keys`, followed by twelve
relative pitch contour bins. Frozen encoders must match those in the artifact.
The manifest binds every ID to `exact_audio_sha256` (or `audio_sha256`). Hash
mismatches, duplicate IDs, invalid vectors and incompatible dimensions are errors.
The output cannot overwrite an existing file. Load only this workflow's trusted
local joblib models.

## Book 2 gate

Extract aligned source-audio features and contextual speaker proposals for a small
Book 2 pilot. Use the voice predictor for proposed families, retain top alternatives
and representative source references, and keep uncertain or unfamiliar characters
visible. Mood proposals must be explicitly presented as weak suggestions until a
better predictor passes independent evaluation. Measure actual correction rates
and review a random sample of high-score cases before expanding to the whole book.

## Series character voice prior

Book 2 voice prefills now use the protected Book 1 human labels before the audio
classifier. A recurring character is reused only when Book 1 contains at least
three reviewed clips in at least two chapters and at least 80% use the same voice
family. Narrator and Opening use structural context. The only audio-only family
accepted without a character prior is mechanical stats; unsupported audio guesses
are shown as **Needs voice review**.

This policy produced the following chapter-separated Book 1 measurements:

- Character voice from other chapters: 356/382 correct (93.2%).
- Stable-character subset in the reserved chapters: 36/36 correct.
- Stable character first, then audio fallback: 68/78 correct (87.2%).
- Audio-only baseline on the same reserved clips: 54/78 correct (69.2%).

These measurements support voice-family prefilling, not automatic acceptance.
They do not improve mood prediction. The current mood model remains a comparison
hint and human listening remains the source of truth.

Rebuild and install the current full Book 2 overlay with:

```powershell
training-work/.venv-tts/Scripts/python.exe -X utf8 scripts/build_series_voice_prefill.py --apply
```

The report and exact policy inputs are written under
`training-work/character-rb2-full/series-voice-prefill-v2/`.

For Book 2, `presentation_channel_proposed=game_ui` takes priority over the broad
narration role. This fixes game readouts that the text pass correctly identified
as UI but whose grammatical role remained narration. A stable recurring-character
prior still wins for ordinary dialogue. When it strongly disagrees with the audio
model, or when telepathy/written text sounds mechanical, the board shows a voice
check and sorts it first. This warning asks for listening; it does not change a
human decision or automatically switch the clip to the audio model's family.
Once at least 75% of the exact audio has played with that voice unchanged, its
passive `playback_acceptance` hides the warning and removes the clip from the
priority group. This remains listening evidence rather than a voice verdict or
training approval. It is tied to the audio hash and voice family, so either kind
of change makes the warning return. An unresolved mood can remain `mood_pending`.
`mood_pending` is preserved only as passive evidence of what was displayed. The
shared export resolver converts it to no mood and requires a real reviewed mood
before a row can enter training.

The full-board browser smoke test runs on a random-port temporary workspace made
from hard links to replaceable board assets and audio. `review-draft.json` is a
real copy with a different file identity, so an in-place test writer cannot touch
the live review. Its POST endpoint and archive are inside that temporary workspace,
and the test also checks that the real protected review did not change.
