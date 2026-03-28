@echo off
setlocal
start cmd /k "python run_news_radar.py"
timeout /t 2 > nul
start cmd /k "python run_stock_consumer.py"
timeout /t 2 > nul
start cmd /k "python run_option_consumer.py"
echo US News Live Bridge started.
pause
