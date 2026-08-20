@echo off
title Libre_Core AutoLoader - VM Simulator
cd /d "%~dp0"
python launch_vm.py
if errorlevel 1 (
    echo.
    echo Press any key to exit...
    pause >nul
)
