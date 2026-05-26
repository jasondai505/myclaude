@echo off
setlocal
chcp 65001 >nul

set "PY=C:\miniconda3\python.exe"
set "BASE=C:\Users\daixin\myclaude\morning_intel"
set "LOG=%BASE%\reports\_cron_validate.log"

echo [%date% %time%] intraday validate >> "%LOG%"
cd /d "%BASE%"
"%PY%" run_morning.py --phase intraday >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: validate failed >> "%LOG%"
endlocal
