# Narrator fine-tuning: how much paired data?

Basis: the current local pipeline, the official Qwen3-TTS family/training source checked on 2026-09-23, and the experimental plan below. No Russian narrator learning curve or model-quality result was measured here.

## Direct answer

This is **fine-tuning pretrained Qwen3-TTS**, not learning text-to-speech from scratch. Do not start by requiring 50 or 100 book/audiobook pairs. The proposed first collection is **three matched titles by one narrator**: two available for training/development and one excluded from optimization and speaker-reference selection. A subsequent entirely unseen book should be used for final acceptance after tuning choices are settled.

Three is a practical collection suggestion, not a minimum or a quality guarantee. A long, clean recording can supply many clips; several noisy, mismatched editions may supply little usable material. Keep Rimsky, Nechaev and Bashkov in separate corpora. Ten titles spread across ten narrators are not ten titles of adaptation data for one narrator.

## Proposed experimental budgets, not published Qwen thresholds

| Experiment | Reviewed training audio per narrator/performance | Decision |
|---|---:|---|
| Technical pilot | 30–60 minutes | Confirm real training, model reload, Russian synthesis and no gross regression; not a product-quality claim |
| First quality comparison | 2–5 hours | Compare against unmodified Base cloning on the same held-out passages |
| Expansion | 10–20 hours | Continue only if added data improves held-out pronunciation, voice identity, omissions/repetitions and continuity |
| Larger corpus | Beyond 20 hours, or many more titles | Justify through observed improvements/coverage; 50 or 100 books is not a prerequisite |

These are engineering budgets selected to avoid spending time curating a huge corpus before the first valid result. Better results with less data are possible; more data may not resolve an incorrect objective, poor transcript or incompatible trainer. No optimal learning rate, epoch count, GPU requirement or useful-data threshold is asserted.

For arithmetic intuition only: 10 seconds per accepted clip gives 360 clips per hour, 1,800 in 5 hours and 7,200 in 20 hours. Ten books averaging 10 recorded hours would be 100 RAW hours, but that book duration is an assumption, not a measurement of the user's library; accepted hours can be much lower. Book counts must not silently become training-readiness scores.

## What a usable pair means

A title must have a matching edition and language, a correctly identified human narrator, and enough clean speech with trustworthy clip text and boundaries. The pipeline transcribes independently before matching to the ebook, records spoken differences separately, and requires review. Music, credits, multiple human speakers and truncated words must not become silently accepted narration examples.

The optimizer receives short `audio`/`text`/`ref_audio` examples, not an EPUB plus an entire multi-hour MP3. The current dataset policy is 1–30 seconds per reviewed clip. The source book/audio remains unchanged, and narrator identity, fictional-character performance and delivery labels remain separate.

## What more books will and will not train here

The current official adapter performs single-speaker adaptation and outputs one CustomVoice speaker. It does not consume character/delivery labels as instruction supervision. The app currently selects one reviewed performance per run and publishes a baseline speaker, not a learned full acting repertoire. More recordings can broaden the material to investigate; they do not add a missing speaker-attribution or controllable-delivery training objective.

Keep character performances labelled for reference-bank curation or a separately verified richer training adapter. The narrator's finite repertoire can be reused across unrelated conversations. No whole-book unique-voice requirement is introduced.

## Evaluation before expanding

Keep complete books or chapters out of training, including out of the reference audio. Compare unmodified Base cloning with the candidate on identical held-out text. Include narration, unfamiliar names, numbers, dialogue and emotional passages relevant to the actual product. Listen for skipped/extra words, repeated phrases, stress/pronunciation errors, voice changes, pacing and appropriate delivery. Long-form listening is needed in addition to short clips.

Use the same held-out development material when comparing data subsets, and record changed optimization steps/settings rather than pretending differing data volumes alone explain a result. Reserve final unseen material after choosing the candidate. Do not select a checkpoint merely by training loss. The existing evaluation command is a small prefix-based held-out preview, not an exhaustive or blinded multi-book quality study.

## Obtain actual reviewed quantities

```sh
python scripts/train_narrator.py dataset-report --dataset /data/datasets/reader-narration-v1
```

The report validates the dataset and prints training/held-out decoded hours, clips, books, chapters and labelled delivery durations. It does not load models, start training or claim sufficient quality. Read TRAINING.md for all commands, compatibility guards and the explicit prepared-trainer path.

## Primary sources and uncertainty

Official Qwen family: https://github.com/QwenLM/Qwen3-TTS and https://huggingface.co/collections/Qwen/qwen3-tts .

Official fine-tuning instructions: https://github.com/QwenLM/Qwen3-TTS/tree/main/finetuning . They specify single-speaker adaptation and paired clips, not a guaranteed number of Russian audiobooks or a per-narrator hours-to-quality curve.

Training input/objective: https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/finetuning/dataset.py and https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/finetuning/sft_12hz.py .

No actual narrator model has been trained or accepted in this release. The amount needed for the desired professional acting quality is unknown until real held-out experiments are performed.
