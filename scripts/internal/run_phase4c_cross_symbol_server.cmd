@echo off
cd /d D:\nautilus
if not exist outputs\baseline_evaluation\phase4c mkdir outputs\baseline_evaluation\phase4c
.venv\Scripts\python.exe -m scripts.internal.run_phase4c_cross_symbol 1>>outputs\baseline_evaluation\phase4c\phase4c_run_stdout.log 2>>outputs\baseline_evaluation\phase4c\phase4c_run_stderr.log
echo exit_code=%ERRORLEVEL%>>outputs\baseline_evaluation\phase4c\phase4c_run_stdout.log
