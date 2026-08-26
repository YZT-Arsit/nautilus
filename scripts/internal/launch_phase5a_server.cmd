@echo off
cd /d D:\nautilus
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\internal\start_phase5a_baselines.ps1
start "Phase5A-Watcher" /min powershell -NoProfile -ExecutionPolicy Bypass -File D:\nautilus\scripts\internal\watch_phase5a_baselines.ps1
