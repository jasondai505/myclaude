@echo off
setlocal
chcp 65001 >nul

set HEADLESS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set "BASE=C:\Users\daixin\myclaude\daily_review"
set "LOG=%BASE%\reports\_cron_review.log"
set "PY=C:\miniconda3\python.exe"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo [%date% %time%] start post-close pipeline for %TODAY% >> "%LOG%"

rem === Step 1: daily_collect ===
echo [%date% %time%] Step 1: daily_collect >> "%LOG%"
cd /d "%BASE%"
"%PY%" "%BASE%\daily_collect.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: daily_collect failed >> "%LOG%"

rem === Step 2: daily_review ===
echo [%date% %time%] Step 2: daily_review >> "%LOG%"
"%PY%" "%BASE%\run.py" --date %TODAY% >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: daily_review failed >> "%LOG%"

rem === Step 3: daily_brief ===
echo [%date% %time%] Step 3: daily_brief >> "%LOG%"
"%PY%" "%BASE%\..\morning_intel\daily_brief.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: daily_brief failed >> "%LOG%"

echo [%date% %time%] pipeline done >> "%LOG%"
endlocal
