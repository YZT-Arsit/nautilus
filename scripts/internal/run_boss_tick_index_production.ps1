$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"
$log = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen\logs\tick_index_production.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
& "D:\nautilus\.venv\Scripts\python.exe" `
    scripts\internal\build_boss_tick_execution_index.py build *>> $log
exit $LASTEXITCODE
