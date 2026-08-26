$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5d"
$archive = "$root\outputs\deliverables\workbook_strategies_phase5d.zip"
New-Item -ItemType Directory -Force $audit, $deliverable | Out-Null

@{status="RUNNING";started_at=(Get-Date).ToUniversalTime().ToString("o");starting_rows=989;new_backtests=0} |
    ConvertTo-Json | Set-Content "$audit\phase5d_pipeline_status.json" -Encoding UTF8

if (-not (Test-Path "$audit\phase5d_protected_hashes_before.json")) {
    & $python -m scripts.internal.prepare_phase5b_integrity --output "$audit\phase5d_protected_hashes_before.json" *> "$audit\phase5d_integrity_before.log"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $python -m scripts.internal.build_phase5d_policy_impact *> "$audit\phase5d_build.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m pytest -q --ignore-glob=**/._* tests\unit_tests\scripts\test_phase5d_policy_impact.py *> "$audit\phase5d_tests.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m scripts.internal.prepare_phase5b_integrity --output "$audit\phase5d_protected_hashes_after.json" *> "$audit\phase5d_integrity_after.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m scripts.internal.validate_phase5d_integrity *> "$audit\phase5d_integrity_validation.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item "$audit\phase5d_integrity_validation.json" "$deliverable\phase5d_integrity_validation.json" -Force

if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
$entries = (Get-ChildItem $deliverable -File | Measure-Object).Count
@{status="DELIVERABLE_READY";finished_at=(Get-Date).ToUniversalTime().ToString("o");starting_rows=989;audited_rows=989;new_strategies=0;new_backtests=0;parameter_optimization=0;archive=$archive;sha256=$hash;files=$entries} |
    ConvertTo-Json | Set-Content "$audit\phase5d_pipeline_status.json" -Encoding UTF8
