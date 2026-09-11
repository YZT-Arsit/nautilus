$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$work = "$root\outputs\baseline_evaluation\execution_method_and_reverse_review"
$symbols = @("XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT")

function Assert-CommandPassed([string]$label) {
    if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}

while ($true) {
    $makerReady = @($symbols | Where-Object {
        $path = "$work\maker_comparison\shards\$_\run_summary.json"
        if (-not (Test-Path $path)) { return $true }
        (Get-Content $path -Raw | ConvertFrom-Json).status -ne "PASSED"
    }).Count -eq 0
    $reverseReady = @($symbols | Where-Object {
        $path = "$work\reverse_validation\shards\$_\run_summary.json"
        if (-not (Test-Path $path)) { return $true }
        (Get-Content $path -Raw | ConvertFrom-Json).status -ne "PASSED"
    }).Count -eq 0
    if ($makerReady -and $reverseReady) { break }
    Start-Sleep -Seconds 30
}

Set-Location $root
& $python scripts\internal\aggregate_execution_review_maker_shards.py --repo $root
Assert-CommandPassed "maker aggregation"
& $python scripts\internal\aggregate_execution_review_reverse_shards.py
Assert-CommandPassed "reverse aggregation"
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts\internal\run_execution_review_reverse_maker_shards.ps1
Assert-CommandPassed "reverse maker shards"
& $python scripts\internal\aggregate_execution_review_maker_shards.py --repo $root --comparison-root "$work\reverse_maker_comparison"
Assert-CommandPassed "reverse maker aggregation"
& $python scripts\internal\finalize_execution_method_reverse_review.py --repo $root
Assert-CommandPassed "delivery finalization"
& $python scripts\internal\package_execution_method_reverse_review.py --delivery "$root\outputs\deliverables\execution_method_and_reverse_review"
Assert-CommandPassed "delivery packaging"
