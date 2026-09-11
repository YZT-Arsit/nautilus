$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$script = "$root\scripts\internal\run_execution_review_maker_comparison.py"
$selection = "$root\outputs\baseline_evaluation\execution_method_and_reverse_review\reverse_validation\reverse_candidate_manifest.csv"
$logRoot = "$root\outputs\baseline_evaluation\execution_method_and_reverse_review\logs"
$symbols = @("XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT")
$failures = @()
for ($offset=0; $offset -lt $symbols.Count; $offset+=2) {
    $running=@()
    $last=[Math]::Min($offset+1,$symbols.Count-1)
    foreach($symbol in $symbols[$offset..$last]) {
        $summary="$root\outputs\baseline_evaluation\execution_method_and_reverse_review\reverse_maker_comparison\shards\$symbol\run_summary.json"
        if(Test-Path $summary) { $content=Get-Content $summary -Raw | ConvertFrom-Json; if($content.status -eq "PASSED"){continue} }
        $out="$logRoot\reverse_maker_$symbol.stdout.log"; $err="$logRoot\reverse_maker_$symbol.stderr.log"
        $process=Start-Process -FilePath $python -ArgumentList @($script,"--repo",$root,"--symbols",$symbol,"--work","$root\outputs\baseline_evaluation\execution_method_and_reverse_review","--result-subdir","..\reverse_maker_comparison\shards\$symbol","--case-selection",$selection,"--reverse-targets") -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
        $process | Add-Member -NotePropertyName Symbol -NotePropertyValue $symbol
        $running += $process
    }
    foreach($entry in $running){
        $entry.WaitForExit()
        if($entry.ExitCode -ne 0){$failures += $entry.Symbol}
    }
}
if($failures.Count -gt 0){throw "Reverse maker shards failed: $($failures -join ',')"}
