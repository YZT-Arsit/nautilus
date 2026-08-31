param(
    [Parameter(Mandatory = $true)][string]$Symbols,
    [Parameter(Mandatory = $true)][string]$Worker,
    [string]$Start,
    [string]$EndExclusive
)

# Native download failures are handled explicitly by the bounded retry loop.
# "Stop" turns redirected native stderr into a terminating NativeCommandError
# on Windows PowerShell and would bypass that loop.
$ErrorActionPreference = "Continue"
Set-Location "D:\nautilus"
$log = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen\logs\tick_index_$Worker.log"
$cache = "outputs\tmp_tick_ingest\$Worker"
New-Item -ItemType Directory -Force (Split-Path $log), $cache | Out-Null
$arguments = @("scripts\internal\build_boss_tick_execution_index.py", "build", "--cache-root", $cache)
foreach ($symbol in $Symbols.Split(",")) {
    $arguments += @("--symbol", $symbol)
}
if ($Start) { $arguments += @("--start", $Start) }
if ($EndExclusive) { $arguments += @("--end-exclusive", $EndExclusive) }
$attempt = 0
do {
    $attempt += 1
    & "D:\nautilus\.venv\Scripts\python.exe" @arguments *>> $log
    $code = $LASTEXITCODE
    if ($code -eq 0) { exit 0 }
    Add-Content $log ("transient/resumable attempt " + $attempt + " exited " + $code)
    # Binance may temporarily reset the source IP after a burst of daily
    # archive requests.  A long bounded backoff avoids sustaining that limit.
    if ($attempt -lt 50) { Start-Sleep -Seconds 600 }
} while ($attempt -lt 50)
exit $code
