param(
    [string]$ParentExperiment = "D:\nautilus\paper_trading\experiments\paper_20260928_4c9ee2b28d67",
    [string]$CandidateId = "pc_2fe14acb95eac19f88d4",
    [string]$AuditRoot = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair",
    [string]$DeliveryRoot = "D:\nautilus\outputs\deliverables\direct_maker_24h_clean_ab",
    [string]$PassedRegressionExperiment = ""
)

$ErrorActionPreference = "Stop"
$repo = "D:\nautilus"
$python = "$repo\.venv\Scripts\python.exe"
$runner = "$repo\scripts\internal\run_focused_continuity.ps1"
$prepare = "$repo\scripts\internal\prepare_focused_paper_experiment.py"
$package = "$repo\scripts\internal\package_demo_paper_ab_resolution.py"
$faultInjector = "$repo\scripts\internal\validate_p0_recovery_faults.py"
$statusPath = Join-Path $AuditRoot "long_horizon_gate_status.json"
New-Item -ItemType Directory -Force -Path $AuditRoot | Out-Null

function Write-Status([hashtable]$value) {
    $value.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    $value.production_exchange_orders = 0
    $value.p1 = "FROZEN_PASSED"
    $value.demo = "WAITING_FOR_CREDENTIALS"
    $value.other_symbol_forward = "NOT_STARTED"
    $value.seven_day = "NOT_STARTED"
    $temp = "$statusPath.tmp"
    $value | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $temp
    Move-Item -Force $temp $statusPath
}

function New-FocusedExperiment([string]$prefix, [string]$purpose) {
    $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd_HHmmss")
    $id = "${prefix}_${stamp}"
    $path = Join-Path "$repo\paper_trading\experiments" $id
    & $python $prepare --parent $ParentExperiment --output $path `
        --experiment-id $id --candidate-id $CandidateId --purpose $purpose | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to prepare $purpose" }
    return [pscustomobject]@{Id=$id; Path=$path}
}

function Sum-Properties($object) {
    $total = 0
    if ($null -ne $object) {
        foreach ($property in $object.PSObject.Properties) { $total += [int]$property.Value }
    }
    return $total
}

function Test-Quality($validation, [int]$expectedBars, [int]$minimumSamples) {
    $summary = $validation.summary
    $runtime = $summary.runtime_observability
    $dropped = Sum-Properties $summary.dropped_events
    $reconnectStorm = [int]$summary.max_reconnects_in_60s -gt 3
    return (
        $validation.status -eq "PASSED" -and
        [int]$summary.observed_bars -eq $expectedBars -and
        [int]$summary.unexplained_missing_bars -eq 0 -and
        -not [bool]$summary.unrecovered_stale -and
        -not $reconnectStorm -and
        $dropped -eq 0 -and
        @($summary.unexpected_worker_deaths).Count -eq 0 -and
        [int]$summary.duplicate_events_affecting_bars -eq 0 -and
        [int]$runtime.samples -ge $minimumSamples -and
        [math]::Abs([int]$runtime.thread_count_growth) -le 2 -and
        [double]$runtime.memory_mb_growth -le 512.0 -and
        [int]$runtime.handle_count_growth -le 128 -and
        [int]$runtime.max_active_recovery_owners -le 1 -and
        [int]$runtime.max_active_HALF_OPEN_owners -le 1 -and
        [int]$runtime.max_active_VALIDATING_owners -le 1 -and
        [int]$runtime.max_queue_depth -lt 100000 -and
        [double]$runtime.max_event_loop_lag_ms -lt 5000.0 -and
        [double]$runtime.max_writer_lag_ms -lt 5000.0
    )
}

function Write-ValidationCsv($validation, [string]$destination, [string]$phase) {
    $summary = $validation.summary
    $runtime = $summary.runtime_observability
    [pscustomobject]@{
        phase = $phase
        status = $validation.status
        expected_bars = [int]$summary.expected_bars
        observed_bars = [int]$summary.observed_bars
        unexplained_missing_bars = [int]$summary.unexplained_missing_bars
        reconnects = Sum-Properties $summary.reconnects
        stale_incidents = @($summary.stale_incidents).Count
        max_reconnects_in_60s = [int]$summary.max_reconnects_in_60s
        thread_count_growth = $runtime.thread_count_growth
        memory_mb_growth = $runtime.memory_mb_growth
        handle_count_growth = $runtime.handle_count_growth
        max_queue_depth = $runtime.max_queue_depth
        max_event_loop_lag_ms = $runtime.max_event_loop_lag_ms
        max_writer_lag_ms = $runtime.max_writer_lag_ms
        max_active_recovery_owners = $runtime.max_active_recovery_owners
        max_active_HALF_OPEN_owners = $runtime.max_active_HALF_OPEN_owners
        max_active_VALIDATING_owners = $runtime.max_active_VALIDATING_owners
        route_switches = $runtime.route_switches
        production_exchange_orders = 0
    } | Export-Csv -NoTypeInformation -Encoding UTF8 $destination
}

function Copy-Observability([string]$experiment, [string]$prefix) {
    $worker = Join-Path $experiment "workers\BTCUSDT"
    Copy-Item (Join-Path $worker "runtime_observability.csv") (Join-Path $AuditRoot "${prefix}_runtime_observability.csv") -Force
    Copy-Item (Join-Path $worker "minute_continuity.csv") (Join-Path $AuditRoot "${prefix}_minute_continuity.csv") -Force
    Copy-Item (Join-Path $worker "connectivity_state_timeline.csv") (Join-Path $AuditRoot "${prefix}_connectivity_timeline.csv") -Force
    if ($prefix -eq "endurance_6h") {
        Copy-Item (Join-Path $worker "runtime_observability.csv") (Join-Path $AuditRoot "endurance_6h_runtime_health.csv") -Force
        Copy-Item (Join-Path $worker "minute_continuity.csv") (Join-Path $AuditRoot "endurance_6h_connection_health.csv") -Force
    }
}

try {
    if ($PassedRegressionExperiment) {
        $regression = [pscustomobject]@{Id=(Split-Path $PassedRegressionExperiment -Leaf); Path=$PassedRegressionExperiment}
        $regressionCode = 0
    } else {
        $regression = New-FocusedExperiment "paper_regression_30m" "LONG_HORIZON_CONNECTIVITY_REGRESSION_30M"
        Write-Status @{status="RUNNING_30M_REGRESSION"; regression_experiment_id=$regression.Id; regression_experiment_path=$regression.Path}
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Experiment $regression.Path `
            -Phase continuity_test -DurationSeconds 1800 -ExpectedBars 30 -CandidateId $CandidateId
        $regressionCode = $LASTEXITCODE
    }
    $regressionValidationPath = Join-Path $regression.Path "workers\BTCUSDT\dry_run_validation.json"
    if (!(Test-Path $regressionValidationPath)) { throw "30m regression produced no validation" }
    $regressionValidation = Get-Content $regressionValidationPath -Raw | ConvertFrom-Json
    Copy-Observability $regression.Path "regression_30m"
    Write-ValidationCsv $regressionValidation (Join-Path $AuditRoot "regression_30m_validation.csv") "30M_REGRESSION"
    if ($regressionCode -ne 0 -or !(Test-Quality $regressionValidation 30 28)) {
        Write-Status @{status="BLOCKED_30M_REGRESSION"; regression_experiment_id=$regression.Id; observed_bars=$regressionValidation.summary.observed_bars}
        exit 2
    }

    Write-Status @{status="RUNNING_TARGETED_RECOVERY_VALIDATION"; regression_status="PASSED"; regression_experiment_id=$regression.Id}
    & $python $faultInjector --output $AuditRoot
    $faultCode = $LASTEXITCODE
    $faultValidationPath = Join-Path $AuditRoot "targeted_recovery_validation.json"
    if ($faultCode -ne 0 -or !(Test-Path $faultValidationPath)) {
        Write-Status @{status="BLOCKED_TARGETED_RECOVERY_VALIDATION"; regression_status="PASSED"; regression_experiment_id=$regression.Id}
        exit 2
    }
    $faultValidation = Get-Content $faultValidationPath -Raw | ConvertFrom-Json
    if ($faultValidation.status -ne "PASSED") {
        Write-Status @{status="BLOCKED_TARGETED_RECOVERY_VALIDATION"; regression_status="PASSED"; regression_experiment_id=$regression.Id}
        exit 2
    }

    $endurance = New-FocusedExperiment "paper_endurance_6h" "CONNECTIVITY_ENDURANCE_6H"
    Write-Status @{status="RUNNING_6H_ENDURANCE"; regression_status="PASSED"; targeted_recovery_validation="PASSED"; regression_experiment_id=$regression.Id; endurance_experiment_id=$endurance.Id; endurance_experiment_path=$endurance.Path}
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Experiment $endurance.Path `
        -Phase continuity_test -DurationSeconds 21600 -ExpectedBars 360 -CandidateId $CandidateId
    $enduranceCode = $LASTEXITCODE
    $enduranceValidationPath = Join-Path $endurance.Path "workers\BTCUSDT\dry_run_validation.json"
    if (!(Test-Path $enduranceValidationPath)) { throw "6h endurance produced no validation" }
    $enduranceValidation = Get-Content $enduranceValidationPath -Raw | ConvertFrom-Json
    Copy-Observability $endurance.Path "endurance_6h"
    Write-ValidationCsv $enduranceValidation (Join-Path $AuditRoot "endurance_6h_validation.csv") "6H_ENDURANCE"
    if ($enduranceCode -ne 0 -or !(Test-Quality $enduranceValidation 360 358)) {
        @{
            label="FAILED_6H_ENDURANCE_GATE"; experiment_id=$endurance.Id
            observed_bars=$enduranceValidation.summary.observed_bars
            unexplained_missing_bars=$enduranceValidation.summary.unexplained_missing_bars
            production_exchange_orders=0
        } | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $endurance.Path "FAILED_6H_ENDURANCE_GATE.json")
        Write-Status @{status="BLOCKED_6H_ENDURANCE"; regression_status="PASSED"; endurance_experiment_id=$endurance.Id; observed_bars=$enduranceValidation.summary.observed_bars}
        exit 2
    }

    $clean = New-FocusedExperiment "paper_clean_ab" "CLEAN_24H_DIRECT_MAKER_AB_AFTER_6H"
    Write-Status @{status="RUNNING_CLEAN_24H"; regression_status="PASSED"; endurance_status="PASSED"; endurance_experiment_id=$endurance.Id; new_experiment_id=$clean.Id; new_experiment_path=$clean.Path}
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Experiment $clean.Path `
        -Phase authoritative_24h -DurationSeconds 86400 -ExpectedBars 1440 -CandidateId $CandidateId
    $cleanCode = $LASTEXITCODE
    $cleanValidationPath = Join-Path $clean.Path "workers\BTCUSDT\dry_run_validation.json"
    if (!(Test-Path $cleanValidationPath)) { throw "24h A/B produced no validation" }
    $cleanValidation = Get-Content $cleanValidationPath -Raw | ConvertFrom-Json
    if ($cleanCode -ne 0 -or !(Test-Quality $cleanValidation 1440 1438)) {
        Write-Status @{status="BLOCKED_CLEAN_24H"; new_experiment_id=$clean.Id; observed_bars=$cleanValidation.summary.observed_bars}
        exit 2
    }

    $worker = Join-Path $clean.Path "workers\BTCUSDT"
    & $python "$repo\scripts\internal\replay_paper_experiment.py" --repo $repo --experiment $clean.Path --phase-root $worker --candidate-id $CandidateId
    if ($LASTEXITCODE -ne 0) { throw "clean 24h replay failed" }
    if (Test-Path $DeliveryRoot) {
        $archive = "$DeliveryRoot.superseded.$([DateTimeOffset]::UtcNow.ToString('yyyyMMdd_HHmmss'))"
        Move-Item $DeliveryRoot $archive
    }
    & $python $package --source-experiment $clean.Path --output $DeliveryRoot
    if ($LASTEXITCODE -ne 0) { throw "clean 24h packaging failed" }
    $case = Join-Path $DeliveryRoot "dynamic_breakout_short\BTCUSDT_1m_STRICT_REVERSE"
    Copy-Item (Join-Path $case "comparison\execution_comparison.csv") $DeliveryRoot -Force
    Copy-Item (Join-Path $case "01_DIRECT\mode_summary.csv") (Join-Path $DeliveryRoot "direct_summary.csv") -Force
    Copy-Item (Join-Path $case "02_MAKER\mode_summary.csv") (Join-Path $DeliveryRoot "maker_summary.csv") -Force
    Copy-Item (Join-Path $worker "daily_turnover.csv") $DeliveryRoot -Force
    Copy-Item (Join-Path $worker "data_quality_summary.csv") $DeliveryRoot -Force
    Copy-Item (Join-Path $worker "replay_validation.csv") $DeliveryRoot -Force
    Copy-Item (Join-Path $case "comparison\comparison.png") $DeliveryRoot -Force
    Write-Status @{status="PASSED"; regression_status="PASSED"; endurance_status="PASSED"; new_24h_status="COMPLETED"; new_experiment_id=$clean.Id; observed_bars=1440; replay_mismatches=0; result=$DeliveryRoot}
    exit 0
} catch {
    Write-Status @{status="BLOCKED_EXCEPTION"; exception_type=$_.Exception.GetType().FullName; exception_message=$_.Exception.Message}
    exit 2
}
