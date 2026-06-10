@echo off
setlocal
chcp 65001 >nul

set "PY=C:\miniconda3\python.exe"
set "BASE=C:\Users\daixin\myclaude"
set "LOG=%BASE%\dashboard\logs\pipeline_pre.log"

echo [%date% %time%] morning pipeline start >> "%LOG%"
cd /d "%BASE%"
"%PY%" orchestrator.py pre >> "%LOG%" 2>&1
echo [%date% %time%] morning pipeline done (exit=%ERRORLEVEL%) >> "%LOG%"
endlocal
