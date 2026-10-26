# One-shot demo generation

For new public excerpts, use `scripts/generate_public_demo_current.py`. It prepares
Russian through the current runtime recipe: Silero/RUAccent stress with pinned
Opus disagreements, conditional Luna е/ё, source-typed heading stress and frozen
module/model caches. Shared human instructions apply after automatic preparation;
the recorded deterministic phonetic layer follows. English avoids Russian text
analysis and still consumes its frozen shared human profile.

```powershell
training-work/.venv-asr/Scripts/python.exe -X utf8 -u scripts/generate_public_demo_current.py --folder training-work/public-demos/current-en-alice-tea-party --output training-work/public-demos/current-en-alice-tea-party --stage all
```

The source workspace must already contain the exact prepared book and completed
structure/casting analysis. `music/selected.json` supplies previously checked cues
and honest reuse provenance. The controller supervises preparation, freeze and
render; `supervision.json` and `progress.json` record actual worker identities,
counts and exits. Temperature 0.8, top-k 30 and top-p 0.8 are frozen sound inputs.
There is one completed native take per passage. Explicit incomplete native runs
can receive a bounded cap extension and then seed+1; unknown dispatch holds, and
unstarted batch suffixes retain their original requests. These are disclosed
technical retries, never recognition/quality retakes. No ASR or acoustic audit runs.

One stable light player and `public/current.mp3`/`current.wav` remain current per
book. Checked temporary mixes replace those files atomically; compact metadata
receipts do not retain complete earlier audio prefixes. Playback bookmarks use
the source identity. Recognition stays `not_checked`, with no human or production
approval. The approved eight-second opening is unchanged; cached music uses
20-second phrases, four-second gaps and 0.9 amplitude relative to −30 dBFS.

The following older entrypoint remains available for its existing frozen runs.

The earlier `scripts/render_public_demo_one_shot.py` entrypoint remains for
existing frozen listening runs. It consumes their preprocessing and generates one native Higgs
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
âˆ’30.9151498 dBFS and ending music is also reduced by 10% in amplitude. The approved
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

User decision, 2026-10-25: restore the previous stress pipeline. Silero
homograph marks are cross-checked with RUAccent, and pinned Claude Opus 5.5 at
high effort resolves their disagreements. The blanket Luna dictionary-context
stress overlay is disabled for new preparation. Its historical decisions,
provider evidence and audio remain immutable; do not resume a frozen generation
under a changed recipe.

`scripts/build_yo_restoration.py` continues to send every ambiguous е/ё
occurrence to isolated `gpt-6-luna` at low effort, including tagger agreement.
Dictionary-certain spellings and explicit source marks remain authoritative.
Its first opinion includes self-rated confidence; every proposed change from
the categorical Silero witness gets an independent second check. Missing or
unmarked witnesses also require that check. The second valid YO opinion wins
on disagreement; retain both opinions and reasons. Confidence never controls
routing. Malformed or unbound answers hold preparation; only transport errors
receive bounded retries. Provider/model provenance and CLI isolation remain
required, with no silent fallback.

For public-demo preparation, `scripts/prepare_public_demo_pronunciation_v4.py`
uses recipe `public-demo-luna-yo-opus-stress-v4`. After Luna chooses е/ё, the
separate Opus finite-verb stress pass preserves that exact spelling. It cannot
replace the older bound Opus homograph decision silently: conflicts hold the
preparation. No blanket dictionary-context Luna stress call runs.

For private chapter debugging, `scripts/debug_higgs_one_shot.py` prepares a new
sibling generation, verifies source slices and binds HS/YO answers to the final
unit texts. It freezes a disabled contextual-stress receipt instead of requesting
Luna stress decisions. Every generation consumes a frozen shared human-correction
snapshot. Explicit listener corrections apply after automatic stress/YO and before
the final synthesis-only phonetic stage. Proper-name corrections cover the full
name family; ordinary contextual corrections remain occurrence-bound.

To restore an already delivered Luna-stress generation, the listener repair helper
uses the full retained pre-context text and source-bound automatic evidence. It
replays the old contextual overlay and requires exact agreement with the archived
result before accepting that baseline. Then it applies the latest human notes,
recomputes phonetics and regenerates only changed/requested units. Missing proof
holds restoration rather than guessing. Music, voice references, source/captions,
model pin and post-processing mode remain unchanged.

The closed whole-word phonetic rule maps кого → каво and никого → никаво,
preserving source text, case and stress. It does not alter коготь or коготки.
Each native request records prephonetic text, substitutions and implementation
checksum. Basic freeze validation recomputes that receipt.

Example preparation without speech generation:

```powershell
$debugGeneration = 'training-work/elirm-iv-higgs-mvp/audio-palette-restored-stress-ch123'
training-work/.venv-asr/Scripts/python.exe -X utf8 -u scripts/debug_higgs_one_shot.py --source-generation training-work/elirm-iv-higgs-mvp/audio-palette-stress-scale-v1-yo-hard-e-ch123-oneshot --output $debugGeneration --chapters c0001 c0002 c0003 --stage prepare
```

The historical `build_contextual_stress.py` and `report_pronunciation_ambiguities.py`
remain available for inspecting preserved experiments. They are not the default
preparation path or an instruction to reinstate Luna stress authority. Their private
reports describe text decisions, never auditory or human approval.

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
