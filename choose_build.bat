@echo off
rem Re-open the graphical PoB/role picker without reinstalling anything.
cd /d "%~dp0"

if not exist python\python.exe goto :venvpython
python\python.exe tools\setup_gui.py --build-only
goto :done

:venvpython
if not exist .venv\Scripts\python.exe goto :syspython
.venv\Scripts\python.exe tools\setup_gui.py --build-only
goto :done

:syspython
where py >nul 2>nul
if errorlevel 1 goto :plain
py -3 tools\setup_gui.py --build-only
goto :done

:plain
python tools\setup_gui.py --build-only

:done
if errorlevel 1 echo Build selection was cancelled or failed.
pause
