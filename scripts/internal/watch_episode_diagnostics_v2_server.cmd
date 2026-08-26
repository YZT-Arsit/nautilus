@echo off
setlocal
cd /d D:\nautilus
powershell -NoProfile -ExecutionPolicy Bypass -Command "$progress='D:\nautilus\outputs\parameter_search\phase3b_wave3\progress.json'; while ($true) { if (Test-Path $progress) { try { $state=(Get-Content $progress -Raw | ConvertFrom-Json).status } catch { $state='RUNNING' }; if ($state -ne 'RUNNING') { break } }; Start-Sleep -Seconds 60 }; & 'D:\nautilus\scripts\internal\run_episode_diagnostics_server.cmd'; exit $LASTEXITCODE"
exit /b %ERRORLEVEL%
