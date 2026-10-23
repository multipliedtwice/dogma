param([switch]$Open, [string]$Generation)
$explicitGeneration = [bool]$Generation

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $workspace 'training-work\book1-listening-app'
$python = Join-Path $workspace 'training-work\.venv-tts\Scripts\python.exe'
$watchPython = Join-Path $workspace 'training-work\.venv-asr\Scripts\python.exe'
$candidate = Join-Path $data 'reported-stress-candidate.json'
$active = Join-Path $data 'active-listening-generation.json'
if (-not $Generation) {
    $Generation = if (Test-Path -LiteralPath $active) { (Get-Content -LiteralPath $active -Raw | ConvertFrom-Json).generation_id } elseif (Test-Path -LiteralPath $candidate) { (Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json).candidate_generation } else { '1b15b833cc8ee135b527c30c' }
}
$generation = $Generation
$port = 1421

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $data 'library.sqlite3'))) {
    throw 'The Book 1 listening environment has not been staged.'
}

function Get-LiveToken($log) {
    try {
        $first = Get-Content -LiteralPath $log -TotalCount 1 -ErrorAction Stop | ConvertFrom-Json
        if ($first.port -ne $port -or -not $first.token) { return $null }
        $body = @{ op = 'health'; data = @{} } | ConvertTo-Json -Compress
        $headers = @{ Authorization = "Bearer $($first.token)" }
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api" -Method Post -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 2
        return $first.token
    } catch {
        return $null
    }
}

$token = $null
$tokenLog = $null
foreach ($candidate in @(Get-ChildItem -LiteralPath $data -Filter 'runtime.stdout*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {
    $token = Get-LiveToken $candidate.FullName
    if ($token) { $tokenLog = $candidate.FullName; break }
}

if (-not $token) {
    $tokenLog = Join-Path $data 'runtime.stdout.run.log'
    $stderrLog = Join-Path $data 'runtime.stderr.run.log'
    $server = Start-Process -FilePath $python -ArgumentList @('runtime/server.py', '--data-dir', $data, '--port', "$port", '--dev', '--persistent-dev-session', '--static', (Join-Path $workspace 'dist')) -WorkingDirectory $workspace -RedirectStandardOutput $tokenLog -RedirectStandardError $stderrLog -WindowStyle Hidden -PassThru
    $server.Id | Set-Content -LiteralPath (Join-Path $data 'runtime.pid')
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Milliseconds 250
        $token = Get-LiveToken $tokenLog
        if ($token) { break }
        if ($server.HasExited) { throw "The listening app exited. See $stderrLog" }
    }
    if (-not $token) { throw "The listening app did not become ready. See $stderrLog" }
}

$demoPath = Join-Path $data 'qwen-17b-demo.json'
$demo = if (Test-Path -LiteralPath $demoPath) { Get-Content -LiteralPath $demoPath -Raw | ConvertFrom-Json } else { $null }
$higgsPlans = @(
    (Join-Path $workspace 'training-work\higgs-tts-3-pilot\book1-demo.json'),
    (Join-Path $workspace 'training-work\higgs-tts-3-pilot\book1-half\plan.json'),
    (Join-Path $workspace 'training-work\higgs-tts-3-pilot\book1-half-sentence\plan.json')
)
$externalReview = $false
foreach ($planPath in $higgsPlans) {
    if (Test-Path -LiteralPath $planPath) {
        $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
        if ($plan.generation_id -eq $generation) { $externalReview = $true; break }
    }
}
$reviewOnlyDemo = $externalReview -or (-not $explicitGeneration -and $demo -and $demo.generation_id -eq $generation -and $demo.status -eq 'ready_for_review')
if (-not $reviewOnlyDemo) {
    $headers = @{ Authorization = "Bearer $token" }
    $body = @{ op = 'resume'; data = @{ id = $generation } } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api" -Method Post -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 30
}

$watchPidPath = Join-Path $data 'watch.pid'
$watchGenerationPath = Join-Path $data 'watch.generation'
$watchPid = if (Test-Path -LiteralPath $watchPidPath) { Get-Content -LiteralPath $watchPidPath -TotalCount 1 } else { $null }
$watchedGeneration = if (Test-Path -LiteralPath $watchGenerationPath) { Get-Content -LiteralPath $watchGenerationPath -TotalCount 1 } else { $null }
if ($watchPid -and $watchedGeneration -and $watchedGeneration -ne $generation) {
    $oldWatcher = Get-Process -Id $watchPid -ErrorAction SilentlyContinue
    if ($oldWatcher -and $oldWatcher.Path -eq $watchPython) { Stop-Process -Id $watchPid }
    $watchPid = $null
}
if (-not $reviewOnlyDemo -and (-not $watchPid -or -not (Get-Process -Id $watchPid -ErrorAction SilentlyContinue))) {
    $watcher = Start-Process -FilePath $watchPython -ArgumentList @((Join-Path $workspace 'scripts\watch_book1_listening.py'), $generation, '--data-dir', $data, '--token-log', $tokenLog, '--port', "$port") -WorkingDirectory $workspace -RedirectStandardOutput (Join-Path $data 'watch.stdout.log') -RedirectStandardError (Join-Path $data 'watch.stderr.log') -WindowStyle Hidden -PassThru
    $watcher.Id | Set-Content -LiteralPath $watchPidPath
    $generation | Set-Content -LiteralPath $watchGenerationPath
}

$bookId = '676d6ad200230b7cfde56a63fd1d8d28'
$url = "http://127.0.0.1:$port/?book=$bookId&generation=$generation#port=$port&token=$token"
if ($Open) { Start-Process $url }
Write-Output $url
