$ErrorActionPreference = "Stop"

$variableNames = @(
    "BINANCE_DEMO_API_KEY",
    "BINANCE_DEMO_API_SECRET",
    "BINANCE_DEMO_DIRECT_API_KEY",
    "BINANCE_DEMO_DIRECT_API_SECRET"
)

$variables = foreach ($name in $variableNames) {
    $processValue = (Get-Item "Env:$name" -ErrorAction SilentlyContinue).Value
    $userValue = [Environment]::GetEnvironmentVariable($name, "User")
    $machineValue = [Environment]::GetEnvironmentVariable($name, "Machine")
    [pscustomobject]@{
        Name = $name
        ProcessPresent = -not [string]::IsNullOrEmpty($processValue)
        UserPresent = -not [string]::IsNullOrEmpty($userValue)
        MachinePresent = -not [string]::IsNullOrEmpty($machineValue)
    }
}

$roots = @(
    "D:\nautilus",
    "C:\ProgramData\nautilus",
    "$env:USERPROFILE\.nautilus",
    "$env:USERPROFILE\.config\nautilus"
)
$files = @()
foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem $root -Force -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '(^\.env|secret|credential|demo)' -and $_.Length -lt 1MB
        } |
        ForEach-Object {
            try {
                $text = [IO.File]::ReadAllText($_.FullName)
                $present = @($variableNames | Where-Object { $text.Contains($_) })
                if ($present.Count -gt 0) {
                    $files += [pscustomobject]@{
                        Path = $_.FullName
                        Bytes = $_.Length
                        VariableNames = $present
                    }
                }
            } catch { }
        }
}

$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -match 'Demo|Binance|Nautilus' } |
    ForEach-Object {
        [pscustomobject]@{
            TaskName = $_.TaskName
            State = [string]$_.State
            UserId = $_.Principal.UserId
            Actions = @($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" })
        }
    }

[pscustomobject]@{
    Variables = @($variables)
    SecretConfigFiles = @($files)
    ScheduledTasks = @($tasks)
} | ConvertTo-Json -Depth 6
