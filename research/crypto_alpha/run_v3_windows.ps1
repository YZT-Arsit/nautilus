param([switch]$Install,[ValidateSet("acquire","research","finalize")][string]$Phase="research")
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=$PSScriptRoot
$config=Get-Content -Raw (Join-Path $root 'configs/v3.json') | ConvertFrom-Json
$out=[IO.Path]::GetFullPath((Join-Path $root $config.output))
$python=Join-Path $root '.venv-cpu/Scripts/python.exe'
$name="CryptoAlphaV3_$Phase"
if ($Install) {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $argument='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Phase {1}' -f $PSCommandPath,$Phase
    $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $root
    $principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -Priority 7
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
    return
}
New-Item -Path $out -ItemType Directory -Force | Out-Null
if ((Get-PSDrive -Name $out.Substring(0,1)).Free -lt ($config.disk_reserve_gib * 1GB)) { throw 'Disk reserve' }
Set-Location $root
$env:OMP_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='2'
$env:PYTHONWARNINGS='ignore::DeprecationWarning'
if ($Phase -eq 'finalize') {
    foreach ($prior in @("acquire","research")) {
        $task="CryptoAlphaV3_$prior"
        while ((Get-ScheduledTask -TaskName $task).State -eq "Running") { Start-Sleep -Seconds 15 }
        if ((Get-ScheduledTaskInfo -TaskName $task).LastTaskResult -ne 0) { throw "$task did not complete" }
    }
}
$argsList=@('-B','-u','-m',"v3.$Phase")
$p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $out "$Phase.log") -RedirectStandardError (Join-Path $out "$Phase.err.log")
if ($p.ExitCode -ne 0) { throw "V3 $Phase failed: $($p.ExitCode)" }
