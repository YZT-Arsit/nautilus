param(
    [string]$ParentExperiment = "D:\nautilus\paper_trading\experiments\paper_20260928_4c9ee2b28d67",
    [string]$CandidateId = "pc_2fe14acb95eac19f88d4",
    [string]$AuditRoot = "D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair",
    [string]$DeliveryRoot = "D:\nautilus\outputs\deliverables\direct_maker_24h_clean_ab"
)

$ErrorActionPreference = "Stop"
$repo = "D:\nautilus"
$python = "$repo\.venv\Scripts\python.exe"
$prepare = "$repo\scripts\internal\prepare_focused_paper_experiment.py"
$gate = "$repo\scripts\internal\run_continuity_gate_and_clean_ab.ps1"
$stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd_HHmmss")
$smokeId = "paper_network_smoke_30m_$stamp"
$gateId = "paper_continuity_2h_$stamp"
$smokeExperiment = Join-Path "$repo\paper_trading\experiments" $smokeId
$continuityExperiment = Join-Path "$repo\paper_trading\experiments" $gateId

& $python $prepare --parent $ParentExperiment --output $smokeExperiment `
    --experiment-id $smokeId --candidate-id $CandidateId --purpose "P0_30M_NETWORK_ONLY_SMOKE"
if ($LASTEXITCODE -ne 0) { exit 2 }
& $python $prepare --parent $ParentExperiment --output $continuityExperiment `
    --experiment-id $gateId --candidate-id $CandidateId --purpose "P0_CLEAN_2H_CONTINUITY_GATE"
if ($LASTEXITCODE -ne 0) { exit 2 }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $gate `
    -ParentExperiment $ParentExperiment `
    -SmokeExperiment $smokeExperiment `
    -ContinuityExperiment $continuityExperiment `
    -CandidateId $CandidateId `
    -AuditRoot $AuditRoot `
    -DeliveryRoot $DeliveryRoot
exit $LASTEXITCODE
