@echo off
REM Quick launcher for Photo & Video Importer
REM Double-click this file to run the importer with folder dialogs

echo ======================================================================
echo PHOTO ^& VIDEO IMPORTER - QUICK LAUNCH
echo ======================================================================
echo.
echo This will open folder selection dialogs.
echo.
echo ======================================================================
echo.

python "%~dp0run_importer.py"

echo.
echo ======================================================================
echo Import complete! Press any key to close this window.
echo ======================================================================
pause > nul
