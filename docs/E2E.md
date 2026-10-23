# Full-app E2E

Lead runs this gate. Keep it foreground. One run at a time. No CI.

## Need

- Node 24. Python 3.12. Install `runtime/requirements.txt` in a virtual environment.
- Install project dev dependencies. Lead regenerates the local lock after dependency edits. Then use `npm ci`.
- Install Chromium with `npm run e2e:install`. Linux may also need Playwright's documented browser OS packages.
- Put **ffmpeg and ffprobe** on PATH. Export specs fail without them. Never skip.

`@playwright/test` is exactly **1.58.2**. Its [browser metadata](https://raw.githubusercontent.com/microsoft/playwright/v1.58.2/packages/playwright-core/browsers.json) pins Chromium and headless shell revision **1208**. Do not point at a random Chrome executable.

Set the Python interpreter. No activation needed:

```sh
export E2E_PYTHON="$PWD/.venv/bin/python"
npm run e2e:install
npm run e2e:check
npm run e2e:list
npm run e2e
```

PowerShell: `$env:E2E_PYTHON = "$PWD\.venv\Scripts\python.exe"`. Default is `python3` on macOS/Linux, `python` on Windows.

Focused foreground run:

```sh
npm run e2e -- e2e/specs/reading.spec.ts
npm run e2e:headed -- e2e/specs/playback.spec.ts
```

Do not raise workers. Do not bypass the guard. Do not add `&`, daemon runners or retries to hide failures.

## Guard

`e2e/guard.mjs` runs before every E2E script. Global setup checks again before building. Check CPU activity/load, usable memory/pressure, checkout/temp disk, competing tests/builds and an ephemeral loopback bind. No fixed ports, containers or external services needed.

Refuse at 90% CPU busy, load above 1.5 per available core, less than 1 GiB or 8% usable memory, high memory pressure, or less than 2 GiB free disk. Refuse competing Playwright, fixture, run-e2e, Cypress or build/test processes. Ignore self and ancestors. Unsupported or unreadable resource checks fail closed. Stop competing work yourself, free resources, then retry. Nothing sends measurements or keeps a resource log.

## Real app, fake models

Global setup typechecks and builds production `dist` once. It writes tiny deterministic EPUB, FB2, malformed inputs and a PNG into a temporary directory. No binary fixtures live in source.

Each test gets a new Python `Application`, new temporary library and tiny verified `pack_fixture`. The real runtime handler serves `dist` and the real API on `127.0.0.1`, port 0. No Vite dev server. Browser opens the printed port/token fragment. Tokens stay per-launch and temporary.

The launcher reuses `SyntheticEngine` analysis and device probe. It turns the cast review stop off on every start, so generation flows run straight to ready; `review.spec.ts` turns it on through Settings. Fixture analysis finds no characters, so the review cast is narrator-only. Character rename, merge, kind and voice changes are not exercised here. Only rendering writes a controlled 24-second mono WAV. The core accepts that duration without a plausibility retry only for passages with at least 5 letters or digits; a shorter fixture passage would be rendered twice. Pack health uses the existing verification seam; missing real inference modules do not block these fake jobs. FFmpeg/FFprobe health stays real. Illustration instance `preflight` and `renderer` use a deterministic local PNG fake. Real Image API calls fail closed. Real consent, job state, storage, hashing, export and UI remain intact.

`runtime.api(op, data)` reads the real authenticated API using source types. Browser actions drive mutations. Test-only stdin controls hold/release renderers, wait for a hold, fail the next illustration, or remove one ready WAV from this test's library. No test-control HTTP endpoint. No arbitrary file-delete control.

Startup stdout: exactly one JSON line, `port` and `token`. Stderr keeps failures plus prefixed control acknowledgements. Fixture teardown closes the page and stdin, sends SIGTERM where supported, waits, then kills its owned process tree only when required. Forced shutdown fails the test. Runtime restart retains that test's library, rotates token/port, and reopens the real app. Normal teardown removes library and pack; global teardown removes generated inputs.

## Coverage

| File | Checks |
|---|---|
| `smoke.spec.ts` | Valid-token built app; missing-token error |
| `reading.spec.ts` | Chooser EPUB/FB2, invalid input, library/chapter search, reading, bookmarks, links, preferences/position reload, Russian, deletion |
| `review.spec.ts` | Settings switch `Stop for cast review before narration`, generation stops at `review` with no ready passage, dock prompt, Review cast dialog, Start narration, ready |
| `playback.spec.ts` | Fake chapter ready, real WAV playback, pause/seek/skip/chapter transport, rate/volume, subtitles, sleep deadline, reload/restart position, controlled preparation pause/resume, missing WAV and Repair |
| `exports.spec.ts` | Browser downloads, SRT/VTT text/timing, MP3/M4B ffprobe, M4B chapters, ZIP contents and chapter audio/captions |
| `illustrations.spec.ts` | Paragraph attachment, inline image/text jump, consent, held progress, failure, explicit retry, saved success |

Sleep test advances browser time only. Media stays real. Rendering holds use acknowledgements, not guessed sleeps. No skipped or pending specs.

## Output and limits

Chromium only. One worker. Autoplay allowed. English selectors except the Russian test. Screenshot/video off. Trace retained only on failure; trace screenshots off. Test output goes under `test-results/`; reports and blob reports stay ignored and excluded from source releases. Traces may contain a temporary session token. Do not publish them as source.

No native Tauri windows/dialogs/file associations. No real Qwen/llama/Codex/Image API. No user `audio-book.epub`. Passing these tests does not prove voice quality, native acceptance or external provider availability. Keep existing `verification/browser_*.py` checks; this suite does not replace them.
