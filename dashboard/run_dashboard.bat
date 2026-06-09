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

rem 跳过 Streamlit 首次运行 email 注册提示
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" echo [general] > "%USERPROFILE%\.streamlit\credentials.toml" && echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
if not exist "%USERPROFILE%\.streamlit\config.toml" echo [server] > "%USERPROFILE%\.streamlit\config.toml" && echo headless = true >> "%USERPROFILE%\.streamlit\config.toml"

python -m streamlit run app.py --server.port 8501 --server.headless true

pause
