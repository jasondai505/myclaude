@echo off
setlocal
chcp 65001 >nul

set HEADLESS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set "BASE=C:\Users\daixin\myclaude"
set "LOG=%BASE%\bom_analyzer\reports\_cron_bom.log"
set "PY=C:\miniconda3\python.exe"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo [%date% %time%] start BOM daily batch for %TODAY% >> "%LOG%"

cd /d "%BASE%"
"%PY%" "%BASE%\bom_analyzer\run.py" --daily >> "%LOG%" 2>&1

if errorlevel 1 (
    echo [%date% %time%] BOM daily failed >> "%LOG%"
) else (
    echo [%date% %time%] BOM daily done >> "%LOG%"
)

endlocal
