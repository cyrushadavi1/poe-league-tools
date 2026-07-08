@echo off
rem Double-click launcher for the gaming PC. Run setup_pc.bat (repo root)
rem once first. Prefers the portable bundle's python\, then the .venv,
rem then a system Python with PyQt6 installed.
rem NB: no %errorlevel% inside ( ) blocks -- cmd expands it at parse time.
cd /d "%~dp0"

if not exist ..\python\python.exe goto :venvpython
..\python\python.exe main.py %*
if errorlevel 1 echo !! Overlay exited with an error - read the message
if errorlevel 1 echo    above, then double-click doctor.bat in the repo root.
pause
exit /b

:venvpython
if not exist ..\.venv\Scripts\python.exe goto :syspython
..\.venv\Scripts\python.exe main.py %*
if errorlevel 1 echo !! Overlay exited with an error - read the message
if errorlevel 1 echo    above, then double-click doctor.bat in the repo root.
pause
exit /b

:syspython
where py >nul 2>nul
if errorlevel 1 goto :plain
py -3 main.py %*
if errorlevel 1 echo !! Overlay exited with an error - read the message
if errorlevel 1 echo    above, then double-click doctor.bat in the repo root.
pause
exit /b

:plain
python main.py %*
if errorlevel 1 echo !! Overlay exited with an error - read the message
if errorlevel 1 echo    above, then double-click doctor.bat in the repo root.
pause
