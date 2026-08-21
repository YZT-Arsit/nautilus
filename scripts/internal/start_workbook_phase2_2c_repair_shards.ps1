$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$groups = @(
    @("xlsx_s2_0441", "xlsx_s2_0512", "xlsx_s2_0618", "xlsx_s2_0660", "xlsx_s2_0690"),
    @("xlsx_s2_0710", "xlsx_s2_0795", "xlsx_s2_0837", "xlsx_s2_0867", "xlsx_s2_0891")
)
for ($index = 0; $index -lt $groups.Count; $index++) {
    $number = $index + 1
    $taskName = "Nautilus_Phase2_2C_RepairShard_$number"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    $strategyArguments = ($groups[$index] | ForEach-Object { " --strategy $_" }) -join ""
    $command = "D:\nautilus\.venv\Scripts\python.exe -m scripts.internal.run_all_strategy_timeframe_lag --source-root strategies --market-root D:\nautilus\historical_data\market_data --output-root D:\nautilus\outputs\batches\workbook_strategies_phase2_2c --start 2021-07-01 --end 2026-06-30 --case 1m:0 --case 1m:1 --continue-on-error$strategyArguments"
    $log = "outputs\internal_audit\strategy_workbook\phase2_2c_repair_shard_$number.log"
    $arguments = "/d /c `"cd /d D:\nautilus && $command > $log 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Days 3) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 2) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings `
        -Description "Nautilus Phase 2.2C repair shard $number" | Out-Null
    Start-ScheduledTask -TaskName $taskName
}
Start-Sleep -Seconds 2
Get-ScheduledTask -TaskName "Nautilus_Phase2_2C_RepairShard_*" | Select-Object TaskName, State
