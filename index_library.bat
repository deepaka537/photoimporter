@echo off
REM Index your existing camera footage library
REM Run this ONCE before your first import to prevent duplicates

echo ======================================================================
echo INDEX EXISTING LIBRARY - FIRST TIME SETUP
echo ======================================================================
echo.
echo This will scan your existing camera footage library and add all
echo media files to the import log, preventing duplicate imports.
echo.
echo IMPORTANT: Run this ONCE before your first import!
echo.
echo ======================================================================
echo.

python "%~dp0import_media.py" --index-destination

echo.
echo ======================================================================
echo Indexing complete! Press any key to close this window.
echo ======================================================================
pause > nul
