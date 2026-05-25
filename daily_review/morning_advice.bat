@echo off
setlocal
chcp 65001 >nul

set "BASE=C:\Users\daixin\myclaude\daily_review"
set "LOG=%BASE%\reports\_cron_advice.log"
cd /d "%BASE%"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo [%date% %time%] start morning collect >> "%LOG%"
"C:\miniconda3\python.exe" "%BASE%\daily_collect.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: daily_collect exited with error >> "%LOG%"

echo [%date% %time%] start claude advice for %TODAY% >> "%LOG%"
powershell -NoProfile -Command "$t='%TODAY%'; $p=(Get-Content '%BASE%\claude_prompt.txt' -Raw).Replace('%%TODAY%%',$t); claude -p $p --dangerously-skip-permissions" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: claude exited with error >> "%LOG%"

echo [%date% %time%] done >> "%LOG%"
endlocal
