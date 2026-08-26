@echo off
cd /d D:\nautilus
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\nautilus\scripts\internal\run_phase5a_single.ps1 > D:\nautilus\outputs\internal_audit\strategy_workbook\phase5a_scheduled_task.log 2>&1
