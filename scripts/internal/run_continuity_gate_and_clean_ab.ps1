param(
    [string]$ParentExperiment = "D:\nautilus\paper_trading\experiments\paper_20260928_4c9ee2b28d67",
    [Parameter(Mandatory = $true)][string]$ContinuityExperiment,
    [string]$CandidateId = "pc_2fe14acb95eac19f88d4",
    [string]$AuditRoot = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair",
    [string]$DeliveryRoot = "D:\nautilus\outputs\deliverables\direct_maker_24h_clean_ab"
)

$ErrorActionPreference = "Stop"
$repo = "D:\nautilus"
$python = "$repo\.venv\Scripts\python.exe"
$runner = "$repo\scripts\internal\run_focused_continuity.ps1"
$prepare = "$repo\scripts\internal\prepare_focused_paper_experiment.py"
$package = "$repo\scripts\internal\package_demo_paper_ab_resolution.py"
$gateStatus = Join-Path $AuditRoot "gate_status.json"
New-Item -ItemType Directory -Force -Path $AuditRoot | Out-Null

function Write-GateStatus([hashtable]$value) {
    $value.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    $value | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $gateStatus
}

Write-GateStatus @{
    status = "RUNNING_2H_CONTINUITY_TEST"
    continuity_experiment = $ContinuityExperiment
    production_exchange_orders = 0
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
    -Experiment $ContinuityExperiment -Phase continuity_test `
    -DurationSeconds 7200 -ExpectedBars 120 -CandidateId $CandidateId
$continuityCode = $LASTEXITCODE
$worker = Join-Path $ContinuityExperiment "workers\BTCUSDT"
$validationPath = Join-Path $worker "dry_run_validation.json"
if (!(Test-Path $validationPath)) {
    Write-GateStatus @{status = "BLOCKED_2H_NO_VALIDATION"; exit_code = $continuityCode}
    exit 2
}
$validation = Get-Content $validationPath -Raw | ConvertFrom-Json
$observed = [int]$validation.summary.observed_bars
$missing = [int]$validation.summary.unexplained_missing_bars
Copy-Item (Join-Path $worker "market_data_continuity_monitor.csv") `
    (Join-Path $AuditRoot "two_hour_continuity_test.csv") -Force
if ($continuityCode -ne 0 -or $validation.status -ne "PASSED" -or $observed -ne 120 -or $missing -ne 0) {
    Write-GateStatus @{
        status = "BLOCKED_2H_CONTINUITY_TEST"
        exit_code = $continuityCode
        expected_bars = 120
        observed_bars = $observed
        unexplained_missing_bars = $missing
    }
    exit 2
}

$stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd_HHmmss")
$experimentId = "paper_clean_ab_$stamp"
$newExperiment = Join-Path "$repo\paper_trading\experiments" $experimentId
& $python $prepare --parent $ParentExperiment --output $newExperiment `
    --experiment-id $experimentId --candidate-id $CandidateId --purpose "CLEAN_24H_DIRECT_MAKER_AB"
if ($LASTEXITCODE -ne 0) {
    Write-GateStatus @{status = "BLOCKED_PREPARE_24H"; experiment_id = $experimentId}
    exit 2
}
Write-GateStatus @{
    status = "RUNNING_CLEAN_24H"
    two_hour_status = "PASSED"
    two_hour_expected_bars = 120
    two_hour_observed_bars = 120
    unexplained_missing_bars = 0
    new_experiment_id = $experimentId
    new_experiment_path = $newExperiment
    production_exchange_orders = 0
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
    -Experiment $newExperiment -Phase authoritative_24h `
    -DurationSeconds 86400 -ExpectedBars 1440 -CandidateId $CandidateId
$runCode = $LASTEXITCODE
$newWorker = Join-Path $newExperiment "workers\BTCUSDT"
$newValidationPath = Join-Path $newWorker "dry_run_validation.json"
if ($runCode -ne 0 -or !(Test-Path $newValidationPath)) {
    Write-GateStatus @{
        status = "BLOCKED_CLEAN_24H"
        new_experiment_id = $experimentId
        runner_exit_code = $runCode
    }
    exit 2
}
$newValidation = Get-Content $newValidationPath -Raw | ConvertFrom-Json
if ($newValidation.status -ne "PASSED" -or [int]$newValidation.summary.observed_bars -ne 1440 -or `
    [int]$newValidation.summary.unexplained_missing_bars -ne 0) {
    Write-GateStatus @{
        status = "BLOCKED_CLEAN_24H_COVERAGE"
        new_experiment_id = $experimentId
        observed_bars = [int]$newValidation.summary.observed_bars
        unexplained_missing_bars = [int]$newValidation.summary.unexplained_missing_bars
    }
    exit 2
}

& $python "$repo\scripts\internal\replay_paper_experiment.py" `
    --repo $repo --experiment $newExperiment --phase-root $newWorker --candidate-id $CandidateId
if ($LASTEXITCODE -ne 0) {
    Write-GateStatus @{status = "BLOCKED_REPLAY"; new_experiment_id = $experimentId}
    exit 2
}
if (Test-Path $DeliveryRoot) {
    $archive = "$DeliveryRoot.superseded.$stamp"
    Move-Item $DeliveryRoot $archive
}
& $python $package --source-experiment $newExperiment --output $DeliveryRoot
if ($LASTEXITCODE -ne 0) {
    Write-GateStatus @{status = "BLOCKED_PACKAGING"; new_experiment_id = $experimentId}
    exit 2
}

$case = Join-Path $DeliveryRoot "dynamic_breakout_short\BTCUSDT_1m_STRICT_REVERSE"
Copy-Item (Join-Path $case "comparison\execution_comparison.csv") $DeliveryRoot -Force
Copy-Item (Join-Path $case "01_DIRECT\mode_summary.csv") (Join-Path $DeliveryRoot "direct_summary.csv") -Force
Copy-Item (Join-Path $case "02_MAKER\mode_summary.csv") (Join-Path $DeliveryRoot "maker_summary.csv") -Force
Copy-Item (Join-Path $newWorker "daily_turnover.csv") $DeliveryRoot -Force
Copy-Item (Join-Path $newWorker "data_quality_summary.csv") $DeliveryRoot -Force
Copy-Item (Join-Path $newWorker "replay_validation.csv") $DeliveryRoot -Force
Copy-Item (Join-Path $case "comparison\comparison.png") $DeliveryRoot -Force

Write-GateStatus @{
    status = "PASSED"
    two_hour_status = "PASSED"
    new_experiment_id = $experimentId
    new_24h_status = "COMPLETED"
    expected_bars = 1440
    observed_bars = 1440
    replay_mismatches = 0
    result = $DeliveryRoot
    production_exchange_orders = 0
    seven_day_run = "NOT_STARTED"
}
exit 0
