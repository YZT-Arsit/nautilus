$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"
& ".venv\Scripts\python.exe" -m scripts.internal.build_all_converted_workbook_results --workers 2
if ($LASTEXITCODE -ne 0) {
    throw "Final workbook consolidation failed with exit code $LASTEXITCODE"
}
