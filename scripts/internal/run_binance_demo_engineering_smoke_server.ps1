$ErrorActionPreference = "Stop"

$env:BINANCE_DEMO_API_KEY = [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_KEY", "User")
$env:BINANCE_DEMO_API_SECRET = [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_SECRET", "User")
if ([string]::IsNullOrEmpty($env:BINANCE_DEMO_API_KEY) -or
    [string]::IsNullOrEmpty($env:BINANCE_DEMO_API_SECRET)) {
    throw "DEMO_CREDENTIALS_UNAVAILABLE"
}

# Route B was independently validated against the Binance Demo public time
# endpoint with certificate verification enabled. Keep proxy scope local to
# this child process; do not alter machine-wide production connectivity.
$env:HTTPS_PROXY = "http://100.64.0.5:7890"
$env:https_proxy = $env:HTTPS_PROXY
$env:HTTP_PROXY = $env:HTTPS_PROXY
$env:http_proxy = $env:HTTPS_PROXY
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY

$output = "D:\nautilus\outputs\deliverables\demo_paper_ab_isolation_resolution\exchange_demo_engineering"
& "D:\nautilus\.venv\Scripts\python.exe" `
    "D:\nautilus\scripts\internal\run_binance_demo_engineering_smoke.py" `
    --output $output `
    --execute-demo-orders
$exit = $LASTEXITCODE

Remove-Item Env:BINANCE_DEMO_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BINANCE_DEMO_API_SECRET -ErrorAction SilentlyContinue
exit $exit
