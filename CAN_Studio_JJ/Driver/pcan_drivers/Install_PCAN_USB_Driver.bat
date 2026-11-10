@echo off
rem PEAK-System PCAN-USB device driver (pcan_usb.inf 5.1.1, Microsoft WHQL signed, x64).
rem Double-click this file. Administrator permission is requested automatically.
rem Supported: PCAN-USB, PCAN-USB FD, PCAN-USB Pro FD, PCAN-Chip USB, PCAN-USB X6, PCAN-USB Hub/XL.

if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo [ERROR] This driver package is for 64-bit Windows ^(x64^) only.
    echo         Download the PEAK driver from https://www.peak-system.com/Drivers.523.0.html
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo  PEAK PCAN-USB driver install
echo ============================================================
pnputil /add-driver "%~dp0pcan_usb.inf" /install
set RC=%errorlevel%
echo.
if "%RC%"=="0" (
    echo [OK] Driver installed. Unplug the PCAN USB device and plug it in again.
) else if "%RC%"=="3010" (
    echo [OK] Driver installed. Restart Windows to finish.
) else if "%RC%"=="259" (
    echo [OK] The same or a newer driver is already installed. Nothing changed.
) else (
    echo [ERROR] pnputil exit code %RC%. Install the full PEAK driver setup instead:
    echo         https://www.peak-system.com/Drivers.523.0.html
)
echo.
pause
