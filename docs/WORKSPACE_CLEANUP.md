# Workspace cleanup inventory

Current-output migration audited: 2026-10-25; other candidates last audited
2026-10-21. Sizes are logical sizes and can overstate reclaimed
disk space where files are hard-linked. This document identifies cleanup
targets; it does not authorize blanket deletion of `training-work/`.

User decision, 2026-10-25: retain one current audiobook output per book and
replace regenerated parts progressively. Earlier full audio outputs are no
longer permanent archives. Preserve human notes and compact source/request/model/
output receipts, migrate the currently playable output to a self-contained
working set, then reclaim obsolete generated media that no current output,
active job or export needs. A raw stress report retains its heard-audio hash;
it does not by itself require keeping an entire earlier audiobook.

The retained paths below describe the pre-migration dependency inventory.
Retire old generated-output paths only after their current source, render and
listener dependencies have been migrated and verified. Human labeling archives,
source books, correction profiles, reviewed training media and production models
remain protected regardless of the output retention policy. Record scoped
removals and actual reclaimed physical space under `training-work/cleanup-reports/`.

## Canonical working set

Keep these paths. They are current product state, protected human work, or the
minimum reproducible production-model set:

- all of `manual-labeling/`;
- the app-local shared `pronunciation/user-corrections.sqlite3` profile and its
  SQLite sidecars (including any explicit `RIMSKY_CORRECTION_PROFILE` location);
- `training-work/character-rb2-full/`, `character-rb3-full/`, active book boards
  and their review drafts;
- `training-work/book1-listening-app/` and
  `training-work/elirm-iv-higgs-mvp/production-listener-library/`;
- `training-work/elirm-iv-higgs-mvp/source/` and its original book input;
- `training-work/elirm-iv-higgs-mvp/current-book-96c636410090662b89c2b38a53a795f4/`
  — the current book manifest, controls and compact render receipts; playable
  parts live once in `production-listener-library/audio/`;
- `training-work/higgs-production/models/custom-voice-plus-stress-scale-v1-q8_0.gguf`
  and its provenance, plus `custom-voice-plus-stress-scale-v1-native/`;
- `training-work/higgs-stress-pilot/runs/production-scale-continuation-v1/`,
  `production-scale-dataset-v1/`, `yo-cpt-ru-consensus-full-v3/` and the scale
  regression reports;
- current ASR, forced-aligner, stress-witness, emotion2vec and RUAccent support
  models; and
- `resources/music/` and `training-work/music/`.

The production-model source of truth is `resources/production-higgs-model.json`.
No directory name or modification time may override that manifest.

## Completed current-output migration

On 2026-10-25 the existing 2,024-part audiobook was adopted without copying or
regenerating audio. The current manifest was verified with access to all twelve
obsolete output roots forbidden. Those twelve roots were then removed after
their 29,692 non-audio files were archived and individually verified. The archive
is `training-work/cleanup-reports/superseded-elirm-book-output-receipts-20261025.zip`;
its member manifest records original paths, sizes and SHA-256 checksums.

The scoped removal reclaimed 10,515,156,288 hardlink-aware file bytes after the
251,291,851-byte receipt archive. Eleven previous library outputs were compacted
to receipts, and unreferenced audio/music plus obsolete playback caches were
collected. Source text, raw listener notes and all 2,022 distinct current speech
assets remained unchanged. Exact inventories and measured free-space changes
are in `training-work/cleanup-reports/`. Protected labeling, source books, voice
references, model files and user correction profiles were not cleanup targets.

Existing bare listening links resolve to the current book. Successful future
corrections discard their temporary generated WAVs after current publication;
failed work retains diagnostic evidence. Playback caches keep the current
requested layout and active readers/jobs, with a thirty-minute grace interval
for obsolete layouts.

## Safe cleanup candidates

Immediately before removal, verify that no `render_higgs*`, `audiocpp_cli` or
listener-retake worker is using the target. These paths are scratch data,
failed launches, or reproducible one-off outputs. Together they account for
about 2.6 GiB logical space, mostly the staging cache.

- `training-work/stg/` — 2.307 GiB content-addressed renderer scratch output;
- `training-work/elirm-iv-higgs-mvp-failed-launch-v4yo-20261021-b/` — 153.3 MiB;
- `training-work/elirm-iv-higgs-mvp-failed-launch-v4yo-20261021/` — negligible;
- `training-work/abort-relaunch-test/` — 1 MiB;
- `training-work/pending-witness-change/` — integrated source copies, negligible;
- `training-work/higgs-batch-size-test/` — 16 MiB;
- `training-work/higgs-stress-pilot/postprocess-smoke-v1/` — 0.5 MiB;
- `training-work/qwen-smoke-v1/` — 1 MiB;
- `training-work/qwen-ch2-first-pass-v1/` — 102 MiB;
- `training-work/elirm-iv-higgs-mvp/audio-palette-v4-ch123-abandoned-analysis-20261019-092001/`
  — 2.8 MiB;
- the `homograph-stress-v1-haiku-run-aborted`,
  `homograph-stress-v1-superseded-v1-lexicon` and
  `homograph-stress-v1-haiku-compare-superseded-v1-lexicon` directories under
  `training-work/elirm-iv-higgs-mvp/` — less than 0.5 MiB combined; and
- stopped-build caches: `src-tauri/target/`, `test-results/` and Python
  `__pycache__/` directories. Do not remove `dist/` while the listener server is
  serving it. `node_modules/` is rebuildable but is useful working state.

## Conditional archive or removal

These are large but carry reproduction or historical evidence. Preserve compact
manifests, hashes and reports, then run a generation/listener-report reachability
check before removing their audio or weights.

- `training-work/higgs-production/packs/voice-lora-v1-pending/` — 9.475 GiB,
  obsolete pack containing the old `3093...` voice-only checkpoint;
- old model generations — 25.326 GiB logical total:
  `custom-voice-lora-v1-native/`, `custom-voice-lora-v1-q8_0.gguf`,
  `custom-voice-plus-stress-v1-native/` and
  `custom-voice-plus-stress-v1-q8_0.gguf`;
- `training-work/higgs-production/models/base/` — 8.681 GiB upstream rebuild
  input, removable only if re-download is acceptable;
- any remaining unregistered preview and experiment output under
  `training-work/elirm-iv-higgs-mvp/`. The twelve registered output trees listed
  in the completed migration audit have already been removed; re-audit other
  experiments individually before treating them as unused;
- old stress-pilot candidate pools and adapters: `yo-cpt-ru-candidates-8h-v1/`,
  `yo-cpt-ru-candidates-8h-v2/`, `yo-cpt-ru-consensus-5h-v2/`,
  `runs/stress-smoke-v2/` and `runs/stress-two-epoch-v2/` — about 4 GiB logical;
- `yo-cpt-ru-candidates-full-v4/` — 4.567 GiB. Prune only audio not reachable
  from the latest consensus/dataset manifest; and
- legacy Qwen environments/models/runtimes — more than 10 GiB logical. They are
  still declared fallback/evaluation infrastructure and some scripts reference
  them, so remove only after that product decision and those references change.

The former `audio-palette-v4yo-ch123-oneshot/` used obsolete model SHA
`3093a099...`, rather than production SHA `78f09d20...`. Its non-audio records
are included in the completed migration archive; its generated audio was removed.
Never attribute those historical records to the current production checkpoint.

## Never infer junk from age

Do not remove a path merely because its name contains `old`, `pilot`, `cache` or
an earlier book number. Before cleanup, verify protected archive hashes, active
process command lines, generation IDs referenced by listener reports, hard-link
relationships and the current production-model manifest. Record the exact
deleted paths and actual reclaimed bytes in `training-work/cleanup-reports/`.
