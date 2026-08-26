$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$python = "$root\.venv\Scripts\python.exe"
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5a"
$archive = "$root\outputs\deliverables\workbook_strategies_phase5a.zip"

Set-Location $root
& $python scripts\internal\finalize_phase5a.py *> "$audit\phase5a_finalize.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python scripts\internal\prepare_phase5a_integrity.py --output "$audit\phase5a_protected_hashes_after.json" *> "$audit\phase5a_integrity_snapshot.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python scripts\internal\validate_phase5a_integrity.py *> "$audit\phase5a_integrity_validation.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item "$audit\phase5a_protected_hashes_after.json" $deliverable -Force
Copy-Item "$audit\phase5a_integrity_validation.json" $deliverable -Force
if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
@{
    status = "DELIVERABLE_READY"
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    logical_strategies = 30
    physical_strategies = 9
    archive = $archive
    sha256 = $hash
} | ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
