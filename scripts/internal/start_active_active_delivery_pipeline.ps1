$ErrorActionPreference = "Stop"
$task = "NautilusP0ActiveActiveDelivery"
$python = "D:\nautilus\.venv\Scripts\python.exe"
$script = "D:\nautilus\scripts\internal\run_active_active_delivery_pipeline.py"
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory "D:\nautilus"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task
[pscustomobject]@{TaskName=$task; State=(Get-ScheduledTask -TaskName $task).State.ToString()} | ConvertTo-Json
