param([switch]$PrepareOnly)
$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONPATH = $root
Set-Location $root
if ($PrepareOnly) {
    & $python scripts/internal/run_phase6e_forward_holdout.py --prepare-only
} else {
    & $python -m pytest tests/unit_tests/scripts/test_phase6d_execution_realism.py tests/unit_tests/scripts/test_phase6e_forward_holdout.py -q
    if ($LASTEXITCODE -ne 0) { throw "Phase 6E unit gate failed" }
    & $python scripts/internal/run_phase6e_forward_holdout.py
}
if ($LASTEXITCODE -ne 0) { throw "Phase 6E failed" }
