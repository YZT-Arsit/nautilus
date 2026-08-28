param(
    [string]$ExchangeInfo = "D:\nautilus\outputs\binance_exchange_info_phase6d.json"
)

$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = Join-Path $root ".venv\Scripts\python.exe"
$logRoot = Join-Path $root "outputs\logs\phase6d"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logRoot "phase6d_$stamp.log"

Set-Location $root
$env:PYTHONPATH = $root
& $python -m pytest tests/unit_tests/scripts/test_phase6d_execution_realism.py -q 2>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) { throw "Phase 6D unit gate failed" }
& $python scripts/internal/run_phase6d_execution_realism.py --exchange-info $ExchangeInfo 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { throw "Phase 6D execution failed" }
