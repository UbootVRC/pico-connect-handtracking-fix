@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "HERE=%~dp0"
set "INSTALLROOT=%HERE%"

echo ============================================================
echo  PICO Hand Tracking Fix - environment installer
echo ============================================================
echo(

rem --- Pick the install location -------------------------------------
rem Miniconda's silent installer rejects target paths containing spaces
rem or special characters such as parentheses. Use this folder if its
rem path is clean, otherwise fall back to a per-user location.
rem ("Run PICO Hand Tracking Fix.bat" checks all of these.)
set "PATHTEST=!INSTALLROOT!"
call :checkpath
if not errorlevel 1 goto :location_ok
set "INSTALLROOT=%LOCALAPPDATA%\PicoHandFix\"
set "PATHTEST=!INSTALLROOT!"
call :checkpath
if not errorlevel 1 goto :fallback_note
set "INSTALLROOT=%SystemDrive%\PicoHandFix\"
:fallback_note
echo NOTE: this folder's path contains characters the Miniconda
echo installer can't handle (a space, parentheses, ...), so the
echo environment will be installed to:
echo   !INSTALLROOT!
echo The fix itself still runs from this folder - no action needed.
echo(
:location_ok

set "ENVDIR=!INSTALLROOT!env"
set "MINICONDA=!INSTALLROOT!miniconda"
set "CONDA=!MINICONDA!\Scripts\conda.exe"

if not exist "!ENVDIR!\python.exe" goto :need_install
echo An environment already exists at:
echo   !ENVDIR!
echo Nothing to do. Delete that "env" folder to reinstall.
goto :done

:need_install
if exist "!CONDA!" goto :createenv

echo Step 1/3: downloading Miniconda...
echo(
curl.exe -L -o miniconda_installer.exe "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
if not errorlevel 1 goto :dl_done
echo curl failed, trying PowerShell instead...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe' -OutFile 'miniconda_installer.exe'"
:dl_done
if exist "miniconda_installer.exe" goto :dl_ok
echo ERROR: download failed. Check your internet connection and retry.
goto :fail
:dl_ok

echo(
echo Step 2/3: installing Miniconda into:
echo   !MINICONDA!
echo Nothing is added to PATH or the registry. This can take a few
echo minutes with no visible progress - please wait...
echo(
if not exist "!INSTALLROOT!" mkdir "!INSTALLROOT!" >nul 2>nul
start /wait "" miniconda_installer.exe /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=!MINICONDA!
del /q miniconda_installer.exe >nul 2>nul

if exist "!CONDA!" goto :miniconda_ok
echo ERROR: Miniconda installation failed.
goto :fail
:miniconda_ok
echo Miniconda installed.
echo(

:createenv
echo Step 3/3: creating the environment at:
echo   !ENVDIR!
echo(
call "!CONDA!" create -y -p "!ENVDIR!" python=3.11
if errorlevel 1 goto :envfail
if not exist "!ENVDIR!\python.exe" goto :envfail

echo(
echo ============================================================
echo  Done - environment ready at:
echo    !ENVDIR!
echo  Next: double-click "Run PICO Hand Tracking Fix.bat"
echo ============================================================
goto :done

:envfail
echo ERROR: conda failed to create the environment.
goto :fail

:fail
echo(
echo Installation did NOT complete.
pause
exit /b 1

:done
echo(
pause
exit /b 0

rem --- helpers ---------------------------------------------------------
rem Returns errorlevel 0 if PATHTEST contains only characters the
rem Miniconda silent installer accepts, 1 otherwise.
:checkpath
powershell -NoProfile -Command "if ($env:PATHTEST -match '^[A-Za-z0-9._\\:\-]+$') { exit 0 } exit 1"
exit /b %errorlevel%
