param([string]$Workspace = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$taskName = 'Dogma Local Services'
$description = 'Keeps the configured local Dogma labeling and listening services available.'
$pythonPath = Join-Path $workspacePath 'training-work\higgs-production\.venv\Scripts\pythonw.exe'
$supervisorPath = Join-Path $workspacePath 'scripts\supervise_local_services.py'
$configPath = Join-Path $workspacePath 'training-work\local-services\config.json'
foreach ($requiredPath in @($pythonPath, $supervisorPath, $configPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Missing local service file: $requiredPath" }
}
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.Description -ne $description) { throw 'An unrelated task already uses this task name.' }
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-X utf8 -u "{0}" --root "{1}" --config "{2}" --run' -f $supervisorPath, $workspacePath, $configPath
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $workspacePath
$logon = New-ScheduledTaskTrigger -AtLogOn -User $account
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$null = Register-ScheduledTask -TaskName $taskName -Description $description -Action $action -Trigger @($logon, $repeat) `
    -Principal $principal -Settings $settings -Force
Start-ScheduledTask -TaskName $taskName
Write-Output "Installed and started '$taskName' for $account."
