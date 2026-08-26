$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$audit = "$root\outputs\internal_audit\strategy_workbook"
$output = "$root\outputs\batches\workbook_strategies_phase5a"
$python = "$root\.venv\Scripts\python.exe"
while ($true) {
    $progress = @(Get-ChildItem $output -Filter "progress_shard_*.json" -ErrorAction SilentlyContinue)
    if ($progress.Count -eq 3) {
        $states = @($progress | ForEach-Object { (Get-Content $_.FullName -Raw | ConvertFrom-Json).status })
        if (@($states | Where-Object { $_ -notlike "complete*" }).Count -eq 0) { break }
    }
    Start-Sleep -Seconds 30
}
& $python scripts\internal\finalize_phase5a.py *> "$audit\phase5a_finalize.log"
$exit = $LASTEXITCODE
if ($exit -eq 0) {
    & $python scripts\internal\prepare_phase5a_integrity.py --output "$audit\phase5a_protected_hashes_after.json" *> "$audit\phase5a_integrity_snapshot.log"
    if ($LASTEXITCODE -eq 0) {
        & $python scripts\internal\validate_phase5a_integrity.py *> "$audit\phase5a_integrity_validation.log"
        $exit = $LASTEXITCODE
    } else { $exit = $LASTEXITCODE }
}
if ($exit -eq 0) {
    $deliverable = "$root\outputs\deliverables\workbook_strategies_phase5a"
    Copy-Item "$audit\phase5a_protected_hashes_after.json" $deliverable -Force
    Copy-Item "$audit\phase5a_integrity_validation.json" $deliverable -Force
    $archive = "$root\outputs\deliverables\workbook_strategies_phase5a.zip"
    if (Test-Path $archive) { Remove-Item $archive -Force }
    Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
    (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower() | Set-Content "$archive.sha256" -Encoding ascii
}
$status = if ($exit -eq 0) { "VALIDATED" } else { "FAILED_VALIDATION" }
@{ status = $status; finished_at = (Get-Date).ToUniversalTime().ToString("o"); exit_code = $exit } |
    ConvertTo-Json | Set-Content "$audit\phase5a_pipeline_status.json" -Encoding UTF8
exit $exit
