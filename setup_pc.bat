@echo off
rem One-time setup on the gaming PC (Windows). Safe to re-run.
rem Portable bundle (made by tools\make_portable.py) ships its own
rem python\ dir -- then NOTHING needs installing. Otherwise this needs
rem Python 3.10+ (python.org installer, "py" launcher checked).
rem NB: no %errorlevel% inside ( ) blocks -- cmd expands it at parse time;
rem the runtime-safe "if errorlevel 1" / goto form is used instead.
cd /d "%~dp0"

echo == poe-league-tools PC setup ==

if not exist python\python.exe goto :needvenv
echo Bundled Python found - nothing to install.
set "PY=python\python.exe"
goto :wizard

:needvenv
if exist .venv\Scripts\python.exe goto :deps

echo Creating .venv ...
where py >nul 2>nul
if errorlevel 1 goto :plainpython
py -3 -m venv .venv
goto :checkvenv

:plainpython
python -m venv .venv

:checkvenv
if not exist .venv\Scripts\python.exe (
    echo FAILED to create .venv - is Python 3.10+ installed? https://python.org
    pause
    exit /b 1
)

:deps
echo Installing requirements ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo pip install FAILED - check your internet connection and re-run.
    pause
    exit /b 1
)
set "PY=.venv\Scripts\python.exe"

:wizard
echo.
echo == First-run setup (finds Client.txt, asks who you are) ==
%PY% tools\join_party.py
echo.
echo == Zone-layout images (optional, ~15 MB, safe to skip) ==
%PY% tools\fetch_layouts.py
if errorlevel 1 echo (layout pack skipped - overlay works without it; re-run to retry)
echo.
echo Re-run this file any time. doctor.bat = health check.
echo Optional LLM extras need a pip install - see README, LLM features.
echo.
pause
