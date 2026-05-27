@echo off
setlocal
chcp 65001 >nul

set HEADLESS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set "BASE=C:\Users\daixin\myclaude\morning_intel"
set "LOG=%BASE%\reports\_cron_pre_market.log"
set "PY=C:\miniconda3\python.exe"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo [%date% %time%] 9:00 pre-market refresh >> "%LOG%"

cd /d "%BASE%"
"%PY%" "%BASE%\pre_market_refresh.py" %TODAY% >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: pre_market_refresh failed >> "%LOG%"

echo [%date% %time%] pre-market refresh done >> "%LOG%"
endlocal
