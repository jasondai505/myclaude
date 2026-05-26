@echo off
chcp 65001 >nul

set HEADLESS=1
set PYTHONUNBUFFERED=1

cd /d %~dp0
echo [%date% %time%] start daily review >> reports\_cron_review.log
"C:\miniconda3\python.exe" run.py >> reports\_cron_review.log 2>&1
echo [%date% %time%] review done >> reports\_cron_review.log

rem === daily_brief: merge morning_intel + review into daily report ===
echo [%date% %time%] start daily brief >> reports\_cron_review.log
"C:\miniconda3\python.exe" ..\morning_intel\daily_brief.py >> reports\_cron_review.log 2>&1
echo [%date% %time%] daily brief done >> reports\_cron_review.log
