$ErrorActionPreference = "Stop"
$task = "NautilusP0LongHorizonGate"
$script = "D:\nautilus\scripts\internal\run_p0_long_horizon_gate.ps1"
$regression = "D:\nautilus\paper_trading\experiments\paper_regression_30m_20261006_035751"
$log = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair\long_horizon_gate_task.log"
$arguments = "/d /c powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`" -PassedRegressionExperiment `"$regression`" >> `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arguments -WorkingDirectory "D:\nautilus"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 36) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 3
[pscustomobject]@{
    TaskName=$task
    State=(Get-ScheduledTask -TaskName $task).State.ToString()
    Regression=$regression
    ProductionOrders=0
} | ConvertTo-Json
