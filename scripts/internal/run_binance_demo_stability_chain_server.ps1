$ErrorActionPreference = "Stop"

$env:BINANCE_DEMO_API_KEY = [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_KEY", "User")
$env:BINANCE_DEMO_API_SECRET = [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_SECRET", "User")
if ([string]::IsNullOrEmpty($env:BINANCE_DEMO_API_KEY) -or
    [string]::IsNullOrEmpty($env:BINANCE_DEMO_API_SECRET)) {
    throw "DEMO_CREDENTIALS_UNAVAILABLE"
}

# Demo-only process-local egress. Never modify the production paper proxy.
$env:HTTPS_PROXY = "http://100.64.0.5:7890"
$env:https_proxy = $env:HTTPS_PROXY
$env:HTTP_PROXY = $env:HTTPS_PROXY
$env:http_proxy = $env:HTTPS_PROXY
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY

$repo = "D:\nautilus"
$root = "D:\nautilus\outputs\demo_stability"
& "$repo\.venv\Scripts\python.exe" `
    "$repo\scripts\internal\run_binance_demo_stability_chain.py" `
    --root $root `
    --short-seconds 180
$exit = $LASTEXITCODE

Remove-Item Env:BINANCE_DEMO_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BINANCE_DEMO_API_SECRET -ErrorAction SilentlyContinue
exit $exit
