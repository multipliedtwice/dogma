# One-shot demo generation

The default development-demo entrypoint is now `scripts/render_public_demo_one_shot.py`.
It supersedes the legacy renderer's audit/recovery `all` command for new listening
renders. It consumes existing frozen preprocessing and generates one native Higgs
take per source unit. It runs no speech recognition, acoustic quality/stress checks,
witness, adjudication, repair, or automatic retake. It does not modify production
runtime code, model weights, source assignments, protected labels, or old attempts.

Run with the existing Python environment, using a separate output directory:

```powershell
training-work/.venv-asr/Scripts/python.exe -X utf8 -u scripts/render_public_demo_one_shot.py --source training-work/public-demos-20261024-one-shot/ru-chameleon --output training-work/public-demos-20261024-one-shot/rendered/ru-chameleon --prepared-overrides training-work/public-demos-20261024-one-shot/listener-preparation-corrections.json
```

For English, use `--source training-work/public-demos-20261023/en-gift-of-magi`
and a separate `rendered/en-gift-of-magi` output, without the Russian overlay.
`--stage plan` freezes inputs without synthesis. `--stage render`, `publish`, and
the default `all` provide explicit execution stages; no post-processing switch is
present. Root review and GPU ownership checks precede the actual demonstration run.

The immutable run key binds source preparation, optional explicit listener correction
file and exact native input, reviewed references, model/provenance, native runner and
DLLs, sampling, implementation, player, and music settings. Each native session gets
a durable physical-start record before launch. Resume reuses existing output; a
reserved unit without a WAV becomes a technical failure and is never automatically
generated again. Other pending units continue. Live native identity, log and heartbeat
are recorded locally; the controller owns its timeout and child cleanup.

Technical checks require nonempty finite mono 24 kHz PCM16 speech. Whole native
samples enter playback without trimming, speech fades, normalization, or stretching.
Source caption text is retained; timings describe the new first-take audio. Approved
matching-language opening stays unchanged. Existing music uses 20-second phrases
with four-second gaps. `--music-volume-scale` defaults to `0.9`: beds are therefore
−30.9151498 dBFS and ending music is also reduced by 10% in amplitude. The approved
opening is exempt from that volume change.

`public/` contains a local player, immutable WAV/MP3/version records, and `latest.json`.
A contiguous prefix becomes available after five minutes, or when the whole short
book is rendered. `render_complete` means source coverage only. Status always says
`recognition_status: not_checked`, `machine_verified: false`, and no human approval.
It never writes `complete.json`, verified counts, or a zero-recognition-error claim.
This unreviewed listening export is intentionally not a `completed_book` package;
do not feed it through the legacy verified-demo exporter or present it as audited.
Keep private run/reference metadata local. Publish only the player/audio and its
sanitized status when explicitly authorized. Atomic metadata writes use unique
temporary files and bounded retries for Windows reader sharing violations. Other
write errors remain visible; this does not authorize another synthesis attempt.

## Private chapter debugging in the local reader

`scripts/debug_higgs_one_shot.py` prepares a new, private sibling generation for
chapter debugging. It checks every original source slice, installs the current
number policy before looking up spoken corrections, and retains only exactly
bound listener/editorial corrections. Homograph stress and YO v4 decisions are
rebuilt on the final unit texts; old answers are not rebound to changed wording.
Every adjudicated batch must report actual Opus 5.5 high usage and CLI provenance.
Existing frozen generations remain unchanged.

For example, prepare Chapters 1–3 without starting speech generation:

```powershell
$debugGeneration = 'training-work/elirm-iv-higgs-mvp/audio-palette-yo-v4-fresh-ch123-oneshot-20261024'
training-work/.venv-asr/Scripts/python.exe -X utf8 -u scripts/debug_higgs_one_shot.py --source-generation training-work/elirm-iv-higgs-mvp/audio-palette-stress-scale-v1-yo-hard-e-ch123-oneshot --output $debugGeneration --chapters c0001 c0002 c0003 --stage prepare
```

After preparation and queue review, use the durable supervisor with **both**
current public-demo outputs. It waits for their complete, stopped-worker evidence
before launching the private adapter; the preparation environment's name does not
mean an ASR model is used:

```powershell
training-work/.venv-asr/Scripts/python.exe -X utf8 -u scripts/supervise_debug_higgs_one_shot.py --output $debugGeneration --data-dir training-work/elirm-iv-higgs-mvp/production-listener-library --demo-output training-work/public-demos-20261024-yo-v4-rerender/rendered/ru-chameleon --demo-output training-work/public-demos-20261024-yo-v4-rerender/rendered/en-gift-of-magi
```

The adapter freshly verifies the pinned `custom-voice-plus-stress-scale-v1` model
before every native session. Each speech unit has one physical first take, with
whole native samples and basic format/nonfinite checks only: no ASR, acoustic
stress/quality gate, recovery or retake. Unknown physical-worker state fails closed;
supervisor restarts resume checkpoints and cannot authorize another take. Inspect
`one-shot-supervision/status.json`, its adapter logs and native worker identities
for actual liveness. A launched process is not evidence of completion.

Chapter-ordered speech publishes progressively after five minutes or a complete
short chapter. The local Store bridge keeps playback available with
`post_processing: off`, `recognition_status: not_checked`, and machine, human and
production approval all false. It preserves source text and analysis parts.
Cached scene/ending music remains separate at 0.9 amplitude; no new music inference
or vocals check runs. The approved matching-language opening is attached once,
separately from chapter speech. Supervisor completion requires every requested
first-take asset, complete chapter coverage, registered cached music and stopped
workers; it means technical delivery, never recognition or listening approval.

All book text, preparation/provider records, reference material, audio and Store
data stay under ignored `training-work/`. Do not export this private debug book
through the public-demo website or remove its checkpoints as disposable cache.
