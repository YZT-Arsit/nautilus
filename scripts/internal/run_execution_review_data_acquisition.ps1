$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$python = "D:\nautilus\.venv\Scripts\python.exe"
$script = "D:\nautilus\scripts\internal\acquire_execution_review_l1_data.py"
$output = "D:\nautilus\outputs\baseline_evaluation\execution_method_and_reverse_review\maker_data"
$temp = "D:\nautilus\outputs\tmp_execution_method_review"
$logRoot = "D:\nautilus\outputs\baseline_evaluation\execution_method_and_reverse_review\logs"
$status = "D:\nautilus\outputs\baseline_evaluation\execution_method_and_reverse_review\data_acquisition_status.json"
$symbols = @("XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "1000PEPEUSDT", "ADAUSDT")
New-Item -ItemType Directory -Force $output, $temp, $logRoot | Out-Null

@{status="RUNNING"; symbols=$symbols; max_parallel=3; started=(Get-Date).ToUniversalTime().ToString("o")} |
    ConvertTo-Json | Set-Content -Encoding UTF8 $status

for ($offset = 0; $offset -lt $symbols.Count; $offset += 3) {
    $batch = $symbols[$offset..([Math]::Min($offset + 2, $symbols.Count - 1))]
    $processes = @()
    foreach ($symbol in $batch) {
        $stdout = Join-Path $logRoot ("acquire_" + $symbol + ".stdout.log")
        $stderr = Join-Path $logRoot ("acquire_" + $symbol + ".stderr.log")
        $arguments = @(
            $script,
            "--symbol", $symbol,
            "--start", "2024-03-01",
            "--end-exclusive", "2024-03-31",
            "--output", $output,
            "--temp", $temp
        )
        $processes += Start-Process -FilePath $python -ArgumentList $arguments -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    $processes | Wait-Process
    foreach ($process in $processes) {
        if ($process.ExitCode -ne 0) {
            @{status="FAILED"; exit_code=$process.ExitCode; completed=(Get-Date).ToUniversalTime().ToString("o")} |
                ConvertTo-Json | Set-Content -Encoding UTF8 $status
            exit $process.ExitCode
        }
    }
}

@{status="PASSED"; symbols=$symbols; max_parallel=3; completed=(Get-Date).ToUniversalTime().ToString("o")} |
    ConvertTo-Json | Set-Content -Encoding UTF8 $status
