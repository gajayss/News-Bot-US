@echo off
chcp 65001 >nul 2>&1
title News Bot US - Launcher
color 0A

echo ============================================
echo   News Bot US - Auto Launcher
echo ============================================
echo.

cd /d %~dp0

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH
    pause
    exit /b 1
)

if not exist "runtime\interface" mkdir "runtime\interface"
if not exist "runtime\logs" mkdir "runtime\logs"

echo [1/4] Starting News Radar...
start "NewsRadar" /min python run_news_radar.py
echo       OK

echo       Waiting 10s for initial scan...
timeout /t 10 /nobreak >nul

echo [2/4] Starting Stock Consumer...
start "StockConsumer" /min python run_stock_consumer.py
echo       OK

timeout /t 3 /nobreak >nul

echo [3/4] Starting Option Consumer...
start "OptionConsumer" /min python run_option_consumer.py
echo       OK

timeout /t 3 /nobreak >nul

echo [4/4] Starting Dashboard...
start "Dashboard" /min python run_dashboard.py
echo       OK

echo.
echo ============================================
echo   All 4 processes started!
echo ============================================
echo.
echo   Dashboard: http://127.0.0.1:6100
echo   Stop all : stop.bat
echo   Status   : status.bat
echo ============================================
pause
