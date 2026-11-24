@echo off
rem ===========================================================================
rem  YouTube Downloader - one-command build script
rem
rem  Usage:
rem    build.bat            -> windowed desktop exe (default)
rem    build.bat console    -> same, but keeps a console window (for debugging)
rem
rem  Output: dist\YouTubeDownloader\  (onedir layout)
rem    YouTubeDownloader.exe
rem    _internal\app.py          (Streamlit script, loaded at runtime)
rem    _internal\bin\ffmpeg.exe  (bundled FFmpeg)
rem    _internal\bin\ffprobe.exe
rem ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "APPNAME=YouTubeDownloader"
set "CONSOLE_FLAG=--windowed"
if /i "%~1"=="console" set "CONSOLE_FLAG=--console"

rem Prefer the local virtualenv when present
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

echo [1/7] Checking Python...
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)"
if errorlevel 1 goto :err_python

echo [2/7] Checking build dependencies...
"%PYTHON%" -c "import PyInstaller, streamlit_desktop_app, webview; print('deps ok')"
if errorlevel 1 goto :err_deps

echo [3/7] Generating icon...
"%PYTHON%" make_icon.py
if errorlevel 1 goto :err

echo [4/7] Checking bundled FFmpeg in vendor\bin...
if not exist "vendor\bin\ffmpeg.exe"  goto :err_ffmpeg
if not exist "vendor\bin\ffprobe.exe" goto :err_ffmpeg

echo [5/7] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist "%APPNAME%.spec" del /q "%APPNAME%.spec"

echo [6/7] Running PyInstaller (this takes a few minutes)...
"%PYTHON%" -m PyInstaller ^
  --noconfirm --clean %CONSOLE_FLAG% ^
  --name "%APPNAME%" ^
  --icon "assets\app.ico" ^
  --paths . ^
  --collect-all streamlit ^
  --copy-metadata streamlit ^
  --copy-metadata altair ^
  --collect-all yt_dlp ^
  --collect-all webview ^
  --collect-all clr_loader ^
  --collect-all pythonnet ^
  --hidden-import clr ^
  --hidden-import pythonnet ^
  --collect-submodules core ^
  --collect-submodules ui ^
  --add-data "app.py;." ^
  --add-data "vendor\bin;bin" ^
  --add-data "assets;assets" ^
  --exclude-module pytest ^
  "desktop_entry.py"
if errorlevel 1 goto :err

echo [7/7] Verifying output...
if not exist "dist\%APPNAME%\%APPNAME%.exe"          goto :err_verify
if not exist "dist\%APPNAME%\_internal\app.py"       goto :err_verify
if not exist "dist\%APPNAME%\_internal\bin\ffmpeg.exe"  goto :err_verify
if not exist "dist\%APPNAME%\_internal\bin\ffprobe.exe" goto :err_verify

rem app.py is exec'd at runtime, so PyInstaller cannot see its imports.
rem Run the packaged binary to prove ui/core were actually bundled.
echo       Running import self-check (ui/core bundled?)...
"dist\%APPNAME%\%APPNAME%.exe" --check-imports
if errorlevel 1 goto :err_imports

echo.
echo Build finished: dist\%APPNAME%\
echo Next: copy the whole "dist\%APPNAME%" folder to D:\YouTube_Downloader\
echo       (see DEPLOY.md)
exit /b 0

:err_python
echo [ERROR] Python 3.11+ not found in PATH.
exit /b 10

:err_deps
echo [ERROR] Missing build dependencies. Run:
echo         python -m pip install -r requirements-build.txt
exit /b 11

:err_ffmpeg
echo [ERROR] Bundled FFmpeg missing. Expected:
echo         vendor\bin\ffmpeg.exe
echo         vendor\bin\ffprobe.exe
echo         Download the "essentials" build from https://www.gyan.dev/ffmpeg/builds/
echo         and copy ffmpeg.exe + ffprobe.exe into vendor\bin\
exit /b 12

:err_verify
echo [ERROR] Output verification failed - see messages above.
exit /b 13

:err
echo [ERROR] Build step failed (see output above).
exit /b 1
