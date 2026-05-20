@echo off
REM AliExpress Gmail Order Analyzer Daily Executor Script
REM Automatically runs in the project directory using the uv environment.

cd /d "c:\Users\rmelamed\Projects\ali-express-orders-analyzer"

echo Running AliExpress Order Status Check...
echo ----------------------------------------
uv run src/main.py
echo ----------------------------------------
echo Done! Opening report...
start reports\latest_report.html
pause
