@echo off
rem One-time setup on the gaming PC (Windows). Needs Python 3.10+ installed
rem (python.org installer, "py" launcher checked). Safe to re-run.
rem NB: no %errorlevel% inside ( ) blocks -- cmd expands it at parse time;
rem the runtime-safe "if errorlevel 1" / goto form is used instead.
cd /d "%~dp0"

echo == poe-league-tools PC setup ==

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

echo.
echo == Setup done. Next steps ==
echo  1. Edit overlay\config.json: check client_txt points at your
echo     Client.txt (auto-detection probes the common Steam/standalone
echo     paths if the configured one is missing).
echo  2. Optional LLM features (advisor, briefs, tradeq, retro text):
echo        .venv\Scripts\pip install anthropic
echo     then set ANTHROPIC_API_KEY in your environment.
echo  3. Run the overlay: double-click overlay\run_overlay.bat
echo  4. Sanity check: .venv\Scripts\python.exe tools\check.py
echo.
pause
