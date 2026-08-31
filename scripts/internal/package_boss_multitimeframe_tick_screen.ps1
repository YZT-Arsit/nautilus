$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$source = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen"
$target = "outputs\deliverables\boss_multitimeframe_tick_screen"
$zip = "outputs\deliverables\boss_multitimeframe_tick_screen.zip"
$files = @(
    "boss_tick_index_data_window.json",
    "boss_multitimeframe_data_availability.csv",
    "tick_execution_index_manifest.csv",
    "tick_execution_index_spot_validation.csv",
    "boss_multitimeframe_tick_master.csv",
    "boss_multitimeframe_strategy_summary.csv",
    "boss_multitimeframe_by_symbol.csv",
    "boss_multitimeframe_by_timeframe.csv",
    "boss_multitimeframe_execution_wait.csv",
    "boss_multitimeframe_overview.csv",
    "reference_position_behavior.csv",
    "persistent_position_reference_audit.csv",
    "persistent_position_candidates.csv",
    "persistence_parameter_audit.csv",
    "hold_until_opposite_feasibility.csv",
    "cross_timeframe_descriptive_best.csv",
    "boss_multitimeframe_candidates.csv",
    "boss_multitimeframe_tick_review.xlsx",
    "boss_multitimeframe_candidates.xlsx",
    "boss_multitimeframe_tick_review_preview.png",
    "boss_multitimeframe_tick_review.html",
    "validation_summary.json",
    "storage_summary.json",
    "protected_hash_validation.json",
    "protected_artifacts_before.csv",
    "protected_artifacts_after.csv"
)

New-Item -ItemType Directory -Force $target | Out-Null
foreach ($file in $files) {
    $inputPath = Join-Path $source $file
    if (-not (Test-Path $inputPath)) { throw "required deliverable missing: $inputPath" }
    Copy-Item $inputPath (Join-Path $target $file) -Force
}
$figureSource = Join-Path $source "figures"
if (-not (Test-Path $figureSource)) { throw "required figures directory missing" }
$figureTarget = Join-Path $target "figures"
New-Item -ItemType Directory -Force $figureTarget | Out-Null
Get-ChildItem $figureSource -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $figureTarget $_.Name) -Force
}
if ((Get-ChildItem $figureTarget -Filter "*.png").Count -lt 3) {
    throw "fewer than three final PNG figures"
}

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $target "*") -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$integrity = & "D:\nautilus\.venv\Scripts\python.exe" -c `
    "import zipfile; z=zipfile.ZipFile(r'$zip'); print('PASSED' if z.testzip() is None else 'FAILED')"
if ($integrity.Trim() -ne "PASSED") { throw "ZIP integrity failed" }
[pscustomobject]@{
    status = "PASSED"
    deliverable_root = (Resolve-Path $target).Path
    zip = (Resolve-Path $zip).Path
    sha256 = $hash
    zip_integrity = "PASSED"
    file_count = (Get-ChildItem $target -Recurse -File).Count
} | ConvertTo-Json | Set-Content (Join-Path $source "delivery_summary.json") -Encoding UTF8
Write-Output $hash
