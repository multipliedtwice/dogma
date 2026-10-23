param([string]$Workspace = (Split-Path -Parent $PSScriptRoot), [string]$Config = '', [switch]$Once)
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$pythonPath = Join-Path $workspacePath 'training-work\.venv-tts\Scripts\python.exe'
$scriptPath = Join-Path $workspacePath 'scripts\supervise_local_services.py'
$logPath = Join-Path $workspacePath 'training-work\local-services'
New-Item -ItemType Directory -Force -Path $logPath | Out-Null
if (-not $Config) { $Config = Join-Path $logPath 'config.json' }
$configPath = (Resolve-Path -LiteralPath $Config).Path
if ($Once) {
    & $pythonPath -X utf8 -u $scriptPath --root $workspacePath --config $configPath --once
    exit $LASTEXITCODE
}
$arguments = '-X utf8 -u "{0}" --root "{1}" --config "{2}" --run' -f $scriptPath, $workspacePath, $configPath
$process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $workspacePath -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logPath 'supervisor.stdout.log') -RedirectStandardError (Join-Path $logPath 'supervisor.stderr.log')
Write-Output "Local service supervisor launched (PID $($process.Id)); readiness is recorded in training-work/local-services/state.json."
