@echo off
:: BridgioWatchdog Service Installer
:: Must be run as Administrator

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Run as Administrator
    pause & exit /b 1
)

set DEST=C:\ProgramData\Bridgio
echo Installing Bridgio to %DEST% ...

:: Create destination folder
if not exist "%DEST%" mkdir "%DEST%"

:: Copy files
copy /y "%~dp0BridgioAgent.exe"    "%DEST%\BridgioAgent.exe"    >nul
copy /y "%~dp0BridgioWatchdog.exe" "%DEST%\BridgioWatchdog.exe" >nul
if exist "%~dp0config.json" (
    if not exist "%DEST%\config.json" (
        copy /y "%~dp0config.json" "%DEST%\config.json" >nul
    )
)

:: Install and start the watchdog service
echo Installing BridgioWatchdog service...
"%DEST%\BridgioWatchdog.exe" install
if %errorLevel% neq 0 (
    echo ERROR: Service install failed.
    pause & exit /b 1
)

:: Set service to auto-restart on failure (3 times, every 60s)
sc failure BridgioWatchdog reset= 86400 actions= restart/60000/restart/60000/restart/60000

:: Start the service
echo Starting service...
net start BridgioWatchdog

echo.
echo Done! BridgioWatchdog is now running as a Windows Service.
echo To stop:   net stop BridgioWatchdog
echo To remove: "%DEST%\BridgioWatchdog.exe" remove
echo.
pause
