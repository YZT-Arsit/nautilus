$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$output = "$root\outputs\batches\workbook_strategies_phase5a"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$plan = Get-Content "$root\configs\semantic_contracts\workbook_phase5a_strategies.json" -Raw | ConvertFrom-Json
$allStrategies = @($plan.PSObject.Properties.Name | Sort-Object)
$groups = @{}
foreach ($strategy in $allStrategies) {
    $hash = $plan.PSObject.Properties[$strategy].Value.rule_hash
    if (-not $groups.ContainsKey($hash)) { $groups[$hash] = $strategy }
}
$strategies = @($groups.Values | Sort-Object)
$arguments = @(
    "-m", "scripts.internal.run_all_strategy_timeframe_lag",
    "--source-root", "strategies", "--market-root", "$root\historical_data\market_data",
    "--output-root", $output, "--start", "2021-07-01", "--end", "2026-06-30",
    "--case", "1m:0", "--case", "1m:1", "--original-only", "--continue-on-error",
    "--shard-index", "0", "--shard-count", "1"
)
foreach ($strategy in $strategies) { $arguments += @("--strategy", $strategy) }
@{ status = "RUNNING_SINGLE_PROCESS"; started_at = (Get-Date).ToUniversalTime().ToString("o"); logical_strategies = $allStrategies.Count; physical_strategies = $strategies.Count } |
    ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
& $python @arguments *> "$audit\phase5a_single.log"
if ($LASTEXITCODE -ne 0) {
    @{ status = "FAILED_RUNNER"; exit_code = $LASTEXITCODE } | ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
& $python scripts\internal\materialize_phase5a_equivalence.py *> "$audit\phase5a_equivalence.log"
if ($LASTEXITCODE -ne 0) {
    @{ status = "FAILED_EQUIVALENCE_MATERIALIZATION"; exit_code = $LASTEXITCODE } | ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
& $python scripts\internal\finalize_phase5a.py *> "$audit\phase5a_finalize.log"
if ($LASTEXITCODE -ne 0) {
    @{ status = "FAILED_VALIDATION"; exit_code = $LASTEXITCODE } | ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
@{ status = "VALIDATED"; finished_at = (Get-Date).ToUniversalTime().ToString("o"); logical_strategies = $allStrategies.Count; physical_strategies = $strategies.Count } |
    ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
& $python scripts\internal\prepare_phase5a_integrity.py --output "$audit\phase5a_protected_hashes_after.json" *> "$audit\phase5a_integrity_snapshot.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python scripts\internal\validate_phase5a_integrity.py *> "$audit\phase5a_integrity_validation.log"
if ($LASTEXITCODE -ne 0) {
    @{ status = "FAILED_INTEGRITY"; exit_code = $LASTEXITCODE } | ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5a"
Copy-Item "$audit\phase5a_protected_hashes_after.json" $deliverable -Force
Copy-Item "$audit\phase5a_integrity_validation.json" $deliverable -Force
$archive = "$root\outputs\deliverables\workbook_strategies_phase5a.zip"
if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
@{ status = "DELIVERABLE_READY"; finished_at = (Get-Date).ToUniversalTime().ToString("o"); logical_strategies = $allStrategies.Count; physical_strategies = $strategies.Count; archive = $archive; sha256 = $hash } |
    ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
