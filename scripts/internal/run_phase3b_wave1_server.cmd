@echo off
setlocal
cd /d D:\nautilus
if not exist outputs\parameter_search\phase3b_wave1\logs mkdir outputs\parameter_search\phase3b_wave1\logs
.venv\Scripts\python.exe scripts\internal\run_phase3b_wave1.py --market-root D:\nautilus\historical_data\market_data --strategy-root D:\nautilus\strategies --output-root D:\nautilus\outputs\parameter_search\phase3b_wave1 --workers 8 1>>outputs\parameter_search\phase3b_wave1\logs\server_stdout.log 2>>outputs\parameter_search\phase3b_wave1\logs\server_stderr.log
echo exit_code=%ERRORLEVEL%>>outputs\parameter_search\phase3b_wave1\logs\server_stdout.log
endlocal
