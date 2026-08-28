$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$audit = "$root\outputs\internal_audit\strategy_workbook"
Set-Location $root
New-Item -ItemType Directory -Force $audit | Out-Null
@{status="RUNNING";started_at=(Get-Date).ToUniversalTime().ToString("o");phase6d_started=$false} |
    ConvertTo-Json | Set-Content "$audit\phase6c_pipeline_status.json" -Encoding UTF8
$runOut = "$audit\phase6c_run_stdout.log"
$runErr = "$audit\phase6c_run_stderr.log"
$process = Start-Process -FilePath $python -ArgumentList "-m","scripts.internal.run_phase6c_conditional_replication" -WorkingDirectory $root -Wait -PassThru -NoNewWindow -RedirectStandardOutput $runOut -RedirectStandardError $runErr
if ($process.ExitCode -ne 0) {
    @{status="FAILED";exit_code=$process.ExitCode;finished_at=(Get-Date).ToUniversalTime().ToString("o");phase6d_started=$false} |
        ConvertTo-Json | Set-Content "$audit\phase6c_pipeline_status.json" -Encoding UTF8
    exit $process.ExitCode
}
$testArgs = @("-m","pytest","-q","tests\unit_tests\scripts\test_phase6c_conditional_replication.py","tests\unit_tests\strategy_framework\test_phase4c_cross_symbol.py","tests\unit_tests\scripts\test_phase6b_cost_episode_audit.py","tests\unit_tests\results\test_trade_episode.py")
$tests = Start-Process -FilePath $python -ArgumentList $testArgs -WorkingDirectory $root -Wait -PassThru -NoNewWindow -RedirectStandardOutput "$audit\phase6c_tests.log" -RedirectStandardError "$audit\phase6c_tests_stderr.log"
if ($tests.ExitCode -ne 0) {
    @{status="FAILED_TESTS";exit_code=$tests.ExitCode;finished_at=(Get-Date).ToUniversalTime().ToString("o");phase6d_started=$false} |
        ConvertTo-Json | Set-Content "$audit\phase6c_pipeline_status.json" -Encoding UTF8
    exit $tests.ExitCode
}
$archive = "$root\outputs\deliverables\phase6c_cross_symbol_falsification.zip"
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
@{status="DELIVERABLE_READY";finished_at=(Get-Date).ToUniversalTime().ToString("o");archive=$archive;sha256=$hash;phase6d_started=$false} |
    ConvertTo-Json | Set-Content "$audit\phase6c_pipeline_status.json" -Encoding UTF8
