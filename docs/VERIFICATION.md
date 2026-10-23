# Verification

Run these from the repository root after installing the relevant declared developer dependencies. Kept scripts are reproducible checks, not historical execution logs. Source archives exclude logs, screenshots and audit reports; write fresh output outside the source tree.

## Core, training-data and frontend checks

```sh
python -m pip install -r runtime/requirements.txt -r verification/requirements.txt
python -m unittest discover -s verification -v
python -m compileall -q runtime training scripts verification
python scripts/train_narrator.py --help
npm ci
node --test verification/*.test.cjs
npm run build
```

Most Python test files import the shared fixtures in `verification/test_core.py`. To run one file, start from `verification/`, which puts those fixtures on the import path:

```sh
cd verification
python -m unittest test_render_1004 -v
```

On macOS, set `TMPDIR` to its resolved path for these runs, for example `TMPDIR="$(python -c 'import os,tempfile;print(os.path.realpath(tempfile.gettempdir()))')"`; a few checks compare resolved temporary paths. `test_export_illustrations_1004` also needs the `openssl` command for its local TLS server.

FFmpeg/FFprobe enable actual encoding and clip-extraction checks. Learned-model operations use controlled interfaces and tiny structural weight fixtures. Those do not establish Qwen quality, ASR accuracy, actual optimization or expressive delivery.

Controller and App-callback checks execute source modules with controlled media/state/services. `npm run build` performs the complete React/Radix/Vite production build; native interactions still require target-platform acceptance.

With Rust and the Tauri platform prerequisites installed, run `cargo test --manifest-path src-tauri/Cargo.toml` for the native file queue, path resolution, export replacement, library migration and navigation checks. Run this on each target platform; source inspection and JavaScript queue checks do not establish native execution.

The checks added for the analysis, casting and review-gate work run with controlled engines only:

| File | Checks |
|---|---|
| `test_analysis_protocol_1004.py` | Passage units, per-target validation and re-ask, degraded passages, registry projection, response schema bounds, worker protocol and UTF-8 streams, transport error types, timeout retry and stop, output limit |
| `test_casting_families_1004.py` | Voice roles, families and pools, narrator identity, unknown-gender targets, volume-weighted fit, alias hints, coded notices, pack voice schema |
| `test_reconcile_1004.py` | Alias candidates, bounds and order, pair-local failures, checkpointed comparisons and resume |
| `test_review_gate_1004.py` | Cast review stop and setting, cast operations, per-chapter narrator voices, coded notices, revision recomputation, new takes, transient startup cleanup and audio collection after jobs, served operations |
| `test_render_1004.py` | Render key schema 3, render retry and failed passages, device probe and adoption, letterless silence, duration plausibility, speech language, pack change checks, bundled pack relocation, alignment stop, worker error output |
| `test_gpt_pack_guard_1004.py` | All file-state fields of sealed pack files (same-size rewrite with the modification time restored, replacement by rename), the unchanged verification cache key, pack checks immediately before and after every request to the inference process, discarded analysis replies and alias decisions after a change during the request, no published or reused audio after a change during or after synthesis |
| `test_gpt_pack_guard_followup_1004.py` | Pack checks before and after every alignment request (no cues saved after a change), files and symbolic links added to the pack folder, operating-system metadata ignored, sealed files replaced by a symlink loop or by a link outside the pack during alignment or synthesis reported as a pack change, with the interrupted take deleted |
| `test_platform_1004.py` | Startup handshake errors, lock-free operations, export cancellation, pack selection locking, bundled pack relocation, release digest, start and linkage checks |
| `test_import_fixes_1004.py` | EPUB/FB2 import corrections |
| `test_export_illustrations_1004.py` | Grouped word captions, caption breaks after initials and abbreviations, merging of caption lines under 1 second, stored exports, encoder time limits, illustration resume and skipped paragraphs, Codex discovery including the Windows folder list, image formats, Image API TLS |
| `test_followups_1004.py` | Dialogue-dash units after colons and spaced hyphen-minus, unresolved speech without degraded passages, export locking and deletion during encoding, Codex availability in health, packaged component build digests |
| `test_evaluate_speakers_1004.py` | Speaker evaluation harness on synthetic cases with fake engines |
| `test_fix3_analysis_1004.py` | Timeout retry and stop in analysis and alias comparison, output limit, quote and dash units, speaker and character ID enums and speaker resolution, registry shortening to keep room for the output limit, character records without valid evidence, descriptor display names, description echoes, one analysis loop shared by the application and the harness |
| `test_fix3_casting_1004.py` | Diminutive forms without common-noun matches, the opposite-gender candidate filter, canonical forms for fleeting vowels and -ай/-ой/-ая/-ея nouns, registry projection from oblique forms |
| `test_fix3_import_1004.py` | Drop-cap hints, fragment landmarks and guide entries, restore guard on narratable text, undeclared FB2 encoding plausibility, endnote list numbers, typed note markers, language names and unknown values, FB2 piece naming, empty-line spacing style per book |
| `test_fix3_pipeline_1004.py` | Startup cleanup safety and deletion markers, pinned small-context packs, cast kind correction, review revisions, queued progress, full alias lists, failed passage warning tail, legacy audio reuse, legacy alias warnings |
| `test_orchestrator_1004.py` | Speech language pinned at the first render, the earlier language rule for generations with audio stored before the upgrade, alias merges that prefer a revealed name over a descriptor |
| `test_fix3e_analysis_1004.py` | New character records kept by the valid targets that speak through them, required registry entries and output room, description echoes of at least 8 words, name-table names outside the book vocabulary, saved timeout counts, one attempt for passages that timed out twice, the stop at a second timeout in a row |
| `test_fix3e_edges_1004.py` | One-pass drop-cap wrapper check, kept space after a drop cap, CSS keywords that are not drop-cap hints, short typed note markers in any script, fragment landmarks that wrap a whole document, undeclared KOI8-U, FB2 spacing style per book for short chapters, Windows Codex lookup without the working folder |
| `test_fix3e_pipeline_1004.py` | Earlier-key audio reuse only in the pinned language (auto-mode packs exempt), the earlier language pin for older generations with stored or earlier-key audio, relocation by the newest pin of the missing folder, re-selecting a moved small-context pack for its pinned generations |
| `test_fix3f_analysis_1004.py` | Output budget independent of optional registry entries (the full output limit, or a capacity failure and split when the required entries leave less room), names written in title case mid-sentence kept as display names |
| `test_fix3f_rest_1004.py` | Pin time recorded by new generations and kept by corrections, relocation by the newest pin, conservative relocation for generations without a pin time, `toc`/`cover` fragments at the start of a page outside the table of contents, one ID lookup per document for landmark fragments, line-break whitespace after a drop cap |
| `test_fix3g_names_1004.py` | Articles and capitalized word pairs (honorific plus surname) are not name evidence, so descriptor labels do not replace revealed names; a one-word label that extends an existing name's stem (Вера → Верочка, Will → Willy) stays an alias |
| `test_fix3g_epub_1004.py` | `toc`/`cover` fragments at the start of a document change its role only for table-of-contents-like or cover-like pages (including chapter-number and page-number entry links); content documents keep their body role |
| `review_gate_1004.test.cjs`, `playback_1004.test.cjs` | Cast review panel and notice translations, kind control, degraded passage list of the rendered chapters, queued status, part-by-part Russian translation of joined messages, export cancel state, character preview positions, next-passage prefetch, drop guard, queued book opening |

## Full-app Playwright E2E

Lead-owned gate. See [E2E.md](E2E.md). Use Node 24, Python 3.12 and installed runtime requirements. Regenerate the local dependency lock after dependency changes, then use `npm ci`. Keep FFmpeg and FFprobe on PATH. Export specs fail when either is missing.

```sh
npm run e2e:install
npm run e2e:check
npm run e2e:list
npm run e2e
```

Every command checks CPU, memory, disk and competing work first. Do not bypass a refusal. One Chromium worker. Real built app, real ephemeral HTTP runtime, fresh library per test. Narration and image rendering are test-only fakes. No Vite server. No native window, real model, Codex account or Image API. Screenshots/video off; failed-test trace only. No CI here.

Guard and packaging policy checks, without a browser:

```sh
node e2e/guard.mjs && node --test e2e/guard.test.mjs
node e2e/guard.mjs && python -m unittest verification.test_source_packaging -v
```

## Existing browser and book checks

Use developer-installed Playwright, Chromium and the declared TypeScript package. They are not added to the player's end-user runtime. Replace paths below with local files; no sample book, recording or generated audiobook is included.

```sh
python verification/browser_playback.py --chromium /path/to/chromium
python verification/browser_reader_controls.py --chromium /path/to/chromium
python verification/browser_paragraph_reader.py --chromium /path/to/chromium
python scripts/prepare_book.py /path/to/book.epub --output /path/to/new-prepared-directory
python verification/check_paragraph_epub.py /path/to/book.epub /path/to/new-prepared-directory
python verification/browser_book_assets.py --chromium /path/to/chromium --prepared /path/to/new-prepared-directory
python verification/browser_epub_play.py /path/to/book.epub --chromium /path/to/chromium
python verification/check_epub_audio_flow.py /path/to/book.epub
```

The joined browser check exercises real local HTTP/storage and browser audio through its documented transport bridge, with controlled narration responses. `browser_epub_play.py` and `check_epub_audio_flow.py` turn the cast review stop off in their own temporary library so that narration runs to ready. Layout harnesses use controlled React/Radix boundaries. Neither establishes a full native window, platform autoplay, real artwork or a narrated audiobook.

## Speaker analysis against the gold set

`scripts/evaluate_speakers.py` is an offline maintainer harness. It reads the local, untracked `manual-labeling/speaker-resolution-gold-v1/` folder (`gold.json`, `evaluation-protocol.json`, `manifest.json`; override with `--gold`) and checks its checksums. It needs a real sealed pack and a Python with `runtime/requirements.txt`. It starts the same inference worker as the application: a packaged `rimsky-infer` under `resources/runtime` (or `$RIMSKY_RESOURCE_DIR/runtime`) when present, otherwise `runtime/worker.py` with the same Python, which then also needs `runtime/requirements-inference.txt`. `--device` (default `auto`) is passed to the worker as the Settings device would be.

```sh
python scripts/evaluate_speakers.py --pack /path/to/pack --out /path/outside/source/report.json
python scripts/evaluate_speakers.py --pack /path/to/pack --limit 10
```

Cases run in gold order, and the character registry grows across them. Each case's text is split into the application's target passages and sent with that case's own before/after context. The harness runs the application's analysis loop itself, `rimsky.analysis.analyse_sources`, the same function `Jobs._prepare` uses, so nothing of the loop is mirrored: registry projection, requests, per-target validation, bisection, the single re-ask, timeout retry and degraded parts are shared. Worker requests still go through `Jobs._call`, which holds the transport retries and the timeout policy, and alias reconciliation through `Jobs._reconcile` (`--no-reconcile` skips it). Per-passage timeout counts are saved only with an application generation, so the harness never sends a passage alone because of earlier timeouts. Display names use the vocabulary of the gold cases' own text. An error that would fail a generation, such as a second timeout in a row, stops the run at that case with its message; later cases are not sent or scored, and the summary says so. The report scores speech/narration boundary precision, recall and F1, with each boundary moved to the next letter or digit so that a space, dash or quote on either side does not count; speaker attribution on gold speech spans after mapping predicted characters to gold characters by name or alias and then by majority overlap; unresolved and degraded rates; and per-case errors. It prints its limits with every summary.

`--secondary codex_cli|claude_cli|local` is a development gate, not production behaviour: a second provider answers each request blind (same source, context and pre-primary registry; never the primary's answer, repair request or new characters). Each target gets one original second answer; the primary result stays frozen and is never re-asked for a second-provider problem. `--secondary-repair own-v1` allows one repair of a semantically invalid second answer, built only from that answer and its validation error, with its own valid speech structure kept fixed; the report then also scores the gate with the unrepaired second answers (`first_secondary_outcome`). Failures keep their type: a missing CLI (`provider_unavailable`) and an indivisible capacity failure (`provider_capacity`) hold the passage, a capacity failure of a larger second request is split for the second provider only, a second-provider timeout is retried once, and any other transport, protocol or CLI failure (`ProviderTransportError`, after the adapter's bounded retry) stops the run. An identity counts as agreed only when both frozen requests offered it; registry growth from a sibling passage or a primary repair never upgrades a cached second answer, and spans where both answers created a person stay held (`provider_new_identity`). The own repair is built from the original second request: same registry projection, sources and evidence, with the other batch targets moved into context in reading order; a repair that answers a different registry or changes its own structure is held. `agreement_audit` is written before each provider call and kept, for every attempted target including degraded, capacity-held and stopped work (also when the run stops before the case is scored, and for unscored prefix or bridging chapters in `--book` mode, which carry `chapter_id` and no case); `attempted_targets` lists every passage actually sent to a provider, `attempted_cases` counts selected cases with such a passage, `planned_cases` the requested cases, and `cases` only completed, scored ones, the primary attempts (original and repair, with validation errors), the second provider's attempts and request summary, raw passage answers, characters, offered IDs and fixed structure. `new_identity_alignment_counterfactual` scores, without changing any hold, a conservative source anchor for held new identities: the dash remark right after the speech (following a comma, `!`, `?` or `…`) must be a deterministic named speech attribution by the existing first-tier rule (`speaker_resolution.named_attribution_speaker`: one speech verb, at most two adverbs, no pronoun and no negation or hearsay particle such as не, ни, якобы, будто, the name last in exactly the form both answers gave), that name must be the only proper name of the remark, and no registered character may already have it. Listeners, silent actors, lead-ins and addressees never align. `--secondary-repair own-v1` is refused with `--secondary local`, whose analysis paths rerun the structure pass. A span is accepted only when both validated answers give the same fixed speech structure and the same already-registered identity unit by unit; disagreement, identities created in the pass, an unusable or failed second answer, and spans whose speaker an alias merge changed afterwards are held (`provider_*`, `alias_merge_after_agreement`). There is no voting. The report keeps every proposal and record, and `single_provider_control` scores the same run without the agreement holds, so baseline and gate are compared on identical primary answers. Passing the same provider twice measures repeated-run agreement.

`--book PATH` runs the production order instead of isolated cases: the generation chapter plan (`preparation.analysis_chapters`: default narration chapters plus the cases' chapters, in book order, so unscored earlier and bridging chapters are analysed too), with one growing registry, the same `analyse_sources` call per chapter as `Jobs._prepare`, and book-wide common words. Only the split cases are scored or reported. It accepts only the exact `training-book.json` the gold was frozen from (the sha256 recorded under `sources.book` in `gold.json`) and checks every case's text hash first; it is not run on this machine because that file (`training-work/corpus-rb1-current/books/676d6ad2…/training-book.json`) is absent.

`scripts/legacy_master_selection.py` is the reference baseline for the legacy master path. It needs `--legacy`, a checkout of master `de954783` without tracked modifications (untracked files are listed in the report), runs the legacy code in its own Python process, and passes it only selection-case text and context. It refuses to run while this checkout's `runtime/` or `scripts/` differ from the index (unstaged or untracked files) and records that index tree as the executed candidate source. `run --provider codex_cli|claude_cli` calls the legacy CLI analysis; `replay --raw <saved file>` revalidates saved raw answers through the legacy validator with no model call and refuses any difference from the saved outer units. Reports score the legacy internal `analysis_parts` with the candidate scorer (`analysis`) and give the outer-unit projection separately (`outer_units_projection`), with the differences from a master generation listed.

The default `selection` split was already used once to compare models. The `test` split requires `--final-acceptance`; use it only for final acceptance, never to tune prompts, rules, thresholds or the model choice. The harness has been exercised only with fake engines; no real-model result is recorded in this repository.

## Release acceptance

Follow [PACKAGING.md](PACKAGING.md) for real runtime assets, native builds and clean-machine acceptance. Check resource readiness before an acceptance build and recorded acceptance before distribution:

```sh
python scripts/check_release.py --resources-only
python scripts/check_release.py
```

These refuse the incomplete source-only bundle. Do not treat expected refusal as release acceptance or manufacture a record to bypass it. Current unresolved capabilities are in [../STATUS.md](../STATUS.md).
