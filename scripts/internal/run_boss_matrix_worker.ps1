param(
    [Parameter(Mandatory = $true)][string]$Symbols,
    [Parameter(Mandatory = $true)][string]$Worker
)

$ErrorActionPreference = "Continue"
Set-Location "D:\nautilus"
$root = "outputs\baseline_evaluation\boss_multitimeframe_tick_screen"
$log = Join-Path $root ("logs\matrix_" + $Worker + ".log")
$stateRoot = Join-Path $root "tick_execution_index_state"
$doneRoot = Join-Path $root "matrix_worker_state"
New-Item -ItemType Directory -Force (Split-Path $log), $doneRoot | Out-Null

foreach ($symbol in $Symbols.Split(",")) {
    $done = Join-Path $doneRoot ($symbol + ".done")
    if (Test-Path $done) { continue }
    while ($true) {
        $count = (Get-ChildItem (Join-Path $stateRoot ("symbol=" + $symbol)) `
            -Filter "date=*.json" -ErrorAction SilentlyContinue).Count
        $marketReady = Test-Path (Join-Path $root "market_data_acquisition\COMPLETE")
        if ($count -eq 729 -and $marketReady) { break }
        Start-Sleep -Seconds 60
    }
    & "D:\nautilus\.venv\Scripts\python.exe" `
        scripts\internal\run_boss_multitimeframe_tick_screen.py --symbol $symbol *>> $log
    if ($LASTEXITCODE -ne 0) {
        Set-Content (Join-Path $doneRoot ($symbol + ".failed")) "FAILED" -Encoding Ascii
        exit $LASTEXITCODE
    }
    $progress = Get-Content (Join-Path $root ("matrix_progress_" + $symbol + ".json")) | ConvertFrom-Json
    if ($progress.status -ne "PASSED" -or $progress.logical_completed -ne 1068) {
        Set-Content (Join-Path $doneRoot ($symbol + ".failed")) "VALIDATION_FAILED" -Encoding Ascii
        exit 2
    }
    Set-Content $done "PASSED" -Encoding Ascii
}

Set-Content (Join-Path $doneRoot ($Worker + ".complete")) "PASSED" -Encoding Ascii
exit 0
