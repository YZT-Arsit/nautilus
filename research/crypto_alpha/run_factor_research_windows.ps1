param([switch]$Worker,[string]$Config="configs/factor_research_v1.json",[string]$Factor="")
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
Set-Location $root
$Config=(Resolve-Path $Config).Path
$cfg=Get-Content -Raw $Config | ConvertFrom-Json
$out=[IO.Path]::GetFullPath((Join-Path $root $cfg.output))
$parent=[IO.Path]::GetFullPath((Join-Path $root '../../outputs/crypto_alpha'))
if ((Split-Path $out -Parent) -ne $parent -or (Split-Path $out -Leaf) -notlike 'factor_research_*') {throw 'Research output path'}
if ($Factor -and $Factor -notmatch '^[A-Za-z_][A-Za-z_0-9]*$') {throw 'Factor identifier'}
if ((Get-PSDrive -Name $out.Substring(0,1)).Free -lt ($cfg.disk_reserve_gib * 1GB)) {throw 'Disk reserve'}
$name="CryptoAlpha_$(Split-Path $out -Leaf)"
if ($Factor) {$name=$name + "_" + $Factor}
if (-not $Worker) {
    if ((Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).State -eq 'Running') {throw 'Task already running'}
    $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arg='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Worker -Config "{1}"' -f $PSCommandPath,$Config
    if ($Factor) {$arg+=' -Factor ' + $Factor}
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
$python=Join-Path $root '.venv-cpu/Scripts/python.exe'
$logs=Join-Path $parent 'factor_research_logs'
New-Item -Path $logs -ItemType Directory -Force | Out-Null
$argsList=@('-B','-u','-m','factor_research.main','--config',('"{0}"' -f $Config))
if ($Factor) {$argsList+=@('--factor',$Factor)}
$p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
if ($p.ExitCode -ne 0) {throw "Factor research failed: $($p.ExitCode)"}
