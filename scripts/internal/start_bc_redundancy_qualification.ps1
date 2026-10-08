$ErrorActionPreference = "Stop"
$repo = "D:\nautilus"
$python = "D:\nautilus\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$log = Join-Path $repo "outputs\baseline_evaluation\paper_market_data_continuity_repair\bc_redundancy_qualification_launcher.log"
Set-Location $repo
& $python "scripts\internal\run_bc_redundancy_qualification.py" *>> $log
exit $LASTEXITCODE
