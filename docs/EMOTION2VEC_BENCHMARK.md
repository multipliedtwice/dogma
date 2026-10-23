# Book 1 emotion2vec comparison

This is a read-only benchmark of speech features against the current saved labels.
It does not change the review UI, labels, training approvals, or generated audio.

## Inputs and provenance

- Official model: `emotion2vec/emotion2vec_plus_base`.
- Pinned revision: `b318240bfe67db81a8c572ecb37ce9c3759b81c9`.
- Local checkpoint: `training-work/models/emotion2vec-plus-base` (about 1.12 GB).
- Exact precision WAVs with matching candidate, quality and waveform hashes.
- A frozen review snapshot and clip manifest in
  `training-work/character-rb1/emotion2vec-benchmark-20260929`.
- Feature caches are keyed by model revision and exact WAV SHA-256, loaded without
  pickle. They contain averaged 768-dimensional features and the model's original
  nine-category scores. The CPU/GPU consistency check is recorded separately.

## Environment

`training-work/.venv-emotion-benchmark` holds FunASR and its added dependencies.
Its `rimsky_tts_dependencies.pth` points to the existing TTS environment to reuse
PyTorch and numerical libraries. This overlay leaves the TTS installation intact.
The benchmark initializes setuptools' distutils compatibility module on Python
3.12 before importing FunASR. Observed dependency versions are in `environment.json`.

Run:

```powershell
training-work/.venv-emotion-benchmark/Scripts/python.exe -X utf8 -u scripts/benchmark_book1_emotion2vec.py --device cuda
```

`--device cpu` is also supported. Cached features are reused. `--max-clips 3` is an
extraction smoke check, not an accuracy benchmark; it writes a partial report.

## Evaluation

Two tasks are evaluated: narrator moods within the narrator baseline family, and
voice families across the usable labeled corpus. A class needs ten clips in at
least three chapters to participate. Rare classes are listed as excluded rather
than silently merged. Exact duplicate WAVs are removed.

Five deterministic, stratified chapter groups are held out in turn. Every scored
prediction comes from a model trained on other chapters; no test labels enter
preprocessing or model fitting. The same fixed logistic regression (`C=0.1`) and
train-only imputation/scaling are used for waveform features, emotion2vec features,
and their combination. The initial run uses balanced class weights. The separate
`empirical-class-prior-control.json` uses the identical folds with natural class
weights to check the effect of unequal class sizes. A majority-label baseline,
top-one/top-three agreement, macro F1 and balanced accuracy are reported.

The waveform comparator is a trained label predictor using existing measurements;
it is not the current UI warning algorithm or the production text/context model.
This benchmark contains no EPUB context features and no tuning of the speech model.

These scores measure agreement with the unfinished Step 1 review. They are not
final human-validated accuracy, a Book 2 transfer result, or calibrated confidence.
The model's generic emotion categories do not directly match our custom mood list.
Voice-family evaluation concerns enacted voices of one narrator, not identification
of different physical speakers.

## References

- [Official model card](https://huggingface.co/emotion2vec/emotion2vec_plus_base)
- [Official repository](https://github.com/ddlBoJack/emotion2vec)
- [Research paper](https://arxiv.org/abs/2312.15185)
