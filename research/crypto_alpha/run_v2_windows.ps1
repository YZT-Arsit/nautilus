param([switch]$Install,[ValidateSet("portfolios","research","finalize")][string]$Phase="research",[string]$Config="configs/v2.json")
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=$PSScriptRoot
$settingsFile=Get-Content -Raw (Join-Path $root $Config) | ConvertFrom-Json
$settingsBase=if ($settingsFile.extends) { Get-Content -Raw (Join-Path $root $settingsFile.extends) | ConvertFrom-Json } else { $settingsFile }
$out=[IO.Path]::GetFullPath((Join-Path $root $settingsFile.output_root))
$python=Join-Path $root '.venv-cpu/Scripts/python.exe'
$suffix=if ($Config -eq "configs/v2.json") { "" } else { "_" + [IO.Path]::GetFileNameWithoutExtension($Config).Replace(".", "_") }
$name="CryptoAlphaV2_$Phase$suffix"
if ($Install) {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $argument='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Phase {1} -Config "{2}"' -f $PSCommandPath,$Phase,$Config
    $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $root
    $principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -Priority 7
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
    return
}
New-Item -Path $out -ItemType Directory -Force | Out-Null
$free=(Get-PSDrive -Name $out.Substring(0,1)).Free
if ($free -lt ($settingsBase.free_space_gib * 1GB)) { throw 'Insufficient disk reserve' }
Set-Location $root
$env:CRYPTO_ALPHA_V2_CONFIG=$Config
$env:OMP_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='2'
$env:PYTHONWARNINGS='ignore::DeprecationWarning'
function Invoke-ResearchStage($stage,$label) {
    $argsList=@('-B','-u','-m',$stage)
    $p=Start-Process -FilePath $python -ArgumentList $argsList -PassThru -Wait -NoNewWindow -RedirectStandardOutput (Join-Path $out "$label.log") -RedirectStandardError (Join-Path $out "$label.err.log")
    if ($p.ExitCode -ne 0) { throw "$stage failed: $($p.ExitCode)" }
}
if ($Phase -eq "finalize") {
    foreach ($prior in @("portfolios","research")) {
        $task="CryptoAlphaV2_$prior$suffix"
        while ((Get-ScheduledTask -TaskName $task).State -eq "Running") { Start-Sleep -Seconds 15 }
        if ((Get-ScheduledTaskInfo -TaskName $task).LastTaskResult -ne 0) { throw "$task did not complete" }
    }
    Invoke-ResearchStage "v2.verify" "artifact_checks"
    Invoke-ResearchStage "v2.report" "finalize"
} else {
    $stage=if ($Phase -eq 'portfolios') { 'v2.portfolios' } else { 'v2.run' }
    Invoke-ResearchStage $stage $Phase
}
