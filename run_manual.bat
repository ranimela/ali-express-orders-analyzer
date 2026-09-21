@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   AliExpress Orders Analyzer - Manual Execution
echo ========================================================
echo.

uv run src/main.py %*

if errorlevel 2 goto auth_error
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

:auth_error
echo.
echo --------------------------------------------------------
echo [CRITICAL ERROR] Gmail App Password failed authentication!
echo Your 16-character Google App Password appears to be invalid or expired.
echo Please generate a new App Password at:
echo   https://myaccount.google.com/apppasswords
echo and update EMAIL_PASSWORD in your .env file.
echo --------------------------------------------------------
goto end

:error
echo.
echo --------------------------------------------------------
echo [ERROR] Pipeline encountered an error.
echo --------------------------------------------------------
goto end

:end
echo.
pause
