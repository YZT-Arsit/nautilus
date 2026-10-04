$ErrorActionPreference = "Stop"

$task = "NautilusPaperBinanceFailoverProxy"
$exe = "D:\nautilus\.venv\Scripts\python.exe"
$arguments = @(
    '"D:\nautilus\scripts\internal\read_only_binance_failover_proxy.py"'
    '--port 18898'
    '--upstream 100.64.0.6:7890'
    '--upstream 100.64.0.5:7890'
    '--route-cooldown-seconds 60'
    '--route-max-cooldown-seconds 600'
    '--short-lived-seconds 30'
    '--log "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair\proxy_lifecycle_round2.jsonl"'
) -join ' '

$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument $arguments `
    -WorkingDirectory "D:\nautilus"
Set-ScheduledTask -TaskName $task -Action $action | Out-Null
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 3

$info = Get-ScheduledTaskInfo -TaskName $task
$state = (Get-ScheduledTask -TaskName $task).State
[pscustomobject]@{
    State = $state.ToString()
    LastResult = $info.LastTaskResult
    ListeningSockets = @(Get-NetTCPConnection -LocalPort 18898 -State Listen -ErrorAction SilentlyContinue).Count
} | ConvertTo-Json
