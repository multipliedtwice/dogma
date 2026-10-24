# Workspace cleanup inventory

Last audited: 2026-10-21. Sizes are logical sizes and can overstate reclaimed
disk space where files are hard-linked. This document identifies cleanup
targets; it does not authorize blanket deletion of `training-work/`.

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
- `training-work/elirm-iv-higgs-mvp/audio-palette-stress-scale-v1-ch123-oneshot/`;
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
- old preview and experiment generations under
  `training-work/elirm-iv-higgs-mvp/` — 12.414 GiB logical. Retain compact
  manifests and listener evidence; do not blanket-delete
  `audio-palette-v3-ch123/`;
- old stress-pilot candidate pools and adapters: `yo-cpt-ru-candidates-8h-v1/`,
  `yo-cpt-ru-candidates-8h-v2/`, `yo-cpt-ru-consensus-5h-v2/`,
  `runs/stress-smoke-v2/` and `runs/stress-two-epoch-v2/` — about 4 GiB logical;
- `yo-cpt-ru-candidates-full-v4/` — 4.567 GiB. Prune only audio not reachable
  from the latest consensus/dataset manifest; and
- legacy Qwen environments/models/runtimes — more than 10 GiB logical. They are
  still declared fallback/evaluation infrastructure and some scripts reference
  them, so remove only after that product decision and those references change.

`audio-palette-v4yo-ch123-oneshot/` is 1.131 GiB and was rendered with obsolete
model SHA `3093a099...`, not the pinned production SHA `78f09d20...`. Keep it
temporarily as immutable listener/debug evidence; never present it as output of
the current production checkpoint, and archive or remove it after its reports
and useful state have been migrated.

## Never infer junk from age

Do not remove a path merely because its name contains `old`, `pilot`, `cache` or
an earlier book number. Before cleanup, verify protected archive hashes, active
process command lines, generation IDs referenced by listener reports, hard-link
relationships and the current production-model manifest. Record the exact
deleted paths and actual reclaimed bytes in `training-work/cleanup-reports/`.
