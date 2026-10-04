$ErrorActionPreference = "Stop"

$task = "NautilusP0RuntimeHealthMonitor"
$python = "D:\nautilus\.venv\Scripts\python.exe"
$script = "D:\nautilus\scripts\internal\monitor_p0_connection_runtime.py"
$audit = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair"
$arguments = '"{0}" --audit-root "{1}" --interval-seconds 60 --port 18898 --max-hours 28' -f $script, $audit
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory "D:\nautilus"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 30)
Register-ScheduledTask `
    -TaskName $task `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Read-only P0 connection runtime health sampler" `
    -Force | Out-Null
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 3
[pscustomobject]@{
    State = (Get-ScheduledTask -TaskName $task).State.ToString()
    LastTaskResult = (Get-ScheduledTaskInfo -TaskName $task).LastTaskResult
} | ConvertTo-Json
