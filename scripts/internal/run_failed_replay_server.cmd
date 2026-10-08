@echo off
setlocal
cd /d D:\nautilus
set EXP=D:\nautilus\paper_trading\experiments\paper_clean_active_active_ab_b9ceff12c3ba_20261006_141701
D:\nautilus\.venv\Scripts\python.exe D:\nautilus\scripts\internal\replay_paper_experiment.py --repo D:\nautilus --experiment %EXP% --phase-root %EXP%\workers\BTCUSDT --candidate-id pc_2fe14acb95eac19f88d4 > %EXP%\logs\preliminary_as_recorded_replay.log 2>&1
echo %errorlevel% > %EXP%\logs\preliminary_as_recorded_replay.exitcode
endlocal
