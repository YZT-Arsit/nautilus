$ErrorActionPreference = "Stop"

Set-Location "D:\nautilus"
$python = "D:\nautilus\.venv\Scripts\python.exe"
$output = "historical_data\market_data"

& $python scripts\ingest_binance_vision.py --market futures_um --symbol BTCUSDT --data-type klines --interval 1m --frequency daily --start 2026-07-17 --end 2026-08-25 --output $output --overwrite
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

foreach ($symbol in @("ETHUSDT", "SOLUSDT")) {
    & $python scripts\ingest_binance_vision.py --market futures_um --symbol $symbol --data-type klines --interval 1m --frequency daily --start 2026-07-01 --end 2026-08-25 --output $output --overwrite
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

foreach ($symbol in @("BTCUSDT", "ETHUSDT", "SOLUSDT")) {
    & $python scripts\ingest_binance_vision.py --market futures_um --symbol $symbol --data-type fundingRate --frequency monthly --start 2026-07 --end 2026-07 --output $output --overwrite
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $python scripts\ingest_binance_vision.py --market futures_um --symbol $symbol --data-type fundingRateApi --frequency daily --start 2026-08-01 --end 2026-08-25 --output $output --overwrite
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Output "PHASE6E_ACQUISITION_DONE"
