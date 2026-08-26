@echo off
cd /d D:\nautilus
:wait_for_runs
if exist outputs\batches\phase4c_cross_symbol\phase4c_run_validation.json goto build_results
timeout /t 30 /nobreak >nul
goto wait_for_runs
:build_results
.venv\Scripts\python.exe -m scripts.internal.build_phase4c_cross_symbol 1>>outputs\baseline_evaluation\phase4c\phase4c_finish_stdout.log 2>>outputs\baseline_evaluation\phase4c\phase4c_finish_stderr.log
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pytest tests\unit_tests\strategy_framework\test_phase4a_baseline_evaluation.py tests\unit_tests\strategy_framework\test_phase4b_cost_episode_audit.py tests\unit_tests\strategy_framework\test_phase4c_cross_symbol.py -q 1>>outputs\baseline_evaluation\phase4c\phase4c_tests.log 2>&1
if errorlevel 1 goto failed
echo status=PASSED>>outputs\baseline_evaluation\phase4c\phase4c_finish_stdout.log
exit /b 0
:failed
echo status=FAILED exit_code=%ERRORLEVEL%>>outputs\baseline_evaluation\phase4c\phase4c_finish_stderr.log
exit /b 1
