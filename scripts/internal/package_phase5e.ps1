$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$deliverable = Join-Path $root "outputs\deliverables\workbook_strategies_phase5e"
$archive = Join-Path $root "outputs\deliverables\workbook_strategies_phase5e.zip"
$audit = Join-Path $root "outputs\internal_audit\strategy_workbook"

if (Test-Path $archive) {
    Remove-Item $archive -Force
}
Compress-Archive -Path "$deliverable\*" -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$hash | Set-Content "$archive.sha256" -Encoding ascii
$members = @((Get-ChildItem $deliverable -Recurse -File)).Count
@{
    status = "DELIVERABLE_READY"
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    new_strategies = 9
    archive = $archive
    sha256 = $hash
    zip_members = $members
} | ConvertTo-Json | Set-Content "$audit\phase5e_pipeline_status.json" -Encoding UTF8
Write-Output $hash
