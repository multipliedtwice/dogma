# Book 3 bucket preparation

Book 3 is the untouched evaluation gate for the frozen Books 1+2 voice-family
and mood candidates in `training-work/series-label-predictor/books12-v1/`.
Book 3 human labels must not be used to retrain or select those candidates until
the frozen evaluation is reported.

## Inputs

- EPUB: `content/reality-benders-3.epub`
  (`bb546ee442d31566c2eb8ece698c3c47be5558e5fd9a031a0853fe9f0ce28025`)
- Audiobook tracks: `content/reality-benders-3/00.mp3` through `32.mp3`
- Complete prepared source: `training-work/character-rb3/complete/source/`
- Track mapping: `00 -> c0003` through `32 -> c0035`

The older `training-work/character-rb3/full/source/` is an incomplete preview
and is not an input to this board.

## Run and contracts

`scripts/run_book3_full_board.py` owns the resumable run. It performs source-
bound ASR, forced alignment, sentence cuts, an independent ASR audit, exact
wave/timbre features, frame-level emotion2vec means, frozen Books 1+2
predictions, three-lane validated context analysis, board/archive construction,
series-prior prefilling and final verification.

All Book 3 outputs are proposals:

- `human_review_required` is true;
- `automatic_acceptance` is false;
- raw predictions are retained unchanged;
- Book 1 and Book 2 decisions are read-only priors;
- Book 3 starts with a separate empty review in
  `manual-labeling/reality-benders-3/`.

The final verifier is `scripts/verify_book3_full_board.py`. It binds candidate,
audio, context, source, review and model hashes and rejects utterance-level
emotion features for the frame-mean mood model.

## UI

The bucket selector navigates between three independent boards instead of
merging their manifests:

- Book 1: `/character-rb1/fine-tune-all/`
- Book 2: `/character-rb2-full/fine-tune-all/`
- Book 3: `/character-rb3-full/fine-tune-all/`

This preserves every existing Book 1 and Book 2 decision and keeps Book 3 saves
isolated. The dedicated local Book 3 fallback port is `1440`.
