$ErrorActionPreference = "Stop"
$task = "NautilusP0RegressionHold"
$script = "D:\nautilus\scripts\internal\hold_p0_after_regression.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory "D:\nautilus"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task
[pscustomobject]@{TaskName=$task; State=(Get-ScheduledTask -TaskName $task).State.ToString()} | ConvertTo-Json
