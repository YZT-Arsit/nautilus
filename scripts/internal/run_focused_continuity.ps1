param(
    [Parameter(Mandatory = $true)][string]$Experiment,
    [Parameter(Mandatory = $true)][ValidateSet("continuity_test", "authoritative_24h")][string]$Phase,
    [Parameter(Mandatory = $true)][int]$DurationSeconds,
    [Parameter(Mandatory = $true)][int]$ExpectedBars,
    [string]$CandidateId = "pc_2fe14acb95eac19f88d4"
)

$ErrorActionPreference = "Stop"
$env:HTTPS_PROXY = "http://127.0.0.1:18898"
$env:HTTP_PROXY = $env:HTTPS_PROXY
$env:NO_PROXY = "127.0.0.1,localhost"
$repo = "D:\nautilus"
$python = "$repo\.venv\Scripts\python.exe"
$runner = "$repo\scripts\run_paper_orchestrator.py"
$log = Join-Path $Experiment "health\runner.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Set-Location $repo

& $python $runner `
    --repo $repo `
    --experiment $Experiment `
    --phase $Phase `
    --duration-seconds $DurationSeconds `
    --candidate-id $CandidateId `
    --symbols BTCUSDT `
    --worker-id BTCUSDT `
    --align-minute `
    --expected-bars $ExpectedBars `
    --quote-stale-seconds 10 `
    --trade-stale-seconds 10 `
    $(if ($Phase -eq "authoritative_24h") { "--freeze-start" }) 2>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE
exit $code
