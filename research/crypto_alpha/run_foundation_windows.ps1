param([switch]$Worker,[string]$Config="configs/foundation_v1.json")
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
Set-Location $root
$Config=(Resolve-Path $Config).Path
$spec=Get-Content -Raw $Config | ConvertFrom-Json
$out=[IO.Path]::GetFullPath((Join-Path $root $spec.output))
$outputParent=[IO.Path]::GetFullPath((Join-Path $root '../../outputs/crypto_alpha'))
if ((Split-Path $out -Parent) -ne $outputParent -or (Split-Path $out -Leaf) -notlike 'foundation_*') {
    throw 'Expected isolated foundation output'
}
if ((Get-PSDrive -Name $out.Substring(0,1)).Free -lt ($spec.disk_reserve_gib * 1GB)) { throw 'Disk reserve' }
$name="CryptoAlpha_$(Split-Path $out -Leaf)"
if (-not $Worker) {
    if ((Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).State -eq 'Running') {
        throw 'This benchmark output already has an active job'
    }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $argument='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Worker -Config "{1}"' -f $PSCommandPath,$Config
    $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $root
    $principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -Priority 7
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
    return
}
$python=Join-Path $root '.venv-cpu/Scripts/python.exe'
$env:OMP_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
$env:PYTHONWARNINGS='ignore::DeprecationWarning'
New-Item -Path $out -ItemType Directory -Force | Out-Null
$logDir=Join-Path $outputParent 'foundation_logs'
New-Item -Path $logDir -ItemType Directory -Force | Out-Null
$argsList=@('-B','-u','-m','foundation.main','--config',('"{0}"' -f $Config))
$p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $logDir "$name.log") -RedirectStandardError (Join-Path $logDir "$name.err.log")
if ($p.ExitCode -ne 0) { throw "Foundation failed: $($p.ExitCode)" }
