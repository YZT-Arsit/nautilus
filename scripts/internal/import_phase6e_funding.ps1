$ErrorActionPreference = "Stop"

Set-Location "D:\nautilus"
$python = "D:\nautilus\.venv\Scripts\python.exe"
$rawRoot = "outputs\internal_audit\phase6e\raw_funding_api"

foreach ($symbol in @("BTCUSDT", "ETHUSDT", "SOLUSDT")) {
    $inputPath = Join-Path $rawRoot "${symbol}_2026-08-01_2026-08-25.json"
    & $python scripts\internal\import_phase6e_funding_json.py --input $inputPath --symbol $symbol --output historical_data\market_data
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
