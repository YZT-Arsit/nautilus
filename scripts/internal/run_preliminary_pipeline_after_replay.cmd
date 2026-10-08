@echo off
setlocal
cd /d D:\nautilus
set EXP=D:\nautilus\paper_trading\experiments\paper_clean_active_active_ab_b9ceff12c3ba_20261006_141701
set WORKER=%EXP%\workers\BTCUSDT
set SCRATCH=D:\nautilus\outputs\baseline_evaluation\paper_market_data_continuity_repair\gap_sensitivity_paper_clean_active_active_ab_b9ceff12c3ba_20261006_141701
set DELIVERY=D:\nautilus\outputs\deliverables\direct_maker_24h_preliminary
set LOG=%EXP%\logs\preliminary_pipeline.log

:WAIT_REPLAY
if not exist %EXP%\logs\preliminary_as_recorded_replay.exitcode (
  ping 127.0.0.1 -n 31 >nul
  goto WAIT_REPLAY
)
set /p REPLAY_RC=<%EXP%\logs\preliminary_as_recorded_replay.exitcode
if not "%REPLAY_RC%"=="0" (
  echo As-recorded replay failed with exit code %REPLAY_RC%.>%LOG%
  echo 2 >%EXP%\logs\preliminary_pipeline.exitcode
  exit /b 2
)

D:\nautilus\.venv\Scripts\python.exe D:\nautilus\scripts\internal\package_preliminary_failed_ab.py --source %EXP% --output %DELIVERY% --gap-sensitivity-json %SCRATCH%\gap_sensitivity_result.json --gap-source-dir %SCRATCH% > %LOG% 2>&1
if errorlevel 1 goto FAILED

echo 0 >%EXP%\logs\preliminary_pipeline.exitcode
exit /b 0

:FAILED
echo %errorlevel% >%EXP%\logs\preliminary_pipeline.exitcode
exit /b %errorlevel%
