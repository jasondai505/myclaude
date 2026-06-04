@echo off
cd /d %~dp0
echo ========================================
echo   A股量化仪表盘
echo ========================================
echo.
echo 正在启动 Streamlit...
echo 浏览器打开后访问 http://localhost:8501
echo 按 Ctrl+C 停止
echo ========================================
echo.

python -m streamlit run app.py --server.port 8501 --server.headless true

pause
