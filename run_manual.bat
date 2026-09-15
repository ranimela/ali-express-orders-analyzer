@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   AliExpress Orders Analyzer - Manual Execution
echo ========================================================
echo.

uv run src/main.py %*

if errorlevel 1 goto error

echo.
echo --------------------------------------------------------
echo [SUCCESS] Pipeline completed successfully!
echo --------------------------------------------------------
if exist "reports\latest_report.html" (
    echo Opening latest report in your default browser...
    start "" "reports\latest_report.html"
)
goto end

:error
echo.
echo --------------------------------------------------------
echo [ERROR] Pipeline encountered an error.
echo --------------------------------------------------------

:end
echo.
pause
