$ErrorActionPreference = "Continue"
Set-Location "D:\nautilus"
$log = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen\logs\market_data_acquisition.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
& scripts\internal\acquire_boss_multitimeframe_market_data.ps1 *>> $log
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code
