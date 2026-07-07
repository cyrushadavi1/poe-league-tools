@echo off
rem Double-click launcher for the gaming PC. Run setup_pc.bat (repo root)
rem once first; falls back to a system Python with PyQt6 installed.
rem NB: no %errorlevel% inside ( ) blocks -- cmd expands it at parse time.
cd /d "%~dp0"

if not exist ..\.venv\Scripts\python.exe goto :syspython
..\.venv\Scripts\python.exe main.py %*
pause
exit /b

:syspython
where py >nul 2>nul
if errorlevel 1 goto :plain
py -3 main.py %*
pause
exit /b

:plain
python main.py %*
pause
