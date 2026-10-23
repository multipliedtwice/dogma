# Local labeling and listening services

The Windows task **Dogma Local Services** keeps the configured local labeling
and listening servers available. It runs at sign-in and checks for a missing
supervisor every minute. Its execution time is unlimited and duplicate task
instances are ignored. The supervisor checks services every ten seconds.

Machine-specific commands and health-check credentials paths live in
`training-work/local-services/config.json`. This configuration, service logs,
PID/health state and all labeling data stay local and are excluded from public
source releases. The status file is `training-work/local-services/state.json`.

Run a read-only readiness check from the project directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start_local_services.ps1 -Once
```

To install or repair the current user's automatic startup task after restoring
the private configuration and Python environments:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_local_services_task.ps1
```

The execution-policy override applies only to that process. It does not alter
the machine's PowerShell policy. The task uses the current interactive Windows
account and a hidden Python interpreter.

The supervisor adopts matching healthy processes. It starts only missing
services, with bounded retries and backoff. It never kills an unknown port
owner or a live unresponsive server. Future listener-runtime starts use
`--no-auto-resume`, so restoring listening availability does not automatically
resume generation. Default desktop runtime behavior is unchanged.

Current port roles are labeling on 1439 and 1440, the LAN audiobook gateway
on 1442, and its private loopback runtime on 1443. Book navigation on either
labeling server preserves each board's separate working review and archive.

Open `/` on port 1442 for the shared Dogma home page. It lists rendered books
and resumes the saved listening version. Local visitors also get links to all
three labeling boards. The roots on 1439 and 1440 provide the same navigation.
Labeling Home links save pending edits before leaving; the hosted player opens
Home in a separate tab so playback can continue. Public tunnel visitors keep
the gateway's authentication and do not receive local labeling links.
