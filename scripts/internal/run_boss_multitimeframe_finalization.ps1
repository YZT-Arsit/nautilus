$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$root = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen"
$stateRoot = Join-Path $root "matrix_worker_state"
$log = Join-Path $root "logs\finalization.log"
$symbols = @(
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT"
)
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

try {
    while ($true) {
        $failed = @($symbols | Where-Object { Test-Path (Join-Path $stateRoot ($_.ToString() + ".failed")) })
        if ($failed.Count -gt 0) {
            throw "matrix worker failure: $($failed -join ',')"
        }
        $done = @($symbols | Where-Object { Test-Path (Join-Path $stateRoot ($_.ToString() + ".done")) })
        if ($done.Count -eq $symbols.Count) { break }
        Start-Sleep -Seconds 60
    }

    & "D:\nautilus\.venv\Scripts\python.exe" -m pytest -q `
        tests\unit_tests\data_engine\test_binance_raw_trade_pipeline.py `
        tests\unit_tests\data_engine\test_boss_tick_execution_index.py `
        tests\unit_tests\scripts\test_timeframe_lag_execution.py *>> $log
    if ($LASTEXITCODE -ne 0) { throw "targeted contract tests failed" }

    & "D:\nautilus\.venv\Scripts\python.exe" `
        scripts\internal\finalize_boss_multitimeframe_tick_screen.py *>> $log
    if ($LASTEXITCODE -ne 0) { throw "result finalization failed" }

    & "D:\nautilus\.venv\Scripts\python.exe" `
        scripts\internal\render_boss_multitimeframe_deliverable.py *>> $log
    if ($LASTEXITCODE -ne 0) { throw "result rendering failed" }

    & "D:\nautilus\.venv\Scripts\python.exe" `
        scripts\internal\audit_boss_multitimeframe_protection.py after *>> $log
    if ($LASTEXITCODE -ne 0) { throw "protected artifact audit failed" }

    Set-Content (Join-Path $root "SERVER_RESULTS_READY") "PASSED" -Encoding Ascii
    exit 0
}
catch {
    $_ | Out-String | Add-Content $log
    Set-Content (Join-Path $root "FINALIZATION_FAILED") $_.Exception.Message -Encoding UTF8
    exit 1
}
