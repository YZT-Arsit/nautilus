# Binance's canonical importer emits harmless CSV-header warnings on stderr.
# PowerShell 5 maps native stderr to non-terminating ErrorRecords, so use the
# explicit LASTEXITCODE gates below rather than treating stderr as a failure.
$ErrorActionPreference = "Continue"

Set-Location "D:\nautilus"
$python = "D:\nautilus\.venv\Scripts\python.exe"
$output = "historical_data\market_data"
$progress = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen\market_data_acquisition"
New-Item -ItemType Directory -Force $progress | Out-Null

$symbols = @("XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "1000PEPEUSDT", "ADAUSDT")
$months = @()
$cursor = Get-Date "2024-07-01"
$last = Get-Date "2026-06-01"
while ($cursor -le $last) {
    $months += $cursor.ToString("yyyy-MM")
    $cursor = $cursor.AddMonths(1)
}

foreach ($symbol in $symbols) {
    foreach ($month in $months) {
        $barMarker = Join-Path $progress "$symbol`_bar_1m_$month.done"
        if (-not (Test-Path $barMarker)) {
            & $python scripts\ingest_binance_vision.py `
                --market futures_um --symbol $symbol --data-type klines --interval 1m `
                --frequency monthly --start $month --end $month --output $output --overwrite
            if ($LASTEXITCODE -ne 0) { throw "bar ingest failed: $symbol $month" }
            Set-Content -Path $barMarker -Value "PASSED" -Encoding Ascii
        }
        $fundingMarker = Join-Path $progress "$symbol`_funding_$month.done"
        if (-not (Test-Path $fundingMarker)) {
            & $python scripts\ingest_binance_vision.py `
                --market futures_um --symbol $symbol --data-type fundingRate `
                --frequency monthly --start $month --end $month --output $output --overwrite
            if ($LASTEXITCODE -ne 0) { throw "funding ingest failed: $symbol $month" }
            Set-Content -Path $fundingMarker -Value "PASSED" -Encoding Ascii
        }
    }
}

Set-Content -Path (Join-Path $progress "COMPLETE") -Value "PASSED" -Encoding Ascii
Write-Output "BOSS_MULTITIMEFRAME_MARKET_DATA_ACQUISITION_PASSED"
