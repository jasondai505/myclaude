@echo off
setlocal & chcp 65001 >nul
set HEADLESS=1 & set PYTHONUNBUFFERED=1 & set PYTHONIOENCODING=utf-8
set "PY=C:\miniconda3\python.exe"
cd /d "C:\Users\daixin\myclaude"
"%PY%" orchestrator.py bom
endlocal
