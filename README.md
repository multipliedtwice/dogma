# Dogma

A local EPUB/FB2 reader and chapter-based audiobook player built with React, Tauri, SQLite and local inference adapters.

**Listen to the public demo:** [Dogma listening room](https://multipliedtwice.github.io/dogma/).
The website publishes selected recordings only. Model weights, training data
and labeling records stay local. New demos use [one-shot generation](docs/PUBLIC_DEMO_ONE_SHOT.md), with automatic recognition and retakes disabled. See [website publishing](docs/GITHUB_PAGES.md).

**Version 0.1.19 is source-only, not an offline installer.** Model weights, narrator recordings and compiled runtimes are not included. Real-model narration, fine-tuning gains and complete native acceptance remain unverified. See [STATUS.md](STATUS.md).

## Main workflows

- Import EPUB/FB2, inspect chapters, read, bookmark and navigate without loading a model.
- Prepare selected chapters with whole-book primary-text analysis, automatic casting from a reusable narrator repertoire, a cast review step before narration (on by default), and saved passage audio.
- Play and resume one current audiobook per book. Corrections replace affected passages progressively while existing audio stays playable until its replacement is ready.
- New audiobooks begin with the eight-second Dogma Soft + Mist branded opening in the book’s language, in playback and exports. Its music always plays independently of the chapter background-music setting. Russian and English are available; other languages require an approved matching opening.
- Export M4B, single MP3, chapter MP3/VTT ZIP, SRT or WebVTT from saved audio without new synthesis.
- Attach illustrations to paragraphs, or explicitly request optional online Codex paragraph jobs. Saved images appear inline and link to their text anchors.
- Prepare reviewed narrator datasets for the planned custom fine-tuned Higgs production backend. The existing Qwen3-TTS maintainer CLI remains legacy evaluation and fallback infrastructure. Training is never triggered by opening or playing a book.

## Developer setup

Use Node.js 24, Python 3.12, Rust and the Tauri platform prerequisites. FFmpeg and FFprobe are needed for audio exports. Inference requires compatible local Torch/llama.cpp installations and a complete model pack.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r runtime/requirements.txt
npm ci
RIMSKY_PYTHON="$PWD/.venv/bin/python" npm run desktop
```

On Windows, create the environment with `py -3.12 -m venv .venv`, use `.venv\Scripts\python.exe`, and set `$env:RIMSKY_PYTHON` to that executable before `npm run desktop`. The npm scripts that run Python (`verify:core`, `runtime`, `package:runtime`, `release:check`, `release:resources`) use the same `RIMSKY_PYTHON`, falling back to `python3` (`python` on Windows).

The Tauri development launcher starts the local core. For browser development, run these in separate terminals and open the exact URL printed by the core:

```sh
npm run dev
.venv/bin/python runtime/server.py --data-dir .local-library --port 1421 --dev
```

The printed token is a per-launch credential; do not publish it. Frontend dependencies are pinned and `package-lock.json` is included. Python requirements pin direct runtime and verification dependencies; platform-specific transitive resolution must still be validated in the release build environment.

## Prepare a book or narrator dataset

```sh
python scripts/prepare_book.py /path/to/book.epub --output /path/to/new-directory
python scripts/inspect_voice_audio.py /path/to/recording.mp3
python scripts/train_narrator.py --help
```

Book preparation performs no inference. Audio inspection does not transcribe, crop or approve recordings. Narrator training requires reviewed matching transcripts, held-out material, explicitly selected local assets and a separate training environment.

## Documentation

| Document | Purpose |
|---|---|
| [Status](STATUS.md) | Current capabilities, compatibility and release blockers |
| [Architecture](docs/ARCHITECTURE.md) | Source, analysis, storage, playback and process boundaries |
| [EPUB preparation](docs/EPUB-PREPARATION.md) | Import, chapter selection and source-linked preparation |
| [Model packs](docs/MODEL_PACKS.md) | Local assets, reference validation and immutable pack revisions |
| [Paragraph illustrations](docs/PARAGRAPH-ILLUSTRATIONS.md) | Inline placement, Codex consent, saved runs and recovery |
| [Narrator training](docs/TRAINING.md) | Corpus-to-pack CLI and actual trainer limitations |
| [Training data](docs/TRAINING-DATA.md) | Collection and held-out evaluation plan |
| [Manual labeling](docs/LABELING.md) | Public review tooling and local-only dataset policy |
| [Full-app E2E](docs/E2E.md) | Guarded Playwright, synthetic books, local runtime and export checks |
| [Verification](docs/VERIFICATION.md) | Reproducible checks and their boundaries |
| [Packaging](docs/PACKAGING.md) | Source archives and offline native releases |
| [Dependency notices](THIRD_PARTY.md) | Provenance and redistribution requirements |

## Data and network behavior

The original external ebook is not modified. Each book keeps one current audiobook; superseded managed audio and playback caches are reclaimed once playback, jobs and exports no longer need them. Saved pronunciation notes and compact render receipts survive replacements. Exported files outside the application library are independent copies.

The library uses schema 5, with upgrades from earlier schemas. Reading and listening positions are stored independently, and bounded change journals support compact generation and illustration polling. Older applications refuse the upgraded library so they cannot bypass current-output replacement safeguards. A second core cannot open an active library.

Reading, narration, subtitle playback, training and audio export use local files/models. No automatic model downloads or remote narration fallback are enabled. Online paragraph illustrations are separately initiated and consented; they may consume Codex allowance or provider charges. Opening a book does not send its text online.

## Source archive

```sh
python scripts/package_source.py --output ../dogma-0.1.19-source.zip
```

The source packager includes maintained code, verification scripts, current documentation and application assets. It regenerates `SOURCE_MANIFEST.sha256` inside the ZIP. Historical audits, execution logs, previews, caches, build outputs, user media and model weights are excluded. Existing output archives are not overwritten. No audit history needs to be carried into the next release.
