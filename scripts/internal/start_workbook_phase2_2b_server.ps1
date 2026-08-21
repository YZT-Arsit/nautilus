$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$output = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $output | Out-Null
$taskName = "Nautilus_Phase2_2B_Workbook"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$arguments = '/d /c "cd /d D:\nautilus && D:\nautilus\.venv\Scripts\python.exe scripts\internal\run_workbook_phase2_2b_pipeline.py --workbook 时序策略.xlsx > outputs\internal_audit\strategy_workbook\phase2_2b_pipeline.log 2>&1"'
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings `
    -Description "Nautilus workbook Phase 2.2B semantic-contract backtests" | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
