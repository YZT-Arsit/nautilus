param(
    [switch]$Install,
    [switch]$Start,
    [string]$TaskName = "CryptoAlphaV1Research",
    [double]$FreeSpaceGiB = 12,
    [int]$WaitForPid = 0
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$moduleRoot = $PSScriptRoot
$config = Get-Content -Raw (Join-Path $moduleRoot 'configs/v1.json') | ConvertFrom-Json
$output = [IO.Path]::GetFullPath((Join-Path $moduleRoot $config.output_root))
$parent = Split-Path -Parent $output
$drive = $output.Substring(0, 1)
$python = Join-Path $moduleRoot '.venv-cpu/Scripts/python.exe'

if ($Install) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $argument = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -FreeSpaceGiB {1} -WaitForPid {2}' -f $PSCommandPath,$FreeSpaceGiB,$WaitForPid
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $moduleRoot
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) -Priority 7
    Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Description 'Offline Crypto Alpha CPU research' -Force | Out-Null
    if ($Start) { Start-ScheduledTask -TaskName $TaskName }
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
    return
}

function Assert-Space {
    $free = (Get-PSDrive -Name $drive).Free
    if ($null -eq $free -or $free -lt ($FreeSpaceGiB * 1GB)) {
        throw "Workspace free-space reserve is $FreeSpaceGiB GiB"
    }
    "UTC=$([DateTime]::UtcNow.ToString('o')) disk_free_gib=$([Math]::Round($free / 1GB, 3))" |
        Out-File (Join-Path $parent 'scheduled_training.log') -Append -Encoding utf8
}

function Invoke-Stage($entry, $label, [string[]]$extraArgs=@()) {
    $arguments = @('-B', $entry) + $extraArgs
    $settings = @{
        FilePath=$python
        ArgumentList=$arguments
        PassThru=$true
        Wait=$true
        NoNewWindow=$true
        RedirectStandardOutput=(Join-Path $parent "$label.scheduled.log")
        RedirectStandardError=(Join-Path $parent "$label.scheduled.err.log")
    }
    $process = Start-Process @settings
    if ($process.ExitCode -ne 0) { throw "$label failed: $($process.ExitCode)" }
}

Set-Location -LiteralPath $moduleRoot
New-Item -Path $parent -ItemType Directory -Force | Out-Null
if ($WaitForPid -gt 0) { Wait-Process -Id $WaitForPid -ErrorAction SilentlyContinue }
Assert-Space
if (-not (Test-Path (Join-Path $output 'derived/prepared.json'))) {
    Invoke-Stage 'training\prepare.py' 'prepare'
}
$env:CRYPTO_ALPHA_FREE_SPACE_GIB = "$FreeSpaceGiB"
Invoke-Stage 'training\run.py' 'training' @('--resume')
Assert-Space
Invoke-Stage 'tests\verify_artifacts.py' 'verification'
"SCHEDULED_RUN_COMPLETE utc=$([DateTime]::UtcNow.ToString('o'))" |
    Out-File (Join-Path $parent 'scheduled_training.log') -Append -Encoding utf8
