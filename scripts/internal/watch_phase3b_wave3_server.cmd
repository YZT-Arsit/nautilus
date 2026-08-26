@echo off
cd /d D:\nautilus
.venv\Scripts\python.exe scripts\internal\watch_phase3b_wave3.py 1>>outputs\parameter_search\phase3b_wave3\logs\watcher_stdout.log 2>>outputs\parameter_search\phase3b_wave3\logs\watcher_stderr.log
echo exit_code=%ERRORLEVEL%>>outputs\parameter_search\phase3b_wave3\logs\watcher_stdout.log
