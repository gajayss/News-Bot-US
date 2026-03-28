@echo off
chcp 65001 >nul 2>&1
title News Bot US - Status

echo ============================================
echo   News Bot US - Process Status
echo ============================================
echo.

tasklist /V /FO CSV 2>nul | findstr /i "NewsRadar" >nul
if %errorlevel%==0 (echo   [ON]  NewsRadar) else (echo   [OFF] NewsRadar)

tasklist /V /FO CSV 2>nul | findstr /i "StockConsumer" >nul
if %errorlevel%==0 (echo   [ON]  StockConsumer) else (echo   [OFF] StockConsumer)

tasklist /V /FO CSV 2>nul | findstr /i "OptionConsumer" >nul
if %errorlevel%==0 (echo   [ON]  OptionConsumer) else (echo   [OFF] OptionConsumer)

tasklist /V /FO CSV 2>nul | findstr /i "Dashboard" >nul
if %errorlevel%==0 (echo   [ON]  Dashboard) else (echo   [OFF] Dashboard)

echo.
pause
