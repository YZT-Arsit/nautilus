# Install PortableGit under the project (no admin, no global install).
#
# Why this script exists
# ----------------------
# `winget install Git.Git` failed on this server with InternetOpenUrl
# 0x80072efd — Microsoft's winget CDN is blocked at the firewall while
# github.com is reachable. PortableGit (the official self-contained build of
# Git for Windows) ships as a normal .zip on GitHub releases, so we install
# it by:
#   1. Resolving the latest stable Git-for-Windows release via the GitHub API
#      (or using a pinned fallback if --version is supplied).
#   2. Downloading the MinGit-*-64-bit.zip asset (~50 MB).
#   3. Extracting to .\.tools\PortableGit\ inside the project.
#   4. (Optionally) emitting a one-line PATH-export helper to stdout.
#
# Idempotent: skips download if the extraction already exists, unless -Force.
# Project-local: never touches the system or user PATH.
#
# Usage
# -----
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_git_portable.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_git_portable.ps1 -Force
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_git_portable.ps1 -Version "2.47.0"
#
# After install, add to PATH for the *current session* with:
#   $env:Path = "$pwd\.tools\PortableGit\cmd;$env:Path"
#
# Or persist for the current user:
#   [Environment]::SetEnvironmentVariable("Path", "$pwd\.tools\PortableGit\cmd;$env:Path", "User")

[CmdletBinding()]
param(
    [string]$Version = "",         # e.g. "2.47.0"; empty => query GitHub for latest
    [string]$InstallRoot = "",     # default: <repo-root>\.tools\PortableGit
    [string]$SourceZip = "",       # path to a pre-downloaded MinGit-*.zip (offline / network-restricted)
    [switch]$Force                 # re-download even if already present
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # suppress noisy progress bars

# Anchor everything under the repository root (the parent of /scripts).
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrEmpty($InstallRoot)) {
    $InstallRoot = Join-Path $repoRoot ".tools\PortableGit"
}

$gitExe = Join-Path $InstallRoot "cmd\git.exe"
if ((Test-Path $gitExe) -and -not $Force) {
    $version = & $gitExe --version
    Write-Host "PortableGit already present: $version"
    Write-Host "Location: $InstallRoot"
    Write-Host "To use in this session: `$env:Path = `"$InstallRoot\cmd;`$env:Path`""
    exit 0
}

function Resolve-DownloadUrl {
    param([string]$Pin)
    if ($Pin -ne "") {
        $tag = "v$($Pin).windows.1"
        $file = "MinGit-$Pin-64-bit.zip"
        return "https://github.com/git-for-windows/git/releases/download/$tag/$file"
    }
    Write-Host "Querying GitHub for latest Git-for-Windows release ..."
    $api = "https://api.github.com/repos/git-for-windows/git/releases/latest"
    $rel = Invoke-RestMethod -UseBasicParsing -Uri $api -Headers @{ "User-Agent" = "qfe-install-script" }
    $asset = $rel.assets | Where-Object { $_.name -match "^MinGit-.*-64-bit\.zip$" } | Select-Object -First 1
    if ($null -eq $asset) {
        throw "No MinGit-*-64-bit.zip asset found in release $($rel.tag_name)"
    }
    Write-Host ("Latest release: {0}  asset: {1}  size: {2:N0} bytes" -f $rel.tag_name, $asset.name, $asset.size)
    return $asset.browser_download_url
}

$cleanupTmp = $false
if ($SourceZip -ne "") {
    if (-not (Test-Path $SourceZip)) {
        throw "SourceZip not found: $SourceZip"
    }
    $tmpZip = (Resolve-Path $SourceZip).Path
    Write-Host "Using pre-downloaded zip: $tmpZip"
} else {
    $url = Resolve-DownloadUrl -Pin $Version
    $tmpZip = Join-Path $env:TEMP ("MinGit-{0}.zip" -f ([Guid]::NewGuid().ToString("N").Substring(0, 8)))
    $cleanupTmp = $true
    Write-Host "Downloading: $url"
    Write-Host "  -> $tmpZip"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmpZip -TimeoutSec 300
}

try {

    if (Test-Path $InstallRoot) {
        if ($Force) {
            Write-Host "Removing existing install at $InstallRoot"
            Remove-Item -Recurse -Force $InstallRoot
        } else {
            throw "Install root $InstallRoot already exists but git.exe was missing; use -Force to overwrite"
        }
    }
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null

    Write-Host "Extracting to $InstallRoot ..."
    Expand-Archive -Path $tmpZip -DestinationPath $InstallRoot -Force

    if (-not (Test-Path $gitExe)) {
        throw "Extraction completed but $gitExe was not found"
    }

    $version = & $gitExe --version
    Write-Host ""
    Write-Host "OK installed PortableGit: $version"
    Write-Host "Location: $InstallRoot"
    Write-Host ""
    Write-Host "To use in this session:"
    Write-Host "  `$env:Path = `"$InstallRoot\cmd;`$env:Path`""
    Write-Host ""
    Write-Host "To persist for the current user:"
    Write-Host "  [Environment]::SetEnvironmentVariable('Path', `"$InstallRoot\cmd;`" + [Environment]::GetEnvironmentVariable('Path','User'), 'User')"
} finally {
    if ($cleanupTmp -and (Test-Path $tmpZip)) {
        Remove-Item -Force $tmpZip
    }
}
