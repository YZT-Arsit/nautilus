param([switch]$Worker)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
Set-Location $root
$Config=(Resolve-Path "configs/portfolio_research_v1.json").Path
$cfg=Get-Content -Raw $Config | ConvertFrom-Json
$out=[IO.Path]::GetFullPath((Join-Path $root $cfg.output))
$parent=[IO.Path]::GetFullPath((Join-Path $root '../../outputs/crypto_alpha'))
if ((Split-Path $out -Parent) -ne $parent -or (Split-Path $out -Leaf) -notlike 'portfolio_research_v1') {throw 'Research output path'}
if ((Get-PSDrive -Name $out.Substring(0,1)).Free -lt ($cfg.disk_reserve_gib * 1GB)) {throw 'Disk reserve'}
$name="CryptoAlpha_$(Split-Path $out -Leaf)"
if (-not $Worker) {
    if ((Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).State -eq 'Running') {throw 'Task already running'}
    $remaining=Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -match '[ -]m portfolio_research\.main(?:\s|$)'
    }
    if ($remaining) {throw 'A portfolio research worker already owns this output'}
    $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arg='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Worker' -f $PSCommandPath
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
$logs=Join-Path $parent 'portfolio_research_logs'
New-Item -Path $logs -ItemType Directory -Force | Out-Null
$argsList=@('-B','-u','-m',("portfolio_research.main"))
$p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
if ($p.ExitCode -ne 0) {throw "Portfolio research failed: $($p.ExitCode)"}
$verify=Start-Process -FilePath $python -ArgumentList @('-B','-u','-m','portfolio_research.verify') -PassThru -Wait -NoNewWindow `
    -RedirectStandardOutput (Join-Path $logs "$name.verify.log") -RedirectStandardError (Join-Path $logs "$name.verify.err.log")
if ($verify.ExitCode -ne 0) {throw "Portfolio delivery validation failed: $($verify.ExitCode)"}
