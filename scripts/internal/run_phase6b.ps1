$ErrorActionPreference = "Stop"
$root = "D:\nautilus"
$python = "$root\.venv\Scripts\python.exe"
$audit = "$root\outputs\internal_audit\strategy_workbook"
Set-Location $root
New-Item -ItemType Directory -Force $audit | Out-Null
@{status="RUNNING";started_at=(Get-Date).ToUniversalTime().ToString("o");new_strategy_backtests=0} |
    ConvertTo-Json | Set-Content "$audit\phase6b_pipeline_status.json" -Encoding UTF8
& $python -m scripts.internal.build_phase6b_cost_episode_audit *> "$audit\phase6b_run.log"
if ($LASTEXITCODE -ne 0) {
    @{status="FAILED";exit_code=$LASTEXITCODE;finished_at=(Get-Date).ToUniversalTime().ToString("o")} |
        ConvertTo-Json | Set-Content "$audit\phase6b_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
& $python -m pytest -q `
    tests\unit_tests\scripts\test_phase6b_cost_episode_audit.py `
    tests\unit_tests\strategy_framework\test_phase4b_cost_episode_audit.py `
    tests\unit_tests\results\test_trade_episode.py `
    tests\unit_tests\scripts\test_phase6a_expanded_screen.py *> "$audit\phase6b_tests.log"
if ($LASTEXITCODE -ne 0) {
    @{status="FAILED_TESTS";exit_code=$LASTEXITCODE;finished_at=(Get-Date).ToUniversalTime().ToString("o")} |
        ConvertTo-Json | Set-Content "$audit\phase6b_pipeline_status.json" -Encoding UTF8
    exit $LASTEXITCODE
}
$archive = "$root\outputs\deliverables\phase6b_cost_episode_review.zip"
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
@{status="DELIVERABLE_READY";finished_at=(Get-Date).ToUniversalTime().ToString("o");archive=$archive;sha256=$hash;new_strategy_backtests=0;phase6c_started=$false} |
    ConvertTo-Json | Set-Content "$audit\phase6b_pipeline_status.json" -Encoding UTF8
