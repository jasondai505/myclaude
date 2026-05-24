@echo off
chcp 65001 >nul
cd /d %~dp0
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo [%date% %time%] start morning collect >> reports\_cron_advice.log
"C:\miniconda3\python.exe" daily_collect.py >> reports\_cron_advice.log 2>&1

echo [%date% %time%] start claude advice for %TODAY% >> reports\_cron_advice.log
claude -p "今天是 %TODAY%（A股交易日早盘前）。请阅读 reports 目录下最新的 review_*.md 复盘报告，以及 reports\feeds 下今日各基本面源（业绩预告/快报 earnings、机构调研 surveys、限售解禁 lockups、一致预期EPS eps、行业研报 industry、公告 announcements、个股新闻 news、互动易 interactions、知识星球 zsxq）。如需隔夜外围市场数据，运行 python -c \"import data; print(data.fetch_global_markets())\"。综合以上，按既有 advice 模板生成今日投资建议，写入 reports\advice_%TODAY%.md。要求：结论先行、简洁直接、给出仓位/主线/回避清单。" --dangerously-skip-permissions >> reports\_cron_advice.log 2>&1
echo [%date% %time%] done >> reports\_cron_advice.log
