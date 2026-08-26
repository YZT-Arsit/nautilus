$root = "D:\nautilus"
Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\internal\post_phase5a_delivery.ps1") `
    -WorkingDirectory $root -WindowStyle Hidden
