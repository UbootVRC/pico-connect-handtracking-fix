@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if exist "pico_handtracking_fix.py" goto :havescript
echo ERROR: pico_handtracking_fix.py was not found in this folder.
echo Keep this .bat next to the Python script.
echo(
pause
exit /b 1
:havescript

rem The environment lives next to the scripts, unless this folder's path
rem has characters the Miniconda installer can't handle (spaces, parens,
rem ...) - then the installer put it in a fallback location instead.
set "PYEXE=%~dp0env\python.exe"
if exist "!PYEXE!" goto :havepython
set "PYEXE=%LOCALAPPDATA%\PicoHandFix\env\python.exe"
if exist "!PYEXE!" goto :havepython
set "PYEXE=%SystemDrive%\PicoHandFix\env\python.exe"
if exist "!PYEXE!" goto :havepython

echo ERROR: no environment found. Looked in:
echo   %~dp0env
echo   %LOCALAPPDATA%\PicoHandFix\env
echo   %SystemDrive%\PicoHandFix\env
echo Run "Install Environment.bat" first.
echo(
pause
exit /b 1

:havepython
echo Starting the PICO hand tracking fix...
echo (If a Windows UAC prompt appears, click YES - admin rights are
echo  needed to edit the hosts file. The tool opens in a new window
echo  when it elevates.)
echo(

rem Any extra arguments are passed straight to the script,
rem e.g.:  "Run PICO Hand Tracking Fix.bat" --restore
"!PYEXE!" "%~dp0pico_handtracking_fix.py" %*

echo(
pause
exit /b %errorlevel%
