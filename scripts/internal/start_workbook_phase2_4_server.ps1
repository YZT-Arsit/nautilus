$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$output = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $output | Out-Null
$taskName = "Nautilus_Phase2_4_Modules"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$arguments = '/d /c "cd /d D:\nautilus && D:\nautilus\.venv\Scripts\python.exe scripts\internal\run_workbook_phase2_4_pipeline.py > outputs\internal_audit\strategy_workbook\phase2_4_pipeline.log 2>&1"'
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings `
    -Description "Nautilus Phase 2.4 workbook module integration" | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "started $taskName"
