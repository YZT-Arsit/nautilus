$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$python = "$root\.venv\Scripts\python.exe"
$statusPath = "$audit\phase5a_pipeline_status.json"
while ($true) {
    if (Test-Path $statusPath) {
        $state = (Get-Content $statusPath -Raw | ConvertFrom-Json).status
        if ($state -eq "VALIDATED") { break }
        if ($state -eq "FAILED_VALIDATION") { exit 1 }
    }
    Start-Sleep -Seconds 30
}
& $python scripts\internal\prepare_phase5a_integrity.py --output "$audit\phase5a_protected_hashes_after.json" *> "$audit\phase5a_integrity_snapshot.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python scripts\internal\validate_phase5a_integrity.py *> "$audit\phase5a_integrity_validation.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$deliverable = "$root\outputs\deliverables\workbook_strategies_phase5a"
Copy-Item "$audit\phase5a_protected_hashes_after.json" $deliverable -Force
Copy-Item "$audit\phase5a_integrity_validation.json" $deliverable -Force
$archive = "$root\outputs\deliverables\workbook_strategies_phase5a.zip"
if (Test-Path $archive) { Remove-Item $archive -Force }
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
(Get-FileHash $archive -Algorithm SHA256).Hash.ToLower() | Set-Content "$archive.sha256" -Encoding ascii
@{ status = "DELIVERABLE_READY"; archive = $archive; sha256 = (Get-Content "$archive.sha256").Trim() } |
    ConvertTo-Json | Set-Content "$audit\phase5a_delivery_status.json" -Encoding UTF8
