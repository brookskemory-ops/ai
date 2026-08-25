@echo off
rem Double-click this file to start GameDeck.
title GameDeck
cd /d "%~dp0"

set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"

if not defined PYTHON (
  echo.
  echo   Python 3 was not found on this PC.
  echo   Install it from https://www.python.org/downloads/ and be sure to tick
  echo   "Add python.exe to PATH" in the installer, then run this file again.
  echo.
  pause
  exit /b 1
)

echo Starting GameDeck...
%PYTHON% -m gamedeck %*

rem Keep the window open if something went wrong, so the error is readable.
if errorlevel 1 pause
