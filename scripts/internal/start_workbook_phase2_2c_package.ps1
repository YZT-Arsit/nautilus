$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"
& "D:\nautilus\.venv\Scripts\python.exe" scripts\internal\package_phase2_2c_review.py `
    --deliverable-root "D:\nautilus\outputs\deliverables\workbook_strategies_phase2_2c" `
    --audit-root "D:\nautilus\outputs\internal_audit\strategy_workbook" `
    --archive "D:\nautilus\outputs\deliverables\workbook_strategies_phase2_2c_review.zip" `
    --status "D:\nautilus\outputs\internal_audit\strategy_workbook\phase2_2c_pipeline_status.json" `
    *> "D:\nautilus\outputs\internal_audit\strategy_workbook\phase2_2c_package.log"
if ($LASTEXITCODE -ne 0) {
    throw "Phase 2.2C review packaging failed with exit code $LASTEXITCODE"
}
