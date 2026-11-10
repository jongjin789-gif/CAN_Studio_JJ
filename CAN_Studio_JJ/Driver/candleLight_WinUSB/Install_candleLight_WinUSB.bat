@echo off
rem candleLight (gs_usb) USB-CAN adapter: switch the CAN interface to the built-in WinUSB driver.
rem Double-click this file. Administrator permission is requested automatically.
rem Check only (no changes, no admin needed): Install_candleLight_WinUSB.bat -DryRun

if /i "%~1"=="-DryRun" goto run

net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_candleLight_winusb.ps1" %*
echo.
pause
