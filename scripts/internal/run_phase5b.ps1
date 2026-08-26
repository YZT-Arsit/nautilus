$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$output = "$root\outputs\batches\workbook_strategies_phase5b"
$dailyOutput = "$root\outputs\batches\workbook_strategies_phase5b_daily"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$planPath = "$root\configs\semantic_contracts\workbook_phase5b_strategies.json"
$executionPlan = Import-Csv "$audit\phase5b_execution_plan.csv"
$all = @($executionPlan.strategy_id | Sort-Object)
$physicalRows = @($executionPlan | Where-Object { $_.physical_execution -eq "true" })
$physical = @($physicalRows.strategy_id | Sort-Object)
$physical1m = @($physicalRows | Where-Object { $_.source_timeframe -eq "1m" } | ForEach-Object { $_.strategy_id } | Sort-Object)
$physical1d = @($physicalRows | Where-Object { $_.source_timeframe -eq "1d" } | ForEach-Object { $_.strategy_id } | Sort-Object)
New-Item -ItemType Directory -Force $output, $audit | Out-Null
@{ status = "RUNNING"; started_at = (Get-Date).ToUniversalTime().ToString("o"); logical_strategies = $all.Count; physical_strategies = $physical.Count } |
    ConvertTo-Json | Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8
$arguments = @(
    "-m", "scripts.internal.run_all_strategy_timeframe_lag",
    "--source-root", "strategies", "--market-root", "$root\historical_data\market_data",
    "--start", "2021-07-01", "--end", "2026-06-30",
    "--original-only", "--continue-on-error", "--overwrite"
)
$shardCount = 4
$workers = @()
for ($shardIndex = 0; $shardIndex -lt $shardCount; $shardIndex++) {
    $shardArguments = @($arguments) + @("--output-root", $output, "--case", "1m:0", "--case", "1m:1")
    foreach ($strategy in $physical1m) { $shardArguments += @("--strategy", $strategy) }
    $shardArguments += @(
        "--shard-index", $shardIndex.ToString(),
        "--shard-count", $shardCount.ToString()
    )
    $workers += Start-Process -FilePath $python -ArgumentList $shardArguments `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput "$audit\phase5b_runner_shard_$shardIndex.log" `
        -RedirectStandardError "$audit\phase5b_runner_shard_$shardIndex.err.log"
}
if ($physical1d.Count -gt 0) {
    $dailyArguments = @($arguments) + @("--output-root", $dailyOutput, "--case", "1d:0", "--case", "1d:1")
    foreach ($strategy in $physical1d) { $dailyArguments += @("--strategy", $strategy) }
    $workers += Start-Process -FilePath $python -ArgumentList $dailyArguments `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput "$audit\phase5b_runner_daily.log" `
        -RedirectStandardError "$audit\phase5b_runner_daily.err.log"
}
$workers | Wait-Process
$workers | ForEach-Object { $_.Refresh() }
$failedWorkers = @($workers | Where-Object { $_.ExitCode -ne 0 })
if ($failedWorkers.Count -ne 0) {
    @{status="FAILED_RUNNER";failed_shards=@($failedWorkers.Id)}|ConvertTo-Json|Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8
    exit 1
}
if ($physical1d.Count -gt 0) {
    foreach ($strategy in $physical1d) {
        Copy-Item "$dailyOutput\$strategy" $output -Recurse -Force
    }
    Copy-Item "$dailyOutput\evaluation_shard_0.csv" "$audit\phase5b_daily_evaluation.csv" -Force
    Copy-Item "$dailyOutput\failures_shard_0.json" "$audit\phase5b_daily_failures.json" -Force
    Remove-Item $dailyOutput -Recurse -Force
}
& $python scripts\internal\materialize_phase5a_equivalence.py --batch-root $output --plan $planPath --audit-root $audit --output-name phase5b_equivalence_reuse.csv *> "$audit\phase5b_equivalence.log"
if ($LASTEXITCODE -ne 0) { @{status="FAILED_EQUIVALENCE";exit_code=$LASTEXITCODE}|ConvertTo-Json|Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8; exit $LASTEXITCODE }
& $python -m scripts.internal.finalize_phase5b *> "$audit\phase5b_finalize.log"
if ($LASTEXITCODE -ne 0) { @{status="FAILED_VALIDATION";exit_code=$LASTEXITCODE}|ConvertTo-Json|Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8; exit $LASTEXITCODE }
& $python -m scripts.internal.prepare_phase5b_integrity --output "$audit\phase5b_protected_hashes_after.json" *> "$audit\phase5b_integrity_snapshot.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m scripts.internal.validate_phase5b_integrity *> "$audit\phase5b_integrity_validation.log"
if ($LASTEXITCODE -ne 0) { @{status="FAILED_INTEGRITY";exit_code=$LASTEXITCODE}|ConvertTo-Json|Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8; exit $LASTEXITCODE }
& $python -m scripts.internal.finalize_phase5b *> "$audit\phase5b_refinalize.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5b"
$archive = "$root\outputs\deliverables\workbook_strategies_phase5b.zip"
if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
@{ status = "DELIVERABLE_READY"; finished_at = (Get-Date).ToUniversalTime().ToString("o"); logical_strategies = $all.Count; physical_strategies = $physical.Count; archive = $archive; sha256 = $hash } |
    ConvertTo-Json | Set-Content "$audit\phase5b_pipeline_status.json" -Encoding UTF8
