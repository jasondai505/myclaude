@echo off
setlocal
chcp 65001 >nul

set HEADLESS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set "BASE=C:\Users\daixin\myclaude\daily_review"
set "LOG=%BASE%\reports\_cron_advice.log"
set "PY=C:\miniconda3\python.exe"

for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-1).ToString('yyyy-MM-dd')"') do set YESTERDAY=%%i

echo [%date% %time%] start morning pipeline for %TODAY% >> "%LOG%"

rem === Step 1: daily_collect ===
echo [%date% %time%] Step 1: daily_collect >> "%LOG%"
cd /d "%BASE%"
"%PY%" "%BASE%\daily_collect.py" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WARNING: daily_collect failed, continuing with cached data >> "%LOG%"
)

rem === Step 2: wechat AI analysis ===
echo [%date% %time%] Step 2: analyze_wechat >> "%LOG%"
powershell -NoProfile -Command "$feed='%BASE%\reports\feeds\wechat_%TODAY%.md'; if (Test-Path $feed) { $c=Get-Content $feed -Raw; if ($c -match '新文章' -or $c.Length -gt 200) { exit 0 } } exit 1" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] WARNING: wechat feed empty or missing, skip AI analysis >> "%LOG%"
) else (
    "%PY%" "%BASE%\analyze_wechat.py" >> "%LOG%" 2>&1
    if errorlevel 1 echo [%date% %time%] WARNING: analyze_wechat failed >> "%LOG%"
)

rem === Step 3: review_summary ===
echo [%date% %time%] Step 3: review_summary >> "%LOG%"
"%PY%" "%BASE%\review_summary.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: review_summary failed >> "%LOG%"

rem === Step 4: track_recommendations ===
echo [%date% %time%] Step 4: track_recommendations >> "%LOG%"
"%PY%" "%BASE%\track_recommendations.py" >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: track_recommendations failed >> "%LOG%"

rem === Step 5: morning intel — supply chain mapping + catalyst → targets ===
echo [%date% %time%] Step 5: morning_intel interpret >> "%LOG%"
"%PY%" "%BASE%\..\morning_intel\run_morning.py" --phase pre --date %TODAY% >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: morning_intel interpret failed >> "%LOG%"

rem === Step 6: classic advice (吸收公众号分析结论 + 星球信号 + 外围映射) ===
echo [%date% %time%] Step 6: classic advice >> "%LOG%"
"%PY%" "%BASE%\_run_advice.py" %TODAY% %YESTERDAY% >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: classic advice failed >> "%LOG%"

echo [%date% %time%] pipeline done >> "%LOG%"
endlocal
