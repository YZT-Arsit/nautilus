$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$output = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $output | Out-Null
$taskName = "Nautilus_Phase2_1_Workbook"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$arguments = '/d /c "cd /d D:\nautilus && D:\nautilus\.venv\Scripts\python.exe scripts\internal\run_workbook_phase2_pipeline.py --workbook strategy_workbook.xlsx > outputs\internal_audit\strategy_workbook\pipeline.log 2>&1"'
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Settings $settings `
    -Description "Nautilus workbook Phase 2.1 resilient audit/backtest/report pipeline" | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Get-FileHash "strategy_workbook.xlsx" -Algorithm SHA256 | Select-Object Hash, Path
