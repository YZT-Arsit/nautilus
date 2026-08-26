$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$output = "$root\outputs\batches\workbook_strategies_phase5f"
$dailyOutput = "$root\outputs\batches\workbook_strategies_phase5f_daily"
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5f"
$archive = "$root\outputs\deliverables\workbook_strategies_phase5f.zip"
$planPath = "$root\configs\semantic_contracts\workbook_phase5f_strategies.json"
Set-Location $root
New-Item -ItemType Directory -Force $audit, $output, $deliverable | Out-Null

@{status="RUNNING";started_at=(Get-Date).ToUniversalTime().ToString("o");starting_rows=980;new_backtests="pending"} |
    ConvertTo-Json | Set-Content "$audit\phase5f_pipeline_status.json" -Encoding UTF8
if (-not (Test-Path "$audit\phase5f_protected_hashes_before.json")) { throw "Phase 5F protected snapshot missing" }

& $python -m scripts.internal.compile_phase5f_strategies *> "$audit\phase5f_compile.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python scripts\internal\generate_workbook_strategy_packages.py --root . --plan $planPath *> "$audit\phase5f_packages.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.validate_phase5f_structure *> "$audit\phase5f_structure_pretest.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$executionPlan = Import-Csv "$audit\phase5f_execution_plan.csv"
$all = @($executionPlan.strategy_id | Sort-Object)
$physicalRows = @($executionPlan | Where-Object { $_.physical_execution -eq "true" })
$physical1m = @($physicalRows | Where-Object { $_.source_timeframe -eq "1m" } | ForEach-Object { $_.strategy_id } | Sort-Object)
$physical1d = @($physicalRows | Where-Object { $_.source_timeframe -eq "1d" } | ForEach-Object { $_.strategy_id } | Sort-Object)
$baseArguments = @(
    "-m", "scripts.internal.run_all_strategy_timeframe_lag",
    "--source-root", "strategies", "--market-root", "$root\historical_data\market_data",
    "--start", "2021-07-01", "--end", "2026-06-30", "--original-only", "--continue-on-error"
)
$workers = @()
if ($physical1m.Count -gt 0) {
    $shardCount = [Math]::Min(4, $physical1m.Count)
    for ($shardIndex = 0; $shardIndex -lt $shardCount; $shardIndex++) {
        $arguments = @($baseArguments) + @("--output-root", $output, "--case", "1m:0", "--case", "1m:1")
        foreach ($strategy in $physical1m) { $arguments += @("--strategy", $strategy) }
        $arguments += @("--shard-index", $shardIndex.ToString(), "--shard-count", $shardCount.ToString())
        $workers += Start-Process -FilePath $python -ArgumentList $arguments -PassThru -NoNewWindow `
            -RedirectStandardOutput "$audit\phase5f_runner_1m_shard_$shardIndex.log" `
            -RedirectStandardError "$audit\phase5f_runner_1m_shard_$shardIndex.err.log"
    }
}
if ($physical1d.Count -gt 0) {
    New-Item -ItemType Directory -Force $dailyOutput | Out-Null
    $arguments = @($baseArguments) + @("--output-root", $dailyOutput, "--case", "1d:0", "--case", "1d:1")
    foreach ($strategy in $physical1d) { $arguments += @("--strategy", $strategy) }
    $workers += Start-Process -FilePath $python -ArgumentList $arguments -PassThru -NoNewWindow `
        -RedirectStandardOutput "$audit\phase5f_runner_1d.log" -RedirectStandardError "$audit\phase5f_runner_1d.err.log"
}
$workers | Wait-Process
$workers | ForEach-Object { $_.Refresh() }
$failedWorkers = @($workers | Where-Object { $_.ExitCode -ne 0 })
if ($failedWorkers.Count -ne 0) {
    @{status="FAILED_RUNNER";failed_workers=@($failedWorkers.Id)} | ConvertTo-Json | Set-Content "$audit\phase5f_pipeline_status.json" -Encoding UTF8
    exit 1
}
if ($physical1d.Count -gt 0) { foreach ($strategy in $physical1d) { Copy-Item "$dailyOutput\$strategy" $output -Recurse -Force } }

& $python scripts\internal\materialize_phase5a_equivalence.py --batch-root $output --plan $planPath --audit-root $audit --output-name phase5f_equivalence_reuse.csv *> "$audit\phase5f_equivalence.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.finalize_phase5f *> "$audit\phase5f_finalize.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.validate_phase5f_structure *> "$audit\phase5f_structure_validation.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m pytest -q --ignore-glob=**/._* `
    tests\unit_tests\scripts\test_phase5f_medium_policy.py `
    tests\unit_tests\strategy_framework\test_workbook_dsl.py `
    tests\unit_tests\strategy_framework\test_modules.py `
    tests\unit_tests\strategy_framework\test_phase5c_contracts.py `
    tests\unit_tests\feature_engine\test_completed_timeframe_indicator.py *> "$audit\phase5f_tests.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m scripts.internal.prepare_phase5b_integrity --output "$audit\phase5f_protected_hashes_after.json" *> "$audit\phase5f_integrity_after.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.validate_phase5f_integrity *> "$audit\phase5f_integrity_validation.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.finalize_phase5f *> "$audit\phase5f_refinalize.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
$members = @((Get-ChildItem $deliverable -Recurse -File)).Count
@{status="DELIVERABLE_READY";finished_at=(Get-Date).ToUniversalTime().ToString("o");new_strategies=$all.Count;archive=$archive;sha256=$hash;zip_members=$members} |
    ConvertTo-Json | Set-Content "$audit\phase5f_pipeline_status.json" -Encoding UTF8
