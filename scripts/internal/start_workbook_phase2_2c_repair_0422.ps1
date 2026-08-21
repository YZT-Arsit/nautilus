$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"
$log = "outputs\internal_audit\strategy_workbook\phase2_2c_repair_0422.log"
& "D:\nautilus\.venv\Scripts\python.exe" -m scripts.internal.run_all_strategy_timeframe_lag `
    --source-root strategies `
    --market-root "D:\nautilus\historical_data\market_data" `
    --output-root "D:\nautilus\outputs\batches\workbook_strategies_phase2_2c" `
    --start 2021-07-01 --end 2026-06-30 `
    --case "1m:0" --case "1m:1" --continue-on-error `
    --strategy "xlsx_s2_0422" *> $log
if ($LASTEXITCODE -ne 0) {
    throw "Phase 2.2C xlsx_s2_0422 repair failed with exit code $LASTEXITCODE"
}
