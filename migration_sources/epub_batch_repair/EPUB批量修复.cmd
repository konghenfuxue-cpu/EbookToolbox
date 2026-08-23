@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title EPUB Repair Tool

set "REPAIR_SCRIPT="
for %%F in ("%~dp0epub_repair*.py") do (
  if not defined REPAIR_SCRIPT if exist "%%~fF" set "REPAIR_SCRIPT=%%~fF"
)

if not defined REPAIR_SCRIPT (
  echo [ERROR] No epub_repair Python script was found in this folder.
  echo Keep this CMD file and epub_repair*.py in the same folder.
  echo.
  pause
  exit /b 4
)

if "%~1"=="" (
  echo Drag EPUB files or a folder onto this CMD file.
  echo.
  pause
  exit /b 2
)

where py.exe >nul 2>nul
if not errorlevel 1 (
  py.exe -3 "%REPAIR_SCRIPT%" --overwrite %*
  set "EXIT_CODE=%errorlevel%"
  goto :finished
)

where python.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3 was not found.
  echo Install Python 3 and enable Add Python to PATH.
  echo.
  pause
  exit /b 3
)

python.exe "%REPAIR_SCRIPT%" --overwrite %*
set "EXIT_CODE=%errorlevel%"

:finished
echo.
echo Exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
