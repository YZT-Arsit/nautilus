$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$script = "$root\scripts\internal\run_execution_review_reverse_validation.py"
$logRoot = "$root\outputs\baseline_evaluation\execution_method_and_reverse_review\logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$symbols = @("XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT")
$failures = @()
$maxParallel = 1
for ($offset = 0; $offset -lt $symbols.Count; $offset += $maxParallel) {
    $running = @()
    $last = [Math]::Min($offset + $maxParallel - 1, $symbols.Count - 1)
    foreach ($symbol in $symbols[$offset..$last]) {
        $summary = "$root\outputs\baseline_evaluation\execution_method_and_reverse_review\reverse_validation\shards\$symbol\run_summary.json"
        if (Test-Path $summary) {
            $content = Get-Content $summary -Raw | ConvertFrom-Json
            if ($content.status -eq "PASSED") { continue }
        }
        $out = "$logRoot\reverse_$symbol.stdout.log"
        $err = "$logRoot\reverse_$symbol.stderr.log"
        $process = Start-Process -FilePath $python -ArgumentList @($script, "--repo", $root, "--symbol", $symbol) -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        $process | Add-Member -NotePropertyName Symbol -NotePropertyValue $symbol
        $running += $process
    }
    foreach ($entry in $running) {
        Wait-Process -Id $entry.Id
        $entry.Refresh()
        if ($entry.ExitCode -ne 0) { $failures += $entry.Symbol }
    }
}
if ($failures.Count -gt 0) {
    throw "Reverse shards failed: $($failures -join ',')"
}
