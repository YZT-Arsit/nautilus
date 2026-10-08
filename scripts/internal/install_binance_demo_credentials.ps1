$ErrorActionPreference = "Stop"

$keySecure = Read-Host "Binance Demo API key" -AsSecureString
$secretSecure = Read-Host "Binance Demo API secret" -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($keySecure)
$secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretSecure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
    $secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
    if ([string]::IsNullOrWhiteSpace($key) -or [string]::IsNullOrWhiteSpace($secret)) {
        throw "Credentials must not be empty"
    }
    [Environment]::SetEnvironmentVariable("BINANCE_DEMO_API_KEY", $key, "User")
    [Environment]::SetEnvironmentVariable("BINANCE_DEMO_API_SECRET", $secret, "User")
} finally {
    if ($keyPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
    }
    if ($secretPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
    }
    $key = $null
    $secret = $null
}

[pscustomobject]@{
    UserEnvironmentConfigured = $true
    ApiKeyPresent = -not [string]::IsNullOrEmpty(
        [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_KEY", "User")
    )
    ApiSecretPresent = -not [string]::IsNullOrEmpty(
        [Environment]::GetEnvironmentVariable("BINANCE_DEMO_API_SECRET", "User")
    )
    ValuesPrinted = $false
} | ConvertTo-Json
