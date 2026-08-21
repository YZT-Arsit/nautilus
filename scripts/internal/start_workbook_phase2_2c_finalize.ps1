$ErrorActionPreference = "Stop"
Set-Location "D:\nautilus"

$auditRoot = "outputs\internal_audit\strategy_workbook"
New-Item -ItemType Directory -Force $auditRoot | Out-Null
$waitLog = Join-Path $auditRoot "phase2_2c_finalize_wait.log"

$predecessors = @(
    "Nautilus_Phase2_2C_Workbook",
    "Nautilus_Phase2_2C_Shard_1",
    "Nautilus_Phase2_2C_Shard_2",
    "Nautilus_Phase2_2C_Shard_3",
    "Nautilus_Phase2_2C_Repair",
    "Nautilus_Phase2_2C_RepairShard_1",
    "Nautilus_Phase2_2C_RepairShard_2",
    "Nautilus_Phase2_2C_Repair_0422"
)

while ($true) {
    $running = @(
        $predecessors | Where-Object {
            $task = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
            $task -and $task.State -eq "Running"
        }
    )
    $stamp = (Get-Date).ToUniversalTime().ToString("o")
    Add-Content -Path $waitLog -Value "$stamp running_predecessors=$($running -join ',')"
    if ($running.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 60
}

$log = Join-Path $auditRoot "phase2_2c_finalize_pipeline.log"
& "D:\nautilus\.venv\Scripts\python.exe" scripts\internal\run_workbook_phase2_2c_pipeline.py `
    --workbook "时序策略.xlsx" *> $log
if ($LASTEXITCODE -ne 0) {
    throw "Phase 2.2C final pipeline failed with exit code $LASTEXITCODE"
}
