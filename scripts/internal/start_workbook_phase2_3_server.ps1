$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$output = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $output | Out-Null
$taskName = "Nautilus_Phase2_3_Workbook"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$arguments = '/d /c "cd /d D:\nautilus && D:\nautilus\.venv\Scripts\python.exe scripts\internal\run_workbook_phase2_3_pipeline.py > outputs\internal_audit\strategy_workbook\phase2_3_pipeline.log 2>&1"'
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings `
    -Description "Nautilus workbook Phase 2.3 crypto UTC session recovery" | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "started $taskName"
