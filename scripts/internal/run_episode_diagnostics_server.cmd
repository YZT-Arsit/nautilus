@echo off
setlocal
cd /d D:\nautilus
set SOURCE=D:\nautilus\outputs\deliverables\existing_registered_strategies_current
set OUT=D:\nautilus\outputs\deliverables\existing_registered_strategies_corrected
set PARTIAL=D:\nautilus\outputs\deliverables\existing_registered_strategies_episode_diagnostics_v2
set ZIP=D:\nautilus\outputs\deliverables\existing_registered_strategies_corrected_review.zip
if not exist "%OUT%" mkdir "%OUT%"
.venv\Scripts\python.exe -m scripts.internal.audit_boss_direction_model --source-root "%SOURCE%" --output-root "%OUT%" 1>"%OUT%\direction_audit_pre_stdout.log" 2>"%OUT%\direction_audit_pre_stderr.log"
if errorlevel 1 exit /b %ERRORLEVEL%
.venv\Scripts\python.exe -m scripts.internal.build_episode_diagnostics --source-root "%SOURCE%" --output-root "%OUT%" --workers 2 --max-scatter-points 100000 --overwrite 1>"%OUT%\episode_diagnostics_server_stdout.log" 2>"%OUT%\episode_diagnostics_server_stderr.log"
if errorlevel 1 exit /b %ERRORLEVEL%
.venv\Scripts\python.exe -m scripts.internal.audit_boss_direction_model --source-root "%SOURCE%" --output-root "%OUT%" --revised-root "%OUT%" 1>"%OUT%\direction_audit_post_stdout.log" 2>"%OUT%\direction_audit_post_stderr.log"
if errorlevel 1 exit /b %ERRORLEVEL%
.venv\Scripts\python.exe -m scripts.internal.cleanup_wrong_direction_results --machine server --source-root "%SOURCE%" --corrected-root "%OUT%" --partial-wrong-root "%PARTIAL%" --old-zip "D:\nautilus\outputs\deliverables\existing_registered_strategies_episode_diagnostics.zip" --old-zip "D:\nautilus\outputs\deliverables\existing_registered_strategies_episode_diagnostics_v2.zip" --manifest "%OUT%\server_wrong_direction_cleanup_manifest_dry_run.csv" 1>"%OUT%\cleanup_dry_run_stdout.log" 2>"%OUT%\cleanup_dry_run_stderr.log"
if errorlevel 1 exit /b %ERRORLEVEL%
.venv\Scripts\python.exe -m scripts.internal.cleanup_wrong_direction_results --machine server --source-root "%SOURCE%" --corrected-root "%OUT%" --partial-wrong-root "%PARTIAL%" --old-zip "D:\nautilus\outputs\deliverables\existing_registered_strategies_episode_diagnostics.zip" --old-zip "D:\nautilus\outputs\deliverables\existing_registered_strategies_episode_diagnostics_v2.zip" --manifest "%OUT%\server_wrong_direction_cleanup_manifest.csv" --apply 1>"%OUT%\cleanup_apply_stdout.log" 2>"%OUT%\cleanup_apply_stderr.log"
if errorlevel 1 exit /b %ERRORLEVEL%
.venv\Scripts\python.exe -m scripts.internal.package_episode_diagnostics --canonical-source "%SOURCE%" --diagnostics-source "%OUT%" --destination "%ZIP%" 1>"%OUT%\package_stdout.log" 2>"%OUT%\package_stderr.log"
exit /b %ERRORLEVEL%
