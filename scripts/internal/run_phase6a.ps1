$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$audit = "$root\outputs\internal_audit\strategy_workbook"
Set-Location $root
New-Item -ItemType Directory -Force $audit | Out-Null
@{status="RUNNING";started_at=(Get-Date).ToUniversalTime().ToString("o");new_five_year_backtests=0} |
    ConvertTo-Json | Set-Content "$audit\phase6a_pipeline_status.json" -Encoding UTF8
& $python -m scripts.internal.build_phase6a_expanded_screen *> "$audit\phase6a_run.log"
if ($LASTEXITCODE -ne 0) {
    @{status="FAILED";exit_code=$LASTEXITCODE;finished_at=(Get-Date).ToUniversalTime().ToString("o")} |
        ConvertTo-Json | Set-Content "$audit\phase6a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
& $python -m pytest -q --ignore-glob=**/._* `
    tests\unit_tests\scripts\test_phase6a_expanded_screen.py `
    tests\unit_tests\results\test_trade_episode.py `
    tests\unit_tests\results\test_strategy_evaluation.py *> "$audit\phase6a_tests.log"
if ($LASTEXITCODE -ne 0) {
    @{status="FAILED_TESTS";exit_code=$LASTEXITCODE;finished_at=(Get-Date).ToUniversalTime().ToString("o")} |
        ConvertTo-Json | Set-Content "$audit\phase6a_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
$archive = "$root\outputs\deliverables\phase6a_expanded_strategy_review.zip"
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$members = @((Get-ChildItem "$root\outputs\baseline_evaluation\phase6a" -Recurse -File)).Count
@{status="DELIVERABLE_READY";finished_at=(Get-Date).ToUniversalTime().ToString("o");archive=$archive;sha256=$hash;members=$members;new_five_year_backtests=0} |
    ConvertTo-Json | Set-Content "$audit\phase6a_pipeline_status.json" -Encoding UTF8
