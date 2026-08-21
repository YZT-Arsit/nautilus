$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$auditRoot = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $auditRoot | Out-Null
$shards = @(
    "Nautilus_Phase2_2C_Shard_1",
    "Nautilus_Phase2_2C_Shard_2",
    "Nautilus_Phase2_2C_Shard_3"
)
while ($true) {
    $running = @(
        $shards | Where-Object {
            $task = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
            $task -and $task.State -eq "Running"
        }
    )
    if ($running.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 30
}

$strategies = @(
    "xlsx_s2_0229", "xlsx_s2_0315", "xlsx_s2_0422", "xlsx_s2_0441",
    "xlsx_s2_0512", "xlsx_s2_0618", "xlsx_s2_0660", "xlsx_s2_0690",
    "xlsx_s2_0710", "xlsx_s2_0795", "xlsx_s2_0837", "xlsx_s2_0867",
    "xlsx_s2_0891"
)
$arguments = @(
    "-m", "scripts.internal.run_all_strategy_timeframe_lag",
    "--source-root", "strategies",
    "--market-root", "D:\nautilus\historical_data\market_data",
    "--output-root", "D:\nautilus\outputs\batches\workbook_strategies_phase2_2c",
    "--start", "2021-07-01", "--end", "2026-06-30",
    "--case", "1m:0", "--case", "1m:1", "--continue-on-error"
)
foreach ($strategy in $strategies) {
    $arguments += @("--strategy", $strategy)
}
$log = Join-Path $auditRoot "phase2_2c_repair.log"
& "D:\nautilus\.venv\Scripts\python.exe" @arguments *> $log
if ($LASTEXITCODE -ne 0) {
    throw "Phase 2.2C repair failed with exit code $LASTEXITCODE"
}
