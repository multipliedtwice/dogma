# Voice-family-conditioned mood recognition

Status: evaluated 2026-10-18; emotion2vec fine-tune **not promoted**. A
separate Books 1+2 continual-learning candidate is frozen for Book 3.

## Product interpretation

Mood labels describe a performance inside a reviewed voice family's palette.
They are not assumed to be acoustically universal. For example, `calm` for
`narrator_baseline` can occupy a different pitch, pace and timbre region from
`calm` for `masculine_2`.

Production should keep the two concepts separate:

1. identify or assign the voice family using the speaker/timbre path;
2. rank moods relative to reviewed performances from that same family;
3. abstain when the family or its relevant palette cells lack support.

The palette is evidence, not a hard allow-list. A mood absent from training for
one family may still occur later and must remain reviewable.

## Frozen evaluation

The experiment used 2,024 exact, human-labelled, boundary-passed clips:
1,271 from Book 1 and 753 from Book 2. The existing frozen chapter split was
preserved: 1,199 training clips, 309 inner-validation clips and 384 untouched
outer-test clips. Book 2 correction examples had 0.5 training weight. No review,
audio, corpus gate or production setting was changed.

There are 19 reviewed voice families, 45 mood labels and 223 observed
voice-family/mood pairs. Only 34 development pairs have at least ten clips, so a
flat classifier over all pairs is not supportable.

Exact Book 2 comparison on the same 161 outer-test clips:

| Candidate | Top-1 | Top-3 | Macro F1 | Balanced accuracy |
| --- | ---: | ---: | ---: | ---: |
| Existing Book 1 label predictor | 31.7% | 59.0% | 0.142 | 0.159 |
| Frozen emotion2vec, universal fixed statistics | 28.0% | 56.5% | 0.067 | 0.086 |
| Frozen emotion2vec, voice-relative neutral reference | 28.6% | 57.1% | 0.067 | 0.080 |
| Frozen emotion2vec, learned voice-conditioned head | 22.4% | 44.7% | 0.105 | 0.115 |
| Last two emotion2vec blocks fine-tuned with voice conditioning | 26.1% | 42.2% | 0.111 | 0.126 |

The paired voice-relative comparison changed 29 of 161 top-1 outcomes: it fixed
12 baseline errors but broke 17 baseline successes. Its paired bootstrap 95%
interval for top-1 change was -9.9 to +3.1 percentage points. This is not an
improvement. Separate per-family experts also underperformed the pooled fixed
model (34.4% versus 35.9% top-1 on all 384 outer clips).

## Incremental-label retry

The first table compares new architectures with the existing model; it does not
isolate whether Book 2 labels helped. A follow-up ablation held architecture,
features, split and tuning procedure constant and changed only the training
books.

| Task and exact Book 2 test | Book 1 training | Books 1+2 training | Change |
| --- | ---: | ---: | ---: |
| Voice family top-1 | 56.8% | 74.1% | **+17.3 pp** |
| Voice family top-3 | 73.5% | 90.1% | **+16.7 pp** |
| Mood top-1 | 28.0% | 31.1% | **+3.1 pp** |
| Mood top-3 | 57.8% | 59.6% | **+1.9 pp** |

On the same 162 voice-family clips, the previous saved Book 1 model scored
65.4% top-1 and 79.6% top-3. The Books 1+2 candidate therefore also exceeded
that previous audio baseline by 8.7 and 10.5 percentage points respectively.

The mood result is weaker: the previous model scored 31.7% top-1 and 59.0%
top-3 on the same 161 clips. The Books 1+2 candidate reached 31.1% and 59.6%.
Book 2 labels clearly helped the identical architecture, but did not establish a
new mood winner.

Because several Book 2 outer results were examined while developing these
features, Book 2 is now diagnostic rather than pristine. The final Books 1+2
voice and mood candidates are frozen under
`training-work/series-label-predictor/books12-v1/`; Book 3 is their next
untouched promotion gate.

## Decision

- Do not replace the current mood suggestion model.
- Keep the Books 1+2 voice model as the frozen pre-Book-3 candidate. Do not
  treat the reused Book 2 diagnostic as automatic production approval.
- Do not enable automatic mood labels or trainability from these predictions.
- Keep voice family as an explicit input to future mood work; the user's palette
  interpretation is correct even though the present corpus cannot estimate the
  family-specific boundaries reliably.
- Keep emotion2vec adaptation artifacts research-only. The limited fine-tune
  overfit after its first epoch and materially reduced top-1/top-3 transfer.
- Use same-family reviewed examples as comparison/ranking aids in the UI, not as
  automatic verdicts.

## Next valid experiment

Collect more labels through normal Book 2/3 listening, concentrating on the
major voice-family/mood cells rather than reviewing random audio. A cell should
have examples from at least three chapters; aim for 20–30 clips for important
contrasts (`neutral`/`calm`, `curious`/`perplexed`, `energetic`/`excited`, and
family-specific alternatives). Preserve natural class frequency and a new book
as a final untouched test.

Re-run a conditional model only after that support exists. Promotion requires a
paired improvement over the existing predictor on the untouched book, no
material regression in top-3 recall, and a validation-selected confidence gate
whose precision is high enough to reduce review rather than silently introduce
wrong labels.

## Artifacts

- Trainer: `scripts/train_emotion2vec_mood_adapter.py`
- Continual checkpoint trainer: `scripts/train_series_label_predictor.py`
- Tests: `verification/test_emotion2vec_mood_adapter.py`
- Series tests: `verification/test_series_label_predictor.py`
- Reports/checkpoints: `training-work/mood-adaptation/e2v-base-books12-v1/`
- Frozen Books 1+2 candidates: `training-work/series-label-predictor/books12-v1/`
- Frozen evidence: `training-work/assignment-benchmark/m1-20261012/`

