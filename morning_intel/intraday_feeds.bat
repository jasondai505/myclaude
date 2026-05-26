@echo off
setlocal
chcp 65001 >nul

set "PY=C:\miniconda3\python.exe"
set "BASE=C:\Users\daixin\myclaude\morning_intel"
set "LOG=%BASE%\reports\_cron_intraday.log"

echo [%date% %time%] intraday feed refresh >> "%LOG%"
cd /d "%BASE%"
"%PY%" "%BASE%\intraday_feeds.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: intraday_feeds failed >> "%LOG%"

echo [%date% %time%] weibo watch >> "%LOG%"
"%PY%" "%BASE%\weibo_watch.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: weibo_watch failed >> "%LOG%"
endlocal
