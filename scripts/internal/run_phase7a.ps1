$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONPATH = $root
Set-Location $root

& $python scripts\internal\build_phase7a_final_synthesis.py
if ($LASTEXITCODE -ne 0) { throw "Phase 7A synthesis failed" }

& $python -m pytest tests\unit_tests\scripts\test_phase7a_final_synthesis.py -q
if ($LASTEXITCODE -ne 0) { throw "Phase 7A validation tests failed" }

& $python scripts\internal\build_phase7a_final_synthesis.py --test-pass-count 8
if ($LASTEXITCODE -ne 0) { throw "Phase 7A final packaging failed" }
