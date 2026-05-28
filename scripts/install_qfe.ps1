# One-command install for quant_feature_engine on Windows.
#
# Goal: from a fresh checkout, get to "39/39 tests green" with one invocation.
#
# What it does
# ------------
# 1. Resolves a Python interpreter (--PythonExe, env QFE_PYTHON, or the
#    project's existing .venv/Scripts/python.exe).
# 2. Installs from requirements.lock.txt (exact pinned versions known to pass
#    validation). If the lockfile is missing, falls back to requirements.txt
#    and prints a loud warning.
# 3. Verifies the install by importing every runtime dep.
# 4. Runs the pytest suite as a smoke test (skip with -SkipTests).
#
# This script is project-local: it never touches the system Python or system
# PATH. The interpreter passed in (or discovered) is the only thing it
# modifies (by pip-installing into its site-packages).
#
# Usage
# -----
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_qfe.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_qfe.ps1 -SkipTests
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_qfe.ps1 -PythonExe C:\custom\python.exe
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_qfe.ps1 -Upgrade

[CmdletBinding()]
param(
    [string]$PythonExe = "",   # explicit interpreter; otherwise auto-discover
    [switch]$Upgrade,          # pass --upgrade to pip (otherwise: only install what's missing)
    [switch]$SkipTests         # don't run pytest at the end
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$qfeDir = Join-Path $repoRoot "quant_feature_engine"
$lockFile = Join-Path $qfeDir "requirements.lock.txt"
$reqFile = Join-Path $qfeDir "requirements.txt"

function Resolve-PythonExe {
    if ($PythonExe -ne "" -and (Test-Path $PythonExe)) { return (Resolve-Path $PythonExe).Path }
    if ($env:QFE_PYTHON -ne $null -and $env:QFE_PYTHON -ne "" -and (Test-Path $env:QFE_PYTHON)) {
        return (Resolve-Path $env:QFE_PYTHON).Path
    }
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { return (Resolve-Path $venvPython).Path }
    throw "No Python interpreter found. Provide -PythonExe, set `$env:QFE_PYTHON, or create a .venv at $repoRoot\.venv."
}

$python = Resolve-PythonExe
Write-Host "Python: $python"
& $python -V

# Pick the input file.
$src = $null
if (Test-Path $lockFile) {
    $src = $lockFile
    Write-Host "Installing from lockfile: $lockFile"
} elseif (Test-Path $reqFile) {
    $src = $reqFile
    Write-Warning "requirements.lock.txt not found; falling back to requirements.txt. Versions may drift."
} else {
    throw "Neither $lockFile nor $reqFile exists; cannot install."
}

# Pip install.
$pipArgs = @("-m", "pip", "install", "-r", $src)
if ($Upgrade) { $pipArgs += "--upgrade" }
Write-Host ""
Write-Host "> $python $($pipArgs -join ' ')"
& $python @pipArgs
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

# Verify imports.
# Use a single-quoted here-string so PowerShell does not interpolate or strip
# any Python-side quotes/dollar-signs. This is a literal block.
Write-Host ""
Write-Host "Verifying imports ..."
$verify = @'
import sys
mods = ['polars', 'pyarrow', 'yaml', 'pytest']
fail = []
for m in mods:
    try:
        mod = __import__(m)
        ver = getattr(mod, '__version__', '?')
        print('  ok  ' + m.ljust(10) + ' ' + str(ver))
    except Exception as e:
        print('  ERR ' + m.ljust(10) + ' ' + repr(e))
        fail.append(m)
sys.exit(1 if fail else 0)
'@
& $python -c $verify
if ($LASTEXITCODE -ne 0) { throw "Import verification failed" }

# Smoke test.
if (-not $SkipTests) {
    Write-Host ""
    Write-Host "Running pytest suite ..."
    Push-Location $repoRoot
    try {
        & $python -m pytest "quant_feature_engine\tests" -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "OK quant_feature_engine install + smoke test complete."
