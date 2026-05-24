@echo off
chcp 65001 >nul
cd /d %~dp0
echo [%date% %time%] start daily review >> reports\_cron_review.log
"C:\miniconda3\python.exe" run.py >> reports\_cron_review.log 2>&1
echo [%date% %time%] done >> reports\_cron_review.log
