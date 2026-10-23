param([int]$WaitForProcessId = 0, [switch]$SentenceSafe)

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $workspace 'training-work\.venv-tts\Scripts\python.exe'
$renderer = Join-Path $workspace 'scripts\render_book1_higgs_half.py'
$folder = Join-Path $workspace $(if ($SentenceSafe) { 'training-work\higgs-tts-3-pilot\book1-half-sentence' } else { 'training-work\higgs-tts-3-pilot\book1-half' })
$log = Join-Path $folder 'background.log'

if ($WaitForProcessId -gt 0) {
    Wait-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue
}

Set-Location -LiteralPath $workspace
$env:PYTHONIOENCODING = 'utf-8'
$env:RIMSKY_HIGGS_SENTENCE_SAFE = if ($SentenceSafe) { '1' } else { '0' }
"START $(Get-Date -Format o)" | Add-Content -LiteralPath $log -Encoding UTF8
& $python $renderer *>> $log
$exit = $LASTEXITCODE
"EXIT $exit $(Get-Date -Format o)" | Add-Content -LiteralPath $log -Encoding UTF8
exit $exit
