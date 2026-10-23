$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$wifi = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.IPv4Address.IPAddress } |
    Select-Object -First 1
if (-not $wifi) { throw 'No active IPv4 network with a default gateway was found.' }
$address = $wifi.IPv4Address.IPAddress
$python = Join-Path $project 'training-work\.venv-tts\Scripts\python.exe'
Set-Location $project
Write-Host "Book 2 mobile labeling: http://${address}:1439/character-rb2-full/fine-tune-all/"
& $python -X utf8 -u scripts/serve_label_pilot.py --host 0.0.0.0 --port 1439 --board-folder character-rb2-full --audio-prefix character-rb2/full
