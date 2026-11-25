param([switch]$Worker,[ValidateSet("acquire","study","finalize")][string]$Phase="study")
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
Set-Location $root
$Config=(Resolve-Path "configs/model_research_v1.json").Path
$cfg=Get-Content -Raw $Config | ConvertFrom-Json
$out=[IO.Path]::GetFullPath((Join-Path $root $cfg.output))
$parent=[IO.Path]::GetFullPath((Join-Path $root '../../outputs/crypto_alpha'))
if ((Split-Path $out -Parent) -ne $parent -or (Split-Path $out -Leaf) -notlike 'model_research_v1') {throw 'Research output path'}
if ((Get-PSDrive -Name $out.Substring(0,1)).Free -lt ($cfg.disk_reserve_gib * 1GB)) {throw 'Disk reserve'}
$name="CryptoAlpha_$(Split-Path $out -Leaf)_$Phase"
if (-not $Worker) {
    if ((Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).State -eq 'Running') {throw 'Task already running'}
    $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arg='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Worker -Phase {1}' -f $PSCommandPath,$Phase
    $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $root
    $principal=New-ScheduledTaskPrincipal -UserId $who -LogonType S4U -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -Priority 7
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
    return
}
$env:OMP_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
$env:POLARS_MAX_THREADS='2'
$env:PYTHONWARNINGS='ignore::DeprecationWarning'
if ($Phase -eq 'finalize') {
    foreach ($dependency in @('acquire','study')) {
        $before="CryptoAlpha_model_research_v1_$dependency"
        while ((Get-ScheduledTask -TaskName $before).State -eq 'Running') {Start-Sleep -Seconds 10}
        if ((Get-ScheduledTaskInfo -TaskName $before).LastTaskResult -ne 0) {throw "$before did not complete"}
    }
}
$python=Join-Path $root '.venv-cpu/Scripts/python.exe'
$logs=Join-Path $parent 'model_research_logs'
New-Item -Path $logs -ItemType Directory -Force | Out-Null
$argsList=@('-B','-u','-m',("model_research.$Phase"))
$p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
if ($p.ExitCode -ne 0) {throw "Model research failed: $($p.ExitCode)"}
