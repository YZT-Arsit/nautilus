$ErrorActionPreference = "Stop"
$taskName = "Nautilus_Phase2_2C_Repair_0422"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File D:\nautilus\scripts\internal\start_workbook_phase2_2c_repair_0422.ps1"
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings `
    -Description "Nautilus Phase 2.2C xlsx_s2_0422 repair" | Out-Null
Start-ScheduledTask -TaskName $taskName
