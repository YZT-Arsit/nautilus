$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$output = "$root\outputs\batches\workbook_strategies_phase5a"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$plan = Get-Content "$root\configs\semantic_contracts\workbook_phase5a_strategies.json" -Raw | ConvertFrom-Json
$strategies = @($plan.PSObject.Properties.Name | Sort-Object)
New-Item -ItemType Directory -Force $output, $audit | Out-Null
for ($shard = 0; $shard -lt 3; $shard++) {
    $arguments = @(
        "-m", "scripts.internal.run_all_strategy_timeframe_lag",
        "--source-root", "strategies", "--market-root", "$root\historical_data\market_data",
        "--output-root", $output, "--start", "2021-07-01", "--end", "2026-06-30",
        "--case", "1m:0", "--case", "1m:1", "--original-only", "--continue-on-error",
        "--shard-index", "$shard", "--shard-count", "3"
    )
    foreach ($strategy in $strategies) { $arguments += @("--strategy", $strategy) }
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
        -RedirectStandardOutput "$audit\phase5a_shard_$shard.log" `
        -RedirectStandardError "$audit\phase5a_shard_$shard.err.log" -WindowStyle Hidden
}
@{ status = "RUNNING"; started_at = (Get-Date).ToUniversalTime().ToString("o"); shards = 3; strategies = $strategies.Count } |
    ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
