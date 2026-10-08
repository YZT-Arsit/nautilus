$ErrorActionPreference = "Stop"
$validation = "D:\nautilus\paper_trading\experiments\paper_regression_30m_20261006_035751\workers\BTCUSDT\dry_run_validation.json"
$auditRoot = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair"
$marker = Join-Path $auditRoot "targeted_fault_injection_hold.json"
$deadline = [DateTimeOffset]::UtcNow.AddHours(1)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path $validation) {
        Stop-ScheduledTask -TaskName "NautilusP0LongHorizonGate" -ErrorAction SilentlyContinue
        @{
            held_at = [DateTimeOffset]::UtcNow.ToString("o")
            regression_validation = $validation
            reason = "TARGETED_TUNNEL_SILENCE_FAULT_INJECTION_REQUIRED_BEFORE_6H"
            production_exchange_orders = 0
        } | ConvertTo-Json | Set-Content -Encoding UTF8 $marker
        exit 0
    }
    Start-Sleep -Milliseconds 100
}
throw "regression validation did not appear before hold deadline"
