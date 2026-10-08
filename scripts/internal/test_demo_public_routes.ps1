$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$rows = @()
foreach ($proxy in @(
    "http://100.64.0.22:7890",
    "http://100.64.0.5:7890",
    "http://100.64.0.6:7890"
)) {
    try {
        $result = Invoke-RestMethod `
            -Uri "https://demo-fapi.binance.com/fapi/v1/time" `
            -Proxy $proxy `
            -TimeoutSec 20
        $rows += [pscustomobject]@{
            Proxy = $proxy
            Success = $true
            ServerTimePresent = ($null -ne $result.serverTime)
            TlsVerification = "ENABLED"
        }
    } catch {
        $rows += [pscustomobject]@{
            Proxy = $proxy
            Success = $false
            ErrorType = $_.Exception.GetType().Name
            Error = $_.Exception.Message
        }
    }
}
$rows | ConvertTo-Json
