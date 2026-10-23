# Preparing a complete offline release

The source ZIP is not an offline installer. Build on each target platform; cross-platform binary compatibility is not assumed.

## Production model and private data

The production speech checkpoint is pinned by
`resources/production-higgs-model.json`. Its local GGUF is
`training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-q8_0.gguf`
(5,095,233,792 bytes, about 4.75 GiB), with adjacent hash-bound provenance.
`training-work/` and the complete `manual-labeling/` archive stay on the
maintainer's machine or in private backups. They are not public source or
installer inputs. See [the labeling policy](LABELING.md).

A distributable inference pack contains the pinned model, its provenance,
compatible audio.cpp runner and dependencies, reviewed runtime voice references,
and required analysis/verifier assets. It must not contain the training corpus,
source books, labeling answers, optimizer state or experiment directories.
The pack is a deliberately assembled runtime subset, not a copy of the working
archive. Keep model identity and integrity metadata public without publishing
the underlying training examples.

The current Tauri resource mapping bundles staged runtimes and packs from
`resources/runtime/` and `resources/packs/`; it does not bundle `training-work/`.
The inference bundling recipe still collects legacy Qwen dependencies and needs
a verified Higgs packaging path before production distribution. Empty staging
directories or a successful frontend build do not constitute a working installer.

For distribution, prefer a platform-specific signed application installer and a
separately versioned model/voice pack, hosted outside source Git. Users can select
a local pack in Settings; automatic model downloading is not implemented.
An offline edition can stage the same complete pack under `resources/packs/`
before the native build. Pack updates should preserve prior immutable revisions
needed by saved generations.

## Directory contract

```text
resources/
  runtime/
    core/rimsky-runtime[.exe]
    infer/rimsky-infer[.exe]
    align/rimsky-align[.exe]
    media/ffmpeg[.exe]
    media/ffprobe[.exe]
    LICENSES/
  packs/
    chosen-pack/pack.json
```

Each PyInstaller onedir component retains its own `_internal` directory. Do not flatten separate distributions together. The aligner is optional; core, inference, media utilities, and at least one complete pack are required by the release check.

## Build isolated runtimes

Prepare a Python 3.12 virtual environment for each component. Install PyInstaller in the build environments. The scripts invoke that already installed tool; they do not create environments or choose a Torch/CUDA package on the user's behalf.

```sh
python -m pip install -r runtime/requirements.txt pyinstaller
python scripts/package_runtime.py core
```

In the separate inference build environment:

```sh
python -m pip install -r runtime/requirements-inference.txt pyinstaller
python scripts/package_runtime.py infer
```

In the optional separate alignment build environment:

```sh
python -m pip install -r runtime/requirements-alignment.txt pyinstaller
python scripts/package_runtime.py align
```

Hardware-specific Torch and llama.cpp builds must be chosen and verified before packaging. The current script is a bundling recipe, not evidence that every native extension freezes correctly on every target. Check hidden imports, shared libraries, and actual offline execution on the resulting bundle.

Each component's `build-info.json` records the application version and `source_digest`, a SHA-256 over `runtime/*.py` and `runtime/rimsky/*.py`. The core, inference and alignment workers share one analysis and reconciliation contract, so rebuild all of them from the same checkout after any runtime change. The release check fails when their digests differ from each other or from the current source. In a packaged build, the health check also compares the digests recorded by the installed core, inference and alignment components, skipping components without one; when they differ it reports `Bundled runtime components are from different builds. Reinstall the application.` and no generation starts. The core also collects `certifi` for the CA bundle used by optional online illustrations.

Supply existing FFmpeg/FFprobe binaries for that same platform:

```sh
python scripts/package_runtime.py media --ffmpeg /path/to/ffmpeg --ffprobe /path/to/ffprobe
```

Include their dependencies and exact build licence files. Prefer static builds. A Homebrew or distribution FFmpeg loads `libav*` libraries from the build machine and fails elsewhere. The helper refuses to overwrite component outputs; remove an old build deliberately when rebuilding.

On the build host, the release check starts every packaged binary. `rimsky-runtime --version` must print the source version, the inference and alignment workers must exit cleanly with closed input, and `ffmpeg -version` and `ffprobe -version` must succeed. On macOS it also reads `otool -L` and rejects load paths under `/opt/homebrew`, `/usr/local/Cellar` or `/usr/local/opt`. On Linux it reads `ldd` and rejects missing libraries and FFmpeg libraries resolved outside `runtime/media`. Binaries are executed only when the target is the host platform; a full check for another target reports that they were not started.

## Record acceptance truthfully

After native build, actual model narration, clean-machine offline installation, and listening checks, create `release-acceptance.json` recording the verified configuration. It must contain true results for `native_build`, `offline_install`, `local_inference`, `russian_casting`, `voice_continuity`, `exports`, and `resume`, together with `hardware` and `model_revisions`.

Do not mark these true merely to pass the script. The repository intentionally omits this file because those checks could not be performed here.

The first production-mode acceptance build validates required resources but does not require acceptance for an installer that has not been built yet. `beforeBuildCommand` invokes `release:resources`, followed by the real frontend build. This is not approval to distribute the result. After native build, clean-machine offline installation, and real narration have actually passed, record the evidence and run `release:check` before distribution.

```sh
npm run release:resources
npm run desktop:build
```

After completing and recording the actual acceptance checks:

```sh
npm run release:check
```

The distribution check is structural, plus host start checks and recorded evidence; it is not an automatic speech-quality evaluator or a signing service. Add platform signing/notarization in the actual release environment. Windows is configured to bundle the WebView2 offline installer.

## Native lifecycle and resource lookup

The Tauri shell launches `runtime/core/rimsky-runtime`, passes the library data directory and resource directory, and receives a random loopback port and token. If the core fails before that handshake (library already open, a library from a newer version, a damaged database, an unwritable folder), it prints one line `{"protocol":1,"error":"..."}` and exits non-zero; the shell shows that message in its startup dialog. The shell starts the core with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, the core passes both to its workers, and the workers reconfigure their standard streams, so the JSON exchange does not depend on the Windows code page; this has not been run on Windows. The local core discovers sibling `infer`, `align`, and `media` components. It selects the sole installed pack for a new library. Several bundled packs require choosing one in Settings. The selected pack folder is checked in a background thread after startup, so the handshake does not wait for it. When that folder no longer contains `pack.json`, as after moving the macOS app or on every new AppImage mount, the core switches to the bundled pack whose fingerprint matches the newest pin among the generations made from that folder. Each new generation records when its pack was pinned, and a corrected version keeps the pin time of the generation it corrects, even after that generation is deleted. When no generation made from that folder has a pin time, as for generations made before this version, this rule switches only when all of them share one fingerprint. Otherwise, when the stored path is a bundled pack location (`…/<resource folder>/packs/<name>`), it switches to the bundled pack with the same folder name, or else to the sole bundled pack. Any other missing custom folder stays selected, and the health check reports it. A selection saved while this check runs is kept.

On macOS and Linux the library lives in the application data directory, as before. On Windows it is the dedicated folder `%LOCALAPPDATA%\app.rimskifikator.desktop\library`, outside roaming profiles and apart from the WebView2 profile (`EBWebView`) that Tauri keeps in `%LOCALAPPDATA%\app.rimskifikator.desktop`. A library found in the earlier `%APPDATA%\app.rimskifikator.desktop` folder is moved there with one folder rename at startup. If the rename is not possible, for example because a file in the folder is in use, `%APPDATA%` is on another volume or the target folder already has content, nothing is moved, the `%APPDATA%` library stays in use and the move is tried again at the next start. When the target already holds a library database, that library is used and the `%APPDATA%` folder is left alone. None of this has been run on Windows.

The main window only navigates to the application's own pages (`tauri://localhost`, `http(s)://tauri.localhost`, and the development server in debug builds). Other navigations, such as a dropped link or file, are cancelled.

Debug builds use the source-tree resources and pass `--dev` to allow the Vite origin. Release builds use packaged resources and do not enable that origin. The runtime holds an OS-level exclusive lock on the library before opening SQLite; launching a second instance for that same library is refused instead of resetting another process's generation state.

The parent pipe closes on app exit. The core checkpoints generation and ends the inference process. Child-process termination and crash recovery need native platform verification; Python-level restart/reuse paths are covered separately.

## Distribution

Distribute the same complete, signed, platform-specific payload through a download or torrent. Do not redistribute secrets, development libraries, user books, application-data directories, or private voice assets. Keep model/voice packs revisioned so they do not need to be replaced for a UI-only update.

## Source-only ZIP

```sh
python scripts/package_source.py --output ../dogma-0.1.19-source.zip
```

This is separate from binary/runtime packaging. It retains application and training code, all executable verification sources and fixtures, example manifests, branding, dependency notices and the current maintained documentation. It embeds a fresh `SOURCE_MANIFEST.sha256` without changing the working-tree manifest or overwriting an existing archive.

The packager selects maintained paths and source types rather than zipping the entire workspace. Add new source locations or maintained guides to its inclusion policy when needed. Audit reports, command logs, previews, dependency trees, build outputs, runtime/model bundles, fonts and user/training media are excluded. Keep execution evidence outside the source release; do not append repeated audit narratives to README or STATUS.
