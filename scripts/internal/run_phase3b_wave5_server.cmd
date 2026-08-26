@echo off
cd /d D:\nautilus
if not exist outputs\parameter_search\phase3b_wave5\logs mkdir outputs\parameter_search\phase3b_wave5\logs
.venv\Scripts\python.exe scripts\internal\run_phase3b_wave5.py --market-root D:\nautilus\historical_data\market_data --strategy-root D:\nautilus\strategies --output-root D:\nautilus\outputs\parameter_search\phase3b_wave5 --workers 6 1>>outputs\parameter_search\phase3b_wave5\logs\server_stdout.log 2>>outputs\parameter_search\phase3b_wave5\logs\server_stderr.log
echo exit_code=%ERRORLEVEL%>>outputs\parameter_search\phase3b_wave5\logs\server_stdout.log
