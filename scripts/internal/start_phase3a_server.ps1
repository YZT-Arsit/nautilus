$ErrorActionPreference = "Stop"

$repo = "D:\nautilus"
$taskName = "Nautilus_Phase3A_Parameter_Search_Design"
$python = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "outputs\internal_audit\strategy_workbook\phase3a_pipeline.log"

$command = "cd /d $repo && `"$python`" scripts\internal\run_phase3a_pipeline.py > `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/d /c `"$command`""
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "started $taskName"
