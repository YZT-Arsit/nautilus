$root = "D:\nautilus"
Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\internal\run_phase5a_single.ps1") `
    -WorkingDirectory $root -WindowStyle Hidden
