# Continual voice-family and mood evaluation

## Contract

For book `N`, freeze the model trained only from protected labels in books
`1..N-1` before inspecting any book `N` labels. Evaluate it on an exact-audio,
chapter-separated sample from book `N`. Human review then becomes the protected
book `N` corpus, and a new candidate is fitted from books `1..N` for the next
book. Never use the same book both to develop an architecture and to call its
result pristine.

Every checkpoint records:

- exact review, audio, split and encoder hashes;
- eligible and unsupported labels;
- top-1, top-3, macro F1 and balanced accuracy by book and voice family;
- paired changes against the previously frozen model;
- high-confidence precision and coverage when a gate exists;
- regressions on all earlier frozen test sets;
- whether the result is diagnostic, a next-book candidate, or production-promoted.

## Promotion rule

A candidate may provide automatic prefills only after it improves the untouched
next-book evaluation, does not materially regress prior books, and meets the
configured high-precision confidence gate. Otherwise it remains a ranked
suggestion and all outputs require review. `mood_pending` is never a trainable
label.

Voice family and mood use different features and gates:

- voice family: waveform/prosody + Qwen speaker/timbre embedding, augmented by
  protected recurring-character and structural priors;
- mood: waveform/prosody + emotion2vec, conditioned or compared within the
  assigned voice family's reviewed performance palette.

The family palette is not a hard allow-list. Unsupported or novel combinations
abstain rather than being silently mapped to the nearest known mood.

## Current checkpoint

`training-work/series-label-predictor/books12-v1/` was frozen on 2026-10-18 from
2,024 exact human-labelled clips. Book 2 results are diagnostic because they
were consulted during development. Book 3 is the next pristine gate.

- Voice family improved from 56.8%/73.5% top-1/top-3 with Book 1-only training
  to 74.1%/90.1% with Books 1+2 on the same 162 Book 2 clips.
- Mood improved from 28.0%/57.8% to 31.1%/59.6% on the same 161 Book 2 clips,
  but did not clearly beat the previous mood model.

No candidate creates human verdicts, corpus approval or trainable labels.

