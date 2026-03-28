@echo off
chcp 65001 >nul 2>&1
title News Bot US - Stop All
color 0C

echo Stopping all processes...
echo.

taskkill /FI "WINDOWTITLE eq NewsRadar" /T /F >nul 2>&1
echo [1/4] NewsRadar stopped
taskkill /FI "WINDOWTITLE eq StockConsumer" /T /F >nul 2>&1
echo [2/4] StockConsumer stopped
taskkill /FI "WINDOWTITLE eq OptionConsumer" /T /F >nul 2>&1
echo [3/4] OptionConsumer stopped
taskkill /FI "WINDOWTITLE eq Dashboard" /T /F >nul 2>&1
echo [4/4] Dashboard stopped

echo.
echo All stopped.
pause
