# Local narrator fine-tuning

## Scope

The source includes a maintainer-operated pipeline from books and matching audiobooks to a reviewed Qwen3-TTS adaptation and a new selectable local player pack. It is **not a training wizard in the reader**, and installing/using the player never starts training. Keep a separate corpus and run for each human narrator, for example Rimsky, Nechaev or Bashkov. A narrator's character performance is a separate label from the narrator's identity.

Implemented commands are `corpus`, `transcribe`, `pair`, `dataset`, `encode`, `dataset-report`, `prepare-trainer`, `plan`, `train`, `evaluate`, and `publish`, under `scripts/train_narrator.py`. They use existing local libraries, Qwen's published APIs and the official Qwen trainer; there is no new optimizer or hosted training service. No command downloads models or installs packages. The currently implemented speech recognition requests Russian. Evaluation records Russian-conditioned Base cloning and Auto-conditioned fine-tuned speech, matching the inspected single-speaker trainer prefix.

**Evidence boundary:** corpus handling, matching, exact clip extraction, data isolation, resume, launch refusal/failure, evaluation routing and player-pack admission have executable checks. Qwen/ASR/tokenizer calls and successful optimizer output were controlled interfaces in those checks. No real ASR, forced alignment, codec extraction, optimization or improved narrator model has been run here.

## Verified family and data quantity

On 2026-09-23 the latest dedicated open-weight Qwen TTS family verified in the official repository and model collection is Qwen3-TTS. This adapter targets **Qwen/Qwen3-TTS-12Hz-1.7B-Base** and its 0.6B Base counterpart, not generic Qwen text/Omni/ASR models. The Python distribution `qwen-tts==0.1.1` is the currently inspected package version; it is not a Qwen-1 model. We pin local model, codec and trainer bytes rather than following a mutable `latest` alias during a run. Upgrades need an explicitly reviewed new adapter/plan.

[TRAINING-DATA.md](TRAINING-DATA.md) separates verified capabilities from our proposed experimental data budgets. Start with a few matched books from one narrator and compare curated subsets before collecting 50 or 100. There is no official Russian audiobook book-count guarantee.

## What fine-tuning means in this version

The inspected Qwen3-TTS `finetuning/sft_12hz.py` supports **single-speaker SFT from 0.6B/1.7B Base**. It produces a CustomVoice checkpoint with the selected speaker name. Its dataset does not consume our character/delivery labels as instruction supervision. It is not a multi-speaker trainer or a demonstrated controllable-prosody trainer. The driver deliberately does not invent flags for either feature.

A dataset build selects one explicit `performance` value within one narrator's corpus, such as a reviewed narration performance. Different character performances can remain labelled in the corpus for separate experiments and reference-bank curation, rather than being silently blended. The current player loads one speech checkpoint per pack; this work does not implement simultaneous selection among several trained checkpoints within one book.

Published packs set `speech.variant = custom` and `instruction_control = false`. Two configuration roles may share that one learned speaker; they are explicitly described as the same baseline performance, not distinct voices. This preserves the user's finite-repertoire policy, but **one trained speaker does not satisfy the full expressive character-repertoire requirement by itself**. Existing reference-based packs are unaffected. Additional attribution/delivery-model training requires reviewed semantic labels and a separate evaluated adapter; it is not implemented merely because those labels are stored here.

## Environments and existing assets

Use Python 3.12 and separate already-installed environments:

| Stage | Environment |
|---|---|
| Corpus, pairing, clip preparation and manifest validation | Existing core dependencies plus local FFmpeg |
| `transcribe` | `training/requirements-asr.txt`, local Qwen3-ASR and Qwen3-ForcedAligner model directories |
| `encode`, `train`, `evaluate` | `training/requirements-tts.txt`, a compatible Torch build and local Qwen assets |
| Official SFT execution | CUDA with BF16 support and FlashAttention 2; memory requirement must be measured |

ASR pins Transformers 4.57.6; TTS pins 4.57.3. Do not combine those requirements. The official trainer hardcodes BF16 and FlashAttention 2, so this adapter refuses CPU/MPS training rather than pretending it works on any laptop. Training on Apple Silicon or LoRA training is not added by this pass. Codec/ASR/evaluation device choices are separate from the optimizer's requirements and remain hardware-unverified.

Models must be complete ordinary local directories with config/tokenizer files and safetensors weights. Symlinked hub cache layouts are not accepted as immutable assets; no online model identifiers are accepted. The upstream source checkout must already contain `finetuning/sft_12hz.py`, `dataset.py`, and `LICENSE`. The planning stage checks the documented CLI flags, the known text-projection path, the speech model family and weight headers, and records hashes of these exact files. A prepared compatibility copy includes hashed provenance. This pins the local input for that run, not an invented claim that every future upstream commit is compatible. The checkout is trusted code explicitly selected by the maintainer; a manifest hash is not a security sandbox.

## 1. Build a private corpus

Copy `training/recipes/corpus.example.json` outside the source checkout. Fill in actual book/recording paths and narrator identity. Record the actual authorization basis; the example intentionally leaves training/redistribution authorization false. A local declaration is a record, not legal verification. Permission to train and permission to distribute a trained voice are separate fields.

```sh
python scripts/train_narrator.py corpus /data/reader-recipe.json --output /data/corpora/reader-v1
```

One recipe can include many books and many chapter recordings per book. Duplicate book/recording hashes and known contradictory narrator IDs are rejected. Unknown speaker identity is not detected acoustically: subsequent listening review must verify it.

The command uses the same EPUB/FB2 importer as the player, preserves source books and recordings, decodes recording dimensions locally, and writes a sealed `corpus.json`. The prepared book manifests contain canonical chapter/passage IDs. Input filenames are not assumed to match chapter titles or book editions. Output must be a new directory; existing corpora and their audio are never overwritten.

## 2. Transcribe independently, then match to the ebook

Do not force-align a complete chapter to an audiobook and assume every printed word was spoken. Recordings can have title credits, additions, omissions, adaptations, music or a different edition.

```sh
python scripts/train_narrator.py transcribe --corpus /data/corpora/reader-v1 --recording book-one-chapter-one --asr-model /models/qwen-asr --aligner-model /models/qwen-aligner --device cuda:0 --output /data/work/reader-chapter-one-asr
```

The adapter transcribes without supplying the ebook as a prompt, then uses Qwen's timestamp output. It decodes bounded 90-second windows instead of holding an entire audiobook in memory. Windows are checkpointed with source/model/settings hashes. A compatible interrupted transcription or encoding stage can be resumed explicitly using `--resume`; incompatible source/model changes fail. Fixed window boundaries can cut words, and timestamp quality remains model-dependent. Inconsistent/overlapping/invalid timestamps are refused rather than assigned to training examples.

To pair the words with one explicitly selected canonical chapter:

```sh
python scripts/train_narrator.py pair --corpus /data/corpora/reader-v1 --recording book-one-chapter-one --chapter c0003 --transcript /data/work/reader-chapter-one-asr/transcript.json --output /data/work/reader-chapter-one-review
```

The matcher uses monotonic exact normalized lexical matches within that chapter, preserving original punctuation/text separately. It does not discover the correct edition/chapter automatically. Inputs above 30,000 lexical tokens are refused; it is not an unbounded whole-book matching operation. Punctuation/case/`ё` normalization is for matching, not rewriting the source. ASR differences around numbers or compound words can leave unmatched passages for review.

Outputs are `review.jsonl` and `pairing-report.json`. The report records unmatched source ranges instead of claiming they were aligned. Every candidate starts unaccepted. No word timestamps, character identities or delivery labels are guessed for the uploaded human recordings in this release.

## 3. Listen and review the clips

Copy the proposed JSONL into a separate reviewed file. Each row retains narrator/book/chapter/passage IDs, exact source offsets/text, recording hash, candidate timestamps, the independently recognized text, intended spoken transcript, performance label and optional character/delivery metadata.

Acceptance requires a named reviewer to check the actual spoken words, boundaries, speaker identity and clean speech. Give `performance` a reviewed stable identifier and `delivery` one of the existing application values. If the actual wording differs from the ebook, preserve both and record `text_exception`; do not silently replace the transcript with the printed text. Reject clips with music, multiple physical speakers, credits, chopped words or uncertain attribution instead of automatically cleaning them into presumed ground truth.

This release stores reviews as JSONL; there is no new visual clip-review editor. Acceptance fields record a human decision and cannot prove it occurred.

## 4. Prepare training and held-out audio

```sh
python scripts/train_narrator.py dataset --corpus /data/corpora/reader-v1 --reviews /data/reviews/chapter-one.jsonl /data/reviews/other-book.jsonl --reference clip_REPLACE_WITH_REVIEWED_TRAIN_ID --performance narration --heldout-book book-two --output /data/datasets/reader-narration-v1
```

Reserve entire books where possible. For an explicitly smaller experiment, `--heldout-chapter book-one:c0004` reserves a complete chapter; that is a weaker separation than unseen books. There is no random sentence split that mixes neighbouring paragraphs across training and evaluation. Both partitions must contain reviewed clips. The single shared speaker reference must come from training, never the held-out material.

FFmpeg decodes each contributing recording once into a temporary 24 kHz mono PCM stream, then extracts its reviewed clips using input seeking. The temporary full recording is removed before the next one; no full decoded recording is retained in the dataset. This avoids repeated prefix decoding and preserves the full-stream resampling timeline. Temporary space is about 172.8 MB per decoded audio hour, plus final clips and container overhead; no disk/throughput guarantee is made. The existing 300-second decoding budget can refuse unusually long/slow inputs. This is not unlimited-recording support. The pipeline's own clip policy is 1–30 seconds, not a claimed universal Qwen optimum. All reviewed offsets are checked against actual recording duration. Overlapping clips, duplicate decoded audio and identical normalized transcripts crossing splits are rejected. Stored source text must match the preserved book; spoken exceptions require an explicit note. Publication fails on changed source bytes, bad duration or failed extraction.

The immutable dataset contains `train.raw.jsonl`, `validation.raw.jsonl`, provenance, clips and hashes. Upstream fields are `audio`, `text`, and a constant `ref_audio`. No narrator/character labels are falsely passed to unsupported upstream training arguments. Build a new dataset revision when corrections are made.

### Language conditioning of trained packs

The inspected upstream dataset emits the Auto-language codec prefix; its `language` input is read but not used to add a language token. Explicit `Russian` inference adds a different prefix in Qwen. New candidate evaluation therefore requests `Auto` and records that setting; the unmodified cloning baseline retains `Russian`, with both choices disclosed in the evaluation manifest. The comparison uses identical Russian text, not an assertion that the two inference mechanisms are identical.

New published fine-tuned packs declare `speech.language_mode = auto`. The speech worker honors this pinned mode while existing packs without the field retain their book-language behavior. This does not translate text, change the source book, or prove correct Russian output; it makes the deployed candidate match the evaluated/training conditioning. Unknown modes fail. Older evaluation bundles need a new matching evaluation before publication, and new auto-mode packs need this matching worker version. No existing pack or saved generation is rewritten.

### Inspect reviewed quantities

```sh
python scripts/train_narrator.py dataset-report --dataset /data/datasets/reader-narration-v1
```

This validates the sealed dataset and streams its provenance to report decoded hours, clip counts, represented books/chapters and labelled delivery duration separately for training and held-out data. Delivery counts describe stored review labels; they do not mean the optimizer consumes those labels. No model loads, network requests or training occur, and there is no fabricated quality/readiness score.

## 5. Encode, plan, explicitly execute

```sh
python scripts/train_narrator.py prepare-trainer --qwen-source /src/Qwen3-TTS --output /data/trainers/qwen-projected-v1
python scripts/train_narrator.py encode --dataset /data/datasets/reader-narration-v1 --tokenizer /models/qwen-tts-base/speech_tokenizer --device cuda:0 --output /data/work/reader-codes-v1
python scripts/train_narrator.py plan --dataset /data/datasets/reader-narration-v1 --encoded /data/work/reader-codes-v1 --base-model /models/qwen-tts-base --qwen-source /data/trainers/qwen-projected-v1 --batch-size 1 --epochs 3 --learning-rate 0.000002 --output /data/runs/reader-v1
python scripts/train_narrator.py train --run /data/runs/reader-v1 --execute
```

The inspected upstream `sft_12hz.py` directly adds text embeddings to codec embeddings, omitting the `text_projection` used by inference. The new planning guard refuses that known incompatible path. `prepare-trainer` explicitly copies only the required trainer/dataset/license files to a NEW directory, wraps that exact expression in the existing model's `text_projection`, and records before/after file hashes. An already identically projected expression is retained without double-wrapping. Unknown expressions, multiple target assignments and changed prepared files fail. The original upstream checkout is not edited. This is a compatibility adaptation, not a new optimizer, a general-purpose source patcher, or proof that real training succeeds. Numerical controlled checks cover same-width and changed-width projection and masking; no real GPU optimization was run.

Encoding uses the existing Qwen tokenizer API one clip at a time, preserving completed rows across interruption. Training rows, the sixteen-codebook shape and code IDs 0–2047 are validated before the selected trainer is invoked. The encoding codec must have exactly the same relevant config/weight/preprocessor bytes as the Base model's `speech_tokenizer`; a separate path with identical bytes is acceptable, but a different revision requires re-encoding. ASR/aligner/codec assets are rechecked before final preparation publication. The held-out file is never passed as its training input.

Planning does not load models or start optimization. It freezes input/data/codec/model/source hashes, selected settings, output paths and the exact CLI contract. Execution has a separate explicit flag and hardware/dependency preflight. The trainer runs synchronously as a child process with local-only model settings, a captured stdout/stderr log, bounded execution budget and stop cleanup. Nothing is scheduled by opening an EPUB or starting playback.

Completed checkpoints must have readable root safetensors (or a complete shard index), talker weights and the expected CustomVoice speaker token and non-dialect flag. Their tensor keys/shapes must retain the Base model's non-speaker-encoder tensors, and the primary weight files cannot be byte-identical to Base. An interrupted copy, tokenizer-only folder or config rename is not a trained checkpoint. Header/hash checks do not prove finite or acoustically useful parameters; real loading and listening evaluation remain necessary. Source/codec/model checks also run when reopening a planned/completed training run; older incomplete or incompatible fixtures/plans can now be refused, not silently upgraded.

The inspected upstream script does **not** expose optimizer-state resume, validation-loss selection or early stopping. The driver does not invent those features. Failed/interrupted runs retain their artifacts and are not promoted; a new optimizer run needs a new output directory. Successfully encoded clips can still be reused. The example hyperparameters are initial configuration, not measured good settings for these narrators. Parameter-efficient multi-narrator training is not implemented here.

## 6. Compare with baseline before installing a candidate

```sh
python scripts/train_narrator.py evaluate --run /data/runs/reader-v1 --checkpoint /data/runs/reader-v1/checkpoints/checkpoint-epoch-2 --device cuda:0 --count 12 --output /data/evaluations/reader-v1
```

This renders the same held-out text with the original Base model using the training-only reference, and with the fine-tuned CustomVoice checkpoint. It also retains the actual held-out target clips for local comparison. Models load sequentially. Their file/decoder checks establish technical output, not narrator quality.

The result is a sealed evaluation bundle and an unaccepted review template. Copy the template outside the bundle, fill its exact evaluation revision and complete a listening review of intelligibility, narrator identity, pronunciation, continuity and regressions. There is no invented automatic quality score or claim that a lower training loss improved the listening experience. The current sample subset is an initial evaluation, not a whole-book proof of character performance.

## 7. Publish a new player pack

```sh
python scripts/train_narrator.py publish --evaluation /data/evaluations/reader-v1 --review /data/reviews/reader-v1-listening.json --text-model /models/text/model.gguf --text-license /licenses/text-model.txt --output /data/packs/reader-v1
```

Publication rechecks the dataset, upstream files, checkpoint, evaluation audio and explicit review, then copies the checkpoint and selected GGUF into a **new** sealed pack. It includes model notices and training provenance, not the corpus books/recordings. It never overwrites the selected pack, recasts saved generations or triggers narration. Select it explicitly in the existing Settings flow; existing generations retain their previous pack revision.

The pack exposes one learned baseline speaker with shared narrator/character roles and disables untrained instruction control. It is not labelled as a separately learned repertoire. A trained pack with no recorded redistribution authorization is blocked by the offline distribution guard; private local selection remains possible. Existing signing/native/offline-quality gates remain required.

## Preparation integrity

Russian space-delimited alignment units now use the inspected Qwen aligner's letter/number/apostrophe cleaning rather than splitting hyphenated compounds into separate timestamped words. For example, the aligned `Ктото` can map to the original `Кто-то` range without inventing two word timings. Source punctuation and the reviewed spoken transcript remain preserved. This is not a general multilingual tokenizer or a claim of acoustic alignment accuracy.

Each new or resumed ASR window must cover its independent recognized text completely, in order, after that lexical normalization. Missing, extra or changed words fail before an accepted window/transcript is published. Pairing also checks window provenance when present. Existing inconsistent transcription outputs may therefore need to be recreated; they are not silently repaired or approved. Human transcript/boundary/speaker review is still required.

Fresh transcription and encoding stages claim their output directory exclusively. A concurrent creator's results cannot be overwritten; `--resume` remains explicit and accepts only the matching input contract. No optimizer-resume support is added.

Audio-code rows are streamed and atomically published. Validation no longer mistakes an encoded training file over 64 MiB for an oversized metadata manifest. Encoded JSONL remains bounded to 1 MiB per row, 2 GiB total and 100,000 rows; each row still matches its reviewed source, checksum and sixteen-codebook shape. Ordinary JSON/review metadata retains its 64 MiB bound. Producer failure preserves a previously complete JSONL file and committed per-row checkpoints can be reused. These limits are application policy, not measured hardware requirements. The selected upstream optimizer still reads its full training JSONL into memory; planning success is not proof that optimization fits GPU or host memory.

## Source contracts checked on 2026-09-23

- Official Qwen single-speaker workflow: https://github.com/QwenLM/Qwen3-TTS/tree/main/finetuning
- Trainer implementation and supported arguments: https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/finetuning/sft_12hz.py
- Dataset/reference rate and unsupported instruction supervision: https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/finetuning/dataset.py
- Tokenizer preparation interface: https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/finetuning/prepare_data.py
- ASR/timestamp API: https://github.com/QwenLM/Qwen3-ASR
- Environment pins: the official TTS/ASR `pyproject.toml` files.

No upstream trainer is vendored into this source ZIP. The explicit compatibility command creates a separate documented local copy; it never modifies the selected original checkout. Documentation inspection and controlled compatibility checks are not real model execution or acceptance of a training result.

The complete Python verification set additionally needs `verification/requirements.txt` (including safetensors for tiny structurally valid, non-inference model fixtures). These developer dependencies do not add model loading to normal reader startup.

Additional implementation references:

- Official model collection: https://huggingface.co/collections/Qwen/qwen3-tts
- Inference text projection: https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/qwen_tts/core/models/modeling_qwen3_tts.py
- Base architecture: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base/raw/main/config.json
- Speech codec contract: https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz/raw/main/config.json
- FFmpeg input and output seeking: https://ffmpeg.org/ffmpeg.html
